from __future__ import annotations

import hashlib
import json
import os
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


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "journal"
        cj.init_root(self.root)
        self.env = mock.patch.dict(os.environ, {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CODEX_TURN_ID": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def record(self, **kw):
        args = dict(actor="codex", source="semantic", phase="verified", summary="checked change",
                    scope_id="PROJECT", host_id="host-a", profile_id="personal", cwd="/workspace/project",
                    changed_paths=["/workspace/project/a.md"], session_id="session-a", turn_id="turn-a",
                    verification_status="passed", verification_refs=["isolated check"])
        args.update(kw)
        return cj.record_event(self.root, **args)

    def ref(self, key="same-key"):
        return self.root / "idempotency" / (cj.idempotency_hash(key, "codex", "semantic", "host-a", "personal") + ".json")

    def legacy_fact(self, result, key):
        path = Path(result["event_path"])
        event = json.loads(path.read_text(encoding="utf-8"))
        (self.root / "idempotency" / (event["idempotency_key_hash"] + ".json")).unlink()
        event["idempotency_key_hash"] = hashlib.sha256(key.encode()).hexdigest()
        event.pop("idempotency_payload_hash", None)
        path.write_text(json.dumps(event), encoding="utf-8")
        cj._atomic_write(self.root / "idempotency" / (event["idempotency_key_hash"] + ".json"),
                         cj._json_bytes({"event_id": event["event_id"]}))

    def facts(self):
        return {p.name: p.read_bytes() for p in (self.root / "events").glob("*/*/*.json")}

    def test_retry_after_index_write_failure_recovers_original_fact(self):
        write = cj._atomic_write
        def fail_index(path, *args, **kw):
            if path.parent.name == "idempotency":
                raise OSError("injected index write failure")
            return write(path, *args, **kw)
        with mock.patch.object(cj, "_atomic_write", side_effect=fail_index), self.assertRaises(OSError):
            self.record(idempotency_key="same-key", work_id="one-task", work_title="Original task")
        before = self.facts()
        self.assertEqual(len(before), 1)
        self.assertTrue(any("missing idempotency index" in i for i in cj.doctor(self.root)["issues"]))
        result = self.record(idempotency_key="same-key", work_id="one-task", work_title="Original task")
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["work_title"], "Original task")
        self.assertEqual(before, self.facts())
        self.assertEqual(json.loads(self.ref().read_text(encoding="utf-8"))["event_id"], result["event_id"])
        self.assertTrue(cj.doctor(self.root)["ok"])

    def test_retry_after_semantic_marker_failure_rebuilds_marker(self):
        with mock.patch.object(cj, "_semantic_marker", side_effect=OSError("injected marker failure")), self.assertRaises(OSError):
            self.record(idempotency_key="same-key")
        before = self.facts()
        result = self.record(idempotency_key="same-key", turn_id="unrelated-retry")
        marker_path = self.root / ".spool/semantic/session-a" / (result["event_id"] + ".json")
        self.assertEqual(json.loads(marker_path.read_text(encoding="utf-8"))["turn_id"], "turn-a")
        mtime = marker_path.stat().st_mtime_ns
        self.record(idempotency_key="same-key")
        self.assertEqual(mtime, marker_path.stat().st_mtime_ns)
        self.assertEqual(before, self.facts())

    def test_missing_broken_or_wrong_index_is_rebuilt_from_facts(self):
        first = self.record(idempotency_key="same-key")
        other = self.record(idempotency_key="other-key")
        before = self.facts()
        for content in (None, "{broken", "[]", '{"event_id":"missing"}', json.dumps({"event_id": other["event_id"]})):
            with self.subTest(content=content):
                if content is None:
                    self.ref().unlink()
                else:
                    self.ref().write_text(content, encoding="utf-8")
                self.assertFalse(cj.doctor(self.root)["ok"])
                result = self.record(idempotency_key="same-key")
                self.assertEqual(result["event_id"], first["event_id"])
                self.assertTrue(cj.doctor(self.root)["ok"])
        self.assertEqual(before, self.facts())

    def test_dangling_index_without_fact_does_not_report_false_success(self):
        self.ref().write_text('{"event_id":"missing"}', encoding="utf-8")
        with self.assertRaisesRegex(cj.JournalError, "no matching fact"):
            self.record(idempotency_key="same-key")
        self.assertEqual(self.facts(), {})

    def test_duplicate_fact_hash_is_reported_and_recovery_does_not_guess(self):
        first = self.record(idempotency_key="same-key")
        second = self.record()
        path = Path(second["event_path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["idempotency_key_hash"] = json.loads(Path(first["event_path"]).read_text(encoding="utf-8"))["idempotency_key_hash"]
        path.write_text(json.dumps(data), encoding="utf-8")  # Simulate an old interrupted-write duplicate.
        self.assertTrue(any("duplicate idempotency key" in i for i in cj.doctor(self.root)["issues"]))
        self.ref().unlink()
        before = self.facts()
        with self.assertRaisesRegex(cj.JournalError, "multiple facts"):
            self.record(idempotency_key="same-key")
        self.assertEqual(before, self.facts())

    def test_concurrent_same_key_produces_one_fact(self):
        fresh_root = Path(self.temp.name) / "fresh-journal"
        cmd = [sys.executable, str(SCRIPTS / "change_journal.py"), "--root", str(fresh_root), "record",
               "--actor", "codex", "--source", "semantic", "--phase", "applied",
               "--summary", "one operation", "--idempotency-key", "same-key"]
        processes = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
        results = []
        for proc in processes:
            out, err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, err)
            results.append(json.loads(out))
        self.assertEqual(len({r["event_id"] for r in results}), 1)
        self.assertEqual(sum(not r["duplicate"] for r in results), 1)
        self.assertEqual(len(cj.load_events(fresh_root)), 1)
        self.assertTrue(cj.doctor(fresh_root)["ok"])

    def test_distinct_long_or_normalized_keys_do_not_merge_operations(self):
        keys = ["x" * 310 + "A", "x" * 310 + "B", "word  spacing", "word spacing"]
        results = [self.record(idempotency_key=key) for key in keys]
        self.assertEqual(len({r["event_id"] for r in results}), len(keys))
        for key, first in zip(keys, results):
            self.assertEqual(self.record(idempotency_key=key)["event_id"], first["event_id"])
        self.assertTrue(cj.doctor(self.root)["ok"])

    def test_idempotency_keys_are_isolated_by_producer(self):
        first = self.record(idempotency_key="shared")
        for field, value in (("actor", "claude"), ("source", "adapter"),
                             ("host_id", "host-b"), ("profile_id", "customer")):
            args = {field: value, "idempotency_key": "shared", "summary": "independent change"}
            with self.subTest(field=field):
                other = self.record(**args)
                self.assertNotEqual(first["event_id"], other["event_id"])
                self.assertTrue(self.record(**args)["duplicate"])
        self.assertEqual(len(self.facts()), 5)
        self.assertTrue(cj.doctor(self.root)["ok"])

    def test_same_key_conflicting_payload_is_visible_and_preserves_facts(self):
        first = self.record(idempotency_key="shared")
        before = self.facts()
        conflicts = [dict(summary="new fact"), dict(phase="active"), dict(verification_refs=["new evidence"]),
                     dict(changed_paths=["/workspace/another/file"]), dict(follow_ups=["still needs verification"]),
                     dict(limitations=["not deployed"]), dict(work_id="another-work"), dict(work_title="new title"),
                     dict(occurred_at="2026-10-01T00:00:00Z"), dict(scope_id="OTHER")]
        for values in conflicts:
            with self.subTest(values=values), self.assertRaisesRegex(cj.JournalError, "idempotency conflict.*" + first["event_id"]):
                self.record(idempotency_key="shared", **values)
        self.assertEqual(before, self.facts())
        # Retry transport metadata may change, while the original provenance stays immutable.
        self.assertEqual(self.record(idempotency_key="shared", session_id="new-session", turn_id="new-turn")["event_id"],
                         first["event_id"])

    def test_legacy_retry_compares_payload_and_does_not_claim_another_producer(self):
        original = self.record(idempotency_key="legacy")
        self.legacy_fact(original, "legacy")
        before = self.facts()
        self.assertEqual(self.record(idempotency_key="legacy")["event_id"], original["event_id"])
        with self.assertRaisesRegex(cj.JournalError, "idempotency conflict"):
            self.record(idempotency_key="legacy", summary="different")
        self.assertEqual(before, self.facts())
        other = self.record(idempotency_key="legacy", actor="claude", summary="different")
        self.assertNotEqual(other["event_id"], original["event_id"])
        self.assertEqual(before[Path(original["event_path"]).name], Path(original["event_path"]).read_bytes())
        self.assertTrue(cj.doctor(self.root)["ok"])

    def test_payload_truncation_does_not_hide_a_conflicting_retry(self):
        first = self.record(idempotency_key="long-payload", summary="a" * 600 + "first")
        before = self.facts()
        self.assertTrue(self.record(idempotency_key="long-payload", summary="a" * 600 + "first")["duplicate"])
        with self.assertRaisesRegex(cj.JournalError, "idempotency conflict"):
            self.record(idempotency_key="long-payload", summary="a" * 600 + "second")
        self.assertEqual(before, self.facts())
        self.assertNotIn("a" * 600, Path(first["event_path"]).read_text(encoding="utf-8"))

    def test_legacy_transformed_key_requires_inspection_instead_of_false_deduplication(self):
        raw = "x" * 310 + "A"
        old_key, _ = cj.safe_text(raw, "idempotency_key", 300)
        self.legacy_fact(self.record(idempotency_key=old_key), old_key)
        before = self.facts()
        with self.assertRaisesRegex(cj.JournalError, "legacy idempotency key"):
            self.record(idempotency_key=raw)
        self.assertEqual(before, self.facts())

    def test_conflicting_routes_preserve_fact_with_visible_unassigned_scope(self):
        registry = cj.default_registry()
        routes = [dict(path="/workspace/project/", scope_id="ONE"), dict(path="/workspace/project", scope_id="TWO")]
        for ordered in (routes, list(reversed(routes))):
            (self.root / "registry.json").write_text(json.dumps(dict(registry, routes=ordered)), encoding="utf-8")
            self.assertFalse(cj.doctor(self.root)["ok"])
            with self.assertRaisesRegex(cj.JournalError, "conflicting scopes"):
                cj.route_scope(self.root, primary_path="/workspace/project")
            result = self.record(scope_id="")
            self.assertTrue(result["warnings"])
            self.assertEqual(json.loads(Path(result["event_path"]).read_text(encoding="utf-8"))["scope_id"], "UNASSIGNED")

    def test_equal_length_target_ambiguity_can_be_resolved_by_primary_path(self):
        registry = cj.default_registry()
        registry["routes"] = [dict(path="/workspace/a", scope_id="ONE"), dict(path="/workspace/b", scope_id="TWO")]
        (self.root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(cj.JournalError, "ambiguous target scopes"):
            cj.route_scope(self.root, changed_paths=["/workspace/a/file", "/workspace/b/file"])
        self.assertEqual(cj.route_scope(self.root, primary_path="/workspace/b"), "TWO")

    def test_corrupt_closure_returns_partial_facts_without_false_pending_conclusions(self):
        old = self.record(follow_ups=["verify"])
        closed = self.record(resolves_event_ids=[old["event_id"]])
        cj.render_views(self.root)
        view = self.root / "views/00_待续接_CONTINUATIONS.md"
        before = view.read_bytes()
        Path(closed["event_path"]).write_text("{broken", encoding="utf-8")
        result = cj.query_events(self.root, scope_id="PROJECT")
        self.assertTrue(result["partial"])
        self.assertEqual(result["events"][0]["event_id"], old["event_id"])
        for field in ("pending", "lessons", "work_items"):
            self.assertIsNone(result[field])
        self.assertEqual(len(result["read_errors"]), 1)
        self.assertFalse(cj.doctor(self.root)["ok"])
        with self.assertRaises(cj.JournalError):
            cj.render_views(self.root)
        self.assertEqual(view.read_bytes(), before)

    def test_invalid_json_shapes_are_reported_without_diagnostic_crashes(self):
        valid = self.record()
        data = json.loads(Path(valid["event_path"]).read_text(encoding="utf-8"))
        bad_path = Path(valid["event_path"]).parent / "bad.json"
        values = [[], None, {}, dict(data, verification=[]), dict(data, verification={"status": []}),
                  dict(data, phase=[]), dict(data, schema_version=[]), dict(data, scope_id=[]),
                  dict(data, occurred_at="no-date"), dict(data, lesson=[]), dict(data, changed_paths="bad")]
        for value in values:
            with self.subTest(value=value):
                bad_path.write_text(json.dumps(value), encoding="utf-8")
                self.assertFalse(cj.doctor(self.root)["ok"])
                result = cj.query_events(self.root, scope_id="PROJECT")
                self.assertTrue(result["partial"])
                self.assertEqual(result["matched"], 1)

    def test_doctor_does_not_create_missing_journal(self):
        absent = Path(self.temp.name) / "absent"
        self.assertFalse(cj.doctor(absent)["ok"])
        self.assertFalse(absent.exists())

    def test_same_second_lesson_update_and_retirement_ignore_random_id_order(self):
        args = dict(occurred_at="2026-10-22T01:00:00Z", lesson_key="one-check", lesson_trigger="before release", lesson_check="old check")
        with mock.patch.object(cj, "_event_id", side_effect=["20261022010000-zzz", "20261022010000-mmm", "20261022010000-aaa"]):
            self.record(**args)
            newer = self.record(**dict(args, lesson_check="updated check"))
            self.assertEqual(cj.current_lessons(cj.load_events(self.root))[0]["event_id"], newer["event_id"])
            self.record(**args, lesson_status="retired")
        self.assertEqual(cj.current_lessons(cj.load_events(self.root)), [])
        self.assertEqual(cj.query_events(self.root, scope_id="PROJECT")["lessons"], [])

    def test_same_second_query_cards_and_inherited_title_agree(self):
        with mock.patch.object(cj, "_event_id", side_effect=["20261022010000-zzz", "20261022010000-mmm", "20261022010000-aaa"]):
            self.record(work_id="one", work_title="Old title", occurred_at="2026-10-22T01:00:00Z")
            self.record(work_id="one", work_title="New title", occurred_at="2026-10-22T01:00:00Z")
            latest = self.record(work_id="one", occurred_at="2026-10-22T01:00:00Z")
        result = cj.query_events(self.root, work_id="one")
        self.assertEqual(result["events"][0]["event_id"], latest["event_id"])
        self.assertEqual(latest["work_title"], "New title")
        self.assertEqual(result["work_items"][0]["latest_event_id"], latest["event_id"])
        cj.render_views(self.root)
        text = (self.root / "views/00_全局变更时间线_CHANGELOG.md").read_text(encoding="utf-8")
        self.assertLess(text.index("20261022010000-aaa"), text.index("20261022010000-zzz"))

    def test_order_normalizes_precision_timezones_and_missing_recorded_time(self):
        values = [dict(event_id="z", occurred_at="2026-10-22T01:00:00Z", recorded_at="2026-10-22T09:00:00+08:00"),
                  dict(event_id="a", occurred_at="2026-10-22T01:00:00Z", recorded_at="2026-10-22T01:00:00.001Z")]
        self.assertEqual([e["event_id"] for e in sorted(values, key=cj._event_order)], ["z", "a"])
        self.assertEqual(cj._event_order(dict(event_id="legacy", occurred_at="2026-10-22T01:00:00Z"))[:2],
                         cj._event_order(values[0])[:2])

    def test_closure_rejects_each_identity_boundary_for_both_relations(self):
        old = self.record(follow_ups=["verify"], work_id="task")
        before = self.facts()
        for field, value in (("scope_id", "OTHER"), ("host_id", "host-b"), ("profile_id", "customer")):
            for relation in ("resolves_event_ids", "supersedes_event_ids"):
                with self.subTest(field=field, relation=relation), self.assertRaisesRegex(cj.JournalError, field):
                    self.record(**{field: value, relation: [old["event_id"]]})
        self.assertEqual(before, self.facts())
        self.assertEqual(cj.status(self.root)["pending"], 1)
        result = self.record(resolves_event_ids=[old["event_id"]])
        self.assertEqual(result["work_id"], "task")
        self.assertEqual(cj.status(self.root)["pending"], 0)

    def test_closure_uses_explicit_scope_correction_without_rewriting_old_fact(self):
        old = self.record(scope_id="BROAD", follow_ups=["verify"])
        old_bytes = Path(old["event_path"]).read_bytes()
        (self.root / "work-items.json").write_text(json.dumps({"schema_version": "1.0", "items": [
            dict(work_id="task", title="Corrected task", scope_id="PROJECT", host_id="host-a", profile_id="personal",
                 event_ids=[old["event_id"]])]}), encoding="utf-8")
        result = self.record(resolves_event_ids=[old["event_id"]])
        self.assertEqual(result["work_id"], "task")
        self.assertEqual(cj.status(self.root)["pending"], 0)
        self.assertEqual(Path(old["event_path"]).read_bytes(), old_bytes)

    def observe(self, paths=("a.md",), **kw):
        payload = dict(session_id="session-a", turn_id="turn-a", host_id="host-a", profile_id="personal",
                       cwd="/workspace/project", tool_name="apply_patch",
                       tool_input={"command": "*** Begin Patch\n" + "\n".join("*** Update File: " + p for p in paths) + "\n*** End Patch"},
                       tool_response={"ok": True})
        payload.update(kw)
        hook.observe(payload, self.root)

    def stop(self):
        hook.finalize(dict(session_id="session-a", turn_id="turn-a"), self.root)
        return [e for e in cj.load_events(self.root) if e["source"] == "codex-hook"]

    def test_hook_never_suppresses_different_turn_session_cwd_host_profile_or_path(self):
        self.observe()
        for kw in (dict(turn_id="turn-b"), dict(session_id="session-b"), dict(cwd="/elsewhere"),
                   dict(host_id="host-b"), dict(profile_id="customer"), dict(changed_paths=["/workspace/project/b.md"]),
                   dict(turn_id=""), dict(session_id="")):
            self.record(**kw)
        fallback = self.stop()
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]["changed_paths"], ["/workspace/project/a.md"])
        self.assertEqual(fallback[0]["profile_id"], "personal")

    def test_hook_retains_only_uncovered_paths_of_partial_observation(self):
        self.observe(("a.md", "b.md"))
        self.record()
        fallback = self.stop()
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]["changed_paths"], ["/workspace/project/b.md"])
        self.assertEqual(len(self.stop()), 1)

    def test_multiple_semantic_events_can_cover_one_observation(self):
        self.observe(("a.md", "b.md"))
        self.record()
        self.record(changed_paths=["/workspace/project/b.md"])
        self.assertEqual(self.stop(), [])
        final = json.loads((self.root / ".spool/codex/session-a/turn-a/.finalized.json").read_text(encoding="utf-8"))
        self.assertEqual(len(final["event_ids"]), 2)

    def test_semantic_event_before_later_same_path_mutation_cannot_suppress_it(self):
        self.observe()
        self.record()
        self.observe()
        self.assertEqual(len(self.stop()), 1)

    def test_unknown_path_is_retained_even_with_same_turn_semantic_event(self):
        self.observe(())
        self.record()
        self.assertEqual(len(self.stop()), 1)

    def test_orphaned_marker_does_not_suppress_observation(self):
        self.observe()
        result = self.record()
        Path(result["event_path"]).unlink()  # Only this synthetic test fixture.
        self.assertEqual(len(self.stop()), 1)

    def test_retry_repairs_marker_before_hook_stop(self):
        self.observe()
        with mock.patch.object(cj, "_semantic_marker", side_effect=OSError("injected")), self.assertRaises(OSError):
            self.record(idempotency_key="same-key")
        self.record(idempotency_key="same-key")
        self.assertEqual(self.stop(), [])
        self.assertEqual(len(self.facts()), 1)

    def test_host_supplied_turn_environment_is_shared_by_record_and_hook(self):
        with mock.patch.dict(os.environ, {"CODEX_TURN_ID": "turn-a"}):
            self.observe(turn_id="")
            self.record(turn_id="")
        self.assertEqual(self.stop(), [])

    def test_hook_fallback_keeps_profile_boundaries_and_retry_idempotency(self):
        self.observe()
        self.observe(profile_id="customer")
        write = cj._atomic_write
        def fail_finalize(path, *args, **kw):
            if path.name == ".finalized.json":
                raise OSError("injected finalize failure")
            return write(path, *args, **kw)
        with mock.patch.object(cj, "_atomic_write", side_effect=fail_finalize), self.assertRaises(OSError):
            self.stop()
        before = self.facts()
        fallback = self.stop()
        self.assertEqual({e["profile_id"] for e in fallback}, {"personal", "customer"})
        self.assertEqual(before, self.facts())

    def test_unfinished_legacy_stop_reuses_existing_fallback_after_upgrade(self):
        self.observe()
        old = self.record(source="codex-hook", actor="observer", phase="observed", verification_status="not_run",
                          idempotency_key="codex-stop:session-a:turn-a")
        self.legacy_fact(old, "codex-stop:session-a:turn-a")
        before = self.facts()
        fallback = self.stop()
        self.assertEqual([e["event_id"] for e in fallback], [old["event_id"]])
        self.assertEqual(before, self.facts())


if __name__ == "__main__":
    unittest.main()
