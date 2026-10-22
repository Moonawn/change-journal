#!/usr/bin/env python3
"""Portable, append-only change journal for Codex and Hermes adapters."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import socket
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path, PurePath
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


SCHEMA_VERSION = "1.2"
SUPPORTED_SCHEMA_VERSIONS = {"1.0", "1.1", "1.2"}
RECORD_LEVELS = {"brief", "full"}
KINDS = {"artifact", "code", "runtime", "policy", "checkpoint"}
DISPLAY_TIMEZONE = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
EMPTY_FOLLOW_UPS = {"无", "暂无", "无未决事项", "暂无未决事项", "无未完成事项", "无需后续", "none", "n/a"}
DEFAULT_ROOT = Path.home() / ".local" / "share" / "change-journal"
PHASES = {
    "observed",
    "applied",
    "verified",
    "active",
    "failed",
    "rolled_back",
    "needs_attention",
}
VERIFICATION_STATUSES = {"not_run", "passed", "failed", "partial"}
SENSITIVITIES = {"private", "internal", "public"}
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
        r"password|app[_ -]?secret|client[_ -]?secret|oauth[_ -]?token)\b"
        r"\s*[:=]\s*['\"]?[^\s,\]}]{4,}"
    ),
)


class JournalError(ValueError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def root_from(value: Optional[str] = None) -> Path:
    raw = value or os.environ.get("CHANGE_JOURNAL_ROOT")
    return Path(raw).expanduser() if raw else DEFAULT_ROOT


def _redact(value: str) -> Tuple[str, int]:
    count = 0
    result = value
    for pattern in SECRET_PATTERNS:
        result, found = pattern.subn("[REDACTED]", result)
        count += found
    return result, count


def safe_text(value: Optional[str], field: str, max_length: int = 1200) -> Tuple[str, int]:
    if value is None:
        return "", 0
    text = str(value).strip()
    if CONTROL_RE.search(text):
        raise JournalError(f"{field} contains control characters")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text, redactions = _redact(text)
    if len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
    return text, redactions


def safe_id(value: Optional[str], field: str, default: str = "") -> str:
    if not value:
        return default
    value = str(value).strip()
    if not SAFE_ID_RE.fullmatch(value):
        raise JournalError(f"{field} must match {SAFE_ID_RE.pattern}")
    return value


def safe_path(value: Optional[str], field: str = "path") -> str:
    if not value:
        return ""
    raw = str(value).strip()
    if CONTROL_RE.search(raw) or "\n" in raw or "\r" in raw:
        raise JournalError(f"{field} contains invalid characters")
    if any(pattern.search(raw) for pattern in SECRET_PATTERNS):
        raise JournalError(f"{field} appears to contain secret material")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise JournalError(f"{field} must be an absolute path")
    if ".." in PurePath(raw).parts:
        raise JournalError(f"{field} must not contain path traversal")
    return os.path.normpath(str(candidate))


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not overwrite and path.exists():
            temporary.unlink(missing_ok=True)
            raise JournalError(f"refusing to overwrite immutable file: {path}")
        os.replace(str(temporary), str(path))
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def journal_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".journal.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def default_registry(root: Optional[Path] = None) -> Dict[str, Any]:
    routes = [
        {
            "path": str((root or DEFAULT_ROOT).expanduser().absolute()),
            "scope_id": "CHANGE-JOURNAL",
        },
        {
            "path": str(Path(__file__).resolve().parents[1]),
            "scope_id": "CHANGE-JOURNAL",
        },
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "routing": "longest_path_prefix",
        "unknown_scope": "UNASSIGNED",
        "routes": sorted(routes, key=lambda item: len(item["path"]), reverse=True),
    }


def init_root(root: Path) -> Dict[str, Any]:
    with journal_lock(root):
        for relative in ("events", "views/by-scope", "views/daily", "idempotency", ".spool/semantic"):
            (root / relative).mkdir(parents=True, exist_ok=True)
        registry_path = root / "registry.json"
        created = False
        if not registry_path.exists():
            _atomic_write(registry_path, _json_bytes(default_registry(root)), overwrite=False)
            created = True
    return {"root": str(root), "registry_created": created}


def load_registry(root: Path) -> Dict[str, Any]:
    with (root / "registry.json").open("r", encoding="utf-8") as handle:
        registry = json.load(handle)
    if not isinstance(registry, dict) or not isinstance(registry.get("routes"), list):
        raise JournalError("registry routes must be a list")
    scopes_by_path = {}
    for item in registry["routes"]:
        if not isinstance(item, dict):
            raise JournalError("registry route must be an object")
        path = safe_path(item.get("path"), "registry path")
        scope = safe_id(item.get("scope_id"), "registry scope_id")
        if not path or not scope:
            raise JournalError("registry route needs path and scope_id")
        if path in scopes_by_path and scopes_by_path[path] != scope:
            raise JournalError(f"conflicting scopes for registry path: {path}")
        scopes_by_path[path] = scope
    return registry


def route_scope(root: Path, cwd: str = "", changed_paths: Sequence[str] = (), primary_path: str = "") -> str:
    registry = load_registry(root)
    candidates = [path for path in changed_paths if path]
    if primary_path:
        candidates = [safe_path(primary_path, "primary_path")]
    else:
        # Journal artifacts describe another change; they must not take its ownership.
        evidence_dirs = [root / part for part in ("备份_BACKUPS", "backups", "views", "events", ".spool", "idempotency")]
        targets = [p for p in candidates if not any(_within(p, str(d)) for d in evidence_dirs)]
        candidates = targets or candidates or ([cwd] if cwd else [])
    matches: List[Tuple[int, str]] = []
    for item in registry["routes"]:
        prefix = safe_path(item.get("path"), "registry path")
        scope_id = safe_id(item.get("scope_id"), "registry scope_id")
        for candidate in candidates:
            try:
                common = os.path.commonpath([prefix, candidate])
            except ValueError:
                continue
            if common == prefix:
                matches.append((len(prefix), scope_id))
    if not matches:
        return safe_id(registry.get("unknown_scope"), "unknown_scope", "UNASSIGNED")
    longest = max(length for length, _ in matches)
    scopes = {scope for length, scope in matches if length == longest}
    if len(scopes) != 1:
        raise JournalError("ambiguous target scopes; specify the primary_path or scope_id")
    return scopes.pop()


def _within(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


def load_work_items(root: Path) -> List[Dict[str, Any]]:
    """Optional curated classifications; never rewrite event evidence."""
    path = root / "work-items.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != "1.0" or not isinstance(data.get("items"), list):
        raise JournalError("work-items.json needs schema_version 1.0 and an items list")
    items, identities, assigned = [], set(), set()
    for raw in data["items"]:
        if not isinstance(raw, dict):
            raise JournalError("work item must be an object")
        item = dict(raw)
        for field in ("work_id", "scope_id", "host_id"):
            item[field] = safe_id(raw.get(field), field)
            if not item[field]:
                raise JournalError(f"work item needs {field}")
        if "profile_id" not in raw:
            raise JournalError("work item needs an explicit profile_id (empty is allowed)")
        item["profile_id"] = safe_id(raw["profile_id"], "profile_id")
        item["title"], _ = safe_text(raw.get("title"), "work title", 200)
        if not item["title"]:
            raise JournalError("work item needs a title")
        identity = tuple(item[k] for k in ("scope_id", "host_id", "profile_id", "work_id"))
        if identity in identities:
            raise JournalError("duplicate work item identity")
        identities.add(identity)
        if not isinstance(raw.get("event_ids", []), list) or not isinstance(raw.get("bindings", []), list):
            raise JournalError("work event_ids and bindings must be lists")
        item["event_ids"] = [safe_id(v, "work event_id") for v in raw.get("event_ids", [])]
        for event_id in item["event_ids"]:
            if not event_id or event_id in assigned:
                raise JournalError("event assigned to multiple work items")
            assigned.add(event_id)
        item["bindings"] = []
        for binding in raw.get("bindings", []):
            if not isinstance(binding, dict):
                raise JournalError("work binding must be an object")
            session = safe_id(binding.get("session_id"), "work session_id")
            target = safe_path(binding.get("path"), "work binding path")
            if not session or not target:
                raise JournalError("work binding needs both session_id and a specific target path")
            item["bindings"].append({"session_id": session, "path": target})
        items.append(item)
    return items


def _work_match(event: Dict[str, Any], items: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    explicit = [i for i in items if event.get("event_id") in i["event_ids"]]
    identity = lambda i: all(event.get(k, "") == i[k] for k in ("host_id", "profile_id"))
    if explicit:
        if not identity(explicit[0]):
            raise JournalError("work classification cannot change host or Profile identity")
        return explicit[0]
    # A caller's explicit work_id takes precedence over automatic bindings.
    if event.get("work_id"):
        return None
    paths = ([event["primary_path"]] if event.get("primary_path") else event.get("changed_paths", []))
    matches = [i for i in items if identity(i) and any(
        event.get("session_id") == b["session_id"] and paths
        and all(_within(p, b["path"]) for p in paths)
        for b in i["bindings"])]
    if len(matches) > 1:
        raise JournalError("ambiguous work bindings; use explicit event assignments or narrow the binding")
    return matches[0] if matches else None


def project_events(root: Path, events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Current classification is a reversible projection over immutable facts."""
    items = load_work_items(root)
    result = []
    for raw in events:
        event = dict(raw)
        match = _work_match(event, items)
        if match:
            event["recorded_scope_id"] = raw["scope_id"]
            event["scope_id"] = match["scope_id"]
            event["work_id"] = match["work_id"]
            event["work_title"] = match["title"]
            event["classification_source"] = "work-items.json"
        result.append(event)
    return result


