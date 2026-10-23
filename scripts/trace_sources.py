#!/usr/bin/env python3
"""痕迹读取器：从本机三个 Agent 的会话痕迹里提取结构性事实。

**只读，不写任何文件。** 输出记录的键集恒等于 `TRACE_FIELDS`——
每个 reader 内部都显式写键名，没有任何 dict 透传。所以
`command` / `stdout` / `diff` / `content` / `prompt` / `assistant_text`
这六类东西在物理上进不来。

唯一允许进入内存、且绝不落盘的，是会话标题（`title`），且默认关闭
（见 `include_titles`）。它只在组 LLM 队列时当场拼进批文件然后丢弃。
"""

from __future__ import annotations

import datetime as dt
import json
import ntpath
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlsplit

import change_journal as cj

HOME = Path.home()
CC_ROOT = HOME / ".claude" / "projects"
CODEX_ROOT = HOME / ".codex"
CINDY_SUPPORT = HOME / "Library" / "Application Support" / "Cindy"

# 输出记录的键集，顺序固定。任何 reader 少写/多写键都会被测试抓住。
TRACE_FIELDS: Tuple[str, ...] = (
    "agent",
    "session_id",
    "turn_id",
    "occurred_at",
    "cwd",
    "changed_paths",
    "path_ops",
    "tool_call_count",
    "tool_names",
    "command_verbs",
    "exit_codes",
    "source_ref",
    "state",
)

# 会话标题：允许进内存（组 LLM 队列时用），**永不进 .trace/**。
TITLE_FIELD = "title"

_MAX_PATHS = 64
_MAX_TOOLS = 12
_MAX_VERBS = 10

# 从工具输入里取文件路径用的键名。取不到就放弃这条，不猜。
_CC_PATH_INPUT_KEYS = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
# 这些工具算「改动」，其余只计调用次数。
_CC_MUTATING = set(_CC_PATH_INPUT_KEYS)

_VERB_RE = re.compile(r"^[A-Za-z0-9_./-]{1,32}")


class TraceError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 公共小工具
# --------------------------------------------------------------------------


def empty_turn(agent: str, session_id: str, turn_id: str, occurred_at: str, cwd: str,
               source_path: str, source_offset: int) -> Dict[str, Any]:
    return {
        "agent": agent,
        "session_id": session_id,
        "turn_id": turn_id,
        "occurred_at": occurred_at,
        "cwd": cwd,
        "changed_paths": [],
        "path_ops": {"add": [], "update": [], "delete": []},
        "tool_call_count": 0,
        "tool_names": [],
        "command_verbs": {},
        "exit_codes": {"ok": 0, "error": 0, "unknown": 0},
        "source_ref": {"path": source_path, "offset": source_offset},
        "state": "observed",
    }


def _norm_ts(value: Any) -> str:
    """任何时间形态 → UTC ISO-8601（秒精度，Z 结尾）。拿不到就空串。"""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # epoch 毫秒
            seconds /= 1000.0
        try:
            stamp = dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return ""
        return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _strip_file_uri(value: Any) -> str:
    """Codex 的 CommandExecution.cwd 是 file:// URI，其余地方是裸路径。"""
    text = str(value or "").strip()
    if text.startswith("file://"):
        uri = urlsplit(text)
        text = unquote(uri.path)
        if uri.netloc and uri.netloc.lower() != "localhost":
            text = "//" + uri.netloc + text
        elif re.match(r"^/[A-Za-z]:/", text):
            text = text[1:]
    return text


def _changed_path(value: Any, cwd: str = "") -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        return cj.absolute_path(_strip_file_uri(value), cwd)
    except cj.JournalError:
        return ""


_SHELL_NAMES = {"zsh", "bash", "sh", "dash", "ksh"}
_WRAPPER_FLAGS = {"-c", "-lc", "-ic", "-cl", "-ci"}
# 无信息量的前缀动词：跳过后再看真正的第一个词
_NOOP_VERBS = {"cd", "true", "set", "export", "source", "umask"}


