from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import change_journal as cj
import codex_hook as hook


class HookContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {
            "CODEX_SESSION_ID": "session", "CODEX_THREAD_ID": "session",
            "CODEX_TURN_ID": "", "CODEX_TOOL_CALL_ID": "call-a",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def payload(self, **kw):
        return dict(session_id="session", turn_id="turn-a", tool_use_id="call-a",
                    tool_name="Bash", tool_input={"command": "python3 change_journal.py record"}, **kw)

    def record(self, **kw):
        return cj.record_event(self.root, actor="codex", source="semantic", phase="applied",
                               summary="context test", **kw)

    def event_turn(self, **kw):
        return json.loads(Path(self.record(**kw)["event_path"]).read_text())["turn_id"]

    def test_exact_tool_call_resolves_real_context_and_post_tool_cleans_it(self):
        payload = self.payload()
        hook.capture_context(payload, self.root)
        self.assertEqual(self.event_turn(), "turn-a")
        hook.observe(payload, self.root)
        self.assertEqual(list((self.root / ".spool/codex-context").rglob("*.json")), [])
        self.assertEqual(self.event_turn(), "")

    def test_parallel_calls_do_not_use_latest_turn(self):
        first = self.payload()
        second = dict(first, tool_use_id="call-b", turn_id="turn-b")
        hook.capture_context(first, self.root)
        hook.capture_context(second, self.root)
        self.assertEqual(self.event_turn(), "turn-a")
        with mock.patch.dict(os.environ, {"CODEX_TOOL_CALL_ID": "call-b"}):
            self.assertEqual(self.event_turn(), "turn-b")
        with mock.patch.dict(os.environ, {"CODEX_TOOL_CALL_ID": "missing"}):
            self.assertEqual(self.event_turn(), "")

    def test_explicit_turn_and_host_profile_session_boundaries(self):
        hook.capture_context(self.payload(), self.root)
        self.assertEqual(self.event_turn(turn_id="explicit"), "explicit")
        for kw in ({"host_id": "other-host"}, {"profile_id": "other-profile"}, {"session_id": "other-session"}):
            with self.subTest(kw=kw):
                self.assertEqual(self.event_turn(**kw), "")

    def test_context_is_allowlisted_and_non_journal_commands_leave_nothing(self):
        payload = self.payload(prompt="private text", tool_response="private output")
        payload["tool_input"]["command"] += " --summary private-details"
        hook.capture_context(payload, self.root)
        path = next((self.root / ".spool/codex-context").rglob("*.json"))
        self.assertEqual(json.loads(path.read_text()), {
            "session_id": "session", "turn_id": "turn-a", "tool_use_id": "call-a",
            "host_id": socket.gethostname(), "profile_id": "",
        })
        hook.observe(payload, self.root)
        hook.capture_context(dict(payload, tool_input={"command": "touch file"}), self.root)
        self.assertEqual(list((self.root / ".spool/codex-context").rglob("*.json")), [])

    def test_missing_invalid_or_mismatched_context_does_not_block_recording(self):
        path = self.root / ".spool/codex-context/session/call-a.json"
        self.assertEqual(self.event_turn(), "")
        path.parent.mkdir(parents=True)
        for content in ("{broken", "[]", "null", json.dumps(dict(self.payload(), tool_use_id="wrong"))):
            with self.subTest(content=content):
                path.write_text(content)
                self.assertEqual(self.event_turn(), "")


if __name__ == "__main__":
    unittest.main()