def _work_identity(event: Dict[str, Any]) -> Tuple[str, ...]:
    return tuple(str(event.get(k, "")) for k in ("scope_id", "host_id", "profile_id")) + (
        str(event.get("work_id") or "event:" + event["event_id"]),)


def _event_order(event: Dict[str, Any]) -> Tuple[dt.datetime, dt.datetime, str]:
    occurred = dt.datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
    recorded = dt.datetime.fromisoformat((event.get("recorded_at") or event["occurred_at"]).replace("Z", "+00:00"))
    return occurred, recorded, event["event_id"]


def _assign_work(root: Path, event: Dict[str, Any], work_id: str, title: str) -> None:
    items = load_work_items(root)
    prior = project_events(root, load_events(root))
    by_id = {e["event_id"]: e for e in prior}
    eligible = [by_id[v] for v in event["resolves_event_ids"] + event["supersedes_event_ids"]
                if v in by_id and all(by_id[v].get(k, "") == event.get(k, "")
                for k in ("scope_id", "host_id", "profile_id")) and by_id[v].get("work_id")]
    if not work_id and not title:
        candidates = {e["work_id"] for e in eligible}
        if len(candidates) == 1:
            latest = max(eligible, key=_event_order)
            work_id = latest["work_id"]
            title = latest.get("work_title", "")
        elif len(candidates) > 1:
            event["quality_notes"].append("关联了多个事项的剩余动作；本事件未自动并入任何事项")
            return
        else:
            match = _work_match(event, items)
            if match and match["scope_id"] == event["scope_id"]:
                work_id, title = match["work_id"], match["title"]
    if title and not work_id:
        work_id = "work-" + event["event_id"]
    if work_id and not title:
        known = [e for e in prior if e.get("work_id") == work_id and all(
            e.get(k, "") == event.get(k, "") for k in ("scope_id", "host_id", "profile_id"))]
        if known:
            title = max(known, key=_event_order).get("work_title", "")
        else:
            definitions = [i for i in items if i["work_id"] == work_id and all(
                i[k] == event.get(k, "") for k in ("scope_id", "host_id", "profile_id"))]
            title = definitions[0]["title"] if definitions else ""
    event["work_id"] = work_id
    event["work_title"] = title or (event["summary"] if work_id else "")


def _event_id(occurred_at: str) -> str:
    compact = re.sub(r"[^0-9]", "", occurred_at)[:14]
    return f"{compact}-{uuid.uuid4().hex[:12]}"


def _event_file(root: Path, event: Dict[str, Any]) -> Path:
    parsed = dt.datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
    return root / "events" / f"{parsed.year:04d}" / f"{parsed.month:02d}" / f"{event['event_id']}.json"


def _find_event(root: Path, event_id: str) -> Optional[Path]:
    found = list((root / "events").glob(f"*/*/{event_id}.json"))
    return found[0] if found else None


def _idempotent_event(root: Path, key_hash: str) -> Optional[Dict[str, Any]]:
    """Called under the journal lock; indexes are hints, event facts are authoritative."""
    ref_path = root / "idempotency" / f"{key_hash}.json"
    if ref_path.exists():
        try:
            ref = json.loads(ref_path.read_text(encoding="utf-8"))
            event_id = safe_id(ref["event_id"], "idempotency event_id")
            path = _find_event(root, event_id) if event_id else None
            if path:
                event = json.loads(path.read_text(encoding="utf-8"))
                if (not validate_event(event) and event.get("idempotency_key_hash") == key_hash
                        and event.get("event_id") == event_id):
                    return dict(event, _path=str(path))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass

    # The process may have stopped after saving the fact but before its index.
    matches = [e for e in load_events(root) if e.get("idempotency_key_hash") == key_hash]
    if len(matches) > 1:
        raise JournalError("multiple facts share this idempotency key; inspect doctor before retrying")
    if not matches:
        if ref_path.exists():
            raise JournalError("idempotency reference has no matching fact; inspect doctor before retrying")
        return None
    event = matches[0]
    _atomic_write(ref_path, _json_bytes({"event_id": event["event_id"]}))
    return event


