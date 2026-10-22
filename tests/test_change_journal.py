from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import change_journal as cj  # noqa: E402
import codex_hook as hook  # noqa: E402
import hermes_adapter as hermes  # noqa: E402


class ChangeJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "journal"
        cj.init_root(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record(self, **overrides):
        values = {
            "actor": "codex",
            "source": "semantic",
            "phase": "verified",
            "summary": "完成测试变更",
            "why": "验证事件台账",
            "cwd": "/workspace/project",
            "changed_paths": ["/workspace/project/example.md"],
            "verification_status": "passed",
            "verification_refs": ["unittest passed"],
            "session_id": "session-test",
        }
        values.update(overrides)
        return cj.record_event(self.root, **values)

    def test_route_uses_longest_prefix_and_unknown_is_unassigned(self):
        self.assertEqual(
            cj.route_scope(self.root, "/workspace/project", [str(SKILL_ROOT / "SKILL.md")]),
            "CHANGE-JOURNAL",
        )
        self.assertEqual(cj.route_scope(self.root, "/tmp/unknown", []), "UNASSIGNED")

    def test_custom_root_initializes_only_actual_tool_and_journal_routes(self):
        registry = cj.load_registry(self.root)
        self.assertEqual({r["path"] for r in registry["routes"]},
                         {str(self.root.absolute()), str(SKILL_ROOT.resolve())})
        self.assertEqual(cj.route_scope(self.root, primary_path=str(self.root / "views/sample.md")), "CHANGE-JOURNAL")
        self.assertEqual(cj.route_scope(self.root, primary_path="/workspace/unregistered-project"), "UNASSIGNED")

    def test_record_is_idempotent_and_lifecycle_is_append_only(self):
        first = self.record(idempotency_key="same-turn")
        duplicate = self.record(summary="不会覆盖原事件", idempotency_key="same-turn")
        self.assertEqual(first["event_id"], duplicate["event_id"])
        self.assertTrue(duplicate["duplicate"])

        later = self.record(
            phase="active",
            summary="运行态已经加载",
            related_event_ids=[first["event_id"]],
            idempotency_key="same-turn-active",
        )
        self.assertNotEqual(first["event_id"], later["event_id"])
        events = cj.load_events(self.root)
        self.assertEqual(len(events), 2)
        by_id = {event["event_id"]: event for event in events}
        self.assertEqual(by_id[later["event_id"]]["related_event_ids"], [first["event_id"]])

    def test_render_is_deterministic_and_creates_views(self):
        self.record(follow_ups=["需要下一轮确认运行态"])
        result = cj.render_views(self.root)
        global_path = Path(result["global_view"])
        continuation_path = Path(result["continuation_view"])
        first = global_path.read_bytes()
        cj.render_views(self.root)
        self.assertEqual(first, global_path.read_bytes())
        self.assertIn("完成测试变更", global_path.read_text(encoding="utf-8"))
        self.assertIn("需要下一轮确认运行态", continuation_path.read_text(encoding="utf-8"))

    def test_new_lifecycle_event_supersedes_old_continuation(self):
        first = self.record(follow_ups=["旧的未决事项"], idempotency_key="old-node")
        self.record(
            summary="后续事件已经接手",
            supersedes_event_ids=[first["event_id"]],
            follow_ups=["只保留新的未决事项"],
            idempotency_key="new-node",
        )
        result = cj.render_views(self.root)
        text = Path(result["continuation_view"]).read_text(encoding="utf-8")
        self.assertNotIn("旧的未决事项", text)
        self.assertIn("只保留新的未决事项", text)
        self.assertEqual(cj.status(self.root)["pending"], 1)

    def test_secret_is_redacted_and_path_traversal_is_rejected(self):
        raw_secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        result = self.record(summary=f"迁移凭据 {raw_secret}")
        event_text = Path(result["event_path"]).read_text(encoding="utf-8")
        self.assertNotIn(raw_secret, event_text)
        self.assertIn("[REDACTED]", event_text)
        with self.assertRaises(cj.JournalError):
            self.record(changed_paths=["/workspace/project/../secret"])

    def test_concurrent_process_writes_do_not_lose_events(self):
        script = SCRIPTS / "change_journal.py"
        processes = []
        for index in range(8):
            cmd = [
                sys.executable,
                str(script),
                "--root",
                str(self.root),
                "record",
                "--actor",
                "codex",
                "--source",
                "semantic",
                "--phase",
                "applied",
                "--summary",
                f"concurrent-{index}",
                "--cwd",
                "/tmp",
                "--idempotency-key",
                f"concurrent-{index}",
            ]
            processes.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn("event_id", stdout)
        events = cj.load_events(self.root)
        self.assertEqual(len(events), 8)
        self.assertEqual(len({event["event_id"] for event in events}), 8)
        self.assertTrue(cj.doctor(self.root)["ok"])

    def test_incomplete_temp_file_is_ignored(self):
        temp_path = self.root / "events" / "2026" / "09" / ".broken.tmp"
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text("not-json", encoding="utf-8")
        self.assertEqual(cj.load_events(self.root), [])
        self.assertTrue(cj.doctor(self.root)["ok"])


class CodexHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "journal"
        cj.init_root(self.root)
        self.base = {
            "session_id": "session-hook",
            "turn_id": "turn-1",
            "cwd": "/workspace/project",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_observe_then_stop_creates_one_sanitized_fallback(self):
        raw_secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        payload = dict(
            self.base,
            tool_name="apply_patch",
            tool_input={
                "command": "*** Begin Patch\n*** Update File: safe.md\nsecret=" + raw_secret + "\n*** End Patch"
            },
            tool_response={"ok": True},
        )
        hook.observe(payload, self.root)
        hook.finalize(dict(self.base), self.root)
        hook.finalize(dict(self.base), self.root)
        events = cj.load_events(self.root)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "codex-hook")
        self.assertEqual(events[0]["changed_paths"], ["/workspace/project/safe.md"])
        all_text = "\n".join(path.read_text(encoding="utf-8") for path in self.root.rglob("*.json"))
        self.assertNotIn(raw_secret, all_text)

    def test_read_only_and_self_commands_create_no_observation(self):
        read_only = dict(
            self.base,
            tool_name="Bash",
            tool_input={"command": "rg --files /workspace/project"},
            tool_response={"exit_code": 0},
        )
        hook.observe(read_only, self.root)
        self_command = dict(
            self.base,
            tool_name="Bash",
            tool_input={"command": "/usr/bin/python3 /x/change-journal/scripts/change_journal.py render"},
            tool_response={"exit_code": 0},
        )
        hook.observe(self_command, self.root)
        hook.finalize(dict(self.base), self.root)
        self.assertEqual(cj.load_events(self.root), [])

    def test_semantic_event_suppresses_mechanical_duplicate(self):
        payload = dict(
            self.base,
            tool_name="apply_patch",
            tool_input={"command": "*** Begin Patch\n*** Update File: semantic.md\n*** End Patch"},
            tool_response={"ok": True},
        )
        hook.observe(payload, self.root)
        cj.record_event(
            self.root,
            actor="codex",
            source="semantic",
            phase="verified",
            summary="语义记录",
            cwd=self.base["cwd"],
            changed_paths=["/workspace/project/semantic.md"],
            verification_status="passed",
            session_id=self.base["session_id"],
            turn_id=self.base["turn_id"],
        )
        hook.finalize(dict(self.base), self.root)
        events = cj.load_events(self.root)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "semantic")

    def test_hook_cli_always_emits_valid_empty_json(self):
        script = SCRIPTS / "codex_hook.py"
        payload = dict(
            self.base,
            tool_name="Bash",
            tool_input={"command": "rg --files"},
            tool_response={"exit_code": 0},
        )
        proc = subprocess.run(
            [sys.executable, str(script), "observe", "--root", str(self.root)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {})


class HermesAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "journal"
        cj.init_root(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_adapter_reuses_schema_and_is_idempotent_per_profile(self):
        payload = {
            "profile_id": "profile-b",
            "external_event_id": "task-42",
            "session_id": "session-hermes",
            "phase": "verified",
            "summary": "Hermes 完成配置变更",
            "cwd": "/workspace/project",
            "changed_paths": ["/workspace/project/hermes.yaml"],
            "verification": {"status": "passed", "refs": ["gateway deep status passed"]},
        }
        first = hermes.record_payload(payload, self.root)
        duplicate = hermes.record_payload(payload, self.root)
        self.assertEqual(first["event_id"], duplicate["event_id"])
        self.assertTrue(duplicate["duplicate"])
        event = cj.load_events(self.root)[0]
        self.assertEqual(event["actor"], "hermes")
        self.assertEqual(event["profile_id"], "profile-b")
        self.assertEqual(event["source"], "hermes-adapter")

    def test_adapter_requires_explicit_identity(self):
        with self.assertRaises(cj.JournalError):
            hermes.record_payload({"external_event_id": "task-1", "summary": "missing profile"}, self.root)


if __name__ == "__main__":
    unittest.main()
