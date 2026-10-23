from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import change_journal as cj
import codex_hook as hook
import trace_sources as trace


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "journal 中文"
        cj.init_root(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, **kwargs):
        values = dict(actor="test", source="semantic", phase="applied", summary="中文变更",
                      scope_id="TEST", host_id="test-host", session_id="session", turn_id="turn")
        values.update(kwargs)
        return cj.record_event(self.root, **values)

    def run_script(self, script, *args, payload=None, env=None):
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), "--root", str(self.root), *args],
                              input=None if payload is None else json.dumps(payload).encode("utf-8"),
                              capture_output=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return json.loads(proc.stdout)

    def test_foreign_windows_and_posix_paths_route_and_query_on_every_os(self):
        registry = dict(cj.default_registry(self.root), routes=[
            {"path": "C:/Projects", "scope_id": "ALL"},
            {"path": "c:/Projects/Demo", "scope_id": "DEMO"},
            {"path": r"\\server\share\Demo", "scope_id": "UNC"},
            {"path": "/workspace/demo", "scope_id": "POSIX"},
        ])
        (self.root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
        for path, scope in ((r"C:\PROJECTS\DEMO\文件.txt", "DEMO"),
                            ("//SERVER/SHARE/demo/file.txt", "UNC"),
                            ("/workspace/demo/file.txt", "POSIX")):
            with self.subTest(path=path):
                self.assertEqual(cj.route_scope(self.root, primary_path=path), scope)
                self.record(scope_id="", primary_path=path, changed_paths=[path])
        self.assertEqual(cj.query_events(self.root, path="c:/projects/demo")["matched"], 1)
        self.assertEqual(cj.query_events(self.root, path=r"\\server\share\DEMO")["matched"], 1)
        self.assertFalse(cj._within("C:/Projects/Demolition/a", "c:/projects/demo"))
        self.assertFalse(cj._within("D:/Projects/Demo/a", "c:/projects/demo"))
        self.assertFalse(cj._within("/workspace/Demo/a", "/workspace/demo"))
        self.assertEqual(cj.absolute_path(r"sub\file.txt", r"C:\Projects\Demo"), r"C:\Projects\Demo\sub\file.txt")

    def test_windows_ambiguous_and_traversing_paths_are_rejected(self):
        for path in ("C:relative", r"\relative", r"C:\demo\..\secret", r"\\server\share\..\secret"):
            with self.subTest(path=path), self.assertRaises(cj.JournalError):
                cj.safe_path(path)
        for path in ("C:relative", r"\relative"):
            with self.subTest(path=path), self.assertRaises(cj.JournalError):
                cj.absolute_path(path, "C:/demo")

    def test_windows_registry_aliases_cannot_silently_change_scope(self):
        registry = dict(cj.default_registry(self.root), routes=[
            {"path": r"C:\Demo", "scope_id": "ONE"},
            {"path": "c:/demo/", "scope_id": "TWO"},
        ])
        (self.root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaises(cj.JournalError):
            cj.load_registry(self.root)

    def test_utf8_bom_registry_and_hook_stdin_are_accepted(self):
        registry = cj.default_registry(self.root)
        (self.root / "registry.json").write_text(json.dumps(registry), encoding="utf-8-sig")
        self.assertEqual(cj.load_registry(self.root), registry)
        payload = dict(session_id="session", turn_id="turn", cwd="C:/Demo", tool_name="apply_patch",
                       tool_input={"command": "*** Update File: file.txt"})
        proc = subprocess.run([sys.executable, str(SCRIPTS / "codex_hook.py"), "--root", str(self.root), "observe"],
                              input=json.dumps(payload).encode("utf-8-sig"), capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(list((self.root / ".spool/errors").glob("*.json")))
        self.assertEqual(len(list((self.root / ".spool/codex").glob("*/*/*.json"))), 1)

    def test_optional_trace_paths_keep_drive_unc_and_uri_information(self):
        self.assertEqual(trace._changed_path("file:///C:/Projects/my%20file.txt"), r"C:\Projects\my file.txt")
        self.assertEqual(trace._changed_path("file://server/share/my%20file.txt"), r"\\server\share\my file.txt")
        self.assertEqual(trace._changed_path("file:///workspace/my%20file.txt"), "/workspace/my file.txt")
        self.assertEqual(trace._changed_path(r"sub\file.txt", "C:/Demo"), r"C:\Demo\sub\file.txt")
        self.assertEqual(trace._changed_path(r"C:relative"), "")
        self.assertEqual(trace._command_verb([r"C:\Program Files\Python\python.exe", "demo.py"]), "python.exe")

    def test_scope_views_and_ids_do_not_collide_or_use_device_names(self):
        scopes = ("a:b", "a-b", "scope", "SCOPE", "con", "CON", "nul.txt", "trailing.")
        for scope in scopes:
            self.record(scope_id=scope, session_id=scope, summary="scope " + scope)
        cj.render_views(self.root)
        views = list((self.root / "views/by-scope").glob("*.md"))
        self.assertEqual(len(views), len(scopes))
        self.assertEqual(len({p.name.lower() for p in views}), len(scopes))
        for scope in scopes:
            path = self.root / "views/by-scope" / (cj._scope_slug(scope) + ".md")
            self.assertIn("scope " + scope, path.read_text(encoding="utf-8"))
        for path in (self.root / ".spool").rglob("*"):
            self.assertTrue(cj._portable_name(path.name), path.name)

    def test_old_case_alias_view_cannot_retire_the_current_view(self):
        directory = self.root / "views/by-scope"
        (directory / "SCOPE.md").write_text("old view", encoding="utf-8")
        self.record(scope_id="scope", summary="current scope")
        self.record(scope_id="SCOPE", summary="other scope")
        cj.render_views(self.root)
        self.assertIn("current scope", (directory / "scope.md").read_text(encoding="utf-8"))
        self.assertIn("other scope", (directory / (cj._scope_slug("SCOPE") + ".md")).read_text(encoding="utf-8"))

    def test_historical_scope_text_cannot_escape_the_view_directory(self):
        for value in ("../escape", "a/b", "a\\b", "", "..", "a~b"):
            with self.subTest(value=value):
                component = cj.file_component(value)
                self.assertNotIn("/", component)
                self.assertNotIn("\\", component)
                self.assertNotIn(component, ("", ".", ".."))

    def test_ascii_console_preserves_unicode_facts_and_json_output(self):
        env = dict(os.environ, PYTHONIOENCODING="ascii")
        result = self.run_script("change_journal.py", "record", "--actor", "test", "--source", "semantic",
                                 "--phase", "applied", "--summary", "完成修改", "--work-id", "unicode",
                                 "--work-title", "中文事项", "--idempotency-key", "unicode", "--render", env=env)
        self.assertEqual(result["work_title"], "中文事项")
        self.assertTrue(result["views_updated"])
        query = self.run_script("change_journal.py", "query", "--work-id", "unicode", env=env)
        self.assertEqual(query["items"][0]["summary"], "完成修改")
        payload = dict(profile_id="local", external_event_id="unicode", summary="适配器记录", work_title="中文事项")
        self.run_script("hermes_adapter.py", payload=payload, env=env)
        self.assertEqual(len(cj.load_events(self.root)), 2)

    def test_markdown_links_are_relative_and_open_real_files(self):
        self.record(work_id="item", work_title="中文事项")
        cj.render_views(self.root)
        links = []
        for view in (self.root / "views").rglob("*.md"):
            for link in re.findall(r"\]\(([^)]+)\)", view.read_text(encoding="utf-8")):
                self.assertNotIn("\\", link)
                self.assertTrue((view.parent / link).is_file(), (view, link))
                links.append(link)
        self.assertTrue(links)

    def test_windows_hook_paths_deduplicate_across_case_and_separators(self):
        payload = dict(session_id="session:one", turn_id="turn:one", host_id="test-host",
                       cwd=r"C:\PROJECTS\Demo", tool_name="apply_patch",
                       tool_input={"command": "*** Update File: sub\\file.txt"})
        hook.observe(payload, self.root)
        self.record(session_id="session:one", turn_id="turn:one", cwd="c:/projects/demo",
                    changed_paths=["c:/projects/demo/SUB/FILE.txt"])
        hook.finalize(payload, self.root)
        self.assertEqual([e["source"] for e in cj.load_events(self.root)], ["semantic"])

    def test_cli_context_record_stop_roundtrip_on_native_filesystem(self):
        cwd = str(Path(self.temp.name) / "project 中文")
        target = str(Path(cwd) / "file.txt")
        payload = dict(session_id="session:one", turn_id="turn:one", tool_use_id="call:one",
                       cwd=cwd, host_id=socket.gethostname(), tool_name="apply_patch",
                       tool_input={"command": "*** Update File: file.txt"}, tool_response={"ok": True})
        self.run_script("codex_hook.py", "observe", payload=payload)
        context = dict(payload, tool_name="Bash", tool_input={"command": "python change_journal.py record"})
        self.run_script("codex_hook.py", "context", payload=context)
        env = dict(os.environ, CODEX_SESSION_ID=payload["session_id"], CODEX_THREAD_ID=payload["session_id"],
                   CODEX_TURN_ID="", CODEX_TOOL_CALL_ID=payload["tool_use_id"])
        args = ("record", "--actor", "codex", "--source", "semantic", "--phase", "applied",
                "--summary", "中文变更", "--cwd", cwd, "--changed-path", target, "--idempotency-key", "roundtrip")
        first = self.run_script("change_journal.py", *args, env=env)
        retry = self.run_script("change_journal.py", *args, env=env)
        self.assertEqual(first["event_id"], retry["event_id"])
        self.run_script("codex_hook.py", "observe", payload=context)
        self.run_script("codex_hook.py", "stop", payload=payload)
        self.run_script("codex_hook.py", "stop", payload=payload)
        events = cj.load_events(self.root)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["turn_id"], "turn:one")
        self.assertEqual(events[0]["source"], "semantic")
        self.assertFalse(list((self.root / ".spool/errors").glob("*.json")))
        self.assertFalse(list((self.root / ".spool/codex-context").rglob("*.json")))
        self.assertTrue(cj.doctor(self.root)["ok"])

    def test_legacy_context_names_remain_readable_and_identity_checked(self):
        directory = self.root / ".spool/codex-context/SessionA"
        directory.mkdir(parents=True)
        context = dict(session_id="SessionA", tool_use_id="CallA", turn_id="TurnA", host_id="test-host", profile_id="")
        path = directory / "CallA.json"
        path.write_text(json.dumps(context), encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_TOOL_CALL_ID": "CallA"}):
            self.assertEqual(cj._hook_turn_id(self.root, "SessionA", "test-host", ""), "TurnA")
            self.assertEqual(cj._hook_turn_id(self.root, "sessiona", "test-host", ""), "")
        hook._clear_context(context, self.root)
        self.assertFalse(path.exists())

    def test_powershell_mutations_are_observed_but_reads_are_not(self):
        for command in ("Set-Content file.txt value", "New-Item -ItemType Directory folder",
                        "Get-Content a | Out-File b", "Remove-Item file.txt"):
            with self.subTest(command=command):
                self.assertEqual(hook._mutation_category("Bash", command), "state_mutation")
        for command in ("Get-Content file.txt", "Get-ChildItem", "Test-Path file.txt"):
            self.assertEqual(hook._mutation_category("Bash", command), "")
        self.assertTrue(hook._is_self_command(r'python "C:\Tools\change-journal\scripts\change_journal.py" record'))


if __name__ == "__main__":
    unittest.main()