def _validate_lifecycle_targets(root: Path, event: Dict[str, Any]) -> None:
    targets = set(event["resolves_event_ids"] + event["supersedes_event_ids"])
    if not targets:
        return
    old_events = []
    for event_id in targets:
        path = _find_event(root, event_id)
        if path is None:
            raise JournalError(f"lifecycle target does not exist: {event_id}")
        old_events.append(json.loads(path.read_text(encoding="utf-8")))
    # Explicit historical reclassification may correct scope, never host/Profile.
    projected = project_events(root, [event, *old_events])
    for old in projected[1:]:
        for field in ("scope_id", "host_id", "profile_id"):
            if old.get(field, "") != projected[0].get(field, ""):
                raise JournalError(f"cannot close follow-ups across {field}: {old['event_id']}")


def _list_text(values: Optional[Sequence[str]], field: str, max_items: int = 32) -> Tuple[List[str], int]:
    result: List[str] = []
    redactions = 0
    for raw in list(values or [])[:max_items]:
        value, found = safe_text(raw, field)
        redactions += found
        if value and value not in result:
            result.append(value)
    return result, redactions


def meaningful_follow_ups(values: Sequence[str]) -> List[str]:
    # Only exact empty markers: "无法读取" and "无；还有其他事项" remain evidence.
    return [v for v in values if str(v).strip() and str(v).strip().rstrip("。.!！").lower() not in EMPTY_FOLLOW_UPS]


def _semantic_marker(root: Path, event: Dict[str, Any]) -> None:
    session_id = event.get("session_id")
    if event.get("source") != "semantic" or not session_id:
        return
    marker = {
        "event_id": event["event_id"],
        "recorded_at": event["recorded_at"],
        "cwd": event.get("cwd", ""),
        "scope_id": event["scope_id"],
        "session_id": session_id,
        "turn_id": event.get("turn_id", ""),
        "host_id": event.get("host_id", ""),
        "profile_id": event.get("profile_id", ""),
        "changed_paths": event.get("changed_paths", []),
    }
    marker_path = root / ".spool" / "semantic" / session_id / f"{event['event_id']}.json"
    payload = _json_bytes(marker)
    if not marker_path.exists() or marker_path.read_bytes() != payload:
        _atomic_write(marker_path, payload)


def _hook_turn_id(root: Path, session: str, host: str, profile: str) -> str:
    """Resolve this exact shell call, never a session's most recent turn."""
    try:
        tool_id = safe_id(os.environ.get("CODEX_TOOL_CALL_ID"), "tool_use_id")
        if not session or not tool_id:
            return ""
        path = root / ".spool" / "codex-context" / session / f"{tool_id}.json"
        context = json.loads(path.read_text(encoding="utf-8"))
        if any(context.get(k, "") != v for k, v in (
                ("session_id", session), ("tool_use_id", tool_id),
                ("host_id", host), ("profile_id", profile))):
            return ""
        return safe_id(context.get("turn_id"), "turn_id")
    except (OSError, ValueError, TypeError, AttributeError):
        return ""


