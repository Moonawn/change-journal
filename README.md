<div align="center">

# Change Journal

### 记录每次变更，让下一次工作有据可接。

**简体中文** · [English](README.en.md)

[快速开始](#快速开始) · [交给 Agent](#交给-agent) · [查阅记录](#查阅与接续) · [记录规范](references/schema.md)

[![Tests](https://github.com/Moonawn/change-journal/actions/workflows/test.yml/badge.svg)](https://github.com/Moonawn/change-journal/actions/workflows/test.yml)

</div>

**面向人和 Agent 协作的轻量变更台账。**

换个会话继续工作时，需要知道：改了什么、验证到哪一步、还有什么没完成。

Change Journal 把实际变化、验证依据和未决事项保存为带时间与来源的事件，供后续追溯、审阅和跨会话接续。

![Change Journal：变化、证据与下一步](assets/cover-zh.png)

## 从一次变更开始

| 工作进展 | 记录后可以查到 |
| :--- | :--- |
| 完成一次修改 | 具体变化、修改原因与涉及的对象 |
| 核对执行结果 | 验证依据、检查结果与已知限制 |
| 在下一次会话继续 | 同一事项的进展、明确待办与原始证据 |

同一事项可以产生多条事件，日常入口汇总为一张事项卡。以具体变更为主；只有已经核实、确有复用价值时，才附带少量有证据的经验，并保留适用条件与原事件的关联。

<a id="安装"></a>

## 快速开始

支持 **macOS、Linux、原生 Windows，Python 3.9+**。运行和测试仅使用 Python 标准库；下方安装示例需要 Git。

安装目录已有同名 Skill 时，先保留原目录，再选择单独位置检查新版本。也可以安装到其他 Agent 支持的 Skills 目录。

**macOS / Linux**

```bash
git clone https://github.com/Moonawn/change-journal.git "$HOME/.agents/skills/change-journal"
cd "$HOME/.agents/skills/change-journal"
python3 scripts/change_journal.py init
```

<details>
<summary><strong>Windows PowerShell 安装</strong></summary>

Python 需要加入 PATH；也可将 `python` 换成 `py -3`。

```powershell
$skillDir = Join-Path $HOME '.agents/skills/change-journal'
git clone https://github.com/Moonawn/change-journal.git "$skillDir"
Set-Location "$skillDir"
python scripts/change_journal.py init
```

</details>

默认台账保存在 `~/.local/share/change-journal/`，与 Skill 源码分开。可以通过 `CHANGE_JOURNAL_ROOT` 自定义目录，或在子命令前传入 `--root /absolute/journal-directory`。

## 交给 Agent

[查看 Skill](SKILL.md) · [记录规范](references/schema.md)

让你使用的 Agent 加载 [SKILL.md](SKILL.md)，然后用自然语言交代记录与续接需求：

> 使用 change-journal。完成本次修改并验证后，记录改动、验证依据和明确待办；下次继续时，先按项目路径查询相关记录。

多个 Agent 使用同一个台账根目录时，可以查询彼此留下的变更证据。不同机器仍需另行同步台账；安装 Skill 不会同步各工具的原生聊天日志。具体加载方式取决于 Agent 宿主，Hook 也需要单独接入。

<a id="使用"></a>

## 查阅与接续

人工审阅从台账里的 `views/00_审阅入口_REVIEW.md` 开始，默认汇总最近 8 个事项或独立记录；展开事项卡查看进展、待办与依据。

以下命令在 Skill 仓库目录运行。将示例路径换成实际项目路径；Windows 使用 `python` 或 `py -3` 替换 `python3`。

```bash
python3 scripts/change_journal.py query --path /absolute/project
```

默认最多返回 **3 张摘要**，完整 JSON 输出最多 **6000 个字符**，省略内容会计数。需要完整事实时，再按返回的事件 ID 展开。

<details>
<summary><strong>展开事项、调整输出量与增量查询</strong></summary>

```bash
python3 scripts/change_journal.py query --work-id export-fix --detail
python3 scripts/change_journal.py show EVENT_ID
python3 scripts/change_journal.py render
python3 scripts/change_journal.py doctor
```

默认查询最多 3 张摘要，并将完整 JSON 输出限制在 6000 个字符内（含 Unicode 转义和换行，不等于精确 token 数）。`--limit` 和 `--max-chars` 可调整；省略内容有计数，完整事实用 `show EVENT_ID`，旧版详细查询结构用 `--detail`，详细模式不受摘要预算限制。

同一上下文可用 `query --path /absolute/project --cursor "上一响应的cursor"` 只取新增证据。返回 `delta` 且 `has_more: true` 时继续传返回的 cursor，直到分页结束；无变化为 `unchanged`。新会话省略 cursor 取得新摘要。cursor 不代表全部历史已读，不能作为另一 Agent 已了解事项的证明；补录旧时间、关闭待办、退役经验均会检测，旧事实或归类变化会重置摘要。实现只做本地读取和哈希，没有模型调用或新增缓存目录。

</details>

<details>
<summary><strong>手动记录一次变更</strong></summary>

下面以一次已完成验证的修改为例；实际使用时替换路径、摘要和证据。同一目标分多次推进时复用 `work-id`。

```bash
python3 scripts/change_journal.py record \
  --actor codex --source semantic --phase verified \
  --summary "修复导出错误并通过隔离验证" \
  --primary-path /absolute/project \
  --changed-path /absolute/project/export.py \
  --work-id export-fix --work-title "导出错误修复" \
  --verification-status passed --verification-ref "对应测试通过" \
  --idempotency-key export-fix-verified --render
```

Windows PowerShell（反引号用于续行，末尾不要加空格）：

```powershell
python scripts/change_journal.py record `
  --actor codex --source semantic --phase verified `
  --summary '修复导出错误并通过隔离验证' `
  --primary-path 'C:/Projects/demo' `
  --changed-path 'C:/Projects/demo/export.py' `
  --work-id export-fix --work-title '导出错误修复' `
  --verification-status passed --verification-ref '对应测试通过' `
  --idempotency-key export-fix-verified --render
python scripts/change_journal.py query --path 'C:/Projects/demo'
python scripts/change_journal.py doctor
```

重试复用相同幂等键和记录内容；键在 `actor/source/host/profile` 内隔离。相同键携带不同内容会报冲突，需核对原事件；真正的新阶段使用新键，并保留事项 `work-id`。

</details>

## 数据与边界

原始事件以 JSON 追加保存；Markdown 审阅视图可以重新生成。记录包含变更摘要与证据引用，保留回到原始依据的线索。

仓库包含程序、规范和测试。真实事件、归类配置、备份及会话痕迹存放在本地台账目录；`.gitignore` 也排除了这些运行数据。文本脱敏只是补充检查，不能代替记录者选择合适的摘要与证据引用。

<a id="记录结构"></a>

<details>
<summary><strong>目录结构与记录规则</strong></summary>

```text
台账目录/
├── registry.json                 路径 → 对象 scope
├── work-items.json               可选的历史归类与特定事项绑定
├── events/YYYY/MM/*.json         原始事件，追加保存
├── views/
│   ├── 00_审阅入口_REVIEW.md      日常入口，最近 8 个事项或独立记录
│   ├── 02_事项索引_WORK_ITEMS.md  已归组事项
│   ├── by-work/                  每件事的进度、待办与依据
│   ├── 00_待续接_CONTINUATIONS.md 明确剩余动作
│   ├── 01_可复用经验_LESSONS.md   当前有效经验
│   ├── 99_历史未决待核_LEGACY.md  待核实的旧备注
│   ├── by-scope/                 按对象追溯
│   ├── daily/                    按日期追溯
│   └── 00_全局变更时间线_CHANGELOG.md
├── idempotency/                  防止重复写入
└── .spool/                       机械观察与中间数据
```

- `scope` 区分对象，`work_id` 区分具体事项，`lesson.key` 区分可复用经验。
- 同一事项按 `scope + host + profile + work_id` 分组，保留身份边界。
- `follow_ups` 保存明确动作，`limitations` 保存限制；旧 `unresolved` 进入历史待核。
- 引用事件不会自动关闭待办；明确解决或接手时使用 `resolves_event_ids` / `supersedes_event_ids`。
- 经验注明条件、检查方法和证据，可以更新或退役，不自动变成全局规则。
- 当前事件 schema 为 `1.2`，兼容读取 `1.0`、`1.1`。事件时间存 UTC，日期视图按 Asia/Shanghai 分组。

字段和适配器说明见 [记录规范](references/schema.md)，Agent 使用入口见 [SKILL.md](SKILL.md)。

对象路由使用台账 `registry.json` 中的最长路径前缀。初始化只注册 Skill 和台账本身；业务目录按需添加，未匹配的目标保留为 `UNASSIGNED`。

并发写入由文件锁保护：POSIX 使用 `fcntl`，Windows 使用 `msvcrt` 字节锁。

Windows 盘符路径和 UNC 路径均可使用，匹配时忽略大小写并统一分隔符；POSIX 路径仍区分大小写。可以在另一系统查询已有路径记录。`registry.json` 中 Windows 路径建议写为 `C:/Projects/demo`；反斜线按 JSON 语法写成 `\\`。事件与视图保存为 UTF-8，CLI JSON 使用 Unicode 转义以兼容重定向和旧控制台编码。

</details>

<a id="适配器与边界"></a>

<details>
<summary><strong>适配器与实验功能</strong></summary>

`scripts/hermes_adapter.py` 从 stdin JSON 接收语义记录；`scripts/codex_hook.py` 提供工具变化观察和机械兜底。安装仓库不会自动配置 Hook、创建定时任务或接入外部服务。

`trace_collect.py` 和 `trace_sources.py` 是可选实验工具，目前仅 `survey` 实现了只读统计；`collect/promote/prune` 尚未实现。它们不参与默认记录流程；Cindy 数据发现仍使用 macOS 的默认目录。

</details>

## 验证

[GitHub Actions](https://github.com/Moonawn/change-journal/actions/workflows/test.yml) 覆盖 Windows、Linux 和 macOS。

<details>
<summary><strong>测试命令与覆盖范围</strong></summary>

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Windows PowerShell：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -v
```

测试使用隔离临时目录，覆盖追加记录、并发与幂等冲突、跨系统路径、中文输出、Hook 子进程去重、事项汇总、身份边界、待办关系、经验版本、摘要预算和增量分页。GitHub Actions 在 Windows、Linux、macOS 运行测试，并在 Windows 验证 PowerShell 7 和 Windows PowerShell 5.1 命令。真实 Codex 宿主测试另行选择运行，不能由 Hook payload 测试推断宿主已经接线。

</details>

---

[快速开始](#快速开始) · [交给 Agent](#交给-agent) · [记录规范](references/schema.md) · [English](README.en.md)

<a id="版权"></a>

Copyright (c) 2026 Moonawn。
