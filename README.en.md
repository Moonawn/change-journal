[简体中文](README.md) · **English**

![Change Journal: a lightweight change ledger for people and AI agents](assets/cover-en.png)

# Change Journal

**A lightweight change ledger for people and AI agents.**

Save work changes, verification evidence, and unfinished next steps as events with timestamps and sources, so work can be reviewed, traced, and continued across sessions.

| What changed? | How was it checked? | What comes next? |
|---|---|---|
| Specific changes and reasons | Check results and evidence | Explicit follow-ups and limitations |

Focus on concrete changes. Add a small number of evidence-backed lessons only when they have been verified and are useful for future work. Keep their applicability conditions and links to the original events.

One work item can have multiple events, presented together as a single work card in the daily review entry point. Original events are append-only; work-item, follow-up, and lesson views can be regenerated.

## Installation

Requirements: macOS, Linux, or native Windows, with Python 3.9+. Runtime and tests use only the Python standard library. Concurrent writes are protected by `fcntl` file locks on POSIX and `msvcrt` byte-range locks on Windows.

macOS / Linux:

```bash
git clone https://github.com/Moonawn/change-journal.git "$HOME/.agents/skills/change-journal"
python3 "$HOME/.agents/skills/change-journal/scripts/change_journal.py" init
```

Windows PowerShell (Python must be on PATH; `py -3` can replace `python`):

```powershell
$skillDir = Join-Path $HOME '.agents/skills/change-journal'
git clone https://github.com/Moonawn/change-journal.git "$skillDir"
python "$skillDir/scripts/change_journal.py" init
```

If a Skill with the same name is already installed, preserve that directory and inspect the new version in a separate location. Do not overwrite personal configuration currently in use. You can also install the repository in a Skill directory supported by another agent.

The default journal directory is `~/.local/share/change-journal`, separate from the Skill source code. Override it with `CHANGE_JOURNAL_ROOT`, or pass `--root /absolute/journal-directory` before the subcommand.

## Usage

Run the following commands from the repository root.

Record a verified change. Reuse the same `work-id` when advancing the same objective across multiple stages:

```bash
python3 scripts/change_journal.py record \
  --actor codex --source semantic --phase verified \
  --summary "Fixed the export error and passed isolated checks" \
  --primary-path /absolute/project \
  --changed-path /absolute/project/export.py \
  --work-id export-fix --work-title "Export error fix" \
  --verification-status passed --verification-ref "Relevant tests passed" \
  --idempotency-key export-fix-verified --render
```

```bash
python3 scripts/change_journal.py query --path /absolute/project
python3 scripts/change_journal.py query --work-id export-fix --detail
python3 scripts/change_journal.py show EVENT_ID
python3 scripts/change_journal.py render
python3 scripts/change_journal.py doctor
```

Scope routing uses the longest matching path prefix in the journal's `registry.json`. Initialization registers only the Skill and the journal itself. Add project directories as needed; unmatched targets remain `UNASSIGNED`.

By default, queries return at most 3 summary cards and limit the complete JSON output to 6,000 characters, including Unicode escapes and newlines. This is not an exact token budget. Adjust these limits with `--limit` and `--max-chars`. Omitted items are counted; use `show EVENT_ID` for the complete event or `--detail` for the detailed query structure. Detailed mode is not subject to the summary budget.

Within the same context, use `query --path /absolute/project --cursor "cursor-from-the-previous-response"` to request incremental updates. When the response has `mode: delta` and `has_more: true`, continue with the returned cursor until all pages have been read. An unchanged result returns `unchanged`. Start a new session without a cursor to obtain fresh summaries. A cursor does not mean that all history has been read, and it does not prove that another agent understands the work item. Backdated events, closed follow-ups, and retired lessons are detected; changes to historical facts or classification reset the summary. The query implementation reads and hashes local files without model calls or an additional cache directory.

For retries, reuse the same idempotency key and record content. Keys are isolated by `actor/source/host/profile`. Reusing a key with different content raises a conflict; inspect the original event. Use a new key for a genuinely new stage while keeping the same `work-id`. Multiple agents can query each other's evidence when they use the same journal root directory. Different machines still need a separate way to share or synchronize the journal. Installing the Skill does not synchronize the agents' native chat logs.