def record_event(
    root: Path,
    *,
    actor: str,
    source: str,
    phase: str,
    summary: str,
    why: str = "",
    cwd: str = "",
    changed_paths: Sequence[str] = (),
    verification_status: str = "not_run",
    verification_refs: Sequence[str] = (),
    rollback_ref: str = "",
    unresolved: Sequence[str] = (),
    related_event_ids: Sequence[str] = (),
    tags: Sequence[str] = (),
    sensitivity: str = "private",
    scope_id: str = "",
    host_id: str = "",
    profile_id: str = "",
    session_id: str = "",
    turn_id: str = "",
    occurred_at: str = "",
    idempotency_key: str = "",
    record_level: str = "brief",
    kind: str = "",
    primary_path: str = "",
    follow_ups: Sequence[str] = (),
    limitations: Sequence[str] = (),
    resolves_event_ids: Sequence[str] = (),
    supersedes_event_ids: Sequence[str] = (),
    lesson_key: str = "",
    lesson_trigger: str = "",
    lesson_check: str = "",
    lesson_status: str = "active",
    work_id: str = "",
    work_title: str = "",
) -> Dict[str, Any]:
    init_root(root)
    actor = safe_id(actor, "actor")
    source = safe_id(source, "source")
    if phase not in PHASES:
        raise JournalError(f"unsupported phase: {phase}")
    if verification_status not in VERIFICATION_STATUSES:
        raise JournalError(f"unsupported verification status: {verification_status}")
    if sensitivity not in SENSITIVITIES:
        raise JournalError(f"unsupported sensitivity: {sensitivity}")
    if record_level not in RECORD_LEVELS or (kind and kind not in KINDS):
        raise JournalError("invalid record_level or kind")
    work_id = safe_id(work_id, "work_id")
    work_title, work_redactions = safe_text(work_title, "work_title", 200)
    quality_notes = []
    if phase in {"verified", "active"} and verification_status != "passed":
        quality_notes.append("阶段与验证结果不一致；保留原始陈述，不视为完整验证通过")

    safe_summary, redactions = safe_text(summary, "summary", 500)
    redactions += work_redactions
    if not safe_summary:
        raise JournalError("summary is required")
    safe_why, found = safe_text(why, "why", 1000)
    redactions += found
    safe_cwd = safe_path(cwd, "cwd") if cwd else ""
    safe_paths = []
    for value in list(changed_paths)[:64]:
        path = safe_path(value, "changed_path")
        if path and path not in safe_paths:
            safe_paths.append(path)
    refs, found = _list_text(verification_refs, "verification_ref")
    redactions += found
    pending, found = _list_text(unresolved, "unresolved")
    redactions += found
    pending = meaningful_follow_ups(pending)
    followups, found = _list_text(follow_ups, "follow_up")
    redactions += found
    followups = meaningful_follow_ups(followups)
    limits, found = _list_text(limitations, "limitation")
    redactions += found
    target_path = safe_path(primary_path, "primary_path") if primary_path else ""
    resolves = [safe_id(v, "resolves_event_id") for v in list(resolves_event_ids)[:32]]
    supersedes = [safe_id(v, "supersedes_event_id") for v in list(supersedes_event_ids)[:32]]
    lesson = {}
    if lesson_key or lesson_trigger or lesson_check:
        key = safe_id(lesson_key, "lesson_key")
        trigger, found = safe_text(lesson_trigger, "lesson_trigger", 600)
        redactions += found
        check, found = safe_text(lesson_check, "lesson_check", 800)
        redactions += found
        if not key or not trigger or not check or lesson_status not in {"active", "retired"}:
            raise JournalError("a lesson needs a stable key, trigger, check and active/retired status")
        if verification_status != "passed" or not refs:
            raise JournalError("a lesson needs checked evidence; do not promote an unverified hypothesis")
        lesson = {"key": key, "trigger": trigger, "check": check, "status": lesson_status}
    safe_tags, found = _list_text(tags, "tag", max_items=24)
    redactions += found
    safe_rollback, found = safe_text(rollback_ref, "rollback_ref", 800)
    redactions += found
    related = [safe_id(value, "related_event_id") for value in list(related_event_ids)[:32]]

    timestamp = occurred_at or utc_now()
    try:
        parsed_time = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JournalError("occurred_at must be ISO-8601") from exc
    if parsed_time.tzinfo is None:
        raise JournalError("occurred_at must include timezone")
    timestamp = parsed_time.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    effective_session = session_id or os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID") or ""
    effective_session = safe_id(effective_session, "session_id") if effective_session else ""
    effective_turn = safe_id(turn_id or os.environ.get("CODEX_TURN_ID"), "turn_id")
    effective_host = safe_id(host_id or socket.gethostname(), "host_id")
    effective_profile = safe_id(profile_id, "profile_id") if profile_id else ""
    if source == "semantic" and not effective_turn:
        effective_turn = _hook_turn_id(root, effective_session, effective_host, effective_profile)
    if scope_id:
        effective_scope = safe_id(scope_id, "scope_id")
    else:
        try:
            effective_scope = route_scope(root, safe_cwd, safe_paths, target_path)
        except (JournalError, OSError, ValueError) as exc:
            effective_scope = "UNASSIGNED"
            quality_notes.append(f"对象路由不可用（{type(exc).__name__}）；已保留事实，scope 为 UNASSIGNED。检查 registry.json 或明确主目标")

    key_hash = ""
    legacy_key_hash = ""
    if idempotency_key:
        clean_key, _ = safe_text(idempotency_key, "idempotency_key", 300)
        if not clean_key:
            raise JournalError("idempotency_key must not be blank")
        # Hash the full key; display redaction/truncation must not merge operations.
        key_hash = hashlib.sha256(idempotency_key.strip().encode("utf-8")).hexdigest()
        legacy_key_hash = hashlib.sha256(clean_key.encode("utf-8")).hexdigest()

    with journal_lock(root):
        if key_hash:
            existing = _idempotent_event(root, key_hash)
            if existing:
                _semantic_marker(root, existing)
                return {
                    "event_id": existing["event_id"],
                    "event_path": existing["_path"],
                    "duplicate": True,
                    "work_id": existing.get("work_id", ""),
                    "work_title": existing.get("work_title", ""),
                    "warnings": existing.get("quality_notes", []),
                }
            if legacy_key_hash != key_hash and _idempotent_event(root, legacy_key_hash):
                raise JournalError("legacy idempotency key was transformed; inspect the existing fact before choosing a new key")

        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": _event_id(timestamp),
            "occurred_at": timestamp,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "actor": actor,
            "source": source,
            "host_id": effective_host,
            "profile_id": effective_profile,
            "session_id": effective_session,
            "turn_id": effective_turn,
            "cwd": safe_cwd,
            "scope_id": effective_scope,
            "phase": phase,
            "summary": safe_summary,
            "why": safe_why,
            "changed_paths": safe_paths,
            "verification": {"status": verification_status, "refs": refs},
            "rollback_ref": safe_rollback,
            "unresolved": pending,
            "related_event_ids": related,
            "tags": safe_tags,
            "sensitivity": sensitivity,
            "redaction_count": redactions,
            "idempotency_key_hash": key_hash,
            "record_level": record_level,
            "kind": kind,
            "primary_path": target_path,
            "follow_ups": followups,
            "limitations": limits,
            "resolves_event_ids": resolves,
            "supersedes_event_ids": supersedes,
            "lesson": lesson,
            "quality_notes": quality_notes,
            "work_id": "",
            "work_title": "",
        }
        try:
            _assign_work(root, event, work_id, work_title)
        except (JournalError, OSError, json.JSONDecodeError) as exc:
            # Classification is optional: a broken index must not erase the fact.
            event["work_id"] = work_id or ("work-" + event["event_id"] if work_title else "")
            event["work_title"] = work_title or (safe_summary if work_id else "")
            quality_notes.append(f"事项索引不可用（{type(exc).__name__}）；已保留事件和显式事项信息，未猜测归类。修复索引后重建视图")
        _validate_lifecycle_targets(root, event)
        event_path = _event_file(root, event)
        _atomic_write(event_path, _json_bytes(event), overwrite=False)
        if key_hash:
            _atomic_write(
                root / "idempotency" / f"{key_hash}.json",
                _json_bytes({"event_id": event["event_id"]}),
                overwrite=False,
            )
        _semantic_marker(root, event)
        return {"event_id": event["event_id"], "event_path": str(event_path), "duplicate": False,
                "work_id": event["work_id"], "work_title": event["work_title"], "warnings": quality_notes}


