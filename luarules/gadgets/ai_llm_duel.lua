local gadget = gadget ---@type Gadget

function gadget:GetInfo()
	return {
		name = "LLM Duel Arena",
		desc = "Opt-in symmetric local/cloud strategy experiment",
		author = "Capstone team / Codex",
		license = "GNU GPL v2 or later",
		layer = 100,
		enabled = true,
	}
end

local opts = Spring.GetModOptions()
if tostring(opts.llm_duel or "") ~= "1" then
	return false
end
local json = VFS.Include("common/luaUtilities/json.lua")

if not gadgetHandler:IsSyncedCode() then
	local function forward(_, payload)
		if Script.LuaUI("BARDuelSnapshot") then
			Script.LuaUI.BARDuelSnapshot(payload)
		end
	end
	function gadget:Initialize()
		gadgetHandler:AddSyncAction("bar_duel_snapshot", forward)
	end
	function gadget:Shutdown()
		gadgetHandler:RemoveSyncAction("bar_duel_snapshot")
	end
	return
end

-- The development game reloads edited gadgets while a match is running.
local state = GG.BARDuelState
	or {
		bases = {},
		factories = {},
		upgrades = { [0] = 0, [1] = 0 },
		lastCommand = { [0] = -1, [1] = -1 },
		finished = false,
	}
GG.BARDuelState = state
state.airFactories = state.airFactories or {}
local airFactories = state.airFactories
local bases, factories, upgrades, lastCommand = state.bases, state.factories, state.upgrades, state.lastCommand
local finished = state.finished
local duration = tonumber(opts.llm_duel_frames) or 27000
local token = tostring(opts.llm_duel_token or "")
local types = {
	light = "armflash",
	heavy = "armstump",
	scout = "armfav",
	antiair = "armsam",
	fighter = "armfig",
	bomber = "armthund",
	gunship = "armkam",
	airscout = "armpeep",
}
local airTypes = { fighter = true, bomber = true, gunship = true, airscout = true }
local controlledTypes = {}
for _, name in pairs(types) do
	controlledTypes[name] = true
end

local function spawn(name, x, z, team, facing)
	return Spring.CreateUnit(name, x, Spring.GetGroundHeight(x, z), z, facing or 0, team)
