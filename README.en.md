<div align="center">

# Change Journal

### Record each change. Give the next session a place to start.

[简体中文](README.md) · **English**

[An example](#what-a-record-keeps) · [Quick start](#quick-start) · [Use with an Agent](#use-with-an-agent) · [Record schema](references/schema.md)

[![Tests](https://github.com/Moonawn/change-journal/actions/workflows/test.yml/badge.svg)](https://github.com/Moonawn/change-journal/actions/workflows/test.yml)

</div>

**A lightweight change ledger for people and AI agents.**

Save actual changes, verification evidence and explicit follow-ups as local events with timestamps and sources. Continue in another session or with another agent knowing what changed, what was checked and what comes next.

![Change Journal: changes, evidence and next steps](assets/cover-en.png)

| Local files | Focused reads | Traceable evidence |
| :--- | :--- | :--- |
| JSON events and Markdown views you can back up and version | Query brief project summaries; open original events when needed | Changes, checks and follow-ups retain their sources and timestamps |

Runtime uses only the Python standard library. No model API, vector database or background service is required. Focus on concrete changes, with a small number of evidence-backed lessons and their applicability conditions when useful.

<a id="start-with-a-change"></a>

## What a record keeps

This is a **human-readable example for a fictional project**, with no real work logs. Actual events also have a unique ID, timestamps and a source.

| Work item: CSV export fix | Recorded information |
| :--- | :--- |
| Change | Fixed missing column headers in exported files |
| Verification | Header and row-count checks passed on an isolated sample, with a test-result reference |
| Follow-up | Check the export in the actual application |
| Continuation | The next agent opens the original event, checks the evidence and continues the follow-up |

**A passed check covers only what was tested.** The follow-up in this example remains open. Further changes can be appended and grouped into a work-item card while preserving earlier evidence. Routine edits do not require extra classification or a lesson.

<a id="installation"></a>
<a id="usage"></a>

## Quick start

Supports **macOS, Linux and native Windows, with Python 3.9+**. These installation examples require Git. If the directory already exists, see [Updating and rolling back](#updating-and-rolling-back).

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

The default journal is `~/.local/share/change-journal/`, separate from the program. Override it with `CHANGE_JOURNAL_ROOT`, or pass `--root /absolute/journal-directory` before the subcommand. Integrations sharing records should use the chosen shared journal directory and the same writer version.

## Use with an Agent

Ask your agent to load [SKILL.md](SKILL.md), then describe what to record and how to continue. Installation paths and automatic loading depend on the host; confirm that it discovers the Skill after copying the directory.

> Use change-journal. After completing and verifying this change, record what changed, the verification evidence and explicit follow-ups. When continuing later, query the relevant records by project path first.

<a id="adapters-and-boundaries"></a>

| Integration | Available now | What still needs checking |
| :--- | :--- | :--- |
| Generic Skill / CLI | Read and write the same local journal | Hosts such as Claude Code, Cursor and Kimi Code must permit Python execution; Skill discovery and automatic integration have not been verified for each tool |
| Codex Hook | Observe tool changes, correlate turns and retain fallback observations | Requires separate setup and trust; automated tests do not mean your local hooks are connected |
| Hermes adapter | Accept JSON and write the shared event format | Requires a caller integration; adapter tests do not verify every Hermes deployment |

Agents with access to the same journal directory can query each other's change evidence. They must still query it explicitly: session context is not transferred automatically, and agents do not automatically invoke one another. Separate machines need a way to synchronize the journal; each tool continues to manage its native chat logs.

Hooks provide observations; semantic records describe completed work. See [integration instructions and verification boundaries](references/schema.md#适配器与隐私) for configuration examples. The Skill and detailed schema are currently in Chinese.

## Review and continue

**For agents: query relevant summaries, then open evidence.**

```bash
python3 scripts/change_journal.py query --path /absolute/project --format text
python3 scripts/change_journal.py show EVENT_ID
```

Replace the project path and event ID. Reading summaries default to at most **3 items and 2,000 characters**, with omissions and incomplete evidence marked. Characters are not tokens. If a result is `partial`, use `doctor` to investigate before drawing conclusions about closed follow-ups. See the [query interface](references/schema.md#查询接口) for full JSON, output budgets and incremental cursors.

**For people: generate Markdown when you want to review.**

```bash
python3 scripts/change_journal.py render
```

Open `views/00_审阅入口_REVIEW.md` in the journal directory, starting with the 8 most recent work items or standalone records. Views reflect the last render; `query/show` read current events directly. Routine recording does not need to rebuild all views.

These commands start in the repository directory. Agents should use the script's absolute path while preserving the task working directory for Hook correlation. On Windows, replace `python3` with `python` or `py -3`; project paths can use `C:/Projects/demo`. For garbled text captured in a PowerShell 5.1 pipeline, first set `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)`.

For manual recording, run `python3 scripts/change_journal.py record --help` and follow the [minimal Skill example](SKILL.md#记录). A retry from the same producer reuses its idempotency key and content. A new stage uses a new key while keeping the same `work_id` where appropriate. Independent evidence from different agents is not deleted as a duplicate.

<a id="journal-structure"></a>

## Data and boundaries

The main journal files:

```text
journal/
├── events/YYYY/MM/*.json   Original events, appended over time
├── views/                 Rebuildable Markdown review views
├── registry.json          Paths mapped to scopes
└── work-items.json        Optional work-item classification
```

The program also maintains deduplication indexes and intermediate Hook data; see [facts and derived views](references/schema.md#事实与派生视图). Initialization registers only the Skill and journal paths. Configure other scopes as needed; unmatched paths remain `UNASSIGNED` and can still be queried by path.

The public repository contains code, specifications and tests. Real logs stay in your chosen journal directory. Record only suitable summaries and evidence references: `.gitignore` and text redaction are supplementary protections, not guarantees that arbitrary input is safe to publish. Git backups and synchronization are yours to configure; installation does not upload logs automatically.

The experimental `trace_collect.py` / `trace_sources.py` scripts currently implement only read-only `survey`. `collect/promote/prune` are unimplemented and are outside the default workflow.

## Updating and rolling back

Before updating, preserve the current program directory, journal location and custom integration settings. For a Git installation, note the current version with `git rev-parse HEAD`. Run `git pull --ff-only` only in a clone with an empty `git status --short` and no local customizations. For modified or manually installed copies, inspect the new version in another directory and retain the original before switching the entry point.

After updating, run `doctor` and a query for your target, then align all integrations on the same writer version. To roll back, restore the retained program and integration settings while keeping journal events. The older program must support the current event format; do not resume writes with it until compatibility is confirmed. Rolling back the program does not undo business changes already made.

<a id="verification"></a>

## Verification and CI

**CI (Continuous Integration) runs checks automatically when code changes.** This repository already uses [GitHub Actions](https://github.com/Moonawn/change-journal/actions/workflows/test.yml) to test Windows, Linux and macOS, including PowerShell 7 and Windows PowerShell 5.1 commands. It runs on GitHub, outside daily recording, and adds no agent context overhead.

Tests cover recording, concurrency, deduplication conflicts, cross-platform paths, Unicode output, work items and identity boundaries, follow-ups and lessons, query budgets and incremental paging. They use isolated temporary directories. Real Codex host tests are opt-in; default CI does not verify your machine's Hook loading or trust state.

<details>
<summary><strong>Run tests locally</strong></summary>

macOS / Linux:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Windows PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -v
```

</details>

---

[Quick start](#quick-start) · [Use with an Agent](#use-with-an-agent) · [Record schema](references/schema.md) · [简体中文](README.md)

<a id="copyright"></a>

Copyright (c) 2026 Moonawn.
