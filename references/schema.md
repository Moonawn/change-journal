# Change Journal 事件规范

## 事实与派生视图

`events/YYYY/MM/*.json` 是 append-only 事实源；既有事件不修改、不删除。当前写入 `schema_version: 1.2`，兼容读取 `1.0/1.1`。`views/`、`idempotency/`、`.spool/` 是可重建数据。`registry.json` 与可选的 `work-items.json` 是归类配置，修改前需保留可回滚版本。日期视图按 Asia/Shanghai 分组，事件时间仍存 UTC。

| 用途 | 入口 |
|---|---|
| 少量近期变化、计数与导航 | `views/00_审阅入口_REVIEW.md` |
| 明确仍需做的动作 | `views/00_待续接_CONTINUATIONS.md` |
| 有证据、注明适用条件的经验 | `views/01_可复用经验_LESSONS.md` |
| 已归组事项及其全部依据 | `views/02_事项索引_WORK_ITEMS.md`、`views/by-work/` |
| 旧未决备注，尚未重新核实 | `views/99_历史未决待核_LEGACY.md` |
| 完整事实 | 原有全局、日期、scope 时间线及事件文件 |

历史待核包含限制、建议和待验证线索，不能统称为待办，也不能因移到该视图而认定已完成。只过滤完全等于“无”“none”等空标记；“无法读取”等真实问题保留。不按时间自动关闭问题。

## 字段与记录成本

基础字段沿用 `event_id`、时间、`actor/source`、`scope_id`、`host_id/profile_id`、`summary/why`、`changed_paths`、`verification`、`rollback_ref`、`sensitivity`、幂等键。

| 字段 | 用途 |
|---|---|
| `record_level` | `brief` 或 `full`，只决定证据详略，不改变授权 |
| `kind` | 可选 `artifact/code/runtime/policy/checkpoint`；空值表示未分类 |
| `primary_path` | 主目标；优先用它做最长路径前缀路由，不用备份或当前工作目录抢归属 |
| `work_id`、`work_title` | 可选的稳定事项 ID 与显示名称；多个事件属于同一具体目标时复用 |
| `follow_ups` | 明确、可执行、仍待完成的动作；没有就 `[]` |
| `limitations` | 已知限制、范围之外的建议；不自动进入任务队列 |
| `unresolved` | 旧接口兼容字段；进入历史待核，等待具体语义核实 |
| `related_event_ids` | 普通关联，1.1 起不自动关闭旧事项 |
| `resolves_event_ids` | 明确解决旧事件所有剩余动作；部分解决请用替代并保留余项 |
| `supersedes_event_ids` | 新事件接手旧事件的待办描述；剩余动作写在新 `follow_ups` 中 |
| `lesson` | 可选的条件与检查；下节说明 |
| `quality_notes` | 阶段与验证冲突时的提示；不静默改写事实，也不丢掉记录 |

跨 scope 的变化应明确主目标，能独立验收的变化可拆成事件。注册表不足时保留 `UNASSIGNED`；不按摘要关键词推断项目。旧事件不会被新路由重写。未显式给主目标时，业务路径优先于台账内备份/派生文件；有 changed_paths 时不让 cwd 抢归属。

`phase` 与验证结果独立保存：`observed` 为观察线索；`applied` 已写入；`verified` 实际检查或测试通过；`active` 运行态已加载；`failed` 尝试失败；`rolled_back` 已回滚；`needs_attention` 需关注。`active` 或 `failed` 本身不创建待办。`verification.status` 沿用 `not_run/passed/failed/partial`。混用状态保留原文并提示质量问题，不能据此宣称完整成功。

旧 1.0 的关联曾表示替代，读取时保留其历史含义，避免重新打开当时已接手的记录。1.1 只有明确 resolves/supersedes 才关闭旧待办；引用、解决、接手三者分开。关闭不同 profile 的事项会被拒绝；应在正确身份上下文记录，不通过改名合并身份。

## 对象、事项与历史归类

