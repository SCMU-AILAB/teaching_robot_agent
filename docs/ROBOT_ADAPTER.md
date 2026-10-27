# 机器人适配框架

在主办方设备未知时，上层只依赖 `RobotAdapter`。目前有模拟实现与自制麦克纳姆底盘 `CarAdapter`（`ROBOT_ADAPTER=car`），没有绑定宇树或其他厂商 SDK；设备选择不会自动退回模拟器。

## 调用关系

```text
Agent / 人工控制 / 通用任务流程
  → TaskCoordinator → ActionManager → RobotSkill → RobotAdapter → 厂商 SDK
```

设备层无需创建 ClassroomHost，也不依赖模型和教学服务。`python -m app.robot_demo` 展示独立运动底座：模拟前进 0.2 米、左转 90 度、再前进 0.2 米，最后到达模拟坐标 (0.2, 0.2)。

## 接口

| 方法或属性 | 实现要求 |
| --- | --- |
| `is_simulated` | 真实适配器保持 False，不隐藏模拟来源 |
| `capabilities` | 稳定的设备能力集合，仅声明实际实现且验证过的能力 |
| `connect()` | 显式建立 SDK 连接；构造和导入时不连接设备 |
| `get_state()` | 返回实际连接、运动、位姿和遥测时间；读不到状态时明确失败，不把“未知”当成静止 |
| `stop()` | 请求停止，包括取消设备端导航目标；返回后 Runtime 仍检查实际运动状态 |
| `disconnect()` | 停止并释放资源；应可重复调用，部分连接失败也可清理 |
| `move_relative(distance_m, speed_m_s)` | 沿起始朝向移动给定距离，负数后退；有真实反馈才开放此能力 |
| `turn_relative(angle_rad, speed_rad_s)` | 原地相对转向，逆时针为正，角速度取正数 |
| `navigate_to(NavigationGoal)` | 显式地图坐标系与目标位姿；设备导航系统负责规划及避障，不得用定时速度移动冒充 |

可选运动方法默认抛出 `UnsupportedCapabilityError`。目前导航只定义接口与目标数据校验，未实现导航 Skill、地图管理或路径规划，也没有注册到 Agent 可用动作中。

`RobotState.position` 当前仍沿用既有平面位姿字段；只有声明 LOCALIZATION 的适配器才允许按该字段验证运动。无定位设备的此字段不具备位置证据语义，必须不声明定位能力；后续设备要求更多状态时扩展领域契约，不能填一个零位姿就声称定位成功。当前尚无专门的定时速度 Skill，无里程计设备暂不开放距离移动。

## 装配方式

1. 编写一个 `RobotAdapter` 子类，实现基础生命周期与实际支持的运动；将厂商异常转换为清晰的 Python 异常。
2. 从环境变量读取连接配置，在装配入口调用 `RobotAdapterFactory.register(name, builder)`。builder 是不带参数的构造器闭包，可封装配置和 SDK 依赖。
3. 调用 `factory.create(name)` 获得未连接实例；用 `build_robot_skills(robot)` 装配实际可用动作。
4. 注入 `ActionManager(robot, registry)`。连接、排队、超时、验证、停止和关闭均复用 Runtime。
5. 将 Runtime 注入 TeamGateway。其 Python 快照携带 capabilities 和 available_skills，ContextBuilder 自动带给 Agent。本次没有修改 HTTP 路由或 HTTP 响应合同。

`ROBOT_ADAPTER` 由独立设备演示读取，默认 simulated。未知名称直接失败；真实设备必须在应用装配入口显式注册。模型和课堂演示仍按原有方式装配，并未自动启用移动技能。

## 单位与执行语义