def load_events(root: Path, *, errors: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for path in sorted((root / "events").glob("*/*/*.json")):
        if path.name.startswith(".") or path.name.endswith(".tmp"):
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                event = json.load(handle)
            issues = validate_event(event)
            if issues:
                raise JournalError("; ".join(issues))
        except (OSError, ValueError) as exc:
            if errors is None:
                raise JournalError(f"cannot read event {path}: {exc}") from exc
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        event["_path"] = str(path)
        events.append(event)
    return events


def _md(value: Any) -> str:
    text = str(value or "").replace("|", "\\|")
    return re.sub(r"[\r\n]+", " ", text)


def _scope_slug(scope_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", scope_id).strip("-") or "UNASSIGNED"


def _event_link(root: Path, view_path: Path, event: Dict[str, Any]) -> str:
    target = Path(event["_path"])
    return os.path.relpath(target, view_path.parent)


def _write_view(path: Path, text: str) -> None:
    payload = text.encode("utf-8")
    if path.exists() and path.read_bytes() == payload:
        return
    _atomic_write(path, payload)


def _timeline(root: Path, view_path: Path, events: Sequence[Dict[str, Any]], title: str) -> str:
    ordered = sorted(events, key=_event_order, reverse=True)
    derived_at = max((event.get("recorded_at", "") for event in ordered), default="无事件")
    lines = [
        f"# {title}",
        "",
        f"> 由 append-only 事件生成；派生基准：{derived_at}。Scope 显示当前归类，原始归属保留在事件中。",
        "",
        "| 时间 | Scope | Phase | Actor | 变化 | 验证 | 事件 |",
        "|---|---|---|---|---|---|---|",
    ]
    for event in ordered:
        verification = event.get("verification", {}).get("status", "not_run")
        link = _event_link(root, view_path, event)
        lines.append(
            "| {time} | {scope} | {phase} | {actor} | {summary} | {verification} | [{event_id}]({link}) |".format(
                time=_md(event.get("occurred_at")),
                scope=_md(event.get("scope_id")),
                phase=_md(event.get("phase")),
                actor=_md(event.get("actor")),
                summary=_md(event.get("summary")),
                verification=_md(verification),
                event_id=_md(event.get("event_id")),
                link=link,
            )
        )
    if not ordered:
        lines.append("| - | - | - | - | 暂无事件 | - | - |")
    return "\n".join(lines) + "\n"


def closed_continuation_ids(events: Sequence[Dict[str, Any]]) -> set:
    closed = set()
    for event in events:
        closed.update(event.get("resolves_event_ids", []))
        closed.update(event.get("supersedes_event_ids", []))
        # Preserve the historical 1.0 interpretation without applying it to new links.
        if event.get("schema_version") == "1.0":
            closed.update(event.get("related_event_ids", []))
    return closed


def open_continuation_events(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Explicit actionable follow-ups only; phase and caveats do not create work."""
    closed = closed_continuation_ids(events)
    return [
        event for event in events
        if event.get("event_id") not in closed and meaningful_follow_ups(event.get("follow_ups", []))
    ]


def legacy_continuation_events(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ambiguous legacy notes stay discoverable; exclusion never means completed."""
    closed = closed_continuation_ids(events)
    return [
        e for e in events if e.get("event_id") not in closed
        and (meaningful_follow_ups(e.get("unresolved", []))
             or (e.get("schema_version") == "1.0" and e.get("phase") in {"failed", "needs_attention"}))
    ]


def current_lessons(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest = {}
    for e in sorted(events, key=_event_order):
        lesson = e.get("lesson") or {}
        if lesson.get("key"):
            key = (e.get("scope_id"), e.get("host_id"), e.get("profile_id"), lesson["key"])
            latest[key] = e
    return [e for e in latest.values() if e["lesson"].get("status", "active") == "active"]


def local_day(timestamp: str) -> str:
    return dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(DISPLAY_TIMEZONE).date().isoformat()


def work_cards(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pending_ids = {e["event_id"] for e in open_continuation_events(events)}
    legacy_ids = {e["event_id"] for e in legacy_continuation_events(events)}
    grouped = defaultdict(list)
    for event in events:
        grouped[_work_identity(event)].append(event)
    cards = []
    for identity, group in grouped.items():
        group = sorted(group, key=_event_order)
        latest = group[-1]
        pending = [{"event_id": e["event_id"], "action": action} for e in group
                   if e["event_id"] in pending_ids for action in meaningful_follow_ups(e.get("follow_ups", []))]
        card = {k: latest.get(k, "") for k in ("scope_id", "host_id", "profile_id", "work_id")}
        card.update(title=latest.get("work_title") or latest["summary"],
                    latest_event_id=latest["event_id"], latest_summary=latest["summary"],
                    latest_at=latest["occurred_at"], latest_phase=latest["phase"],
                    latest_verification=latest.get("verification", {}).get("status", "not_run"),
                    event_count=len(group), event_ids=[e["event_id"] for e in group],
                    follow_ups=pending, legacy_count=sum(e["event_id"] in legacy_ids for e in group),
                    limitations=latest.get("limitations", []), quality_notes=latest.get("quality_notes", []),
                    _events=group)
        if card["work_id"]:
            digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:12]
            card["view_name"] = f"{_scope_slug(card['work_id'])}-{digest}.md"
        else:
            card["view_name"] = ""
        cards.append(card)
    return sorted(cards, key=lambda c: _event_order(c["_events"][-1]), reverse=True)


def _card_link(root: Path, view: Path, card: Dict[str, Any]) -> str:
    if card["view_name"]:
        return os.path.relpath(root / "views" / "by-work" / card["view_name"], view.parent)
    return _event_link(root, view, card["_events"][-1])


def _card_lines(root: Path, view: Path, cards: Sequence[Dict[str, Any]]) -> List[str]:
    lines = []
    for card in cards:
        identity = " / ".join(card[k] for k in ("scope_id", "host_id", "profile_id") if card[k])
        stamp = dt.datetime.fromisoformat(card["latest_at"].replace("Z", "+00:00")).astimezone(DISPLAY_TIMEZONE).strftime("%m-%d %H:%M")
        lines.append(f"- **[{_md(card['title'])}]({_card_link(root, view, card)})** · {_md(identity)} · {card['event_count']} 条记录")
        summary = f"{_md(card['latest_summary'])}；" if card['title'] != card['latest_summary'] else ""
        lines.append(f"  最近记录 {stamp}：{summary}{card['latest_phase']} / {card['latest_verification']}")
        if card["follow_ups"]:
            count = len(card["follow_ups"])
            lines.append(f"  下一步：{_md(card['follow_ups'][0]['action'])}" + (f"（共 {count} 项，展开查看）" if count > 1 else ""))
    return lines or ["暂无记录。"]


def render_work_views(root: Path, events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cards = work_cards(events)
    named = [c for c in cards if c["work_id"]]
    for card in named:
        path = root / "views" / "by-work" / card["view_name"]
        latest = card["_events"][-1]
        lines = [f"# {_md(card['title'])}", "", "由事件与归类索引生成；最新记录不代表已经重新探测运行现状。", "",
                 f"对象：{card['scope_id']} · 主机：{card['host_id']} · Profile：{card['profile_id'] or '未指定'}",
                 f"事项 ID：`{card['work_id']}` · 关联事件：{card['event_count']} 条", "",
                 "## 最近进度", "", f"{_md(card['latest_summary'])} · [原事件]({_event_link(root, path, latest)})",
                 f"记录时间：{card['latest_at']} · 阶段：{card['latest_phase']} · 验证：{card['latest_verification']}", "",
                 "## 明确剩余动作", ""]
        by_id = {e["event_id"]: e for e in card["_events"]}
        lines += [f"- {_md(a['action'])} · [来源]({_event_link(root, path, by_id[a['event_id']])})" for a in card["follow_ups"]] or ["未登记明确剩余动作；不据此判定整个事项完成。"]
        if card["limitations"]:
            lines += ["", "最近记录的限制说明：", *[f"- {_md(v)}" for v in card["limitations"]]]
        if card["quality_notes"]:
            lines += ["", "记录质量提示：", *[f"- {_md(v)}" for v in card["quality_notes"]]]
        lines += ["", f"历史待核 {card['legacy_count']} 条；原记录的限制和旧备注仍可在下面追溯。", "",
                  _timeline(root, path, card["_events"], "关联事件与证据").replace("# 关联事件与证据", "## 关联事件与证据", 1)]
        _write_view(path, "\n".join(lines) + "\n")
    index = root / "views" / "02_事项索引_WORK_ITEMS.md"
    lines = ["# 事项索引", "", f"已归组事项 {len(named)} 项；另有 {len(cards)-len(named)} 条独立记录，保留在日期、scope 和全局时间线。", "",
             "同一事项的事件合并展示；待办只由明确解决或接手关系关闭。", "", *_card_lines(root, index, named)]
    _write_view(index, "\n".join(lines) + "\n")
    # A corrected classification must not leave an obsolete card looking current.
    live_names = {c["view_name"] for c in named}
    for old in (root / "views" / "by-work").glob("*.md"):
        if old.name not in live_names:
            _write_view(old, "# 事项归类已更新\n\n此派生页已停用；请从[当前事项索引](../02_事项索引_WORK_ITEMS.md)重新查找。原始事件仍保留。\n")
    return cards


def _followup_lines(root: Path, view: Path, events: Sequence[Dict[str, Any]]) -> List[str]:
    lines = []
    for e in sorted(events, key=_event_order, reverse=True):
        identity = " / ".join(str(e.get(k)) for k in ("scope_id", "host_id", "profile_id") if e.get(k))
        for item in meaningful_follow_ups(e.get("follow_ups", [])):
            lines.append(f"- **{_md(identity)}**：{_md(item)} · [来源]({_event_link(root, view, e)})")
    return lines or ["没有明确登记的待续接事项；这不代表历史事项已经完成。"]


def _lesson_lines(root: Path, view: Path, events: Sequence[Dict[str, Any]]) -> List[str]:
    lines = []
    for e in sorted(events, key=_event_order, reverse=True):
        lesson = e["lesson"]
        identity = " / ".join(str(e.get(k)) for k in ("scope_id", "host_id", "profile_id") if e.get(k))
        lines.extend([f"- **{_md(identity)}** · {_md(lesson['trigger'])}",
                      f"  检查：{_md(lesson['check'])} · [证据]({_event_link(root, view, e)})"])
    return lines or ["尚无经过核实、明确登记的经验。已有日志不会自动升级成规则。"]


def render_review_views(root: Path, events: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    views = root / "views"
    pending = open_continuation_events(events)
    legacy = legacy_continuation_events(events)
    lessons = current_lessons(events)
    continuation = views / "00_待续接_CONTINUATIONS.md"
    legacy_path = views / "99_历史未决待核_LEGACY.md"
    lessons_path = views / "01_可复用经验_LESSONS.md"
    review = views / "00_审阅入口_REVIEW.md"
    cards = render_work_views(root, events)
    _write_view(legacy_path, _timeline(root, legacy_path, legacy,
        "历史未决待核：保留原文引用，未认定完成，也未自动加入任务队列"))
    lines = ["# 待续接", "", f"明确待续接 {len(pending)} 条事件；历史待核 {len(legacy)} 条事件。", "",
             *_followup_lines(root, continuation, pending), "",
             f"[历史未决记录]({legacy_path.name})保留限制说明与待核线索；核实后才登记 follow-up。"]
    attention = sorted([e for e in legacy if e.get("phase") in {"failed", "needs_attention"}],
                       key=_event_order, reverse=True)
    if attention:
        lines.extend(["", f"## 历史需关注（共 {len(attention)} 条，未核实现状，显示最近 3 条）", ""])
        for e in attention[:3]:
            lines.append(f"- {_md(e['summary'])} · [原记录]({_event_link(root, continuation, e)})")
    _write_view(continuation, "\n".join(lines) + "\n")
    lines = ["# 可复用经验", "", "按适用范围检索；一次事件不自动成为永久约束。同一范围与 key 的新记录替代旧版本。", "",
             *_lesson_lines(root, lessons_path, lessons)]
    _write_view(lessons_path, "\n".join(lines) + "\n")
    lines = ["# 变更审阅入口", "", f"事实记录 {len(events)} 条 · 明确待续接 {len(pending)} 条 · 可复用经验 {len(lessons)} 条 · 历史待核 {len(legacy)} 条", "",
             "按事项汇总展示最近 8 项；未归组事件独立显示。最新记录不等于事项完成，历史待核不等于当前任务队列。", "",
             "## 近期事项", "", *_card_lines(root, review, cards[:8])]
    lines.extend(["", "## 明确待续接", "", *_followup_lines(root, review, pending)[:8], "",
                  f"[全部明确待续接]({continuation.name}) · [历史待核]({legacy_path.name})", "",
                  "## 按需复用", "", f"[全部已归组事项](02_事项索引_WORK_ITEMS.md) · [有证据的经验]({lessons_path.name}) · [完整时间线](00_全局变更时间线_CHANGELOG.md)", "",
                  "可以用 query --path 查对象，或 query --work-id 续接事项；关联依据展开查看。"])
    _write_view(review, "\n".join(lines) + "\n")
    return {"review_view": str(review), "continuation_view": str(continuation), "legacy_pending": len(legacy), "pending": len(pending), "lessons": len(lessons),
            "work_items": sum(bool(c["work_id"]) for c in cards), "review_items": min(len(cards), 8)}


def render_views(root: Path) -> Dict[str, Any]:
    with journal_lock(root):
        events = project_events(root, load_events(root))
        global_path = root / "views" / "00_全局变更时间线_CHANGELOG.md"
        _write_view(global_path, _timeline(root, global_path, events, "全局变更时间线"))

        by_scope: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for event in events:
            by_scope[event.get("scope_id", "UNASSIGNED")].append(event)
            by_day[local_day(event["occurred_at"])].append(event)

        for scope_id, grouped in by_scope.items():
            path = root / "views" / "by-scope" / f"{_scope_slug(scope_id)}.md"
            _write_view(path, _timeline(root, path, grouped, f"Scope: {scope_id}"))
        current_scope_files = {f"{_scope_slug(scope)}.md" for scope in by_scope}
        for old in (root / "views" / "by-scope").glob("*.md"):
            if old.name not in current_scope_files:
                _write_view(old, "# 对象归类已更新\n\n此派生页已停用；请从[当前事项索引](../02_事项索引_WORK_ITEMS.md)或[完整时间线](../00_全局变更时间线_CHANGELOG.md)重新查找。\n")
        for day, grouped in by_day.items():
            path = root / "views" / "daily" / f"{day}.md"
            _write_view(path, _timeline(root, path, grouped, f"Daily: {day}"))

        review_result = render_review_views(root, events)
        return {
            "events": len(events),
            "scopes": len(by_scope),
            "days": len(by_day),
            "global_view": str(global_path),
            **review_result,
        }


def validate_event(event: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if not isinstance(event, dict):
        return ["event must be an object"]
    required = {
        "schema_version",
        "event_id",
        "occurred_at",
        "recorded_at",
        "actor",
        "source",
        "scope_id",
        "phase",
        "summary",
        "verification",
    }
    missing = sorted(required - set(event))
    if missing:
        issues.append(f"missing fields: {', '.join(missing)}")
    if not isinstance(event.get("schema_version"), str) or event["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
        issues.append("unsupported schema_version")
    if not isinstance(event.get("phase"), str) or event["phase"] not in PHASES:
        issues.append("invalid phase")
    if not isinstance(event.get("event_id"), str) or not SAFE_ID_RE.fullmatch(event["event_id"]):
        issues.append("invalid event_id")
    for field in ("scope_id", "host_id", "profile_id", "actor", "source", "session_id", "turn_id", "work_id", "work_title", "summary", "primary_path", "cwd"):
        if not isinstance(event.get(field, ""), str):
            issues.append(f"invalid {field}")
    for field in ("occurred_at", "recorded_at"):
        try:
            parsed = dt.datetime.fromisoformat(event[field].replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("missing timezone")
        except (KeyError, ValueError, TypeError, AttributeError):
            issues.append(f"invalid {field}")
    for field in ("changed_paths", "follow_ups", "unresolved", "related_event_ids", "resolves_event_ids", "supersedes_event_ids", "limitations", "quality_notes"):
        value = event.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            issues.append(f"invalid {field}")
    lesson = event.get("lesson", {})
    if not isinstance(lesson, dict) or (lesson and any(not isinstance(lesson.get(k), str) for k in ("key", "trigger", "check", "status"))):
        issues.append("invalid lesson")
    key_hash = event.get("idempotency_key_hash", "")
    if not isinstance(key_hash, str) or (key_hash and not re.fullmatch(r"[0-9a-f]{64}", key_hash)):
        issues.append("invalid idempotency_key_hash")
    if event.get("work_id") and not SAFE_ID_RE.fullmatch(str(event["work_id"])):
        issues.append("invalid work_id")
    verification = event.get("verification", {})
    if (not isinstance(verification, dict) or not isinstance(verification.get("status"), str)
            or verification["status"] not in VERIFICATION_STATUSES):
        issues.append("invalid verification status")
    serialized = json.dumps(event, ensure_ascii=False)
    if any(pattern.search(serialized) for pattern in SECRET_PATTERNS):
        issues.append("possible secret material")
    return issues


def doctor(root: Path) -> Dict[str, Any]:
    issues: List[str] = []
    try:
        registry = load_registry(root)
        for item in registry.get("routes", []):
            safe_path(item.get("path"), "registry path")
            safe_id(item.get("scope_id"), "registry scope")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"registry: {type(exc).__name__}: {exc}")

    ids = set()
    by_id = {}
    by_key = defaultdict(list)
    event_count = 0
    for path in sorted((root / "events").glob("*/*/*.json")):
        event_count += 1
        try:
            with path.open("r", encoding="utf-8") as handle:
                event = json.load(handle)
            for issue in validate_event(event):
                issues.append(f"{path}: {issue}")
            if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
                continue
            event_id = event.get("event_id")
            if event_id in ids:
                issues.append(f"duplicate event_id: {event_id}")
            ids.add(event_id)
            by_id[event_id] = event
            if isinstance(event.get("idempotency_key_hash"), str) and event["idempotency_key_hash"]:
                by_key[event["idempotency_key_hash"]].append(event_id)
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"{path}: {type(exc).__name__}: {exc}")

    for ref_path in sorted((root / "idempotency").glob("*.json")):
        try:
            with ref_path.open("r", encoding="utf-8") as handle:
                ref = json.load(handle)
            event_id = safe_id(ref.get("event_id"), "idempotency event_id")
            if not event_id or event_id not in by_id:
                issues.append(f"{ref_path}: event not found: {event_id}")
            elif by_id[event_id].get("idempotency_key_hash") != ref_path.stem:
                issues.append(f"{ref_path}: idempotency key does not match event: {event_id}")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            issues.append(f"{ref_path}: {type(exc).__name__}: {exc}")

    for key_hash, event_ids in by_key.items():
        if len(event_ids) > 1:
            issues.append(f"duplicate idempotency key {key_hash}: {', '.join(event_ids)}")
        if not (root / "idempotency" / f"{key_hash}.json").exists():
            issues.append(f"missing idempotency index: {key_hash}")

    try:
        for item in load_work_items(root):
            for event_id in item["event_ids"]:
                if event_id not in ids:
                    issues.append(f"work classification event not found: {event_id}")
        project_events(root, load_events(root))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        issues.append(f"work-items: {type(exc).__name__}: {exc}")
    return {"ok": not issues, "root": str(root), "events": event_count, "issues": issues}


def status(root: Path) -> Dict[str, Any]:
    events = project_events(root, load_events(root))
    return {
        "root": str(root),
        "events": len(events),
        "by_phase": dict(Counter(event.get("phase", "unknown") for event in events)),
        "by_scope": dict(Counter(event.get("scope_id", "UNASSIGNED") for event in events)),
        "pending": len(open_continuation_events(events)),
        "legacy_pending": len(legacy_continuation_events(events)),
        "lessons": len(current_lessons(events)),
        "work_items": sum(bool(c["work_id"]) for c in work_cards(events)),
    }


def query_events(root: Path, path: str = "", scope_id: str = "", profile_id: str = "", limit: int = 5,
                 work_id: str = "", host_id: str = "") -> Dict[str, Any]:
    if not path and not scope_id and not work_id:
        raise JournalError("query requires --path, --scope-id or --work-id")
    target = safe_path(path) if path else ""
    scope = safe_id(scope_id, "scope_id") if scope_id else ""
    profile = safe_id(profile_id, "profile_id") if profile_id else ""
    work = safe_id(work_id, "work_id") if work_id else ""
    host = safe_id(host_id, "host_id") if host_id else ""
    if not 1 <= limit <= 50:
        raise JournalError("limit must be between 1 and 50")
    read_errors: List[Dict[str, str]] = []
    events = project_events(root, load_events(root, errors=read_errors))
    def matches(e):
        if scope and e.get("scope_id") != scope:
            return False
        if profile and e.get("profile_id") != profile:
            return False
        if work and e.get("work_id") != work:
            return False
        if host and e.get("host_id") != host:
            return False
        paths = [e.get("primary_path", ""), *e.get("changed_paths", [])]
        return not target or any(p and (_within(p, target) or _within(target, p)) for p in paths)
    selected = sorted([e for e in events if matches(e)], key=_event_order, reverse=True)
    keys = ("event_id", "scope_id", "recorded_scope_id", "host_id", "profile_id", "work_id", "work_title", "phase", "summary", "primary_path", "verification", "follow_ups", "limitations", "quality_notes")
    if read_errors:
        # Missing events could contain a closure or lesson retirement: withhold those conclusions.
        return {"partial": True, "read_errors": read_errors, "matched": len(selected),
                "events": [{k: e[k] for k in keys if k in e} for e in selected[:limit]],
                "pending": None, "pending_matched": None, "legacy_pending": None,
                "lessons": None, "lessons_matched": None, "work_items": None, "work_items_matched": None,
                "warning": "部分事件无法读取；仅返回已读事实，数量为下限，待办/经验/事项结论暂不可用。请运行 doctor 定位异常"}
    pending = sorted([e for e in open_continuation_events(events) if matches(e)], key=_event_order, reverse=True)
    lessons = sorted([e for e in current_lessons(events) if matches(e)], key=_event_order, reverse=True)
    selected_groups = {_work_identity(e) for e in selected}
    cards = [c for c in work_cards(events) if c["work_id"] and _work_identity(c["_events"][-1]) in selected_groups]
    public_cards = [{**{k: v for k, v in c.items() if k not in {"_events", "event_ids", "follow_ups"}},
                     "follow_ups": c["follow_ups"][:limit], "follow_ups_total": len(c["follow_ups"])} for c in cards[:limit]]
    return {"partial": False, "matched": len(selected), "events": [{k: e[k] for k in keys if k in e} for e in selected[:limit]],
            "pending_matched": len(pending), "lessons_matched": len(lessons),
            "pending": [{k: e.get(k, "") for k in ("event_id", "scope_id", "host_id", "profile_id", "follow_ups")} for e in pending[:limit]],
            "legacy_pending": sum(matches(e) for e in legacy_continuation_events(events)),
            "work_items_matched": len(cards), "work_items": public_cards,
            "lessons": [{"event_id": e["event_id"], "scope_id": e["scope_id"], "host_id": e.get("host_id", ""), "profile_id": e.get("profile_id", ""), **e["lesson"]} for e in lessons[:limit]]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="Journal root; overrides CHANGE_JOURNAL_ROOT")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("render")
    sub.add_parser("status")
    sub.add_parser("doctor")
    query = sub.add_parser("query")
    query.add_argument("--path", default="")
    query.add_argument("--scope-id", default="")
    query.add_argument("--profile-id", default="")
    query.add_argument("--work-id", default="")
    query.add_argument("--host-id", default="")
    query.add_argument("--limit", type=int, default=5)

    route = sub.add_parser("route")
    route.add_argument("--cwd", default=os.getcwd())
    route.add_argument("--changed-path", action="append", default=[])
    route.add_argument("--primary-path", default="")

    record = sub.add_parser("record")
    record.add_argument("--actor", required=True)
    record.add_argument("--source", required=True)
    record.add_argument("--phase", required=True, choices=sorted(PHASES))
    record.add_argument("--summary", required=True)
    record.add_argument("--why", default="")
    record.add_argument("--cwd", default=os.getcwd())
    record.add_argument("--changed-path", action="append", default=[])
    record.add_argument("--verification-status", default="not_run", choices=sorted(VERIFICATION_STATUSES))
    record.add_argument("--verification-ref", action="append", default=[])
    record.add_argument("--rollback-ref", default="")
    record.add_argument("--unresolved", action="append", default=[])
    record.add_argument("--related-event-id", action="append", default=[])
    record.add_argument("--tag", action="append", default=[])
    record.add_argument("--sensitivity", default="private", choices=sorted(SENSITIVITIES))
    record.add_argument("--scope-id", default="")
    record.add_argument("--host-id", default="")
    record.add_argument("--profile-id", default="")
    record.add_argument("--session-id", default="")
    record.add_argument("--turn-id", default="")
    record.add_argument("--occurred-at", default="")
    record.add_argument("--idempotency-key", default="")
    record.add_argument("--record-level", choices=sorted(RECORD_LEVELS), default="brief")
    record.add_argument("--kind", choices=sorted(KINDS), default="")
    record.add_argument("--work-id", default="")
    record.add_argument("--work-title", default="")
    record.add_argument("--primary-path", default="")
    record.add_argument("--follow-up", action="append", default=[])
    record.add_argument("--limitation", action="append", default=[])
    record.add_argument("--resolves-event-id", action="append", default=[])
    record.add_argument("--supersedes-event-id", action="append", default=[])
    record.add_argument("--lesson-key", default="")
    record.add_argument("--lesson-trigger", default="")
    record.add_argument("--lesson-check", default="")
    record.add_argument("--lesson-status", choices=("active", "retired"), default="active")
    record.add_argument("--render", action="store_true", help="Update derived views after recording; never repeat the business change")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = root_from(args.root)
    try:
        if args.command == "init":
            result = init_root(root)
        elif args.command == "render":
            result = render_views(root)
        elif args.command == "status":
            result = status(root)
        elif args.command == "doctor":
            result = doctor(root)
        elif args.command == "query":
            result = query_events(root, args.path, args.scope_id, args.profile_id, args.limit, args.work_id, args.host_id)
        elif args.command == "route":
            cwd = safe_path(args.cwd, "cwd")
            paths = [safe_path(path, "changed_path") for path in args.changed_path]
            result = {"scope_id": route_scope(root, cwd, paths, args.primary_path)}
        elif args.command == "record":
            result = record_event(
                root,
                actor=args.actor,
                source=args.source,
                phase=args.phase,
                summary=args.summary,
                why=args.why,
                cwd=args.cwd,
                changed_paths=args.changed_path,
                verification_status=args.verification_status,
                verification_refs=args.verification_ref,
                rollback_ref=args.rollback_ref,
                unresolved=args.unresolved,
                related_event_ids=args.related_event_id,
                tags=args.tag,
                sensitivity=args.sensitivity,
                scope_id=args.scope_id,
                host_id=args.host_id,
                profile_id=args.profile_id,
                session_id=args.session_id,
                turn_id=args.turn_id,
                occurred_at=args.occurred_at,
                idempotency_key=args.idempotency_key,
                record_level=args.record_level,
                kind=args.kind,
                work_id=args.work_id,
                work_title=args.work_title,
                primary_path=args.primary_path,
                follow_ups=args.follow_up,
                limitations=args.limitation,
                resolves_event_ids=args.resolves_event_id,
                supersedes_event_ids=args.supersedes_event_id,
                lesson_key=args.lesson_key,
                lesson_trigger=args.lesson_trigger,
                lesson_check=args.lesson_check,
                lesson_status=args.lesson_status,
            )
            if args.render:
                try:
                    result["views"] = render_views(root)
                    result["views_updated"] = True
                except (JournalError, OSError, ValueError) as exc:
                    result["views_updated"] = False
                    result["view_error"] = type(exc).__name__
                    result["next_step"] = "事件已保存；修复后只重试 render，不重做业务变更"
        else:
            parser.error("unknown command")
            return 2
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1 if args.command == "doctor" and not result.get("ok") else 0
    except (JournalError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