end
local function army(team)
	local out = {}
	for _, id in ipairs(Spring.GetTeamUnits(team)) do
		local d = UnitDefs[Spring.GetUnitDefID(id)]
		if d and controlledTypes[d.name] then
			out[#out + 1] = id
		end
	end
	return out
end
local function productionQueue(factory)
	local result, total = {}, 0
	if not factory or not Spring.ValidUnitID(factory) then
		return result, total
	end
	for _, entry in ipairs(Spring.GetFullBuildQueue(factory) or {}) do
		for defID, count in pairs(entry) do
			result[#result + 1] = { unit = UnitDefs[defID].name, count = count }
			total = total + count
		end
	end
	return result, total
end
local function finish(winner, frame)
	if finished then
		return
	end
	finished = true
	state.finished = true
	SendToUnsynced("bar_duel_snapshot", json.encode({ kind = "finished", winner = winner, frame = frame }))
	Spring.Echo("BAR_DUEL_FINISHED winner=" .. tostring(winner))
	if winner >= 0 then
		Spring.GameOver({ winner })
	else
		Spring.GameOver({})
	end
end

function gadget:GameFrame(frame)
	if finished then
		return
	end
	if frame == 30 then
		for team = 0, 1 do
			local x, _, z = Spring.GetTeamStartPosition(team)
			bases[team] = { x = x, z = z }
			factories[team] = spawn("armvp", x, z + (team == 0 and 200 or -200), team, team == 0 and 0 or 2)
			airFactories[team] = spawn("armap", x + (team == 0 and 300 or -300), z, team, team == 0 and 0 or 2)
			Spring.SetTeamResource(team, "ms", 5000)
			Spring.SetTeamResource(team, "es", 20000)
			Spring.SetTeamResource(team, "m", 1500)
			Spring.SetTeamResource(team, "e", 10000)
			for i = 1, 6 do
				spawn("armflash", x + (i - 3.5) * 60, z + (team == 0 and 380 or -380), team)
			end
		end
		Spring.Echo("BAR_DUEL_READY")
	end
	if frame < 30 then
		return
	end
	if frame % 30 == 0 then
		for team = 0, 1 do
			Spring.AddTeamResource(team, "m", 15 + 3 * upgrades[team])
			Spring.AddTeamResource(team, "e", 150 + 30 * upgrades[team])
		end
	end
	if frame >= duration then
		finish(-1, frame)
		return
	end
	if frame % 150 ~= 0 then
		return
	end
	local states = {}
	for team = 0, 1 do
		local metal = Spring.GetTeamResources(team, "metal")
		local energy = Spring.GetTeamResources(team, "energy")
		local enemies = {}
		for _, id in ipairs(Spring.GetTeamUnits(1 - team)) do
			local los = Spring.GetUnitLosState(id, team)
			if los and los.los and #enemies < 40 then
				local x, _, z = Spring.GetUnitPosition(id)
				enemies[#enemies + 1] = {
					unit = UnitDefs[Spring.GetUnitDefID(id)].name,
					x = x / Game.mapSizeX,
					z = z / Game.mapSizeZ,
				}
			end
		end
		local f = factories[team]
		local queue, total = productionQueue(f)
		local airFactory = airFactories[team]
		local airQueue, airTotal = productionQueue(airFactory)
		local unitCounts, readyCounts = {}, {}
		for _, id in ipairs(army(team)) do
			local name = UnitDefs[Spring.GetUnitDefID(id)].name
			unitCounts[name] = (unitCounts[name] or 0) + 1
			local _, _, _, _, progress = Spring.GetUnitHealth(id)
			if progress and progress >= 1 then
				readyCounts[name] = (readyCounts[name] or 0) + 1
			end
		end
		states[#states + 1] = {
			team = team,
			frame = frame,
			metal = math.floor(metal or 0),
			energy = math.floor(energy or 0),
			combat_units = #army(team),
			income_upgrades = upgrades[team],
			factory_alive = f and Spring.ValidUnitID(f) or false,
			factory_queue = queue,
			queued_total = total,
			air_factory_alive = airFactory and Spring.ValidUnitID(airFactory) or false,
			air_factory_queue = airQueue,
			air_queued_total = airTotal,
			unit_counts = unitCounts,
			ready_unit_counts = readyCounts,
			base = { x = bases[team].x / Game.mapSizeX, z = bases[team].z / Game.mapSizeZ },
			enemy_base = { x = bases[1 - team].x / Game.mapSizeX, z = bases[1 - team].z / Game.mapSizeZ },
			enemies = enemies,
		}
	end
	SendToUnsynced("bar_duel_snapshot", json.encode({ kind = "snapshot", frame = frame, teams = states }))
end

function gadget:RecvLuaMsg(message, player)
	if finished or player ~= 0 or message:sub(1, 9) ~= "BARDUEL: " or #message > 2048 then
		return
	end
	local ok, c = pcall(json.decode, message:sub(10))
	if not ok or type(c) ~= "table" or c.token ~= token then
		return
	end
	local t = c.team
	if (t ~= 0 and t ~= 1) or not bases[t] or type(c.frame) ~= "number" then
		return
	end
	local frame = Spring.GetGameFrame()
	if c.frame <= lastCommand[t] or frame - c.frame > 2700 or c.frame > frame then
		return
	end
	lastCommand[t] = c.frame
	if c.action == "produce" then
		local f = airFactories[t]
		if not airTypes[c.unit] then
			f = factories[t]
		end
		local name = types[c.unit]
		if name and f and Spring.ValidUnitID(f) and #army(t) < 100 then
			local count = math.max(1, math.min(3, math.floor(tonumber(c.count) or 1)))
			local _, queued = productionQueue(f)
			count = math.min(count, math.max(0, 9 - queued))
			-- Factory SHIFT multiplies the quantity; ordinary build commands already append.
			for _ = 1, count do
				Spring.GiveOrderToUnit(f, -UnitDefNames[name].id, {}, {})
			end
		end
	elseif c.action == "economy" then
		if upgrades[t] < 5 and Spring.UseTeamResource(t, "m", 300) then
			upgrades[t] = upgrades[t] + 1
		end
	elseif c.action == "attack" or c.action == "defend" then
		local x, z = bases[t].x, bases[t].z
		if c.action == "attack" then
			if type(c.x) ~= "number" or type(c.z) ~= "number" or c.x ~= c.x or c.z ~= c.z then
				return
			end
			x = math.max(32, math.min(Game.mapSizeX - 32, c.x * Game.mapSizeX))
			z = math.max(32, math.min(Game.mapSizeZ - 32, c.z * Game.mapSizeZ))
		end
		for _, id in ipairs(army(t)) do
			Spring.GiveOrderToUnit(id, CMD.FIGHT, { x, Spring.GetGroundHeight(x, z), z }, {})
		end
	else
		return
	end
	Spring.Echo("BAR_DUEL_ACTION team=" .. t .. " action=" .. c.action)
end

function gadget:UnitDestroyed(_, defID, team)
	if bases[team] and UnitDefs[defID].customParams.iscommander then
		finish(1 - team, Spring.GetGameFrame())
	end
end
function gadget:TeamDied(team)
	if team == 0 or team == 1 then
		finish(1 - team, Spring.GetGameFrame())
	end
end
