"""Audit a duel log without displaying session tokens or model response bodies."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re


def inspect_run(directory, require_finished=False, allow_mock=False):
    events = [json.loads(line) for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    engine = (directory / 'infolog.txt').read_text(encoding='utf-8',errors='replace')
    starts = [e for e in events if e['kind']=='start']
    snapshots = [e for e in events if e['kind']=='snapshot']
    decisions = [e for e in events if e['kind']=='decision']
    errors = [e for e in events if e['kind']=='error']
    results = [e for e in events if e['kind']=='finished']
    problems=[]
    if len(starts)!=1:
        problems.append('Expected exactly one bridge start')
    if starts and starts[0].get('mock') and not allow_mock:
        problems.append('Mock run is not an actual model match')
    if not snapshots:
        problems.append('No game observations received')
    if require_finished and len(results)!=1:
        problems.append('Match result missing or duplicated')
    if any('Fatal:' in line or ('error' in line.lower() and
           ('ai_llm_duel.lua' in line or 'gui_llm_duel.lua' in line)) for line in engine.splitlines()):
        problems.append('Fatal engine error or duel Lua runtime error')
    frames=[e['frame'] for e in snapshots]
    if any(b<=a for a,b in zip(frames,frames[1:])):
        problems.append('Observations are not strictly increasing')
    if any(b-a>300 for a,b in zip(frames,frames[1:])):
        problems.append('Observation gap longer than 10 game seconds')
    if snapshots and results and results[0]['frame']-snapshots[-1]['frame']>300:
        problems.append('Observations stopped before match end')
    counts=Counter(e['team'] for e in decisions+errors)
    budget=starts[0]['config']['max_calls_per_team'] if starts else 0
    if any(n>budget for n in counts.values()):
        problems.append('Model call budget exceeded')
    states=[state for event in snapshots for state in event['teams']]
    if any(state.get('queued_total',0)>9 for state in states):
        problems.append('Factory queue exceeds limit')
    if any(state.get('air_queued_total',0)>9 for state in states):
        problems.append('Aircraft factory queue exceeds limit')
    if any(not 0<=state['income_upgrades']<=5 for state in states):
        problems.append('Income upgrade limit violated')
    initial=snapshots[0]['teams'] if snapshots else []
    if len(initial)!=2 or any(s['combat_units']!=6 or not s['factory_alive'] for s in initial):
        problems.append('Initial army or factory missing')
    teams={}
    for team in (0,1):
        selected=[e for e in decisions if e['team']==team]
        team_states=[s for s in states if s['team']==team]
        latencies=[e['latency_seconds'] for e in selected]
        teams[team]=dict(decisions=len(selected),errors=sum(e['team']==team for e in errors),
                         actions=dict(Counter(e['action']['action'] for e in selected)),
                         latency_min=min(latencies,default=None),latency_max=max(latencies,default=None),
                         max_combat=max((s['combat_units'] for s in team_states),default=0),
                         max_queue=max((s.get('queued_total',0) for s in team_states),default=0),
                         max_seen_enemies=max((len(s['enemies']) for s in team_states),default=0))
        if not selected:
            problems.append('No decisions for team '+str(team))
    applied=Counter(int(t) for t in re.findall(r'BAR_DUEL_ACTION team=(\d)',engine))
    return dict(passed=not problems,problems=problems,model_errors=errors,
                mock=starts[0].get('mock') if starts else None,
                last_frame=frames[-1] if frames else None,
                result=results[-1] if results else None,snapshots=len(snapshots),
                teams=teams,engine_actions=dict(applied))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    parser.add_argument('--require-finished',action='store_true')
    parser.add_argument('--allow-mock',action='store_true')
    args=parser.parse_args()
    result=inspect_run(args.directory,args.require_finished,args.allow_mock)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':
    main()