`scope` 表示被维护的对象，`work_id` 表示一次具体目标，`lesson.key` 表示可复用机制。同一事项以 `scope_id + host_id + profile_id + work_id` 分组；事项名相同、目录相同或会话相同都不足以自动合并。

多次推进的任务首次可传 `--work-id release-019 --work-title "应用 0.19 发布"`，后续复用 ID。没有明确 ID 时，仅在 resolves/supersedes 指向同一身份与对象的唯一事项时继承。多事项关联保留为独立事件并提示；普通 related 不继承。只传事项名会生成 ID；两者都省略且没有可靠关联时仍是独立记录。

`query --work-id <ID>` 返回事项卡、少量事件、明确动作和相关经验；可加 `--scope-id/--host-id/--profile-id` 缩小范围。事项卡的最近进度来自最新事件；全部未关闭动作独立计算，不因最新一次验证通过而消失。未登记后续动作不等于事项已完成。机械观察不自动晋升为完成事实。

旧记录按需整理到台账根目录 `work-items.json`，不改写原事件。结构如下：

```json
{
  "schema_version": "1.0",
  "items": [{
    "work_id": "review-20261015",
    "title": "合同本轮审阅",
    "scope_id": "CONTRACT-REVIEW",
    "host_id": "local-host",
    "profile_id": "",
    "event_ids": ["existing-event-id"],
    "bindings": [{"session_id": "known-session-id", "path": "/absolute/specific-task-directory"}]
  }]
}
```

`event_ids` 是经核实的明确归类；`bindings` 可省略，仅用于已有明确目标的连续任务，同时匹配主机、Profile、会话和目标路径。没有 primary_path 时，全部 changed_paths 都须落在绑定路径内。绑定不能仅用仓库根目录或整个长期会话兜底。调用方显式新 work_id 优先于绑定；跨身份、重复归类或歧义绑定由 doctor/query/render 报错，不猜测。索引损坏时 record 仍保存事实与调用方显式事项信息，并返回质量提示；修复后只重建视图。更正配置后，查询保留 `recorded_scope_id` 供对照，旧卡片标记停用。

对象路由仅影响新增记录；旧对象归类通过上述索引明确调整。试点对象只按路径区分，不据此判定公司、个人或客户业务归属。台账根目录的 registry.json 是路由配置；默认只注册 Skill 与台账本身，业务路径按需添加。

## 可复用经验

经验需要已经完成的核验和 evidence refs；不是每次工作必须填写。示例：

```bash
--lesson-key journal-follow-up-semantics \
--lesson-trigger "接手旧日志或生成待续接清单时" \
--lesson-check "区分明确动作与限制说明；核实当前证据后再登记待办"
```

落盘为 `lesson: {key, trigger, check, status}`。同一 `scope_id + host_id + profile_id + key` 按时间保留最新有效版本；纠正时复用 key，失效时追加 `--lesson-status retired`，历史不删。经验只有当前目标相关时才读取，不自动注入全部任务，不自动写入 AGENTS/SOUL/Skill。一次故障不推出全局限制。

## 适配器与隐私

Hermes 继续通过 `scripts/hermes_adapter.py` 写同一台账。必须提供 `profile_id` 和稳定的 `external_event_id`；二者构成幂等键。可传基础字段及 `record_level/kind/primary_path/work_id/work_title/follow_ups/limitations/resolves_event_ids/supersedes_event_ids/lesson_key/lesson_trigger/lesson_check/lesson_status`，各列表字段必须是数组。旧调用仍可用；旧 unresolved 只作为待核线索。

Hook 同时读取 1.0/1.1 的安全 spool；只记录可能的变化、路径和工具结果，不能当作已验证成果。trace 不自动晋升为语义事件。

只保存摘要、绝对安全路径和证据引用；不接受 Token、OAuth、Cookie、密码、原始聊天、模型推理或完整敏感配置。`record --render` 将事实保存和视图刷新分开报告，视图失败只需重试 render；幂等重试不覆盖事实。
