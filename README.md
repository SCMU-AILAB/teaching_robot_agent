# Teaching Robot Agent

教学机器人的最小可运行基础层。当前实现了数据模型、模拟设备、相对移动 Skill、异步动作 Runtime、任务协调器和内存存储，运行只依赖 Python 3.12+ 标准库。

## 运行

在项目根目录执行：

```bash
python3 main.py
python3 main.py --scenario all
python3 -m unittest discover -s tests -v
```

也可以用 `uv run main.py --scenario all`。演示包含正常完成、设备失败、执行超时、运行中取消、排队取消和两个动作顺序执行；失败与超时场景是主动注入的预期结果。

## 开发检查

使用 `uv sync --locked` 安装锁定的开发依赖，随后运行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run python -m unittest discover -s tests -v
```

Ruff 负责代码检查、导入排序和统一格式；basedpyright 使用 `recommended` 模式，警告也会使检查失败，业务代码与测试均纳入检查。具体开发约定见 `AGENTS.md`。

## 模拟范围

所有设备动作都是**模拟相对移动**，没有连接真实硬件，也不代表目标点导航。成功证据含 `simulated: True`。模拟器的 `time_scale` 只压缩执行时间，距离和速度参数仍使用米和米/秒。

## 按什么顺序读

| 文件                          | 职责                                               |
| ----------------------------- | -------------------------------------------------- |
| `domain/models.py`            | 动作请求、动作记录、任务状态、机器人位姿和状态事件 |
| `robot/base.py`               | 设备适配器契约：连接、查询、相对移动、停止         |
| `robot/simulated.py`          | 可配置成功、失败、挂起的模拟设备                   |
| `skills/base.py`              | Skill 的参数校验、前置检查、执行、验证和清理接口   |
| `skills/move_relative.py`     | 一个完整的相对移动 Skill                           |
| `skills/registry.py`          | 按名称注册和查找 Skill                             |
| `storage/memory.py`           | 内存中的动作与任务记录，读写均复制数据             |
| `runtime/action_manager.py`   | 动作排队、状态转换、超时、取消、停止确认和事件     |
| `runtime/task_coordinator.py` | 创建任务、关联动作、明确结束或取消整个任务         |
| `app/demo.py`                 | 依赖装配与六种演示场景                             |

调用路径是：`演示入口 → TaskCoordinator → ActionManager → RobotSkill → RobotAdapter`。

## 状态与执行约定

- `ActionStatus` 表达某个动作是否排队、执行、验证、取消或结束。
- `RobotState` 表达设备的连接、运动状态及位姿，不代替动作状态。
- `TaskStatus` 表达用户任务是否进行或结束。一个动作成功后任务仍保持运行，调用方通过 `complete_task()` 明确结束多步骤任务。

提交动作会立即返回带有整数编号的 `ActionRecord`。Runtime 的单执行器随后依次执行所有动作，完成验证和清理后再保存终态；`wait_for_action()` 等待终态，`next_event()` 接收状态事件。事件队列当前是单消费者模型，取消等待者不会取消设备动作。

正常状态转换为 `queued → running → verifying → succeeded`。取消排队动作直接得到 `cancelled`；取消执行中的动作先进入 `cancelling`，停止确认后才进入 `cancelled`。执行或验证报错得到 `failed`，超过执行期限得到 `timed_out`。

超时从动作开始执行时计时，覆盖前置检查、执行与验证，不含排队等待；清理另有 `cleanup_timeout_s`。如果无法确认停止，动作记为 `failed`，Runtime 阻止新动作并使已排队动作失败。此时应排查设备状态并建立新的 Runtime。关闭 Runtime 会取消未完成动作、等待清理，再断开设备；断开失败会报告异常。

`complete_task()` 要求任务至少有一个动作且所有动作已结束；全部成功时任务为 `completed`，否则为 `failed`。`cancel_task()` 先取消全部关联动作，再等待终态；停止失败或已有失败、超时动作时任务记为 `failed`，其余情况为 `cancelled`。这些是首版明确的任务策略，后续教学逻辑可以扩展重试和恢复规则。

Runtime 关闭负责结束动作和设备连接，任务的业务终态由调用方通过协调器结束或取消；应用关闭时应先取消尚未结束的任务。取消 `cancel_task()` 的等待者不会中断后台的任务取消流程。事件流没有结束哨兵，应用退出时应取消自己的事件监听协程（演示入口已处理）。

## 扩展一个动作

1. 继承 `RobotSkill`，给出唯一名称和参数校验。
2. 实现前置检查与执行，设备操作通过 `RobotAdapter` 调用。
3. 用设备反馈验证完成，返回 `SkillResult` 和证据。
4. 实现异常、超时与取消后的清理，并确认设备停止。
5. 在装配入口注册到 `SkillRegistry`，通过协调器提交。

相对移动参数为 `distance_m` 和可选的 `speed_m_s`（默认 0.1）。距离绝对值不超过 2 米，速度大于 0 且不超过 0.5 米/秒；拒绝未知字段、布尔值、非数值及 NaN/Infinity。位置使用平面坐标 `x/y`（米）和 `yaw`（弧度）；记录时间是 Unix 秒，超时由 asyncio 的单调时钟处理。

## 当前边界与下一步

这是单进程、单 asyncio 事件循环、单执行器的基础实现。记录与事件保存在内存，进程退出后不保留；没有数据库恢复或多消费者广播。异步适配器和 Skill 必须响应协程取消，不能在事件循环中执行阻塞 SDK 调用；真实硬件还需要独立验证停止确认与异常处理能力。

`agent/`、`education/`、`perception/` 和 `providers/` 保留现有目录，尚未接入模型、教学服务、摄像头或语音。接下来先扩充感知快照和一个教学主题，再将已验证的 Runtime 包装为 Agent 工具；API 和控制台可以通过同一套提交、查询、取消与事件接口接入。
