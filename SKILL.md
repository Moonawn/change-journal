---
name: change-journal
description: 记录持久变更及验证证据，按目标查询历史、明确待办和可复用经验。用于修改、修复、部署后的留痕，以及查改动、排障续接；纯只读问答且未要求 checkpoint 时不记录。
---

# Change Journal

记录可核查的变更，接续时只取相关证据。原始事件只追加；身份隔离、幂等与校验由程序处理。只记安全摘要和证据引用，不收录秘密、私人聊天、原始命令或完整工具输出；Hook/trace 不冒充完成事实，日志不代替授权，不自动外部同步或写入全局规则。

## 记录

持久变更完成、失败或中断时按真实状态记一条；同一任务的中间步骤合并。只读、临时产物不单独记，明确要求 checkpoint 时例外。

使用实际脚本绝对路径，在任务工作目录运行；actor 使用实际 Agent 名称：

```bash
python3 /absolute/change-journal/scripts/change_journal.py record \
  --actor codex --source semantic --phase verified \
  --summary "实际变化及结果" --primary-path /absolute/target \
  --changed-path /absolute/changed/file \
  --verification-status passed --verification-ref "检查证据" \
  --idempotency-key "稳定的任务及阶段标识"
```

普通修改默认 `brief`；规则、运行行为、共享接口、部署或权限变化用 `--record-level full --why ... --rollback-ref ...`。`applied` 仅表示已写入，`verified` 需检查通过，`active` 还需运行态加载证据；局部通过须说明范围。

明确待办用 `--follow-up`，限制或建议用 `--limitation`，没有就省略。重试复用同一幂等键和内容；新变化用新键。保存失败说明留痕缺口，不重复执行业务变更。

## 查询

```bash
python3 /absolute/change-journal/scripts/change_journal.py query --path /absolute/target --format text
```

默认最多 3 项、2000 字符，保留身份、验证、待办和省略提示；按 `show EVENT_ID` 取完整证据。最新事件通过验证不代表整件事完成。`partial` 表示证据不全，先用 `doctor` 定位异常，不推断待办已关闭。

查询直接读取事件。需要人工审阅时运行 `render`，再读台账的 `views/00_审阅入口_REVIEW.md`；普通记账不必重建全部视图。仍支持 `record --render`，视图更新失败只重试 `render`。

## 按需展开

- 多次推进或关联旧事项：读 [事项归类](references/schema.md#对象事项与历史归类)；关闭待办读 [关系语义](references/schema.md#字段与记录成本)，沉淀经验读 [经验](references/schema.md#可复用经验)。普通修改不必归类或总结经验。
- 程序调用、完整 JSON 或增量游标：读 [查询接口](references/schema.md#查询接口)。
- 安装、台账位置或 Windows 命令：读 [README](README.md#使用)。Python 3.9+；Windows 可用 `python` 或 `py -3`。
