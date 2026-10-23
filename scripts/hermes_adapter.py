#!/usr/bin/env python3
"""Thin stdin adapter that lets Hermes reuse the change-journal authority."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional, Sequence

import change_journal as cj


MAX_STDIN = 65_536


def read_payload() -> Dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    if len(raw) > MAX_STDIN:
        raise cj.JournalError("Hermes adapter payload exceeds size limit")
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise cj.JournalError("Hermes adapter payload must be an object")
    return payload


def record_payload(payload: Dict[str, Any], root) -> Dict[str, Any]:
    profile_id = cj.safe_id(payload.get("profile_id"), "profile_id")
    external_event_id = cj.safe_id(payload.get("external_event_id"), "external_event_id")
    if not profile_id or not external_event_id:
        raise cj.JournalError("profile_id and external_event_id are required")

    verification = payload.get("verification") or {}
    if not isinstance(verification, dict):
        raise cj.JournalError("verification must be an object")
    changed_paths = payload.get("changed_paths") or []
    unresolved = payload.get("unresolved") or []
    related = payload.get("related_event_ids") or []
    tags = payload.get("tags") or []
    refs = verification.get("refs") or []
    for name, value in (
        ("changed_paths", changed_paths),
        ("unresolved", unresolved),
        ("related_event_ids", related),
        ("tags", tags),
        ("verification.refs", refs),
        ("follow_ups", payload.get("follow_ups", [])),
        ("limitations", payload.get("limitations", [])),
        ("resolves_event_ids", payload.get("resolves_event_ids", [])),
        ("supersedes_event_ids", payload.get("supersedes_event_ids", [])),
    ):
        if not isinstance(value, list):
            raise cj.JournalError(f"{name} must be a list")

    return cj.record_event(
        root,
        actor="hermes",
        source="hermes-adapter",
        phase=str(payload.get("phase") or "applied"),
        summary=str(payload.get("summary") or ""),
        why=str(payload.get("why") or ""),
        cwd=str(payload.get("cwd") or ""),
        changed_paths=[str(value) for value in changed_paths],
        verification_status=str(verification.get("status") or "not_run"),
        verification_refs=[str(value) for value in refs],
        rollback_ref=str(payload.get("rollback_ref") or ""),
        unresolved=[str(value) for value in unresolved],
        related_event_ids=[str(value) for value in related],
        tags=[str(value) for value in tags] + ["hermes", profile_id],
        sensitivity=str(payload.get("sensitivity") or "private"),
        scope_id=str(payload.get("scope_id") or ""),
        host_id=str(payload.get("host_id") or ""),
        profile_id=profile_id,
        session_id=str(payload.get("session_id") or ""),
        turn_id=str(payload.get("turn_id") or ""),
        occurred_at=str(payload.get("occurred_at") or ""),
        idempotency_key=f"hermes:{profile_id}:{external_event_id}",
        record_level=str(payload.get("record_level") or "brief"),
        kind=str(payload.get("kind") or ""),
        work_id=str(payload.get("work_id") or ""),
        work_title=str(payload.get("work_title") or ""),
        primary_path=str(payload.get("primary_path") or ""),
        follow_ups=payload.get("follow_ups", []),
        limitations=payload.get("limitations", []),
        resolves_event_ids=payload.get("resolves_event_ids", []),
        supersedes_event_ids=payload.get("supersedes_event_ids", []),
        lesson_key=str(payload.get("lesson_key") or ""),
        lesson_trigger=str(payload.get("lesson_trigger") or ""),
        lesson_check=str(payload.get("lesson_check") or ""),
        lesson_status=str(payload.get("lesson_status") or "active"),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    try:
        result = record_payload(read_payload(), cj.root_from(args.root))
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (cj.JournalError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