def _verb_from_script(script: str) -> str:
    """从一段 shell 文本里取首个有信息量的 token。剩下的**立即丢弃**。"""
    text = script.lstrip()
    for _ in range(4):  # 最多剥 4 层前缀（env VAR=1 cd /x && ...）
        match = _VERB_RE.match(text)
        if not match:
            return ""
        token = match.group(0)
        name = ntpath.basename(token) or token
        if name in _NOOP_VERBS:
            separator = text.find("&&")
            if separator == -1:
                return name
            text = text[separator + 2:].lstrip()
            continue
        return name[:32]
    return ""


def _command_verb(command: Any) -> str:
    """命令的**首个有信息量的 token**——闭集词汇，不是原命令。

    Codex 的形态是 `["/bin/zsh", "-lc", "<真命令>"]`（实测 1496/1496 都是），
    所以必须剥掉 shell 包装，否则整个直方图只有一个 "zsh"。
    """
    if isinstance(command, (list, tuple)):
        if not command:
            return ""
        head = ntpath.basename(str(command[0])) or str(command[0])
        if head in _SHELL_NAMES and len(command) >= 3 and str(command[1]) in _WRAPPER_FLAGS:
            return _verb_from_script(str(command[2]))
        if head in ("env", "sudo", "nohup", "time") and len(command) > 1:
            return _command_verb(command[1:])
        return head[:32]
    return _verb_from_script(str(command or ""))


def _bump(counter: Dict[str, int], key: str, cap: int) -> Dict[str, int]:
    if not key:
        return counter
    if key in counter:
        counter[key] += 1
    elif len(counter) < cap:
        counter[key] = 1
    return counter


def _finish(turn: Dict[str, Any]) -> Dict[str, Any]:
    """封口：排序 + 截断，保证同输入产出字节一致。"""
    turn["changed_paths"] = sorted(set(turn["changed_paths"]))[:_MAX_PATHS]
    keep = set(turn["changed_paths"])
    ops = turn["path_ops"]
    turn["path_ops"] = {
        key: sorted(set(ops.get(key, [])) & keep)[:_MAX_PATHS]
        for key in ("add", "update", "delete")
    }
    turn["tool_names"] = sorted(set(turn["tool_names"]))[:_MAX_TOOLS]
    turn["command_verbs"] = dict(sorted(turn["command_verbs"].items(), key=lambda kv: (-kv[1], kv[0]))[:_MAX_VERBS])
    return {k: turn[k] for k in TRACE_FIELDS if k in turn}


# --------------------------------------------------------------------------
# Claude Code —— ~/.claude/projects/<扁平 cwd>/<sessionId>.jsonl
# --------------------------------------------------------------------------


def _iter_jsonl(path: Path, start: int = 0) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """逐行读，返回 (行结束时的字节偏移, 解析结果)。坏行跳过。"""
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            while True:
                line = handle.readline()
                if not line:
                    break
                offset = handle.tell()
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(parsed, dict):
                    yield offset, parsed
    except OSError:
        return


