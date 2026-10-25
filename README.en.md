<div align="center">

# Change Journal

### Record each change. Give the next session a place to start.

[简体中文](README.md) · **English**

[Quick start](#quick-start) · [Use with an Agent](#use-with-an-agent) · [Find records](#review-and-continue) · [Record schema](references/schema.md)

[![Tests](https://github.com/Moonawn/change-journal/actions/workflows/test.yml/badge.svg)](https://github.com/Moonawn/change-journal/actions/workflows/test.yml)

</div>

**A lightweight change ledger for people and AI agents.**

When work continues in another session, you need to know what changed, what was checked, and what remains unfinished.

Change Journal saves actual changes, verification evidence, and follow-ups as events with timestamps and sources, so work can be reviewed, traced, and continued across sessions.

![Change Journal: changes, evidence and next steps](assets/cover-en.png)

## Start with a change

| As work progresses | What the record helps you find |
| :--- | :--- |
| Make a change | What changed, why, and which target it affects |
| Check the result | Verification evidence, check results and known limitations |
| Continue in another session | Progress on the same work item, explicit follow-ups and original evidence |

One work item can have multiple events, grouped into a single card for everyday review. Focus on concrete changes. Add a small number of evidence-backed lessons only when they have been verified and are useful for future work, keeping their applicability conditions and links to the original events.

<a id="installation"></a>

## Quick start

Supports **macOS, Linux and native Windows, with Python 3.9+**. Runtime and tests use only the Python standard library. The installation examples below require Git.

If a Skill with the same name is already installed, preserve that directory and inspect the new version in a separate location. You can also install this repository in another agent's supported Skills directory.

**macOS / Linux**

```bash
git clone https://github.com/Moonawn/change-journal.git "$HOME/.agents/skills/change-journal"
cd "$HOME/.agents/skills/change-journal"
python3 scripts/change_journal.py init
```

<details>
<summary><strong>Install with Windows PowerShell</strong></summary>

Python must be on PATH; `py -3` can replace `python`.

```powershell
$skillDir = Join-Path $HOME '.agents/skills/change-journal'
git clone https://github.com/Moonawn/change-journal.git "$skillDir"
Set-Location "$skillDir"
python scripts/change_journal.py init
```

</details>

The default journal directory is `~/.local/share/change-journal/`, separate from the Skill source code. Override it with `CHANGE_JOURNAL_ROOT`, or pass `--root /absolute/journal-directory` before the subcommand.

## Use with an Agent

[Read the Skill](SKILL.md) · [Record schema](references/schema.md)

Ask your agent to load [SKILL.md](SKILL.md), then describe what to record and how to resume. The agent instructions and schema reference are currently in Chinese.

> Use change-journal. After completing and verifying this change, record what changed, the verification evidence, and explicit follow-ups. When continuing later, query the relevant records by project path first.

Multiple agents can query each other's change evidence when they use the same journal root directory. Different machines still need a separate way to share or synchronize the journal. Installing the Skill does not synchronize native chat logs. Loading the Skill depends on the agent host; hooks require separate integration.

<a id="usage"></a>

## Review and continue

For manual review, open `views/00_审阅入口_REVIEW.md` in the journal directory. It shows the 8 most recent work items or standalone records; open a work card for its progress, follow-ups and evidence.

Run the following commands from the Skill repository directory. Replace example paths with your project's actual path. On Windows, use `python` or `py -3` in place of `python3`.

```bash
python3 scripts/change_journal.py query --path /absolute/project
```

By default, queries return at most **3 summary cards**, with the complete JSON output limited to **6,000 characters**. Omitted items are counted. Use the returned event IDs when you need the complete facts.

<details>
<summary><strong>Expand work items, adjust output limits and query incrementally</strong></summary>

```bash
python3 scripts/change_journal.py query --work-id export-fix --detail
python3 scripts/change_journal.py show EVENT_ID
python3 scripts/change_journal.py render
python3 scripts/change_journal.py doctor
```

By default, queries return at most 3 summary cards and limit the complete JSON output to 6,000 characters, including Unicode escapes and newlines. This is not an exact token budget. Adjust these limits with `--limit` and `--max-chars`. Omitted items are counted; use `show EVENT_ID` for the complete event or `--detail` for the detailed query structure. Detailed mode is not subject to the summary budget.

Within the same context, use `query --path /absolute/project --cursor "cursor-from-the-previous-response"` to request incremental updates. When the response has `mode: delta` and `has_more: true`, continue with the returned cursor until all pages have been read. An unchanged result returns `unchanged`. Start a new session without a cursor to obtain fresh summaries. A cursor does not mean that all history has been read, and it does not prove that another agent understands the work item. Backdated events, closed follow-ups, and retired lessons are detected; changes to historical facts or classification reset the summary. The query implementation reads and hashes local files without model calls or an additional cache directory.

</details>

<details>
<summary><strong>Record a change manually</strong></summary>

This example records a change that has already been verified. Replace the paths, summary and evidence with your own. Reuse the same `work-id` when advancing the same objective across multiple stages.

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

Windows PowerShell (a backtick continues the line; do not add spaces after it):

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

For retries, reuse the same idempotency key and record content. Keys are isolated by `actor/source/host/profile`. Reusing a key with different content raises a conflict; inspect the original event. Use a new key for a genuinely new stage while keeping the same `work-id`.

</details>

## Data and boundaries

Original events are appended as JSON files; Markdown review views can be regenerated. Records retain change summaries and evidence references, so you can return to the underlying evidence.

This repository contains the program, specifications, and tests. Real events, classification settings, backups, and session traces stay in the local journal directory; `.gitignore` also excludes these runtime files. Text redaction is a supplementary check and does not replace choosing appropriate summaries and evidence references.

<a id="journal-structure"></a>

<details>
<summary><strong>Directory structure and record rules</strong></summary>

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

Scope routing uses the longest matching path prefix in the journal's `registry.json`. Initialization registers only the Skill and the journal itself. Add project directories as needed; unmatched targets remain `UNASSIGNED`.

Concurrent writes are protected by `fcntl` file locks on POSIX and `msvcrt` byte-range locks on Windows.

Windows drive-letter and UNC paths are supported. Matching is case-insensitive and normalizes separators for Windows paths; POSIX paths remain case-sensitive. Existing path records can be queried from another operating system. In `registry.json`, prefer Windows paths such as `C:/Projects/demo`; backslashes must be escaped as `\\` in JSON. Events and views are stored as UTF-8. CLI JSON escapes Unicode for compatibility with redirected output and older consoles.

</details>

<a id="adapters-and-boundaries"></a>

<details>
<summary><strong>Adapters and experimental tools</strong></summary>

`scripts/hermes_adapter.py` accepts semantic records as JSON on stdin. `scripts/codex_hook.py` provides observations of potentially state-changing tool calls and a mechanical fallback. Installing the repository does not automatically configure hooks, create scheduled jobs, or connect external services.

`trace_collect.py` and `trace_sources.py` are optional experimental tools. Only `survey` currently implements read-only statistics; `collect/promote/prune` are not implemented. They are outside the default recording workflow. Cindy data discovery still uses macOS default directories.

</details>

## Verification

[GitHub Actions](https://github.com/Moonawn/change-journal/actions/workflows/test.yml) covers Windows, Linux and macOS.

<details>
<summary><strong>Test commands and coverage</strong></summary>

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Windows PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -v
```

Tests use isolated temporary directories. They cover append-only recording, concurrency and idempotency conflicts, cross-platform paths, Chinese output, Hook subprocess deduplication, work-item grouping, identity boundaries, follow-up relationships, lesson versions, summary budgets, and incremental pagination. GitHub Actions runs the tests on Windows, Linux, and macOS, with additional Windows checks for PowerShell 7 and Windows PowerShell 5.1 commands. Tests against a real Codex host are opt-in; passing Hook payload tests does not prove that the host is wired up.

</details>

---

[Quick start](#quick-start) · [Use with an Agent](#use-with-an-agent) · [Record schema](references/schema.md) · [简体中文](README.md)

<a id="copyright"></a>

Copyright (c) 2026 Moonawn.
