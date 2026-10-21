# 下一步分工（2026-10-15）

当前进度：Agent 的物体讲解、安全提醒和同任务追问已合入 main；本机常驻 SenseVoice 识别已接入主控制台（`ROBOT_AUDIO=real`）。主控制台目前仍使用模拟感知，看不到真实物体，赛题主路径“看到物体 → 讲解 + 安全提醒 → 追问”还不能端到端演示。

开工前请先同步最新 main，在 `feature/`、`fix/` 分支上开发，通过 PR 合并。

## @hxh100100 感知

1. **真实相机和 LivePerception 接入主控制台（最优先）**
   - 参照 `ROBOT_AUDIO`，增加开关，如 `ROBOT_PERCEPTION=simulated|live`，装配方式参考 `app/perception_demo.py`。
   - 向 `RobotApplication` 传入 `_evidence`，以便任务结束后释放观察帧。
   - 验收：运行 `uv run main.py`，对着真实水壶问“这是什么？能碰吗？”，回答里应同时有物体讲解和热水安全提醒。
2. **验收真实 VLM 的物体输出**
   - 拍 15–20 张家庭场景照片，覆盖热水壶、插座、刀具、药品、燃气灶、清洁剂、窗户阳台 7 类。
   - 逐张运行 `uv run python -m app.model_demo vision <图片> '图中有什么？哪些东西小朋友要小心？'`。
   - 记录两项：标签和方位（左侧/中间/右侧）是否准确；标签能否匹配到知识库条目，例如“不锈钢水壶”能否匹配热水壶条目。
   - 照片和结果保留下来，作为以后的回归素材。
3. **摄像头异常恢复（可往后放）**：真机上测试拔线、重插、驱动无响应。原生驱动卡住时目前无法强制中断，根据测试结果决定是否把驱动放进独立进程。

## @Nanako-Arasaka 语音

1. **真人声音验收 SenseVoice（最优先）**
   - 目前只用合成语音测过。请找 2–3 个人，每人录 20 句左右赛题场景用语，如“热水壶能碰吗”“那插座呢”“燃气灶能自己开吗”。
   - 分别在近距离和远距离、安静和有背景噪音的条件下录音，统计字错率和识别耗时。
   - 启动命令：`ROBOT_AUDIO=real SPEECH_ASR_MODEL_PATH=models/sensevoice-small uv run --extra speech main.py`，在控制台输入 `/record` 开始录音，`/done` 结束并识别。
2. **确定 TTS 方案**
   - 现在本机用系统合成，服务器上没有部署 Piper。
   - 按比赛实际运行的机器选择中文离线 TTS，对比音质和首包延迟；合适的话也做成进程内常驻。
3. **远端 SSH 语音链路的去留**：默认方案已改为本机常驻识别。远端链路遗留的两个问题（每次请求都重建 SSH、重新加载模型；没有协议版本校验），等确定保留远端链路后再处理。
4. **交互改进（先写方案再动手）**
   - 用语音活动检测自动判断说话结束，替代 `/record`、`/done`。
   - 用户开口时打断正在播放的回答。
   - 打断会改变录音与播放的互斥规则，请先在 `docs/MODULE_CONTRACT.md` 写出方案，负责人确认后再实现。

## 暂时都不要直接写代码的

- **HTTP/SSE 与 Flutter 联网**：接口改动需要先更新 `API_CONTRACT.md`，经负责人确认后才能实现。前端页面是 Nanako 做的，可以先起草合同修改提交审核。

## 提交要求（照旧）

以下检查全部通过再提 PR，basedpyright 要求零错误、零警告，不得通过 ignore/noqa 绕过：

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run python -m unittest discover -s tests -v
```

PR 说明里写清楚做了哪些真机验证、哪些还没验证。
