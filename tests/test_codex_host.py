"""Opt-in real Codex host test; model responses are a deterministic loopback fixture.

Requires the three reviewed/trusted hooks described in references/schema.md.
Run with CHANGE_JOURNAL_HOST_TEST=1; no external model is called, no trust bypass.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import change_journal as cj
import codex_hook as hook


@unittest.skipUnless(os.environ.get("CHANGE_JOURNAL_HOST_TEST") == "1", "opt-in Codex host integration")
class CodexHostTests(unittest.TestCase):
    def run_case(self, coverage, code_mode=False):
        self.assertIsNotNone(shutil.which("codex"), "Codex CLI is required")
        with tempfile.TemporaryDirectory(prefix="change-journal-host-") as temporary:
            cwd = Path(temporary).resolve()
            root = cwd / "journal"
            paths = [cwd / "a.txt", cwd / "b.txt"]
            record = [sys.executable, str(SCRIPTS / "change_journal.py"), "--root", str(root),
                      "record", "--actor", "codex", "--source", "semantic", "--phase", "verified",
                      "--summary", "isolated host fixture", "--verification-status", "passed",
                      "--verification-ref", "fixture files checked", "--idempotency-key", "host-fixture"]
            for path in paths[:2 if coverage == "full" else 1]:
                record += ["--changed-path", str(path)]
            record_command = "test -f a.txt && test -f b.txt && " + shlex.join(record)
            patch = "*** Begin Patch\n*** Add File: a.txt\n+fixture a\n*** Add File: b.txt\n+fixture b\n*** End Patch"
            state = {"requests": 0, "tools": []}

            class Fixture(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    # Discard request contents immediately: no prompt/output logs.
                    request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    state["tools"] = [{k: t.get(k) for k in ("type", "name", "namespace")} for t in request.get("tools", [])]
                    index = state["requests"]
                    state["requests"] += 1
                    call_id = "fixture_" + uuid.uuid4().hex
                    if index == 0:
                        item = dict(type="function_call", name="exec_command", call_id=call_id,
                                    arguments=json.dumps({"cmd": "mkdir -p . && apply_patch <<'PATCH'\n" + patch + "\nPATCH", "workdir": str(cwd)}))
                    elif coverage != "none" and index in (1, 2):
                        item = dict(type="function_call", name="exec_command", call_id=call_id,
                                    arguments=json.dumps({"cmd": record_command, "workdir": str(cwd)}))
                    else:
                        item = dict(type="message", role="assistant", status="completed",
                                    content=[{"type": "output_text", "text": "Fixture complete.", "annotations": []}])
                    if code_mode and item.get("name") == "exec_command":
                        item = dict(type="custom_tool_call", name="exec", call_id=call_id,
                                    input="const result = await tools.exec_command(" + item["arguments"] + "); text(result);")
                    item["id"] = "item_" + str(index)
                    response_id = "response_" + str(index)
                    events = [
                        {"type": "response.created", "response": {"id": response_id}},
                        {"type": "response.output_item.done", "output_index": 0, "item": item},
                        {"type": "response.completed", "response": {
                            "id": response_id, "status": "completed", "output": [item],
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}},
                    ]
                    body = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            provider = '{name="Local fixture",base_url="http://127.0.0.1:%d/v1",wire_api="responses",requires_openai_auth=false}' % server.server_port
            cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--json",
                   "-C", str(cwd), "-s", "workspace-write", "-c", 'approval_policy="never"',
                   "-c", 'model_provider="journal_fixture"', "-c", "model_providers.journal_fixture=" + provider,
                   "-c", 'model="gpt-5.2-codex"', "-c", "features.hooks=true",
                   "-c", "features.code_mode=" + str(code_mode).lower(),
                   "-c", "features.plugins=false", "-c", "features.apps=false",
                   "-c", "features.memories=false", "-c", "features.shell_snapshot=false"]
            # Keep persisted hook trust, but do not start unrelated user MCP servers.
            config_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            config_path = config_home / "config.toml"
            config = config_path.read_text() if config_path.exists() else ""
            for quoted, bare in re.findall(r'^\[mcp_servers\.(?:"([^"]+)"|([\w-]+))\]\s*$', config, re.M):
                cmd += ["-c", 'mcp_servers.' + (quoted or bare) + '.enabled=false']
            cmd.append("Run the local Hook integration fixture.")
            env = {k: v for k, v in os.environ.items()
                   if (not k.startswith("CODEX_") or k == "CODEX_HOME")
                   and k not in ("OPENAI_API_KEY", "OPENAI_BASE_URL")}
            env.update(CHANGE_JOURNAL_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")
            try:
                result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=50)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)
            self.assertEqual(result.returncode, 0, result.stderr[-2500:])
            self.assertTrue(all(p.exists() for p in paths), "host did not execute apply_patch: " + str(state) + result.stdout[-3000:])
            stream = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
            commands = [e["item"] for e in stream if e.get("type") == "item.completed"
                        and e.get("item", {}).get("type") == "command_execution"]
            self.assertTrue(all(e.get("exit_code") == 0 for e in commands), "a fixture command failed")
            if coverage != "none":
                records = [json.loads(e["aggregated_output"]) for e in commands
                           if "change_journal.py" in e["command"]]
                self.assertEqual(len(records), 2)
                self.assertEqual([e["duplicate"] for e in records], [False, True])
                self.assertEqual(records[0]["event_id"], records[1]["event_id"])
            facts = cj.load_events(root)
            semantic = [e for e in facts if e["source"] == "semantic"]
            fallback = [e for e in facts if e["source"] == "codex-hook"]
            self.assertEqual(len(semantic), 0 if coverage == "none" else 1, result.stdout[-3000:])
            self.assertEqual(len(fallback), 0 if coverage == "full" else 1)
            observations = [json.loads(p.read_text()) for p in (root / ".spool/codex").glob("*/*/*.json")
                            if not p.name.startswith(".")]
            self.assertTrue(observations, "trusted PostToolUse did not run")
            identities = {(o["session_id"], o["turn_id"]) for o in observations}
            self.assertEqual(len(identities), 1)
            session, turn = next(iter(identities))
            self.assertTrue(turn)
            self.assertIn(session, [e.get("thread_id") for e in stream if e.get("type") == "thread.started"])
            self.assertTrue(all((e["session_id"], e["turn_id"]) == (session, turn) for e in facts))
            if fallback:
                self.assertEqual(set(fallback[0]["changed_paths"]), {str(p) for p in paths[0 if coverage == "none" else 1:]})
            finalized = root / ".spool/codex" / session / turn / ".finalized.json"
            self.assertEqual(json.loads(finalized.read_text())["mode"], "semantic" if coverage == "full" else "mechanical")
            before = {p: p.read_bytes() for p in (root / "events").glob("*/*/*.json")}
            hook.finalize({"session_id": session, "turn_id": turn}, root)
            self.assertEqual(before, {p: p.read_bytes() for p in (root / "events").glob("*/*/*.json")})
            self.assertEqual(list((root / ".spool/codex-context").rglob("*.json")), [])
            self.assertEqual(list((root / ".spool/errors").glob("*.json")), [])
            self.assertTrue(cj.doctor(root)["ok"])
            print(json.dumps({"host_case": coverage, "code_mode": code_mode, "host_turn_present": bool(turn),
                              "semantic_events": len(semantic), "fallback_events": len(fallback),
                              "record_retry_deduplicated": True if coverage != "none" else None,
                              "stop_retry_unchanged": True}))

    def test_full_coverage_and_record_retry(self):
        self.run_case("full")

    def test_partial_coverage_keeps_only_uncovered_file(self):
        self.run_case("partial")

    def test_no_semantic_record_keeps_fallback(self):
        self.run_case("none")

    def test_code_mode_nested_tools_share_real_turn(self):
        self.run_case("full", code_mode=True)


if __name__ == "__main__":
    unittest.main()
