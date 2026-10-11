"""Lua 5.1 contract tests; test-only dependency: lupa==2.6."""
import json
from pathlib import Path
import unittest
from lupa.lua51 import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r'''
gadget = {}
GG = {}
frame = 30
orders, payloads, echoes, winners = {}, {}, {}, {}
units, resources, queues = {}, {[0]=1500,[1]=1500}, {}
nextID=10
options={llm_duel="1",llm_duel_token="test",llm_duel_frames="27000"}
Game={mapSizeX=8192,mapSizeZ=8192}
CMD={FIGHT=16}
UnitDefs={}
UnitDefNames={}
for i,name in ipairs({"armcom","armvp","armflash","armstump","armfav","armap","armfig","armthund","armkam","armpeep","armsam"}) do
 UnitDefs[i]={name=name,customParams={iscommander=i==1 and true or nil}}
 UnitDefNames[name]={id=i}
end
units[1]={def=1,team=0,x=1200,z=1200}
units[2]={def=1,team=1,x=6800,z=6800}
Spring={
 GetModOptions=function() return options end,
 GetGameFrame=function() return frame end,
 GetGroundHeight=function() return 0 end,
 GetTeamStartPosition=function(t) local p=t==0 and 1200 or 6800; return p,0,p end,
 CreateUnit=function(name,x,y,z,tFacing,team)
  nextID=nextID+1; units[nextID]={def=UnitDefNames[name].id,team=team,x=x,z=z}; return nextID
 end,
 GetTeamUnits=function(t)
  local result={}; for id,u in pairs(units) do if u.team==t then result[#result+1]=id end end
  table.sort(result); return result
 end,
 GetUnitDefID=function(id) return units[id] and units[id].def end,
 GetUnitHealth=function(id) return 100,100,0,0,units[id].progress or 1 end,
 GetUnitPosition=function(id) local u=units[id]; return u.x,0,u.z end,
 GetUnitLosState=function(id,t) return {los=units[id].visible or false} end,
 GetTeamResources=function(t) return resources[t] end,
 SetTeamResource=function(t,k,v) if k=='m' then resources[t]=v end end,
 AddTeamResource=function(t,k,v) if k=='m' then resources[t]=resources[t]+v end end,
 UseTeamResource=function(t,k,v) if resources[t]<v then return false end; resources[t]=resources[t]-v; return true end,
 ValidUnitID=function(id) return units[id]~=nil end,
 GetFullBuildQueue=function(id) return queues[id] or {} end,
 GiveOrderToUnit=function(id,cmd,args,opts) orders[#orders+1]={id=id,cmd=cmd,args=args,opts=opts} end,
 Echo=function(s) echoes[#echoes+1]=s end,
 GameOver=function(teams) winners[#winners+1]=teams end,
}
gadgetHandler={IsSyncedCode=function() return true end}
VFS={Include=function() return Json end}
SendToUnsynced=function(name,payload) payloads[#payloads+1]=payload end
'''


class ArenaTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().Json = self.lua.execute((ROOT / 'common/luaUtilities/json.lua').read_text())
        self.lua.execute(HARNESS)
        self.lua.execute((ROOT / 'luarules/gadgets/ai_llm_duel.lua').read_text())
        self.lua.execute('gadget:GameFrame(30); frame=150')

    def run_lua(self, source):
        return self.lua.execute(source)

    def command(self, **changes):
        command = dict(token='test',team=0,frame=150,action='produce',unit='light',count=3,x=.8,z=.8)
        command.update(changes)
        self.lua.globals().message = 'BARDUEL: ' + json.dumps(command)
        self.run_lua('gadget:RecvLuaMsg(message,0)')

    def test_equal_starting_armies(self):
        self.assertEqual(self.run_lua('return #Spring.GetTeamUnits(0),#Spring.GetTeamUnits(1)'), (9,9))

    def test_all_aircraft_route_to_air_factory_and_join_orders(self):
        for i,unit in enumerate(('fighter','bomber','gunship','airscout')):
            frame=150*(i+1)
            self.lua.globals().frame=frame
            self.command(unit=unit,count=1,frame=frame)
            self.assertEqual(self.run_lua('return orders[#orders].id'),12)
        self.run_lua('Spring.CreateUnit("armfig",1200,0,1200,0,0); orders={}; frame=900')
        self.command(action='attack',frame=900)
        self.assertEqual(self.run_lua('return #orders'),7)

    def test_air_queue_limit_and_destroyed_air_factory(self):
        self.run_lua('queues[12]={{[7]=8}}')
        self.command(unit='fighter')
        self.assertEqual(self.run_lua('return #orders'),1)
        self.run_lua('units[12]=nil; frame=300')
        self.command(unit='bomber',frame=300)
        self.assertEqual(self.run_lua('return #orders'),1)

    def test_air_observation_and_antiair_routing(self):
        self.command(unit='antiair',count=1)
        self.assertEqual(self.run_lua('return orders[1].id,orders[1].cmd'),(11,-11))
        self.run_lua('Spring.CreateUnit("armkam",1200,0,1200,0,0); gadget:GameFrame(300)')
        state=json.loads(self.lua.globals().payloads[1])['teams'][0]
        self.assertTrue(state['air_factory_alive'])
        self.assertEqual(state['unit_counts']['armkam'],1)
        self.assertEqual(state['ready_unit_counts']['armkam'],1)

    def test_production_quantity_and_no_shift_multiplier(self):
        self.command()
        self.assertEqual(self.run_lua('return #orders'),3)
        self.assertEqual(self.run_lua('return orders[1].cmd,#orders[1].opts'),(-3,0))

    def test_queue_cap_and_destroyed_factory(self):
        self.run_lua('queues[11]={{[3]=8}}')
        self.command()
        self.assertEqual(self.run_lua('return #orders'),1)
        self.run_lua('units[11]=nil; frame=300')
        self.command(frame=300)
        self.assertEqual(self.run_lua('return #orders'),1)

    def test_auth_duplicate_old_and_future_commands(self):
        for changes in ({'token':'wrong'},{'team':2},{'frame':151},{'frame':-3000}):
            self.command(**changes)
        self.assertEqual(self.run_lua('return #orders'),0)
        self.command()
        self.command()
        self.assertEqual(self.run_lua('return #orders'),3)
        self.run_lua('orders={}; gadget:RecvLuaMsg(message,1)')
        self.assertEqual(self.run_lua('return #orders'),0)

    def test_attack_and_defend_only_combat_units(self):
        self.command(action='attack',x=.5,z=.25)
        self.assertEqual(self.run_lua('return #orders,orders[1].args[1],orders[1].args[3]'),(6,4096,2048))
        self.run_lua('frame=300; orders={}')
        self.command(action='defend',frame=300)
        self.assertEqual(self.run_lua('return #orders,orders[1].args[1],orders[1].args[3]'),(6,1200,1200))

    def test_economy_cost_limit_and_insufficient_funds(self):
        self.run_lua('resources[0]=10000')
        for frame in range(150,1051,150):
            self.lua.globals().frame=frame
            self.command(action='economy',frame=frame)
        self.assertEqual(self.run_lua('return resources[0]'),8500)
        self.run_lua('gadget:GameFrame(1200)')
        state=json.loads(self.lua.globals().payloads[1])['teams'][0]
        self.assertEqual(state['income_upgrades'],5)
        self.assertEqual(state['metal'],8530)
        self.run_lua('resources[1]=299; frame=1350')
        self.command(team=1,action='economy',frame=1350)
        self.assertEqual(self.run_lua('return resources[1]'),299)

    def test_enemy_observations_require_los(self):
        self.run_lua('gadget:GameFrame(150)')
        hidden=json.loads(self.lua.globals().payloads[1])['teams'][0]['enemies']
        self.assertEqual(len(hidden),0)
        self.run_lua('units[2].visible=true; gadget:GameFrame(300)')
        seen=json.loads(self.lua.globals().payloads[2])['teams'][0]['enemies']
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0]['unit'],'armcom')

    def test_commander_death_finishes_once_and_blocks_commands(self):
        self.run_lua('gadget:UnitDestroyed(2,1,1); gadget:TeamDied(1)')
        self.assertEqual(self.run_lua('return #winners,winners[1][1]'),(1,0))
        self.command()
        self.assertEqual(self.run_lua('return #orders'),0)
        self.assertEqual(json.loads(self.lua.globals().payloads[1])['winner'],0)

    def test_time_limit_draw(self):
        self.run_lua('gadget:GameFrame(27000); gadget:GameFrame(27030)')
        self.assertEqual(self.run_lua('return #winners,#winners[1]'),(1,0))
        self.assertEqual(json.loads(self.lua.globals().payloads[1])['winner'],-1)

    def test_development_reload_preserves_match_state(self):
        self.command(action='economy')
        self.lua.execute((ROOT/'luarules/gadgets/ai_llm_duel.lua').read_text())
        self.command(action='economy')
        self.run_lua('gadget:GameFrame(300)')
        state=json.loads(self.lua.globals().payloads[1])['teams'][0]
        self.assertEqual(state['income_upgrades'],1)
        self.assertTrue(state['factory_alive'])
        self.assertEqual(state['combat_units'],6)
        self.run_lua('gadget:GameFrame(27000)')
        self.lua.execute((ROOT/'luarules/gadgets/ai_llm_duel.lua').read_text())
        self.run_lua('gadget:GameFrame(27030)')
        self.assertEqual(self.run_lua('return #winners'),1)

    def test_arena_is_disabled_for_regular_matches(self):
        self.run_lua('options={}; gadget={}')
        result=self.lua.execute((ROOT/'luarules/gadgets/ai_llm_duel.lua').read_text())
        self.assertIs(result,False)
        self.assertIsNone(self.lua.globals().gadget.GameFrame)


class WidgetTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().Json=self.lua.execute((ROOT/'common/luaUtilities/json.lua').read_text())
        self.lua.execute(r'''
widget={}; sent={}; replies={}; forwarded={}; connectCount=0; failSend=false
Spring={GetModOptions=function() return {llm_duel="1",llm_duel_token="test"} end,
 GetMyPlayerID=function() return 0 end, Echo=function() end,
 SendLuaRulesMsg=function(message) forwarded[#forwarded+1]=message end}
widgetHandler={RegisterGlobal=function(self,name,fn) callback=fn end,
 DeregisterGlobal=function() end, RemoveWidget=function() removed=true end}
socket={tcp=function()
 connectCount=connectCount+1
 return {settimeout=function() end,connect=function() return true end,close=function() end,
 send=function(self,data)
  if failSend then failSend=false; return nil,"closed",0 end
  sent[#sent+1]=data; return #data
 end,
 receive=function()
  if #replies>0 then return table.remove(replies,1) end
  return nil,"timeout",""
 end}
end}
''')
        self.lua.execute((ROOT/'luaui/Widgets/gui_llm_duel.lua').read_text())
        self.lua.execute('widget:Initialize()')

    def test_snapshot_token_and_command_forward(self):
        self.lua.execute('callback(Json.encode({kind="snapshot",frame=150,teams={}})); widget:Update(.5)')
        sent=json.loads(self.lua.globals().sent[1])
        self.assertEqual((sent['kind'],sent['token']),('snapshot','test'))
        self.lua.execute('replies[1]=Json.encode({commands={{team=0,frame=150,action="attack",unit="light"}}}); widget:Update(.1)')
        msg=self.lua.globals().forwarded[1]
        self.assertEqual(json.loads(msg[9:])['token'],'test')

    def test_result_is_retried_after_broken_connection_until_ack(self):
        self.lua.execute('failSend=true; callback(Json.encode({kind="finished",winner=0,frame=900})); widget:Update(.5); widget:Update(3.1)')
        sent=json.loads(self.lua.globals().sent[1])
        self.assertEqual(sent['kind'],'finished')
        self.lua.execute('replies[1]=Json.encode({commands={},finished=true}); widget:Update(.1); widget:Update(.5)')
        last=self.lua.globals().sent[len(self.lua.globals().sent)]
        self.assertEqual(json.loads(last)['kind'],'poll')

    def test_widget_is_disabled_for_regular_matches(self):
        self.lua.execute('Spring.GetModOptions=function() return {} end; widget={}')
        result=self.lua.execute((ROOT/'luaui/Widgets/gui_llm_duel.lua').read_text())
        self.assertIs(result,False)
        self.assertIsNone(self.lua.globals().widget.Update)


if __name__ == '__main__':
    unittest.main()
