# 家庭学习陪伴与安全教育机器人

围绕赛题提供通用机器人任务框架：语音/文字输入、物体观察、移动讲解和安全知识问答。默认流程不创建课程，不要求出题评分；固定课堂是独立可选模式。

## 当前主链路

```text
文字 / 最终语音转写 → TeamGateway → RobotHost → EmbodiedAgent
    → LangChain + 本地 Qwen → 查询知识 / 观察现场 / 提交动作
    → Skill Runtime → RobotAdapter → 模拟器或未来官方 SDK
    → 执行验证 → 唤醒宿主继续决策 → 返回文本与任务状态
```

主应用通过 `RobotApplication(robot, perception, model, knowledge)` 注入依赖。`RobotContextBuilder` 提供任务、设备能力、动作记录和最近观察；`KnowledgeService` 独立于教学会话。模型不直接操作设备，停止不等待模型返回。

架构、模块边界及验收项见 [赛题框架](docs/COMPETITION_ARCHITECTURE.md)。

## 启动

```bash
uv sync --locked
# 在环境中设置 OLLAMA_BASE_URL，必要时先建立 SSH 隧道。
uv run main.py
```

控制台直接输入“查询热水安全知识”“看看桌上有什么”“模拟左转 0.1 弧度后再观察”等任务。`/stop` 停止当前任务，`/quit` 退出。每条请求创建独立任务，忙碌期间普通请求被拒绝，停止命令仍可处理。当前控制台支持 macOS/Linux 终端。

**默认模型是真实本地 Qwen，机器人和感知均为模拟。** 文字输出没有自动语音播报，主入口没有连接真实摄像头。跨任务长期对话记忆尚未实现。所有真实部署地址与凭据由环境提供，程序不自动读取 `.env`。

主入口已从旧动作演示改为机器人控制台。原演示使用独立模块：

```bash
uv run python -m app.demo --scenario all
uv run python -m app.robot_demo
uv run python -m app.model_demo vision /path/to/photo.png '图中有什么？'
uv run python -m app.classroom_demo
```

第一项为离线动作生命周期测试；第二项验证模拟前进、转向、再前进；第三项是真实 VLM 读取静态图；第四项为可选固定课堂。模型相关入口均需配置本地服务地址。

## 关键代码

| 模块 | 责任 |
| --- | --- |
| `app/robot_application.py` | 通用应用装配和生命周期 |
| `agent/robot_host.py` | 输入去重、模型决策、等待动作、停止和任务结束 |
| `agent/embodied_agent.py` | 有界工具循环，模型参数验证 |
| `agent/context.py` | 机器人上下文；课程上下文独立保留 |
| `agent/tools.py` | 按任务绑定工具，课程工具可选 |
| `education/knowledge_service.py` | 家庭学习及安全知识资料，与课程状态解耦 |
| `perception/interfaces.py` | 相机、视觉提供者和感知服务契约 |
| `providers/ollama_vision.py` | 真实本地 VLM 图片校验和响应解析 |
| `runtime/` | 动作生命周期、任务状态、事件广播 |
| `robot/base.py`、`robot/factory.py` | 与厂商无关的设备能力、接口与构造器注册 |
| `skills/robot_registry.py` | 按设备能力开放相对移动与转向 |
| `speech/interfaces.py` | 录音、识别、合成、播放契约 |
| `agent/classroom_host.py` | 可选课程模式，不是默认机器人入口 |

## 执行规则

- 正常动作进入 Runtime，接受不表示完成。执行后核对位姿、静止状态与证据。
- 动作失败、超时或取消不会触发模型自动重试；无法确认停止时 Runtime 阻止后续运动。
- 通用宿主最多续接三个动作，达到上限会停止，不把上限当作目标完成。
- 每次运动使旧观察失效；已有场景的任务在动作成功后由宿主强制重新观察，再续接模型。观察时间、模拟标记及证据进入上下文。
- 任务 completed 表示本次交互处理结束且实际动作均已成功结束，不代表模型文本已经过外部事实验证。
- 成功输入按编号去重；处理失败先取消任务，禁止无条件重放。
- 应用关闭时取消任务，再释放感知与设备。Runtime 的事件队列由一个消费者读取，需要多消费者时装配一个 ActionEventHub。

当前设备动作只有模拟相对移动和转向，没有真实导航、避障或硬件急停。导航只定义接口，模拟位移不能被称为导航到物体。真实设备需按反馈精度、状态新鲜度及停止行为独立验收。

## 团队接入与规范

- [设备适配接口](docs/ROBOT_ADAPTER.md)：拿到主办方 SDK 后实现 RobotAdapter。
- [本地模型配置及调研](docs/LOCAL_MODELS.md)：Qwen 默认、宇树 VLM 对照、服务器联调结果。
- [模块协作合同](docs/MODULE_CONTRACT.md)：共享媒体、感知、语音接口及证据存储。
- [HTTP 合同草案](API_CONTRACT.md)：尚未实现 HTTP/SSE；本次 Python 重构不新增网络端点。
- [编码规范](docs/CODING_SPEC.md)：代码由负责人审核；当前按用户指示直接在 main 修改。

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run python -m unittest discover -s tests -v
```

basedpyright 要求零错误、零警告。记录、输入去重和教学状态目前均在内存，退出后不保留。