- 米、米/秒、弧度、弧度/秒；平面坐标 x/y 为右手系，yaw 逆时针为正。相对位姿必须处于同一个连续的本地坐标系；地图目标必须携带 frame_id，适配器拒绝未知坐标系。
- 方法等待操作结束或失败，不能在 SDK 仅“接受命令”后返回成功。Skill 仍通过遥测独立验证位置和静止状态。
- 当前相对运动 Skill 使用 1 毫米和 0.001 弧度的模拟验收阈值。真实设备接入时应依据设备精度制定可配置的容差与遥测新鲜度策略，不能直接宣称通过真机验收。
- Runtime 提交时检查能力，执行前再次检查；不支持的能力不创建动作。转向角度当前限制为正负 π，角速度上限为 1 rad/s。
- 协程取消必须向上传播。阻塞 SDK 需要隔离，但取消线程等待不代表设备停止；适配器必须实现独立 stop 通路及可靠的状态反馈。普通 stop 不是硬件急停的替代品。
- 相对移动、转向和未来导航提交均使场景缓存失效；运动期间观察标为 stale。真实感知服务仍须处理停止后的采集与缓存更新。

## 麦克纳姆底盘 CarAdapter

`robot/car_adapter.py` 经串口协议（`API_CONTRACT.md` 第 5.1 节）驱动 STM32 阶段 1 固件，固件不改：

- **连接**：打开串口前 DTR/RTS 置低；发送 `$S` 锁定通信口并解除故障，2 秒内收不到遥测即失败。后台线程读取 20 Hz 遥测，超过 0.5 秒没有遥测报告未连接。
- **里程计**：主机按轮位（左前 M4、右前 M1、左后 M3、右后 M2）把编码器增量代入麦克纳姆正运动学，取半步航向积分位姿，处理 i32 回绕。它看不到打滑，声明的 LOCALIZATION 只是编码器里程计。
- **标定**：`CAR_COUNTS_PER_METER`（前进 1 米时四轮平均计数）与 `CAR_COUNTS_PER_RADIAN`（逆时针 1 弧度时 (-左前+右前-左后+右后)/4）必填，可选 `CAR_COUNTS_PER_METER_LATERAL`。未标定时不声明任何能力，`build_robot_skills` 不注册运动技能。用 `uv run --extra car python -m app.car_calibrate PORT forward|turn|lateral` 实测，小车会真实移动。
- **运动**：`move_relative` / `turn_relative` 每帧遥测发送一次 `$V`（刷新 300 ms 看门狗），按剩余量减速，移动时修正侧向与航向偏差、转向时保持位置；到位后停车并以连续两帧四轮增量不超过 1 个计数确认静止（正交编码器停在边沿会来回跳 1 个计数）。速度千分比 = 每秒计数 / 4，单轴上限 `CAR_MAX_PERMILLE`（默认 600）。
- **失败**：固件报告堵转/编码器故障（state 3）、命令被拒（应答码非 0）、运动中 0.3 秒无遥测或超过期限（估算耗时 × 2 + 3 秒）都报错；失败与取消先立即发 `$S`，再由 Runtime 清理阶段的 `stop()` 确认静止，确认不了则报错并锁定执行层。
- **动作期限**：`RobotAdapter.motion_timeout_s` 给出设备完成一次运动至少需要的期限（底盘为估算耗时 × 2 + 5 秒，覆盖到位期限与停车确认），Runtime 提交动作时把较短的调用方期限放宽到该值；模拟器为 0，不改变调用方期限。
- **验收容差**：`RobotAdapter.position_tolerance_m` / `yaw_tolerance_rad`（模拟器 1 毫米 / 0.001 弧度），底盘默认 2 厘米 / 0.035 弧度（约 2°），可由 `CAR_POSITION_TOLERANCE_M`、`CAR_YAW_TOLERANCE_RAD` 调整。`MoveRelativeSkill.verify` 按设备容差判定。
- **测试**：`tests/car_firmware_fixture.py` 按固件协议行为实现串口替身（CRC 校验、应答、遥测、斜率、看门狗、故障锁定），`tests/test_car_adapter.py` 覆盖连接、未标定、移动与转向到位、经 Runtime 执行技能、运动中取消、故障、遥测中断与计数回绕。

## 当前验证范围

模拟运动序列、转向后移动方向、转向失败与超时清理、非法参数拒绝、未知设备拒绝及不支持能力拒绝已纳入测试。现有 Runtime 的停止确认和取消机制继续复用。真实避障、定位、导航、硬件急停和厂商 SDK 待拿到资料后接入与验收。