def iter_claude_code(since: Optional[str] = None, start_offsets: Optional[Dict[str, int]] = None,
                     include_titles: bool = False) -> Iterator[Dict[str, Any]]:
    """按 promptId 分组，每个 turn 产出一条记录。

    注意：`cwd` 是**逐行**字段不是会话常量（实测一个 session 里有 11 个不同 cwd），
    所以 turn 的 cwd 取该 turn 内最后一次出现的值。
    """
    offsets = start_offsets or {}
    if not CC_ROOT.exists():
        return
    for jsonl in sorted(CC_ROOT.glob("*/*.jsonl")):
        session_id = jsonl.stem
        start = int(offsets.get(str(jsonl), 0))
        if start and jsonl.stat().st_size < start:
            start = 0  # 被截断/轮转

        turns: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        pending_tool: Dict[str, str] = {}   # tool_use_id -> tool name
        title = ""
        last_offset = start

        for offset, row in _iter_jsonl(jsonl, start):
            last_offset = offset
            kind = row.get("type")
            cwd = _strip_file_uri(row.get("cwd"))
            if include_titles and not title:
                raw = row.get("aiTitle") or row.get("customTitle")
                if isinstance(raw, str) and raw.strip():
                    title = raw.strip()[:160]

            if kind == "assistant":
                message = row.get("message") or {}
                for block in (message.get("content") or []):
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = str(block.get("name") or "")
                    tool_id = str(block.get("id") or "")
                    turn_id = str(row.get("promptId") or message.get("id") or "")
                    if not turn_id:
                        continue
                    turn = turns.get(turn_id)
                    if turn is None:
                        turn = empty_turn("claude-code", session_id, turn_id,
                                          _norm_ts(row.get("timestamp")), cwd, str(jsonl), offset)
                        turns[turn_id] = turn
                        order.append(turn_id)
                    turn["tool_call_count"] += 1
                    if name not in turn["tool_names"]:
                        turn["tool_names"].append(name)
                    if cwd:
                        turn["cwd"] = cwd
                    if tool_id:
                        pending_tool[tool_id] = turn_id

                    payload = block.get("input")
                    if not isinstance(payload, dict):
                        continue
                    key = _CC_PATH_INPUT_KEYS.get(name)
                    if key:
                        candidate = _changed_path(payload.get(key), cwd)
                        if candidate:
                            turn["changed_paths"].append(candidate)
                            turn["path_ops"]["update"].append(candidate)
                    elif name == "Bash":
                        _bump(turn["command_verbs"], _command_verb(payload.get("command")), _MAX_VERBS)

            elif kind == "user":
                message = row.get("message") or {}
                for block in (message.get("content") or []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        turn_id = pending_tool.get(str(block.get("tool_use_id") or ""))
                        if not turn_id or turn_id not in turns:
                            continue
                        turn = turns[turn_id]
                        if block.get("is_error") is True:
                            turn["exit_codes"]["error"] += 1
                        else:
                            turn["exit_codes"]["ok"] += 1

        for turn_id in order:
            record = _finish(turns[turn_id])
            if include_titles and title:
                record[TITLE_FIELD] = title
            record["source_ref"]["eof"] = last_offset
            yield record


# --------------------------------------------------------------------------
# Codex —— ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl（+ archived_sessions）
# --------------------------------------------------------------------------


def _codex_files() -> List[Path]:
    files: List[Path] = []
    for root in (CODEX_ROOT / "sessions", CODEX_ROOT / "archived_sessions"):
        if root.exists():
            files.extend(sorted(root.rglob("*.jsonl")))
    return files


def iter_codex(since: Optional[str] = None, start_offsets: Optional[Dict[str, int]] = None,
               include_titles: bool = False) -> Iterator[Dict[str, Any]]:
    """按 turn_id 分组。文件变更走 FileChange（changes 的 key 就是绝对路径）。"""
    offsets = start_offsets or {}
    for jsonl in _codex_files():
        start = int(offsets.get(str(jsonl), 0))
        if start and jsonl.stat().st_size < start:
            start = 0

        session_id = ""
        turns: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        last_offset = start
        last_ts = ""
        cwd = ""

        for offset, row in _iter_jsonl(jsonl, start):
            last_offset = offset
            stamp = _norm_ts(row.get("timestamp")) or last_ts
            last_ts = stamp
            kind = row.get("type")
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}

            if kind == "session_meta":
                session_id = str(payload.get("session_id") or payload.get("id") or jsonl.stem)
                cwd = _strip_file_uri(payload.get("cwd")) or cwd
                continue

            if kind == "turn_context":
                cwd = _strip_file_uri(payload.get("cwd")) or cwd
                continue

            if kind != "event_msg" or payload.get("type") != "item_completed":
                continue

            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            turn_id = str(payload.get("turn_id") or "")
            if not turn_id or not item:
                continue

            turn = turns.get(turn_id)
            if turn is None:
                turn = empty_turn("codex", session_id or jsonl.stem, turn_id, stamp, cwd, str(jsonl), offset)
                turns[turn_id] = turn
                order.append(turn_id)
            if cwd:
                turn["cwd"] = cwd
            if not turn["occurred_at"]:
                turn["occurred_at"] = stamp

            item_type = item.get("type")
            if item_type == "FileChange":
                for path, detail in (item.get("changes") or {}).items():
                    path = _changed_path(path, cwd)
                    if not path:
                        continue
                    turn["changed_paths"].append(path)
                    op = "update"
                    if isinstance(detail, dict):
                        raw_op = str(detail.get("type") or "").lower()
                        op = {"add": "add", "update": "update", "delete": "delete"}.get(raw_op, "update")
                    turn["path_ops"][op].append(path)
            elif item_type == "CommandExecution":
                turn["tool_call_count"] += 1
                if "CommandExecution" not in turn["tool_names"]:
                    turn["tool_names"].append("CommandExecution")
                _bump(turn["command_verbs"], _command_verb(item.get("command")), _MAX_VERBS)
                code = item.get("exit_code")
                if isinstance(code, int):
                    turn["exit_codes"]["ok" if code == 0 else "error"] += 1
                else:
                    turn["exit_codes"]["unknown"] += 1
            elif item_type:
                turn["tool_call_count"] += 1
                name = str(item_type)[:32]
                if name not in turn["tool_names"]:
                    turn["tool_names"].append(name)

        for turn_id in order:
            record = _finish(turns[turn_id])
            record["source_ref"]["eof"] = last_offset
            yield record


