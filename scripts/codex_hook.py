#!/usr/bin/env python3
"""Privacy-preserving Codex context/PostToolUse/Stop adapter for change-journal."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import socket
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import change_journal as cj


MAX_STDIN = 1_048_576
MUTATING_SHELL_RE = re.compile(
    r"(?im)(?:^|[;&|]\s*|\n\s*)"
    r"(?:mkdir|cp|mv|rm|touch|chmod|chown|ln|install|truncate|tee)\b|"
    r"\bsed\s+-i\b|"
    r"\bgit\s+(?:commit|merge|rebase|reset|clean|tag|switch|checkout)\b|"
    r"\b(?:pip3?|uv)\s+(?:install|uninstall|sync)\b|"
    r"\b(?:npm|pnpm|yarn|brew)\s+(?:install|uninstall|update|upgrade)\b|"
    r"\bdocker\s+(?:run|start|stop|restart|rm|compose\s+(?:up|down|restart))\b"
)
PATCH_PATH_RE = re.compile(r"(?m)^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$")
LOCAL_WRITE_NAME_RE = re.compile(
    r"(?i)(?:^|__|_)(?:apply_patch|write_file|edit_file|create_file|delete_file|move_file|rename_file)$"
)


def _read_payload() -> Dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    if len(raw) > MAX_STDIN:
        raise cj.JournalError("hook payload exceeds size limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise cj.JournalError("hook payload must be an object")
    return payload


def _payload_command(payload: Dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("command", "patch", "input"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value
    if isinstance(tool_input, str):
        return tool_input
    return ""


def _is_self_command(command: str) -> bool:
    return "change-journal/scripts/change_journal.py" in command or "change-journal/scripts/codex_hook.py" in command


def _mutation_category(tool_name: str, command: str) -> str:
    if tool_name == "apply_patch":
        return "file_mutation"
    if tool_name == "Bash":
        return "state_mutation" if MUTATING_SHELL_RE.search(command) else ""
    if tool_name in {"functions.exec", "functions.apply_patch"}:
        if "tools.apply_patch" in command or "*** Begin Patch" in command:
            return "file_mutation"
        if "tools.exec_command" in command and MUTATING_SHELL_RE.search(command):
            return "state_mutation"
        return ""
    if LOCAL_WRITE_NAME_RE.search(tool_name):
        return "file_mutation"
    return ""


def _normalise_candidate(raw: str, cwd: str) -> Optional[str]:
    value = raw.strip().strip("'\"")
    if not value or value == "/dev/null":
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        if not cwd:
            return None
        candidate = Path(cwd) / candidate
    try:
        return cj.safe_path(str(candidate), "hook path")
    except cj.JournalError:
        return None


def _extract_paths(payload: Dict[str, Any], command: str, cwd: str) -> List[str]:
    found: List[str] = []
    for raw in PATCH_PATH_RE.findall(command):
        candidate = _normalise_candidate(raw, cwd)
        if candidate and candidate not in found:
            found.append(candidate)

    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("path", "file", "file_path", "target_path"):
            value = tool_input.get(key)
            if isinstance(value, str):
                candidate = _normalise_candidate(value, cwd)
                if candidate and candidate not in found:
                    found.append(candidate)
    return found[:64]


def _result_state(response: Any) -> str:
    if isinstance(response, dict):
        if response.get("isError") is True or response.get("is_error") is True:
            return "error"
        if response.get("exit_code") not in (None, 0):
            return "error"
        if response.get("ok") is False:
            return "error"
        if response.get("isError") is False or response.get("exit_code") == 0 or response.get("ok") is True:
            return "ok"
    return "unknown"


def _safe_hook_ids(payload: Dict[str, Any]) -> Tuple[str, str]:
    session = payload.get("session_id") or os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")
    turn = payload.get("turn_id") or os.environ.get("CODEX_TURN_ID")
    if not session or not turn:
        raise cj.JournalError("hook payload lacks session_id or turn_id")
    return cj.safe_id(str(session), "session_id"), cj.safe_id(str(turn), "turn_id")


def _turn_dir(root: Path, session: str, turn: str) -> Path:
    return root / ".spool" / "codex" / session / turn


def capture_context(payload: Dict[str, Any], root: Path) -> None:
    # PreToolUse runs before the shell, when its real turn is already known.
    if payload.get("tool_name") != "Bash" or "change_journal.py" not in _payload_command(payload):
        return
    session, turn = _safe_hook_ids(payload)
    tool_id = cj.safe_id(payload.get("tool_use_id"), "tool_use_id")
    if not tool_id:
        return
    context = {
        "session_id": session, "turn_id": turn, "tool_use_id": tool_id,
        "host_id": cj.safe_id(payload.get("host_id") or socket.gethostname(), "host_id"),
        "profile_id": cj.safe_id(payload.get("profile_id"), "profile_id"),
    }
    cj._atomic_write(root / ".spool" / "codex-context" / session / f"{tool_id}.json", cj._json_bytes(context))


def _clear_context(payload: Dict[str, Any], root: Path) -> None:
    session = cj.safe_id(payload.get("session_id"), "session_id")
    tool_id = cj.safe_id(payload.get("tool_use_id"), "tool_use_id")
    if session and tool_id:
        (root / ".spool" / "codex-context" / session / f"{tool_id}.json").unlink(missing_ok=True)


def observe(payload: Dict[str, Any], root: Path) -> None:
    _clear_context(payload, root)
    tool_name, _ = cj.safe_text(str(payload.get("tool_name") or ""), "tool_name", 160)
    if not tool_name:
        return
    command = _payload_command(payload)
    if _is_self_command(command):
        return
    category = _mutation_category(tool_name, command)
    if not category:
        return

    session, turn = _safe_hook_ids(payload)
    raw_cwd = str(payload.get("cwd") or "")
    cwd = cj.safe_path(raw_cwd, "cwd") if raw_cwd else ""
    observation = {
        "schema_version": cj.SCHEMA_VERSION,
        "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "session_id": session,
        "turn_id": turn,
        "cwd": cwd,
        "host_id": cj.safe_id(payload.get("host_id") or socket.gethostname(), "host_id"),
        "profile_id": cj.safe_id(payload.get("profile_id"), "profile_id"),
        "tool_name": tool_name,
        "category": category,
        "changed_paths": _extract_paths(payload, command, cwd),
        "result": _result_state(payload.get("tool_response")),
    }
    cj.init_root(root)
    target = _turn_dir(root, session, turn) / f"{observation['occurred_at'].replace(':', '')}-{uuid.uuid4().hex}.json"
    cj._atomic_write(target, cj._json_bytes(observation), overwrite=False)


def _load_observations(turn_dir: Path) -> List[Dict[str, Any]]:
    observations: List[Dict[str, Any]] = []
    for path in sorted(turn_dir.glob("*.json")):
        if path.name.startswith(".finalized"):
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                item = json.load(handle)
            if isinstance(item, dict) and item.get("schema_version") in getattr(cj, "SUPPORTED_SCHEMA_VERSIONS", {"1.0", "1.1"}):
                observations.append(item)
        except (OSError, json.JSONDecodeError):
            continue
    return observations


def _parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _matching_semantic_markers(root: Path, session: str, turn: str) -> List[Dict[str, Any]]:
    markers = []
    marker_dir = root / ".spool" / "semantic" / session
    for path in sorted(marker_dir.glob("*.json")):
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
            if marker.get("session_id") != session or marker.get("turn_id") != turn:
                continue
            event_id = cj.safe_id(marker["event_id"], "semantic event_id")
            event_path = cj._find_event(root, event_id) if event_id else None
            if not event_path:
                continue
            event = json.loads(event_path.read_text(encoding="utf-8"))
            if (not cj.validate_event(event) and event.get("event_id") == event_id and event.get("source") == "semantic"
                    and event.get("session_id") == session and event.get("turn_id") == turn):
                _parse_utc(event["recorded_at"])
                markers.append(event)
        except (OSError, KeyError, ValueError, TypeError, AttributeError):
            continue
    return markers


def _uncovered_observations(observations: Sequence[Dict[str, Any]], markers: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    remaining, used = [], set()
    for item in observations:
        paths = item.get("changed_paths", [])
        covered = set()
        for marker in markers:
            if any(marker.get(k, "") != item.get(k, "") for k in
                   ("session_id", "turn_id", "cwd", "host_id", "profile_id")):
                continue
            if _parse_utc(marker["recorded_at"]) < _parse_utc(item["occurred_at"]):
                continue
            overlap = set(paths).intersection(marker.get("changed_paths", []))
            if overlap:
                covered.update(overlap)
                used.add(marker["event_id"])
        # Unknown paths cannot be proved covered by a directory or time proximity.
        if not paths or set(paths) - covered:
            remaining.append(dict(item, changed_paths=[p for p in paths if p not in covered]))
    return remaining, sorted(used)


def finalize(payload: Dict[str, Any], root: Path) -> None:
    session, turn = _safe_hook_ids(payload)
    turn_dir = _turn_dir(root, session, turn)
    finalized = turn_dir / ".finalized.json"
    if finalized.exists():
        return
    observations = _load_observations(turn_dir)
    if not observations:
        return

    observations, semantic_ids = _uncovered_observations(
        observations, _matching_semantic_markers(root, session, turn))
    if not observations:
        cj._atomic_write(
            finalized,
            cj._json_bytes({"mode": "semantic", "event_id": semantic_ids[-1],
                           "event_ids": semantic_ids, "finalized_at": cj.utc_now()}),
            overwrite=False,
        )
        return

    # A turn can visit more than one identity/cwd; keep their fallback evidence apart.
    groups = {}
    for item in observations:
        identity = (item.get("host_id") or socket.gethostname(), item.get("profile_id", ""), item.get("cwd", ""))
        groups.setdefault(identity, []).append(item)
    event_ids = []
    for identity, group in groups.items():
        event_ids.append(_record_fallback(root, session, turn, identity, group)["event_id"])
    cj._atomic_write(
        finalized,
        cj._json_bytes({"mode": "mechanical", "event_id": event_ids[-1], "event_ids": event_ids,
                       "semantic_event_ids": semantic_ids, "finalized_at": cj.utc_now()}),
        overwrite=False,
    )


def _record_fallback(root: Path, session: str, turn: str, identity: Tuple[str, str, str],
                     observations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    changed_paths: List[str] = []
    tool_names: List[str] = []
    result_states: List[str] = []
    host, profile, cwd = identity
    for item in observations:
        if item.get("tool_name") and item["tool_name"] not in tool_names:
            tool_names.append(item["tool_name"])
        if item.get("result"):
            result_states.append(item["result"])
        for path in item.get("changed_paths", []):
            if path not in changed_paths:
                changed_paths.append(path)

    verification = "failed" if "error" in result_states else "not_run"
    legacy_key = f"codex-stop:{session}:{turn}"
    with cj.journal_lock(root):
        legacy = cj._idempotent_event(root, hashlib.sha256(legacy_key.encode()).hexdigest())
        if (legacy and legacy.get("source") == "codex-hook"
                and (legacy.get("host_id", ""), legacy.get("profile_id", ""), legacy.get("cwd", "")) == identity
                and legacy.get("session_id") == session and legacy.get("turn_id") == turn):
            # A previous adapter may have saved its fact before Stop was finalized.
            return {"event_id": legacy["event_id"], "duplicate": True}
    group_key = hashlib.sha256(cj._json_bytes(identity)).hexdigest()[:16]
    return cj.record_event(
        root,
        actor="observer",
        source="codex-hook",
        phase="observed",
        summary=f"Codex Hook 观察到本轮 {len(observations)} 个可能改变状态的工具调用",
        why="机械兜底记录；这些观察尚未被本轮语义事件覆盖",
        cwd=cwd,
        changed_paths=changed_paths,
        verification_status=verification,
        verification_refs=[f"工具类别：{', '.join(tool_names[:8])}"],
        unresolved=["需要在后续语义记录中确认实际变化、验证状态和回滚方式"],
        tags=["codex", "hook", "mechanical-fallback"],
        host_id=host,
        profile_id=profile,
        session_id=session,
        turn_id=turn,
        idempotency_key=f"codex-stop:{session}:{turn}:{group_key}",
    )


def _record_safe_error(root: Path, mode: str, exc: BaseException) -> None:
    try:
        cj.init_root(root)
        target = root / ".spool" / "errors" / f"{cj.utc_now().replace(':', '')}-{uuid.uuid4().hex}.json"
        payload = {"occurred_at": cj.utc_now(), "mode": mode, "error_type": type(exc).__name__}
        cj._atomic_write(target, cj._json_bytes(payload), overwrite=False)
    except BaseException:
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("context", "observe", "stop"))
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    root = cj.root_from(args.root)
    try:
        payload = _read_payload()
        if args.mode == "context":
            capture_context(payload, root)
        elif args.mode == "observe":
            observe(payload, root)
        else:
            finalize(payload, root)
    except BaseException as exc:
        _record_safe_error(root, args.mode, exc)
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
