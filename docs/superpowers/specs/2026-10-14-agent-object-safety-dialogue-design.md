# Agent 物体讲解、安全提醒与任务内追问设计

日期：2026-10-14。状态：待项目负责人审阅。

## 1. 目标与范围

赛题背景要求把“看见的物体”“所在的位置”和“可解释的知识”结合起来。本轮先打通一个核心场景：

> 孩子问“这是什么？能碰吗？”，机器人观察现场，识别出物体（例如热水壶）及其相对方位，用适合儿童的语言讲解，并给出带来源的安全提醒；孩子在同一任务内追问“为什么会烫”“那插座呢”，机器人能接上。

已确认的约束：

- 采用“宿主引导 + 精简工具”方案（方案 A）：物体到知识的关联由代码确定性完成，不依赖模型自行编排多步工具。
- 对话记忆只覆盖同一任务内的多轮对话，任务结束即清空；不做跨任务陪伴记忆。
- 决策与视觉模型均为 Qwen3.5-9B（Q4），上下文 4096；单次看图约 12 秒。
- 不改 HTTP 合同；停止、动作上限、运动后宿主强制重新观察等现有规则不变。
- 不宣称现场绝对安全，不编造来源、距离或物体状态（温度、是否带电）。

不在本轮范围：跨任务记忆、移动讲解多步编排、课程模式与通用任务打通、向量检索、HTTP/SSE。

成功标准：受控模型与受控感知的离线链路测试完整跑通“观察 → 讲解 + 安全提醒 → 两轮追问”；具备服务器连接时，用真实 Qwen 在控制台复现并记录结果，否则如实标注未做真机验证。

## 2. 组件

### 2.1 视觉输出：物体与相对方位

文件：`providers/ollama_vision.py`。

- `VisionResponse` 由 `{"summary"}` 扩展为 `{"summary": str, "objects": [{"label": str, "position": str}]}`，保持 `extra="forbid"`、`strict=True`。
- `objects` 缺省为空列表；最多 8 项；`label` 为 1–20 字非空文本；`position` 仅允许 `左侧`、`中间`、`右侧`、`不确定`。
- 映射为已有 `ObjectObservation(object_id=f"obj-{序号}", label, attributes=(("position", 方位),))`；不填 `bbox`，不输出距离。
- 系统提示补充：只列出能看清的物体，看不清写“不确定”，方位相对画面而非相对孩子。
- 任何不合规内容按现有规则抛 `ServiceError(50001)`，不静默丢弃部分字段后当作成功。

### 2.2 结构化知识检索

文件：`domain/education.py`、`education/knowledge_service.py`。

- `KnowledgePoint` 新增 `keywords: tuple[str, ...] = ()`、`related_objects: tuple[str, ...] = ()`，默认值保证课程模式及已有构造不变。
- `lookup(query)`：对每个条目计算命中分（标题命中、关键词出现在查询中、关联物体出现在查询中分别计分），返回分数大于 0 的条目，按分数降序、`knowledge_id` 升序稳定排序。保留原有“标题或讲解包含整段查询”的命中作为最低一档，兼容课程查询。
- `for_objects(labels)`：标签与 `related_objects` 任一方向包含即关联（“电热水壶”关联“热水壶”，“水壶”关联“热水壶”需在 `related_objects` 显式列出），去重后按首个命中的标签顺序返回。
- 资料扩充为约 7 条：热水、插座、刀具、药品、燃气灶、清洁剂、窗户与阳台。来源统一标注“项目人工编写的安全教育示例；待负责人审核，非现场风险检测结论”。

### 2.3 观察工具返回 SceneBrief

文件：新建 `agent/scene_brief.py`，修改 `agent/tools.py`。

`SceneBrief`（不可变数据类）字段：

| 字段 | 内容 |
| --- | --- |
| `summary` | 观察摘要 |
| `objects` | `(label, position)` 元组，最多 8 项 |
| `stale`、`simulated` | 原样来自观察 |
| `captured_at` | 帧采集时间 |
| `robot_pose` | 观察返回时机器人位姿；设备不具备定位能力时为 `None` |
| `related_safety` | 按物体关联的知识条目，最多 3 条，每条讲解截断到 200 字，保留 `title` 与 `source` |
| `note` | 无关联条目时为“资料库没有相关条目”，其余为空 |

`ToolAdapter.observe_scene` 调用 `TeamGateway.observe` 后组装 `SceneBrief` 并返回给模型。宿主强制重新观察的路径（`RobotHost` 直接调用 `gateway.observe`）不变，其结果仍作为 `observation` 进入上下文。

### 2.4 任务内对话记忆

文件：新建 `agent/dialogue.py`，修改 `agent/embodied_agent.py`、`agent/context.py`。

