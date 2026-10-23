**简体中文** · [English](README.en.md)

![Change Journal：柔和雕塑封面](assets/cover-zh.png)

# Change Journal

**面向人和 Agent 协作的轻量变更台账。**

把工作中的实际变化、验证依据和未决事项保存为带时间与来源的事件，供后续追溯、审阅和跨会话接续。

| 改了什么 | 如何验证 | 接下来做什么 |
|---|---|---|
| 具体变化与原因 | 检查结果与证据 | 明确待办与限制 |

以具体变更为主；只有已经核实、确有复用价值时，才附带少量有证据的经验。经验注明适用条件，保留与原事件的关联。

同一事项可以产生多条事件，日常入口汇总为一张事项卡。原始事件追加保存；事项、待办和经验视图可以重新生成。

## 安装

运行环境：macOS、Linux 或原生 Windows，Python 3.9+。运行和测试仅使用 Python 标准库；POSIX 使用 `fcntl` 文件锁，Windows 使用 `msvcrt` 字节锁保护并发写入。

macOS / Linux：

```bash
git clone https://github.com/Moonawn/change-journal.git "$HOME/.agents/skills/change-journal"
python3 "$HOME/.agents/skills/change-journal/scripts/change_journal.py" init
```

Windows PowerShell（Python 已加入 PATH；也可将 `python` 换成 `py -3`）：

```powershell
$skillDir = Join-Path $HOME '.agents/skills/change-journal'
git clone https://github.com/Moonawn/change-journal.git "$skillDir"
python "$skillDir/scripts/change_journal.py" init
```

安装目录已有同名 Skill 时，先保留原目录，再选择单独位置检查新版本；不要覆盖正在使用的个人配置。也可以将仓库安装到其他 Agent 支持的 Skill 目录。

默认台账目录是 `~/.local/share/change-journal`，与 Skill 源码分开。可设置 `CHANGE_JOURNAL_ROOT`，或在子命令前传入 `--root /absolute/journal-directory`。

## 使用

以下命令在仓库根目录运行。

记录一次经过验证的变更；多次推进同一目标时复用 `work-id`：

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

```bash
python3 scripts/change_journal.py query --path /absolute/project
python3 scripts/change_journal.py query --work-id export-fix --detail
python3 scripts/change_journal.py show EVENT_ID
python3 scripts/change_journal.py render
python3 scripts/change_journal.py doctor
```

对象路由使用台账 `registry.json` 中的最长路径前缀。初始化只注册 Skill 和台账本身；业务目录按需添加，未匹配的目标保留为 `UNASSIGNED`。

默认查询最多 3 张摘要，并将完整 JSON 输出限制在 6000 个字符内（含 Unicode 转义和换行，不等于精确 token 数）。`--limit` 和 `--max-chars` 可调整；省略内容有计数，完整事实用 `show EVENT_ID`，旧版详细查询结构用 `--detail`，详细模式不受摘要预算限制。

同一上下文可用 `query --path /absolute/project --cursor "上一响应的cursor"` 只取新增证据。返回 `delta` 且 `has_more: true` 时继续传返回的 cursor，直到分页结束；无变化为 `unchanged`。新会话省略 cursor 取得新摘要。cursor 不代表全部历史已读，不能作为另一 Agent 已了解事项的证明；补录旧时间、关闭待办、退役经验均会检测，旧事实或归类变化会重置摘要。实现只做本地读取和哈希，没有模型调用或新增缓存目录。

重试复用相同幂等键和记录内容；键在 `actor/source/host/profile` 内隔离。相同键携带不同内容会报冲突，需核对原事件；真正的新阶段使用新键，并保留事项 `work-id`。多个 Agent 使用同一个台账根目录即可查询彼此的证据；不同机器仍需另行同步台账，安装 Skill 不会同步各工具原生聊天日志。

Windows PowerShell 示例（反引号用于续行，末尾不要加空格）：

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

Windows 盘符路径和 UNC 路径均可使用，匹配时忽略大小写并统一分隔符；POSIX 路径仍区分大小写。可以在另一系统查询已有路径记录。`registry.json` 中 Windows 路径建议写为 `C:/Projects/demo`；反斜线按 JSON 语法写成 `\\`。事件与视图保存为 UTF-8，CLI JSON 使用 Unicode 转义以兼容重定向和旧控制台编码。

## 记录结构

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

## 适配器与边界

`scripts/hermes_adapter.py` 从 stdin JSON 接收语义记录；`scripts/codex_hook.py` 提供工具变化观察和机械兜底。安装仓库不会自动配置 Hook、创建定时任务或接入外部服务。

`trace_collect.py` 和 `trace_sources.py` 是可选实验工具，目前仅 `survey` 实现了只读统计；`collect/promote/prune` 尚未实现。它们不参与默认记录流程；Cindy 数据发现仍使用 macOS 的默认目录。

仓库包含程序、规范和测试。真实事件、归类配置、备份及会话痕迹存放在本地台账目录；`.gitignore` 也排除了这些运行数据。文本脱敏只是补充检查，不能代替记录者选择合适的摘要与证据引用。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Windows PowerShell：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -v
```

测试使用隔离临时目录，覆盖追加记录、并发与幂等冲突、跨系统路径、中文输出、Hook 子进程去重、事项汇总、身份边界、待办关系、经验版本、摘要预算和增量分页。GitHub Actions 在 Windows、Linux、macOS 运行测试，并在 Windows 验证 PowerShell 7 和 Windows PowerShell 5.1 命令。真实 Codex 宿主测试另行选择运行，不能由 Hook payload 测试推断宿主已经接线。

## 版权

Copyright (c) 2026 Moonawn。
