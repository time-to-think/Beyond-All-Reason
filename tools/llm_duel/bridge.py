"""Loopback-only BAR strategy bridge. No third party Python dependencies.

Models choose macro actions; identical Lua execution rules handle both sides.
Ollama's authenticated daemon handles cloud credentials, never game scripts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import secrets
import socket
import socketserver
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["produce", "attack", "defend", "economy"]},
        "unit": {"type": "string", "enum": ["light", "heavy", "scout", "antiair", "fighter", "bomber", "gunship", "airscout"]},
        "count": {"type": "integer", "minimum": 1, "maximum": 3},
        "x": {"type": "number", "minimum": 0, "maximum": 1},
        "z": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["action", "unit", "count", "x", "z"],
    "additionalProperties": False,
}
SYSTEM = """You control one side in a Beyond All Reason strategy duel.
Return ONLY one JSON object: action (produce/attack/defend/economy),
unit (light/heavy/scout/antiair/fighter/bomber/gunship/airscout), count (1..3),
x and z (normalized map coordinates 0..1).
Include all five fields for every action, even when unused. For example:
{"action":"economy","unit":"light","count":1,"x":0.5,"z":0.5}
Both players have identical rules and limited vision. Enemy_base is public starting
position, not live intelligence. Enemy units contain only line-of-sight observations.
produce queues 1..3 units at the appropriate factory: vehicle factory has
light=armflash, heavy=armstump, scout=armfav, antiair=armsam; aircraft factory has
fighter=armfig (anti-air), bomber=armthund (ground bombing), gunship=armkam
(ground attack), airscout=armpeep (unarmed reconnaissance). Both factories are
available from the start; each has a queue limit of 9. Aircraft cost real resources.
attack sends all controlled ground and air units to x,z using fight.
defend regroups all controlled units around your own base. economy spends 300 metal for +3
metal and +30 energy income per second, at most 5 upgrades. Start with both factories,
commander, 6 light tanks, 1500 metal and 10000 energy. Base bonus income 15 metal
and 150 energy/sec. Factory loss cannot be repaired in this bounded demo.
Win by destroying the opposing commander. Balance production, economy, and attacks.
Avoid endlessly investing or producing without attacking. Do not invent other actions.
"""


def validate_action(value):
    if not isinstance(value, dict) or set(value) != set(SCHEMA["required"]):
        raise ValueError("Invalid action fields")
    if value["action"] not in SCHEMA["properties"]["action"]["enum"]:
        raise ValueError("Invalid action")
    if value["unit"] not in SCHEMA["properties"]["unit"]["enum"]:
        raise ValueError("Invalid unit")
    if type(value["count"]) is not int or not 1 <= value["count"] <= 3:
        raise ValueError("Invalid count")
    for key in ("x", "z"):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or not 0 <= value[key] <= 1:
            raise ValueError("Invalid position")
    return value


def chat(config, model, state):
    cloud = model.endswith("cloud")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": json.dumps(state, ensure_ascii=False)}],
        "stream": False,
        "think": "low" if model.startswith("gpt-oss") else False,
        "options": {"temperature": 0.2, "num_predict": config["max_output_tokens"]},
        "keep_alive": "10m",
    }
    # Ollama Cloud does not guarantee schema-constrained generation.
    if not cloud:
        body["format"] = SCHEMA
        body["options"]["num_ctx"] = config["local_context"]
    request = urllib.request.Request(config["ollama_url"] + "/api/chat",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=config["request_timeout_seconds"]) as response:
        result = json.load(response)
    content = result["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    action = validate_action(json.loads(content))
    return action, {"latency_seconds": round(time.monotonic() - start, 3),
                    "eval_count": result.get("eval_count"),
                    "prompt_eval_count": result.get("prompt_eval_count"),
                    "reported_model": result.get("model")}


class Match:
    def __init__(self, config, token, log_dir, mock=False):
        self.config, self.token, self.mock = config, token, mock
        self.models = {0: config["local_model"], 1: config["cloud_model"]}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.busy = set()
        self.calls = {0: 0, 1: 0}
        self.last = {0: -1e10, 1: -1e10}
        self.pending = []
        self.finished = False
        self.latest_frame = -1
        self.log_path = log_dir / "events.jsonl"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log("start", models=self.models, config=config, mock=mock)

    def log(self, kind, **fields):
        record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def handle(self, message):
        if not isinstance(message, dict):
            return {"error": "invalid message"}
        supplied_token = message.get("token", "")
        if not isinstance(supplied_token, str) or not secrets.compare_digest(
                supplied_token.encode(), self.token.encode()):
            return {"error": "unauthorized"}
        with self.lock:
            if message.get("kind") == "snapshot" and not self.finished:
                frame = message.get("frame")
                if type(frame) is not int or frame < 0 or frame <= self.latest_frame:
                    return {"error": "stale frame"}
                states = message.get("teams", [])
                if (not isinstance(states, list) or len(states) > 2
                        or any(not isinstance(state, dict) or type(state.get("team")) is not int
                               or state["team"] not in self.models for state in states)
                        or len({state["team"] for state in states}) != len(states)):
                    return {"error": "invalid teams"}
                self.latest_frame = frame
                self.log("snapshot", frame=frame, teams=states)
                for state in states:
                    team = state.get("team")
                    if type(team) is not int or team not in self.models:
                        continue
                    now = time.monotonic()
                    if (team not in self.busy and self.calls[team] < self.config["max_calls_per_team"]
                            and now - self.last[team] >= self.config["decision_interval_seconds"]):
                        self.busy.add(team)
                        self.calls[team] += 1
                        self.last[team] = now
                        self.pool.submit(self.decide, team, frame, state)
            elif message.get("kind") == "finished":
                if (type(message.get("winner")) is not int or message["winner"] not in (-1, 0, 1)
                        or type(message.get("frame")) is not int
                        or message["frame"] < self.latest_frame):
                    return {"error": "invalid result"}
                if not self.finished:
                    self.log("finished", winner=message.get("winner"), frame=message.get("frame"))
                self.finished = True
                self.pending.clear()
            elif message.get("kind") not in ("poll", "snapshot"):
                return {"error": "invalid kind"}
            commands = [c for c in self.pending if self.latest_frame - c["frame"] <= 2700]
            self.pending = []
            return {"commands": commands, "calls": dict(self.calls), "finished": self.finished}

    def decide(self, team, frame, state):
        try:
            if self.mock:
                action = dict(action="attack", unit="light", count=1,
                              x=state["enemy_base"]["x"], z=state["enemy_base"]["z"])
                if self.config.get("mock_scenario") == "air":
                    sequence = ["fighter", "bomber", "gunship", "airscout", "antiair"]
                    index = self.calls[team] - 1
                    if index < len(sequence):
                        action.update(action="produce", unit=sequence[index])
                metrics = {"latency_seconds": 0, "mock": True}
            else:
                action, metrics = chat(self.config, self.models[team], state)
            with self.lock:
                # Do not execute decisions from an obsolete battlefield state.
                stale = self.latest_frame - frame > 2700
                self.log("decision", team=team, frame=frame, model=self.models[team],
                         action=action, stale=stale, **metrics)
                if not self.finished and not stale:
                    self.pending.append({"team": team, "frame": frame, **action})
        except Exception as exc:
            # Never log arbitrary HTTP bodies, headers, or credentials.
            with self.lock:
                self.log("error", team=team, frame=frame, error_type=type(exc).__name__,
                         status=getattr(exc, "code", None),
                         validation=str(exc) if type(exc) is ValueError and str(exc) in
                         {"Invalid action fields", "Invalid action", "Invalid unit", "Invalid count", "Invalid position"}
                         else None)
        finally:
            with self.lock:
                self.busy.discard(team)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        while True:
            try:
                line = self.rfile.readline(131073)
                if not line or len(line) > 131072:
                    return
                response = self.server.match.handle(json.loads(line))
                self.wfile.write((json.dumps(response) + "\n").encode())
                self.wfile.flush()
            except (ValueError, OSError, TypeError):
                return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--session", type=Path)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if args.probe:
        state = {"team": 0, "metal": 1500, "energy": 10000, "combat_units": 6,
                 "enemy_base": {"x": 0.8, "z": 0.8}, "enemies": []}
        failed = False
        for model in (config["local_model"], config["cloud_model"]):
            try:
                action, metrics = chat(config, model, state)
                print(json.dumps({"model": model, "action": action, **metrics}), flush=True)
            except Exception as exc:
                print(json.dumps({"model": model, "error_type": type(exc).__name__,
                                  "status": getattr(exc, "code", None)}), flush=True)
                failed = True
        raise SystemExit(1 if failed else 0)
    session = json.loads(args.session.read_text(encoding="utf-8-sig"))
    match = Match(config, session["token"], args.session.parent, args.mock)
    with Server(("127.0.0.1", config["port"]), Handler) as server:
        server.match = match
        (args.session.parent / "bridge.ready").write_text("ready", encoding="ascii")
        print("BAR duel bridge ready on loopback port", config["port"], flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
