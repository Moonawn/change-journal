# Change Journal

为 Codex、Hermes 和其他 Agent 保存可核查的变更记录，并按对象和事项查询历史、明确待办与可复用经验。

同一事项可以产生多条事件，日常入口汇总为一张事项卡。原始事件追加保存；事项、待办和经验视图可以重新生成。

## 安装

运行环境：macOS 或 Linux，Python 3.9+。运行和测试仅使用 Python 标准库；文件锁使用 POSIX `fcntl`，Windows 原生环境尚未适配。

```bash
git clone https://github.com/Moonawn/change-journal.git "$HOME/.agents/skills/change-journal"
python3 "$HOME/.agents/skills/change-journal/scripts/change_journal.py" init
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
python3 scripts/change_journal.py query --path /absolute/project --limit 5
python3 scripts/change_journal.py query --work-id export-fix --limit 5
python3 scripts/change_journal.py render
python3 scripts/change_journal.py doctor
```

对象路由使用台账 `registry.json` 中的最长路径前缀。初始化只注册 Skill 和台账本身；业务目录按需添加，未匹配的目标保留为 `UNASSIGNED`。

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

`trace_collect.py` 和 `trace_sources.py` 是可选实验工具，目前仅 `survey` 实现了只读统计；`collect/promote/prune` 尚未实现。它们不参与默认记录流程。

仓库包含程序、规范和测试。真实事件、归类配置、备份及会话痕迹存放在本地台账目录；`.gitignore` 也排除了这些运行数据。文本脱敏只是补充检查，不能代替记录者选择合适的摘要与证据引用。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

测试使用隔离临时目录，覆盖追加记录、幂等写入、对象路由、事项汇总、身份边界、待办关系、经验版本和归类失败时的事实保留。

## 许可证

本项目采用 [MIT License](LICENSE)。Copyright (c) 2026 Moonawn。