# --------------------------------------------------------------------------
# Cindy —— SQLite（WAL）+ turn-change-sets/
# --------------------------------------------------------------------------


def cindy_db_candidates() -> List[Path]:
    if not CINDY_SUPPORT.exists():
        return []
    return sorted(p for p in CINDY_SUPPORT.glob("cindy-*.db") if p.is_file())


def _cindy_connect(db: Path) -> Optional[sqlite3.Connection]:
    """活 WAL 库：必须 mode=ro（不能 immutable=1，会漏 WAL 内容）。"""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")
        return conn
    except sqlite3.Error:
        return None


def iter_cindy(since: Optional[str] = None, start_offsets: Optional[Dict[str, int]] = None,
               include_titles: bool = False) -> Iterator[Dict[str, Any]]:
    """会话元数据来自 SQLite；文件变更来自 turn-change-sets/。

    ⚠️ turn-change-sets 经常是 `files:[]` + `state:"partial"`，**不能当穷尽来源**。
    与 Claude Code 同 session id 时，文件变更以 CC 为准（调用方去重）。
    """
    seen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for db in cindy_db_candidates():
        conn = _cindy_connect(db)
        if conn is None:
            continue
        try:
            rows = conn.execute(
                "SELECT id, title, working_dir, created_at, updated_at, agent_kind, status "
                "FROM sessions"
            ).fetchall()
        except sqlite3.Error:
            conn.close()
            continue
        for row in rows:
            session_id = str(row["id"])
            record = empty_turn("cindy", session_id, session_id,
                                _norm_ts(row["created_at"]), _strip_file_uri(row["working_dir"]),
                                f"sqlite:{db.name}:sessions", 0)
            record["state"] = str(row["status"] or "observed")[:32]
            if include_titles and row["title"]:
                record[TITLE_FIELD] = str(row["title"])[:160]
            seen[session_id] = record
            order.append(session_id)
        conn.close()

    # turn-change-sets：每轮一个 JSON
    tcs_root = CINDY_SUPPORT / "turn-change-sets"
    if tcs_root.exists():
        for session_dir in sorted(p for p in tcs_root.iterdir() if p.is_dir()):
            session_id = session_dir.name
            base = seen.get(session_id)
            for item in sorted(session_dir.glob("*.json")):
                try:
                    payload = json.loads(item.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                files = payload.get("files")
                if not isinstance(files, list) or not files:
                    continue  # partial / 空 —— 不当成来源
                cwd = _strip_file_uri(payload.get("cwd")) or (base or {}).get("cwd", "")
                turn_id = str(payload.get("providerTurnId") or payload.get("id") or item.stem)
                record = empty_turn("cindy", session_id, turn_id,
                                    _norm_ts(payload.get("createdAt")), cwd, str(item), 0)
                op_map = {"added": "add", "modified": "update", "deleted": "delete"}
                for entry in files:
                    if not isinstance(entry, dict):
                        continue
                    rel = entry.get("path")
                    if not isinstance(rel, str) or not rel:
                        continue
                    absolute = _changed_path(rel, cwd)
                    if not absolute:
                        continue
                    record["changed_paths"].append(absolute)
                    record["path_ops"][op_map.get(str(entry.get("status") or "").lower(), "update")].append(absolute)
                if not record["changed_paths"]:
                    continue
                yield _finish(record)

    for session_id in order:
        record = seen[session_id]
        yield _finish(record)


READERS = {
    "claude-code": iter_claude_code,
    "codex": iter_codex,
    "cindy": iter_cindy,
}
