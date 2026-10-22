---
name: change-journal
description: 记录持久变更及验证证据，按目标查询历史、明确待办和可复用经验。用于修改、修复、部署后的留痕，以及查改动、排障续接；纯只读问答且未要求 checkpoint 时不记录。
---

# Change Journal

保留可核查的变更事实，让当前任务只读相关记录。原始事件 append-only；审阅视图可重建。台账不代替授权判断，不自动写入 Wiki、全局规则或外部服务。

## 记录多少

| 情况 | 做法 |
|---|---|
| 只读、缓存、临时产物、同一任务的中间修改 | 不单独记；明确要求 checkpoint 时例外 |
| 普通交付物或局部可逆的持久修改 | `brief`：摘要、目标、验证即可 |
| 规则/Skill、运行行为、共享接口、部署或权限变化 | `full`：补充原因、影响和回滚线索 |

按影响选择，不按文件数选择；等级不增加审批。默认合并同一任务的中间步骤；独立归属、独立验收的变化可分别记录。完成、失败或中断都按真实状态留痕，不为填写字段额外制造工作。

## 记录入口

默认台账位于 `~/.local/share/change-journal`，可用 `CHANGE_JOURNAL_ROOT` 或全局 `--root` 参数指定。首次运行 `python3 <Skill目录>/scripts/change_journal.py init`；实际安装位置不同时调整下方脚本路径。对象路由按需维护在台账的 `registry.json`。

验证完成后、最终回复前调用一次；`--render` 同时更新视图：

```bash
python3 "$HOME/.agents/skills/change-journal/scripts/change_journal.py" record \
  --actor codex --source semantic --phase verified \
  --summary "实际变化及结果" --primary-path /absolute/target \
  --changed-path /absolute/changed/file \
  --verification-status passed --verification-ref "与影响相称的检查证据" \
  --idempotency-key "稳定的任务及阶段标识" --render
```

- `primary-path` 是业务目标或所属目录，避免备份路径抢占 scope；多个候选无法核实时保留未知，不猜归属。`kind` 可选 `artifact/code/runtime/policy/checkpoint`，不要求细分标签。
- 同一目标需要多次推进时，首次给 `--work-id` 稳定标识和 `--work-title` 简短事项名；后续复用查询返回的 ID。独立目标用新 ID，普通单次修改可省略。明确 resolves/supersedes 唯一事项时会在同一对象、主机和 Profile 内继承；普通引用不会合并事项。
- 完整记录补 `--record-level full --why ... --rollback-ref ...`；仅在存在内容时填写。
- **真正承诺继续的动作**用 `--follow-up`；已知限制、未授权扩展或未来建议用 `--limitation`。没有待办就省略，不写“无”。旧 `unresolved` 兼容保留在历史待核，不自动认作当前工作。
- 普通关联用 `--related-event-id`；全部解决旧事件的待办用 `--resolves-event-id`；部分接手用 `--supersedes-event-id` 并列出仍未完成的动作。不得因新增关联就宣称已解决。
- `applied` 表示已写入；`verified` 要有实际检查或测试；`active` 还要有运行态加载证据。文档可用差异、链接或内容核对验证。局部通过时说明范围，不宣称整体通过。
- 事件保存成功、视图更新失败时只重试 `render`。记录本身失败则说明留痕缺口，使用同一幂等键补记，不重复执行业务变更。

## 按需查询与复用

遇到续接、复发问题或需要复用历史经验时，按具体目标读取，默认返回少量相关项：

```bash
python3 "$HOME/.agents/skills/change-journal/scripts/change_journal.py" query --path /absolute/target --limit 5
```

人工审阅从台账 `views/00_审阅入口_REVIEW.md` 开始，默认按事项汇总最近 8 项；展开事项卡查看全部依据。续接可用 `query --work-id <ID>`，跨身份同名事项分别返回。查询若标为 `partial`，只使用已读事实，先用 `doctor` 定位缺失证据，不据此判定待办或经验状态。事实、明确待办、历史待核和经验分开显示；最新记录不等于整件事完成，旧状态仍需结合当前证据核实。

只有已经核实、下次遇到相同条件确实有用的教训，才附加一条 `lesson`（适用条件＋具体检查＋原事件证据）；普通成功不必总结经验。同一 scope/host/profile/key 更新或退役，避免不断追加同义教条。仅在需要记录经验、纠正历史归类、关联状态或接入适配器时读 [schema.md](references/schema.md)。

只记摘要和安全证据引用，不收录秘密、私人聊天、原始命令或完整工具输出。身份边界按 scope、host、profile 保留；Hook/trace 只是机械线索，不冒充完成事实。工作分析与外部同步按用户要求另行处理。