Windows PowerShell example (a backtick continues the line; do not add spaces after it):

```powershell
python scripts/change_journal.py record `
  --actor codex --source semantic --phase verified `
  --summary 'Fixed the export error and passed isolated checks' `
  --primary-path 'C:/Projects/demo' `
  --changed-path 'C:/Projects/demo/export.py' `
  --work-id export-fix --work-title 'Export error fix' `
  --verification-status passed --verification-ref 'Relevant tests passed' `
  --idempotency-key export-fix-verified --render
python scripts/change_journal.py query --path 'C:/Projects/demo'
python scripts/change_journal.py doctor
```

Windows drive-letter and UNC paths are supported. Matching is case-insensitive and normalizes separators for Windows paths; POSIX paths remain case-sensitive. Existing path records can be queried from another operating system. In `registry.json`, prefer Windows paths such as `C:/Projects/demo`; backslashes must be escaped as `\\` in JSON. Events and views are stored as UTF-8. CLI JSON escapes Unicode for compatibility with redirected output and older consoles.

## Journal structure

Generated view filenames below are shown as they exist on disk.

```text
journal-root/
├── registry.json                 Path-to-scope routing
├── work-items.json               Optional historical classification and work-item bindings
├── events/YYYY/MM/*.json         Original events, append-only
├── views/
│   ├── 00_审阅入口_REVIEW.md      Daily entry point: 8 recent work items or standalone records
│   ├── 02_事项索引_WORK_ITEMS.md  Grouped work items
│   ├── by-work/                  Progress, follow-ups, and evidence for each work item
│   ├── 00_待续接_CONTINUATIONS.md Explicit remaining actions
│   ├── 01_可复用经验_LESSONS.md   Currently valid lessons
│   ├── 99_历史未决待核_LEGACY.md  Historical notes awaiting review
│   ├── by-scope/                 History by scope
│   ├── daily/                    History by date
│   └── 00_全局变更时间线_CHANGELOG.md
├── idempotency/                  Duplicate-write prevention
└── .spool/                       Mechanical observations and intermediate data
```

- `scope` identifies a target, `work_id` identifies a work item, and `lesson.key` identifies a reusable lesson.
- Events for the same work item are grouped by `scope + host + profile + work_id`, preserving identity boundaries.
- `follow_ups` holds explicit actions; `limitations` holds known limits. Legacy `unresolved` notes remain in historical review.
- Referencing an event does not automatically close its follow-ups. Use `resolves_event_ids` or `supersedes_event_ids` to explicitly resolve or take over the remaining work.
- Lessons include applicability conditions, checks, and evidence. They can be updated or retired and do not automatically become global rules.
- The current event schema is `1.2`, with read compatibility for `1.0` and `1.1`. Event timestamps use UTC; daily views are grouped using Asia/Shanghai.

See the [record schema (Chinese)](references/schema.md) for fields and adapters, and [SKILL.md (Chinese)](SKILL.md) for agent instructions.

## Adapters and boundaries

`scripts/hermes_adapter.py` accepts semantic records as JSON on stdin. `scripts/codex_hook.py` provides observations of potentially state-changing tool calls and a mechanical fallback. Installing the repository does not automatically configure hooks, create scheduled jobs, or connect external services.

`trace_collect.py` and `trace_sources.py` are optional experimental tools. Only `survey` currently implements read-only statistics; `collect/promote/prune` are not implemented. They are outside the default recording workflow. Cindy data discovery still uses macOS default directories.

This repository contains the program, specifications, and tests. Real events, classification settings, backups, and session traces stay in the local journal directory; `.gitignore` also excludes these runtime files. Text redaction is a supplementary check and does not replace choosing appropriate summaries and evidence references.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Windows PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -v
```

Tests use isolated temporary directories. They cover append-only recording, concurrency and idempotency conflicts, cross-platform paths, Chinese output, Hook subprocess deduplication, work-item grouping, identity boundaries, follow-up relationships, lesson versions, summary budgets, and incremental pagination. GitHub Actions runs the tests on Windows, Linux, and macOS, with additional Windows checks for PowerShell 7 and Windows PowerShell 5.1 commands. Tests against a real Codex host are opt-in; passing Hook payload tests does not prove that the host is wired up.

## Copyright

Copyright (c) 2026 Moonawn.