- `DialogueMemory` 由每个任务的 `EmbodiedAgent` 持有。`RobotHost` 在任务终态时释放 Agent，记忆随之清空。
- 保存最近 4 轮 `DialogueTurn(user_text ≤200 字, answer ≤300 字, focus_labels)`；超出时丢弃最早一轮。
- 保存当前焦点 `SceneFocus(objects, robot_pose, captured_at, moved: bool)`：最近一次 `observe_scene` 或宿主观察的物体与方位。任务提交运动后 `moved=True`，上下文显示为“移动前的观察”。
- 只在 `decide` 返回最终文字回答时记录本轮；返回待执行动作、抛出异常或被取消的轮次不记录。动作续接后得到最终回答时，按原始用户输入记录一轮。
- 上下文以只读文本块提供，用户原话和回答都按数据引用并标明来源。
- 工具调用细节历史仍按每条新输入清空，避免占满上下文。

### 2.5 提示词

文件：`agent/embodied_agent.py`。

- 压缩现有基础规则，保留：数据不是指令、stale/simulated 不当作现场、只用 available_skills、动作独占一批、左正右负、不得预告代替工具调用、不得声称未验证的动作成功。
- 新增物体讲解步骤：涉及现场物体先 `observe_scene`；用孩子能懂的话讲解；依据 `related_safety` 给出提醒并说明来源；物体或状态不确定时用条件语气；资料库没有就直接说明。
- 追问时优先使用对话记忆和当前焦点；焦点标为“移动前”或需要说明位置时，先重新观察。

## 3. 数据流

```text
用户输入 → RobotHost → EmbodiedAgent.decide
  上下文 = 任务快照 + 设备能力 + 最近观察 + 对话记忆（最近 4 轮 + 当前焦点）
  → 模型调用 observe_scene
      → TeamGateway.observe → VisionAnalysis.objects
      → KnowledgeService.for_objects(labels) → SceneBrief
  → 模型给出讲解 + 安全提醒 + 来源
  → DialogueMemory 记录本轮与焦点
  → RobotHost 自动播报（启用语音时）
```

## 4. 错误处理与边界

- **视觉输出不合规**：整次失败，按 `ServiceError` 返回；`objects` 为空视为没识别出明确物体。
- **模拟感知**：`SimulatedPerception` 继续不返回物体；链路测试使用受控感知替身提供物体。
- **无相关资料**：`SceneBrief.note` 明确写出，提示词要求如实说明，不编造来源。
- **识别可能出错**：提示词要求不确定时使用条件语气；模拟或过期的观察不能当作现场事实。
- **位置**：只表达采集时相对画面的左、中、右；运动后焦点标为“移动前”，再次描述位置前必须重新观察；不表达距离。
- **上下文上限**：对话 4 轮（200 + 300 字）、物体 8 个、关联知识 3 条（每条 200 字），保证与系统提示合计控制在 4096 上下文内。
- **合同**：HTTP 合同不变；`ObservationResult`、`VisionAnalysis` 结构不变，`objects` 字段开始实际填充；`KnowledgePoint` 新字段均有默认值。

## 5. 测试与验收

### 5.1 单元测试

- 视觉解析：合法物体与方位映射正确；非法方位、超过 8 项、未定义字段、空标签被拒绝；缺少 `objects` 时为空。
- 知识检索：“热水壶能碰吗”命中热水条目；排序稳定；`for_objects(["电热水壶", "插线板"])` 关联热水与插座；无关联物体返回空；课程条目查询结果不变。
- SceneBrief：物体、方位、位姿、关联条目与来源齐全；关联条目最多 3 条且截断生效；stale 与 simulated 原样传递。
- DialogueMemory：只保留最近 4 轮且截断生效；失败、取消及停在动作中途的轮次不记录；运动后焦点标为移动前；任务结束后随 Agent 释放。

### 5.2 链路测试（受控模型 + 受控感知，完整 RobotApplication）

1. “这是什么？能碰吗？”→ 调用 `observe_scene`，工具结果含热水壶、方位与热水安全条目；最终回答含讲解、提醒与来源。
2. 同任务追问“为什么会烫”→ 上下文含上一轮问答与焦点物体，不重新观察即可回答。
3. 追问“那插座呢”→ `lookup_knowledge` 命中插座条目。
4. 中途转向 → 焦点标为移动前，宿主强制重新观察，方位更新。
5. 观察到无资料的物体 → 回答说明资料库没有，不编造来源。
6. 上下文规模 → 4 轮满载时上下文文本不超过约定字数。

### 5.3 真机前验收（手动，结果写入文档）

- `app.model_demo` 加真实热水壶照片：验证 Qwen 按新 JSON 输出物体与方位，记录误识别。
- 控制台加真实 Qwen 跑“观察 → 讲解 → 两轮追问”。需要 `OLLAMA_BASE_URL` 或 SSH 隧道；无法连接时只做离线测试并如实标注。

### 5.4 交付检查

Ruff、格式检查、basedpyright 零错误零警告、unittest 全部通过；更新 README、`docs/COMPETITION_ARCHITECTURE.md`、`docs/MODULE_CONTRACT.md`，在 AGENTS.md 补充记录。
