# Change Journal 事件规范

## 事实与派生视图

`events/YYYY/MM/*.json` 是 append-only 事实源；既有事件不修改、不删除。当前写入 `schema_version: 1.2`，兼容读取 `1.0/1.1`。`views/`、`idempotency/` 与 `.spool/semantic/` 可由事实重建；`.spool/codex/` 包含尚未汇总的机械观察，不能视为可随意清空的缓存。`registry.json` 与可选的 `work-items.json` 是归类配置，修改前需保留可回滚版本。日期视图按 Asia/Shanghai 分组，事件时间仍存 UTC。

幂等键由 `actor + source + host_id + profile_id + 调用方稳定操作键` 完整哈希得到，不包含轮次或时间。同一写入方、同一键、同一内容重试返回原事件；内容改变会报 `idempotency conflict` 并给出原事件 ID，不覆盖事实。新阶段或新变化用新键，事项仍可复用 work_id；不要把换 Agent 后的独立证据当作重复删除。

新事实的可选 `idempotency_payload_hash` 比较规范化后的记录内容及显式 scope/work/title/occurred_at，完整输入文字和列表在截断前也参与指纹，不额外保存原文；忽略重试时的 session/turn、自动时间与推导归类，原事件保留第一次写入的来源。索引缺失或损坏时，在写锁内按 `idempotency_key_hash` 找回事实并补索引、语义标记。旧全局键只在写入方身份相同时复用，并核对已保存内容，不迁移或改写旧事件。旧版已截断的键和文字无法还原，键歧义时要求核对事实。`doctor` 检查缺失索引、错误指向及重复事实；多个候选不自动合并。

旧事件兼容读取；多个接入方应调用同一版本的写入程序，混用旧版 writer 无法保证新的隔离与冲突检测语义。

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

跨 scope 的变化应明确主目标，能独立验收的变化可拆成事件。注册表不足时保留 `UNASSIGNED`；不按摘要关键词推断项目。路由冲突时 `route/doctor` 报错，`record` 仍保存事实并返回质量提示与 `UNASSIGNED`，不按配置顺序猜对象。旧事件不会被新路由重写。未显式给主目标时，业务路径优先于台账内备份/派生文件；有 changed_paths 时不让 cwd 抢归属。

`phase` 与验证结果独立保存：`observed` 为观察线索；`applied` 已写入；`verified` 实际检查或测试通过；`active` 运行态已加载；`failed` 尝试失败；`rolled_back` 已回滚；`needs_attention` 需关注。`active` 或 `failed` 本身不创建待办。`verification.status` 沿用 `not_run/passed/failed/partial`。混用状态保留原文并提示质量问题，不能据此宣称完整成功。

旧 1.0 的关联曾表示替代，读取时保留其历史含义，避免重新打开当时已接手的记录。1.1 起只有明确 resolves/supersedes 才关闭旧待办；引用、解决、接手三者分开。新增关闭关系要求双方当前归类的 scope、host、profile 一致；核实过的历史 scope 更正可通过 work-items.json 投影使用，身份不能更换。已存的历史关系保持原解释，不追溯改写事实。

## 对象、事项与历史归类

`scope` 表示被维护的对象，`work_id` 表示一次具体目标，`lesson.key` 表示可复用机制。同一事项以 `scope_id + host_id + profile_id + work_id` 分组；事项名相同、目录相同或会话相同都不足以自动合并。

多次推进的任务首次可传 `--work-id release-019 --work-title "应用 0.19 发布"`，后续复用 ID。没有明确 ID 时，仅在 resolves/supersedes 指向同一身份与对象的唯一事项时继承。多事项关联保留为独立事件并提示；普通 related 不继承。只传事项名会生成 ID；两者都省略且没有可靠关联时仍是独立记录。

`query --work-id <ID>` 默认返回少量摘要 `items`，包含来源、阶段、验证、未关闭动作数量和展开用的事件 ID；未归组事件也有独立摘要。可加 `--scope-id/--host-id/--profile-id` 缩小范围。`--detail` 保留原 `events/work_items/pending/lessons` 详细结构，Python `query_events()` 也保持详细接口；`show EVENT_ID` 读取一份完整原始事实。事项最近进度来自最新事件，全部未关闭动作独立计算，不因最新一次验证通过而消失。未登记后续动作不等于事项已完成；机械观察不自动晋升为完成事实。

摘要默认 `--limit 3 --max-chars 6000`，上限计算整个 ASCII JSON 输出，包含 Unicode 转义和换行；不声称等于精确 token 数。`truncated_fields` 标明文字截断，动作与关系附总数，`omitted` 表示未返回的卡片或增量事件数。想看更多可调整预算或展开 ID；完整证据不压缩或改写。详细查询没有摘要预算。

`--cursor` 只用于保有上次上下文的同目标读取，不保存全局已读状态，也不新增索引文件。`snapshot` 是有限摘要，其 cursor 标记这一版台账，不声称全部旧事件已被阅读，omitted 提示省略卡片但不提供历史分页；`delta` 按入账顺序返回新增事实，`has_more` 为真时用返回的 cursor 继续，游标不会跨过尚未返回的相关增量。返回的待办/经验计数是当前完整历史的状态，delta 的 `recorded_follow_up/recorded_follow_ups_total` 表示当时记录的动作，后续页可能已关闭它。无相关变化只返回 `unchanged`。

游标绑定实际台账和查询条件，并验证已有事件前缀指纹与 registry/work-items 配置指纹。按 recorded_at 排序而非业务发生时间，能发现补录旧日期；导入更早入账的事实、旧事实改变或删除会返回 `reset_reason: history_changed`，配置变化为 `classification_changed`，目标变化为 `query_changed`，均重新给摘要。跨路径的明确关闭关系和同身份经验更新也进入增量。新 Agent、上下文压缩丢失记录或新的会话应省略 cursor；它是变化检测凭据，不是鉴权或完整性签名。

