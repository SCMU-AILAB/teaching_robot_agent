# 机器人适配框架

在主办方设备未知时，上层只依赖 `RobotAdapter`。目前仅有模拟实现，没有绑定宇树或其他厂商 SDK；设备选择不会自动退回模拟器。

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

## 当前验证范围

模拟运动序列、转向后移动方向、转向失败与超时清理、非法参数拒绝、未知设备拒绝及不支持能力拒绝已纳入测试。现有 Runtime 的停止确认和取消机制继续复用。真实避障、定位、导航、硬件急停和厂商 SDK 待拿到资料后接入与验收。
