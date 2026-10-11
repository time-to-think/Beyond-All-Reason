import json
from pathlib import Path
import tempfile
import time
import unittest
import threading
import socket
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from bridge import Match, validate_action, chat, Server, Handler


class BridgeTests(unittest.TestCase):
    def make_match(self, **changes):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = dict(local_model="local", cloud_model="cloud", max_calls_per_team=5,
                      decision_interval_seconds=0)
        config.update(changes)
        match = Match(config, "secret", Path(directory.name), mock=True)
        self.addCleanup(match.pool.shutdown, wait=True)
        return match

    def snapshot(self, frame=150, teams=None):
        return dict(kind="snapshot", token="secret", frame=frame,
                    teams=teams if teams is not None else [dict(team=0, enemy_base=dict(x=.8,z=.8))])

    def wait_idle(self, match):
        deadline = time.monotonic() + 3
        while match.busy and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(match.busy)

    def test_malformed_messages_do_not_crash_or_launch_models(self):
        match = self.make_match()
        for value in (None, [], 5, "hello", {"token":"secret", "kind":"snapshot", "frame":150, "teams":[None]},
                      self.snapshot(teams="bad"), self.snapshot(-1),
                      {"kind":"finished", "token":"secret", "winner":2,"frame":150}):
            self.assertIn("error", match.handle(value))
        self.assertEqual(match.calls, {0:0,1:0})
        self.assertFalse(match.finished)

    def test_duplicate_and_old_observations_do_not_spend_calls(self):
        match = self.make_match()
        match.handle(self.snapshot())
        self.wait_idle(match)
        match.handle(self.snapshot())
        match.handle(self.snapshot(149))
        self.wait_idle(match)
        self.assertEqual(match.calls[0], 1)

    def test_pending_commands_expire_before_delivery(self):
        match = self.make_match()
        match.handle(self.snapshot())
        self.wait_idle(match)
        reply = match.handle(self.snapshot(3000, teams=[]))
        self.assertEqual(reply["commands"], [])

    def test_both_teams_and_interval(self):
        match = self.make_match(decision_interval_seconds=60)
        teams = [dict(team=t, enemy_base=dict(x=.8,z=.8)) for t in (0,1)]
        match.handle(self.snapshot(teams=teams))
        self.wait_idle(match)
        reply = match.handle(self.snapshot(300,teams))
        self.assertEqual({c["team"] for c in reply["commands"]}, {0,1})
        self.assertEqual(match.calls, {0:1,1:1})

    def test_inflight_result_is_dropped_after_finish(self):
        match = self.make_match()
        match.mock = False
        entered, release = threading.Event(), threading.Event()
        def delayed(*args):
            entered.set()
            release.wait(3)
            return dict(action="attack",unit="light",count=1,x=.8,z=.8), {}
        with patch("bridge.chat", side_effect=delayed):
            match.handle(self.snapshot())
            self.assertTrue(entered.wait(2))
            match.handle(dict(kind="finished",token="secret",winner=1,frame=180))
            release.set()
            self.wait_idle(match)
        self.assertEqual(match.handle(dict(kind="poll",token="secret"))["commands"], [])

    def test_model_failure_has_no_fallback_or_secret_body_in_log(self):
        match = self.make_match()
        match.mock = False
        with patch("bridge.chat", side_effect=TimeoutError("private body")):
            match.handle(self.snapshot())
            self.wait_idle(match)
        self.assertEqual(match.pending, [])
        log = match.log_path.read_text()
        self.assertIn("TimeoutError", log)
        self.assertNotIn("private body", log)

    def test_loopback_transport_rejects_bad_input_and_recovers(self):
        match = self.make_match()
        server = Server(("127.0.0.1",0), Handler)
        server.match = match
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with socket.create_connection(server.server_address, timeout=2) as client:
                stream = client.makefile("rwb")
                for value in (None, {"kind":"poll"}, {"kind":"poll","token":"비밀"}):
                    stream.write((json.dumps(value)+"\n").encode())
                    stream.flush()
                    self.assertIn("error",json.loads(stream.readline()))
                stream.write(b'{"kind":"poll","token":"secret"}\n')
                stream.flush()
                self.assertEqual(json.loads(stream.readline())["commands"],[])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_second_server_cannot_claim_same_port(self):
        with Server(("127.0.0.1",0), Handler) as first:
            with self.assertRaises(OSError):
                with Server(first.server_address, Handler):
                    pass


class OllamaContractTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.action = dict(action="attack",unit="light",count=1,x=.8,z=.8)
        owner = self
        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(owner.status)
                self.end_headers()
                self.wfile.write(json.dumps(owner.response).encode())
            def log_message(self,*args):
                pass
        self.status = 200
        self.response = dict(model="fixture",message=dict(content=json.dumps(self.action)))
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Endpoint)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.config=dict(ollama_url="http://127.0.0.1:"+str(self.server.server_port),
                         max_output_tokens=700,local_context=4096,request_timeout_seconds=2)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def test_local_schema_context_and_cloud_contract(self):
        for model in ("qwen3.5:4b","gpt-oss:120b-cloud"):
            action,metrics=chat(self.config,model,dict(team=0))
            self.assertEqual(action,self.action)
            self.assertEqual(metrics['reported_model'],'fixture')
        local,cloud=self.requests
        self.assertIn('format',local)
        self.assertEqual(local['options']['num_ctx'],4096)
        self.assertIs(local['think'],False)
        self.assertNotIn('format',cloud)
        self.assertNotIn('num_ctx',cloud['options'])
        self.assertEqual(cloud['think'],'low')

    def test_markdown_and_invalid_outputs(self):
        self.response['message']['content']='```json\n'+json.dumps(self.action)+'\n```'
        self.assertEqual(chat(self.config,'cloud',{})[0],self.action)
        for text in ('not json','{}',json.dumps({**self.action,'count':99})):
            self.response['message']['content']=text
            with self.assertRaises(ValueError):
                chat(self.config,'cloud',{})

    def test_auth_and_limit_http_errors(self):
        for status in (401,402,429,500):
            self.status=status
            with self.assertRaises(urllib.error.HTTPError) as caught:
                chat(self.config,'cloud',{})
            self.assertEqual(caught.exception.code,status)

    def test_rejects_unsafe_actions(self):
        good = dict(action="attack", unit="light", count=1, x=0.5, z=0.5)
        self.assertEqual(validate_action(good), good)
        for change in ({"action":"lua"}, {"count":True}, {"count":100},
                       {"x":float("nan")}, {"z":2}, {"code":"anything"}):
            with self.assertRaises(ValueError): validate_action({**good, **change})

    def test_auth_budget_and_end_of_match(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(local_model="local",cloud_model="cloud",max_calls_per_team=1,
                          decision_interval_seconds=0)
            match = Match(config,"secret",Path(directory),mock=True)
            try:
                state={"team":0,"enemy_base":{"x":0.8,"z":0.8}}
                msg={"kind":"snapshot","frame":150,"teams":[state]}
                self.assertIn("error",match.handle(msg))
                match.handle({**msg,"token":"secret"})
                deadline=time.monotonic()+2
                while match.busy and time.monotonic()<deadline: time.sleep(.01)
                response=match.handle({"kind":"poll","token":"secret"})
                self.assertEqual(len(response["commands"]),1)
                match.handle({**msg,"frame":300,"token":"secret"})
                self.assertEqual(match.calls[0],1)
                match.handle({"kind":"finished","token":"secret","winner":0,"frame":450})
                self.assertTrue(match.finished)
                self.assertEqual(match.handle({**msg,"frame":600,"token":"secret"})["commands"],[])
                events=[json.loads(x) for x in match.log_path.read_text().splitlines()]
                self.assertEqual(sum(x["kind"]=="finished" for x in events),1)
                self.assertNotIn('secret',match.log_path.read_text())
            finally:
                match.pool.shutdown(wait=True)


if __name__ == '__main__': unittest.main()