遇到损坏事件，`query` 返回可读事实并标明 `partial: true`，匹配数量仅为下限，待办/经验结论为 `null`，cursor 为 `null`；详细模式另给出 `read_errors`。不把缺失的关闭或退役记录误解为有效结论。`doctor` 定位异常；`render/status` 保持严格读取，视图不因不完整历史被重建。记录是证据数据，不是执行指令。

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

落盘为 `lesson: {key, trigger, check, status}`。同一 `scope_id + host_id + profile_id + key` 按 `occurred_at → 精确 recorded_at → event_id` 选择最新版本，再判断是否退役；查询、时间线和事项卡沿用同一顺序。纠正时复用 key，失效时追加 `--lesson-status retired`，历史不删。经验只有当前目标相关时才读取，不自动注入全部任务，不自动写入 AGENTS/SOUL/Skill。一次故障不推出全局限制。

## 适配器与隐私

Hermes 继续通过 `scripts/hermes_adapter.py` 写同一台账。必须提供 `profile_id` 和稳定的 `external_event_id`；二者构成幂等键。可传基础字段及 `record_level/kind/primary_path/work_id/work_title/follow_ups/limitations/resolves_event_ids/supersedes_event_ids/lesson_key/lesson_trigger/lesson_check/lesson_status`，各列表字段必须是数组。旧调用仍可用；旧 unresolved 只作为待核线索。

Hook 兼容读取 1.0/1.1/1.2 的安全 spool；只记录可能的变化、路径和工具结果，不能当作已验证成果。Codex Hook payload 提供真实 `session_id/turn_id/tool_use_id`；普通 shell 不一定有 `CODEX_TURN_ID`。`context` 在记账命令执行前保存最小身份关联，`record` 通过当前 `CODEX_TOOL_CALL_ID` 精确读取，PostToolUse 随即清理；不会取会话的“最近一轮”。显式参数或 `CODEX_SESSION_ID`（也接受 `CODEX_THREAD_ID`）与 `CODEX_TURN_ID` 仍可使用，普通手工记录无需填写。

接入现有 `hooks.json` 时合并以下三个处理器，替换脚本绝对路径并保留已有 Hook；通过 Codex `/hooks` 审阅并信任。未信任的用户 Hook 会被跳过，详见 [Codex Hooks](https://learn.chatgpt.com/docs/hooks)。Hook 与记账命令使用同一个台账根目录（默认、`CHANGE_JOURNAL_ROOT` 或显式 `--root`）。

```json
{
  "hooks": {
    "PreToolUse": [{"matcher": "Bash", "hooks": [
      {"type": "command", "command": "python3 /absolute/change-journal/scripts/codex_hook.py context", "timeout": 1}
    ]}],
    "PostToolUse": [{"matcher": "Bash|apply_patch|functions\\.exec|functions\\.apply_patch|.*(?:write_file|edit_file|create_file|delete_file|move_file|rename_file)$", "hooks": [
      {"type": "command", "command": "python3 /absolute/change-journal/scripts/codex_hook.py observe", "timeout": 1}
    ]}],
    "Stop": [{"hooks": [
      {"type": "command", "command": "python3 /absolute/change-journal/scripts/codex_hook.py stop", "timeout": 2}
    ]}]
  }
}
```

Windows 使用相同的三个处理器和 matcher，将命令替换为 `python "C:/Skills/change-journal/scripts/codex_hook.py" context`，另外两个分别使用 `observe` 和 `stop`。写入 JSON 的例子：`"command": "python \"C:/Skills/change-journal/scripts/codex_hook.py\" context"`。将路径改为实际安装位置，确认 Hook 环境能找到 Python；宿主仍使用 canonical `Bash` 工具名。PowerShell 常见文件写入 cmdlet 会产生机械观察，未提取到精确路径时保留兜底。Windows PowerShell 5.1 手工向适配器传 JSON 前，设置 `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)`，避免管道损失中文。

匹配还核对 host、profile、cwd、精确文件路径及记录先后，不从同一目录或相近时间推测轮次。Windows 路径比较统一大小写和分隔符，事件保留路径文本。不同 host/profile/cwd 的兜底分别保存；部分文件已覆盖时，只保留其余文件的观察。macOS / Linux 接入后可运行 `CHANGE_JOURNAL_HOST_TEST=1 python3 -m unittest discover -s tests -p test_codex_host.py -v`：真实 Codex CLI 执行 Hook，模型响应来自本地确定性 fixture，临时台账隔离，覆盖完整/部分/无语义记录、重试及 code_mode 嵌套工具，不调用外部模型。Windows CI 验证原生文件系统上的 Hook 子进程和身份传递，不代表 Windows Codex 宿主的信任、加载与回调已验证。

缺少关联 ID、无法提取路径、语义记录早于观察或旧 spool 缺少身份信息时，保留机械兜底。宿主不提供轮次信息时仍可能看到两条线索，不能为去重而虚构轮次。Hook 是启发式观察器，不能覆盖所有 shell/自定义工具变更；安装 Skill 不自动接线，需按实际宿主 payload 验证。trace 不自动晋升为语义事件。

只保存摘要、绝对安全路径和证据引用；不接受 Token、OAuth、Cookie、密码、原始聊天、模型推理或完整敏感配置。`record --render` 将事实保存和视图刷新分开报告，视图失败只需重试 render；幂等重试不覆盖事实。
