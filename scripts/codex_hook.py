#!/usr/bin/env python3
"""Privacy-preserving Codex PostToolUse/Stop adapter for change-journal."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
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
    turn = payload.get("turn_id")
    if not session or not turn:
        raise cj.JournalError("hook payload lacks session_id or turn_id")
    return cj.safe_id(str(session), "session_id"), cj.safe_id(str(turn), "turn_id")


def _turn_dir(root: Path, session: str, turn: str) -> Path:
    return root / ".spool" / "codex" / session / turn


def observe(payload: Dict[str, Any], root: Path) -> None:
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
        "occurred_at": cj.utc_now(),
        "session_id": session,
        "turn_id": turn,
        "cwd": cwd,
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


def _matching_semantic_marker(root: Path, session: str, observations: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not observations:
        return None
    earliest = min(_parse_utc(item["occurred_at"]) for item in observations)
    cwd_values = {item.get("cwd", "") for item in observations if item.get("cwd")}
    marker_dir = root / ".spool" / "semantic" / session
    for path in sorted(marker_dir.glob("*.json"), reverse=True):
        try:
            with path.open("r", encoding="utf-8") as handle:
                marker = json.load(handle)
            if _parse_utc(marker["recorded_at"]) < earliest:
                continue
            if cwd_values and marker.get("cwd") not in cwd_values:
                continue
            return marker
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
    return None


def finalize(payload: Dict[str, Any], root: Path) -> None:
    session, turn = _safe_hook_ids(payload)
    turn_dir = _turn_dir(root, session, turn)
    finalized = turn_dir / ".finalized.json"
    if finalized.exists():
        return
    observations = _load_observations(turn_dir)
    if not observations:
        return

    semantic = _matching_semantic_marker(root, session, observations)
    if semantic:
        cj._atomic_write(
            finalized,
            cj._json_bytes({"mode": "semantic", "event_id": semantic["event_id"], "finalized_at": cj.utc_now()}),
            overwrite=False,
        )
        return

    changed_paths: List[str] = []
    tool_names: List[str] = []
    result_states: List[str] = []
    cwd = ""
    for item in observations:
        cwd = cwd or item.get("cwd", "")
        if item.get("tool_name") and item["tool_name"] not in tool_names:
            tool_names.append(item["tool_name"])
        if item.get("result"):
            result_states.append(item["result"])
        for path in item.get("changed_paths", []):
            if path not in changed_paths:
                changed_paths.append(path)

    verification = "failed" if "error" in result_states else "not_run"
    result = cj.record_event(
        root,
        actor="observer",
        source="codex-hook",
        phase="observed",
        summary=f"Codex Hook 观察到本轮 {len(observations)} 个可能改变状态的工具调用",
        why="机械兜底记录；本轮没有匹配到更高质量的语义事件",
        cwd=cwd,
        changed_paths=changed_paths,
        verification_status=verification,
        verification_refs=[f"工具类别：{', '.join(tool_names[:8])}"],
        unresolved=["需要在后续语义记录中确认实际变化、验证状态和回滚方式"],
        tags=["codex", "hook", "mechanical-fallback"],
        session_id=session,
        turn_id=turn,
        idempotency_key=f"codex-stop:{session}:{turn}",
    )
    cj._atomic_write(
        finalized,
        cj._json_bytes({"mode": "mechanical", "event_id": result["event_id"], "finalized_at": cj.utc_now()}),
        overwrite=False,
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
    parser.add_argument("mode", choices=("observe", "stop"))
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    root = cj.root_from(args.root)
    try:
        payload = _read_payload()
        if args.mode == "observe":
            observe(payload, root)
        else:
            finalize(payload, root)
    except BaseException as exc:
        _record_safe_error(root, args.mode, exc)
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
