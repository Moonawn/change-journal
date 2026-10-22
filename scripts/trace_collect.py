#!/usr/bin/env python3
"""痕迹采集 CLI：读会话痕迹 → 结构化 turn → 聚合 bucket → 晋升事件。

设计约束（见 references/schema.md 的适配器与隐私）：
- 本脚本**纯标准库**，**不调用任何 LLM**，**不联网**。LLM 那一段走文件契约。
- `.trace/` 与 `.index/` 都是**派生数据**，可整体删除重算。
- `render_views()` 永不读 `.trace/`。

子命令：
    survey    只打印分桶统计，零写入
    collect   写 .trace/ + .index/rollup/ + checkpoints
    promote   把「台账零覆盖」的 bucket 晋升为事件
    prune     删除 .trace/ 里指定日期之前的记录
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import change_journal as cj  # noqa: E402
import trace_sources as ts  # noqa: E402


TRACE_DIR = ".trace"
INDEX_DIR = ".index"
CHECKPOINT_DIR = f"{INDEX_DIR}/checkpoints"
ROLLUP_DIR = f"{INDEX_DIR}/rollup"


def local_date(occurred_at: str) -> str:
    """UTC ISO → 本地日期（YYYY-MM-DD）。拿不到就空串。"""
    if not occurred_at:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.astimezone().strftime("%Y-%m-%d")


def bucket_id(agent: str, session_id: str, day: str) -> str:
    return f"{agent}:{session_id}:{day}"


def _load_checkpoints(root: Path, agent: str) -> Dict[str, int]:
    path = root / CHECKPOINT_DIR / f"{agent}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    offsets = payload.get("offsets")
    return {str(k): int(v) for k, v in offsets.items()} if isinstance(offsets, dict) else {}


# --------------------------------------------------------------------------
# survey —— 只读，零写入
# --------------------------------------------------------------------------


def cmd_survey(args: argparse.Namespace) -> int:
    root = cj.root_from(args.root)
    print(f"root: {root}")
    print(f"{TRACE_DIR}/ 存在: {(root / TRACE_DIR).exists()}   （survey 不写任何东西）\n")

    print(f"{'agent':14s} {'turn':>7s} {'有变更':>7s} {'会话':>5s} {'路径(去重)':>11s} {'命令动词':>8s} {'日期范围':>21s}")
    print("-" * 82)

    grand: Counter = Counter()
    for agent, reader in ts.READERS.items():
        turns = 0
        with_changes = 0
        sessions: set = set()
        paths: set = set()
        verbs: set = set()
        lo = hi = ""
        error = ""
        try:
            for record in reader():
                turns += 1
                sessions.add(record["session_id"])
                if record["changed_paths"]:
                    with_changes += 1
                    paths.update(record["changed_paths"])
                verbs.update(record["command_verbs"])
                stamp = record["occurred_at"]
                if stamp:
                    if not lo or stamp < lo:
                        lo = stamp
                    if not hi or stamp > hi:
                        hi = stamp
        except Exception as exc:  # 单源失败不该拖垮整张表
            error = f"{type(exc).__name__}: {exc}"
        span = f"{lo[:10]} ~ {hi[:10]}" if lo else "—"
        print(f"{agent:14s} {turns:7d} {with_changes:7d} {len(sessions):5d} {len(paths):11d} {len(verbs):8d} {span:>21s}")
        if error:
            print(f"{'':14s} ⚠️ {error}")
        grand[agent] = turns

    print("-" * 82)
    print(f"{'合计':14s} {sum(grand.values()):7d}")
    return 0


# --------------------------------------------------------------------------
# 其余子命令在 P1–P4 落地
# --------------------------------------------------------------------------


def cmd_not_implemented(args: argparse.Namespace) -> int:
    print(f"{args.command} 尚未实现（计划里属于后续阶段）", file=sys.stderr)
    return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trace_collect", description=__doc__)
    parser.add_argument("--root", default=None, help="台账根目录（缺省用 CHANGE_JOURNAL_ROOT 或内置默认）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("survey", help="只打印分桶统计，零写入").set_defaults(func=cmd_survey)

    for name, help_text in (
        ("collect", "写 .trace/ + .index/rollup/ + checkpoints"),
        ("promote", "把台账零覆盖的 bucket 晋升为事件"),
        ("prune", "删除 .trace/ 里指定日期之前的记录"),
    ):
        parser_sub = sub.add_parser(name, help=help_text)
        parser_sub.set_defaults(func=cmd_not_implemented)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except cj.JournalError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
