<div align="center">

# Change Journal

### 记录每次变更，让下一次工作有据可接。

**简体中文** · [English](README.en.md)

[一条记录](#一条记录能留下什么) · [快速开始](#快速开始) · [交给 Agent](#交给-agent) · [记录规范](references/schema.md)

[![Tests](https://github.com/Moonawn/change-journal/actions/workflows/test.yml/badge.svg)](https://github.com/Moonawn/change-journal/actions/workflows/test.yml)

</div>

**面向人和 Agent 协作的轻量变更台账。**

把实际变化、验证依据和明确待办，保存为带时间与来源的本地事件。换个会话、换个 Agent，仍能查到：改了什么、检查到哪一步、接下来做什么。

![Change Journal：变化、证据与下一步](assets/cover-zh.png)

| 本地保存 | 按需读取 | 证据可追溯 |
| :--- | :--- | :--- |
| JSON 事件与 Markdown 视图，可自行备份和版本管理 | 按项目查询少量摘要，需要时展开原事件 | 变更、验证与待办保留来源和时间 |

运行只需 Python 标准库，无需模型 API、向量数据库或常驻服务。以具体变更为主，按需附带少量有证据、注明适用条件的经验。

<a id="从一次变更开始"></a>

## 一条记录能留下什么

以下是**虚构项目的示例展示**，不含真实工作日志；实际事件另有唯一 ID、时间和来源。

| 事项：CSV 导出修复 | 记录内容 |
| :--- | :--- |
| 变更 | 修复导出文件缺少列名的问题 |
| 验证 | 隔离样例的列名、行数检查通过，附测试结果引用 |
| 待办 | 在实际应用中核对导出结果 |
| 接续 | 下个 Agent 展开原事件，核对依据后继续待办 |

**一次验证通过，只说明这次检查的范围。** 示例中的待办仍然保留。同一事项的后续变化可以追加事件、汇总成事项卡，历史依据继续可查；普通修改不必额外分类或总结经验。

<a id="安装"></a>
<a id="使用"></a>

## 快速开始

支持 **macOS、Linux、原生 Windows，Python 3.9+**；以下安装示例需要 Git。已有同名目录时，先看[更新与回退](#更新与回退)。

**macOS / Linux**

```bash
git clone https://github.com/Moonawn/change-journal.git "$HOME/.agents/skills/change-journal"
cd "$HOME/.agents/skills/change-journal"
python3 scripts/change_journal.py init
```

<details>
<summary><strong>Windows PowerShell 安装</strong></summary>

Python 需加入 PATH，也可将 `python` 换成 `py -3`。

```powershell
$skillDir = Join-Path $HOME '.agents/skills/change-journal'
git clone https://github.com/Moonawn/change-journal.git "$skillDir"
Set-Location "$skillDir"
python scripts/change_journal.py init
```

</details>

默认台账位于 `~/.local/share/change-journal/`，与程序分开。自定义位置可用环境变量 `CHANGE_JOURNAL_ROOT`，或在子命令前传入 `--root /absolute/journal-directory`。各接入方需要共享记录时，应使用同一个选定的台账目录和同一版本的写入程序。

## 交给 Agent

让 Agent 加载 [SKILL.md](SKILL.md)，再交代记录与续接需求。安装位置和自动加载方式取决于宿主，复制目录后仍需确认 Skill 已被发现。

> 使用 change-journal。完成本次修改并验证后，记录改动、验证依据和明确待办；下次继续时，先按项目路径查询相关记录。

<a id="适配器与边界"></a>

| 接入方式 | 当前提供 | 需要确认的边界 |
| :--- | :--- | :--- |
| 通用 Skill / CLI | 读写同一份本地台账 | Claude Code、Cursor、Kimi Code 等需允许执行 Python；Skill 发现与宿主自动接入未逐一验证 |
| Codex Hook | 工具变化观察、轮次关联与机械兜底 | 需单独配置并信任 Hook；自动测试不等于本机已接线 |
| Hermes 适配器 | 接收 JSON，写入同一事件格式 | 需接入实际调用方；适配器测试不代表所有 Hermes 部署已验证 |

多个 Agent 指向同一台账、拥有相应文件权限，就能查询彼此留下的变更证据。它们仍需主动查询；这不会自动传递会话上下文，也不会让 Agent 自动互相调用。不同机器需要另行同步台账，各工具的原生聊天日志仍由各自管理。

Hook 只提供变化线索，完成事实由语义记录表达。[接入说明与验证边界](references/schema.md#适配器与隐私)包含配置示例。Skill 和详细规范目前为中文。

## 查阅与接续

**给 Agent：先查相关摘要，再展开证据。**

```bash
python3 scripts/change_journal.py query --path /absolute/project --format text
python3 scripts/change_journal.py show EVENT_ID
```

替换项目路径和事件 ID。默认阅读摘要最多 **3 项、2000 个字符**，明确标出省略与证据不全；字符数不等于 token 数。看到 `partial` 时先用 `doctor` 查异常，不能据此认定待办已关闭。完整 JSON、预算调整与增量游标见[查询接口](references/schema.md#查询接口)。

**给人：需要审阅时生成 Markdown。**

```bash
python3 scripts/change_journal.py render
```

打开台账目录中的 `views/00_审阅入口_REVIEW.md`，从最近 8 个事项或独立记录进入。视图反映上次生成的状态；`query/show` 直接读当前事件，普通记账无需每次生成全部视图。

以上命令以仓库目录为起点；Agent 实际调用应使用脚本的绝对路径，并保留任务工作目录，以便 Hook 关联。Windows 使用 `python` 或 `py -3` 替换 `python3`，项目路径可写为 `C:/Projects/demo`。PowerShell 5.1 管道读取文本出现乱码时，先设置 `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)`。

手动记账可运行 `python3 scripts/change_journal.py record --help`，并参考 [Skill 中的最小记录示例](SKILL.md#记录)。同一写入方重试时复用幂等键与内容；新的变更阶段用新键，同一事项可保留 `work_id`。跨 Agent 的独立证据不会被当作重复记录删除。

<a id="记录结构"></a>

## 数据与边界

台账中的主要文件：

```text
台账目录/
├── events/YYYY/MM/*.json   追加保存的原始事件
├── views/                 可重新生成的 Markdown 审阅视图
├── registry.json          路径与对象的对应关系
└── work-items.json        可选的事项归类
```

程序另行维护去重索引和 Hook 中间数据，详见[事实与派生视图](references/schema.md#事实与派生视图)。初始化只登记 Skill 与台账自身的路径；业务对象按需配置，未匹配路径保留为 `UNASSIGNED`，仍可按路径查询。

公共仓库保存程序、规范与测试。真实日志保存在你选定的台账目录；记录者只应写入合适的摘要和证据引用。`.gitignore` 与文本脱敏是补充保护，不能保证任意输入都可安全公开。Git 备份或跨机同步由你自行选择和配置，安装不会自动上传日志。

实验脚本 `trace_collect.py` / `trace_sources.py` 当前仅实现只读 `survey`；`collect/promote/prune` 尚未实现，不参与默认流程。

## 更新与回退

更新前保留当前程序目录、台账位置及自定义接入配置。Git 安装可用 `git rev-parse HEAD` 记下当前版本；仅在 `git status --short` 为空、无本地定制的克隆目录中运行 `git pull --ff-only`。已有修改或手动安装时，在另一目录检查新版本，保留原安装再切换入口。

更新后用 `doctor` 和一次目标查询核对台账，再让各接入方使用同一版本。回退时切回保留的程序和接入配置，台账事件仍保留；旧程序须支持现有事件格式，确认兼容前不要用旧版本继续写入。程序回退不会撤销已经完成的业务变更。

<a id="验证"></a>

## 验证与 CI

**CI（Continuous Integration，持续集成）就是每次代码更新后自动运行检查。** 本仓库已有 [GitHub Actions](https://github.com/Moonawn/change-journal/actions/workflows/test.yml)，在 Windows、Linux、macOS 上运行测试，并验证 PowerShell 7 与 Windows PowerShell 5.1 命令。它在 GitHub 执行，不参与日常记账，也不增加 Agent 的上下文负担。

测试覆盖记录、并发、去重冲突、跨系统路径、中文输出、事项与身份边界、待办和经验、查询预算及增量分页。测试使用隔离临时目录；真实 Codex 宿主测试需另行启用，默认 CI 不验证你本机的 Hook 加载和信任状态。

<details>
<summary><strong>在本地运行测试</strong></summary>

macOS / Linux：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Windows PowerShell：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -v
```

</details>

---

[快速开始](#快速开始) · [交给 Agent](#交给-agent) · [记录规范](references/schema.md) · [English](README.en.md)

<a id="版权"></a>

Copyright (c) 2026 Moonawn。
