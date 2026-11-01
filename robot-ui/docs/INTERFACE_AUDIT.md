# 后端接口审计

审计基线：`origin/main` 提交 `bcf14e4`，`API_CONTRACT.md` v1.0.1，实现位于 `api/` 与 `app/robot_server.py`。前端没有新增或修改任何后端接口。

## 1. 当前能力

后端提供机器人设备状态、单活动任务、文字对话、麦克风录音与后端 ASR、Agent 回答、动作执行、语音播报、摄像头预览和视觉观察结果。语音合成、Agent 和 VLM 的外部服务由后端调用，前端不直接访问 3080 服务器。

## 2. 前端可用 HTTP API

| 方法 | 路径 | 用途 | 前端实现 |
| --- | --- | --- | --- |
| GET | `/api/v1/system` | 设备、摄像头、音频与服务配置状态 | 每 1 秒轮询 |
| POST | `/api/v1/tasks` | 创建任务 | Assistant 新建任务 |
| GET | `/api/v1/tasks/{id}` | 任务快照与事件游标 | 初始化、命令后及断线重同步 |
| PATCH | `/api/v1/tasks/{id}` | `cancelling` 或 `completed` | 停止任务、验收完成 |
| POST | `/api/v1/tasks/{id}/messages` | 提交文字 | Assistant 文本输入 |
| POST | `/api/v1/tasks/{id}/recordings` | 开始录音 | Assistant 语音按钮 |
| PATCH | `/api/v1/tasks/{id}/recordings/{rid}` | 结束识别或取消 | Assistant 再次点击语音按钮 |
| GET | `/api/v1/tasks/{id}/events` | SSE 任务事件 | 独立接收线程、断线重连 |
| GET | `/api/v1/camera/snapshot` | 最近 JPEG 帧 | 3 fps 轮询，按比例显示 |
| GET | `/api/v1/evidence/{id}` | 观察证据画面 | 观察编号变化时读取 |

## 3. 请求与响应格式

统一 JSON 包装：

```json
{"code": 0, "message": "success", "data": {}}
```

失败时 `code` 为非零合同错误码，`data` 为 `null`。创建类 POST 必须携带非空 `client_request_id`，前端为每次用户操作生成新编号；网络层不自动重试 POST，避免重复执行。

任务状态：`pending`、`running`、`cancelling`、`completed`、`failed`、`cancelled`。

动作状态：`queued`、`running`、`verifying`、`cancelling`、`succeeded`、`failed`、`cancelled`、`timed_out`。播报是 `skill_name=speak` 的动作，播完才成功。

## 4. 语音流程

1. UI `POST recordings` 开始后端麦克风录音。
2. 用户再次点击或到时，UI `PATCH status=transcribing`。
3. 后端完成 ASR，自动创建 `source=speech` 的用户消息，并推送 `recording.updated` 和 `message.updated`。
4. UI 只消费事件，不再次提交转写文本，因此不会产生重复消息。
5. 机器人回答通过 `message.updated` 到达；播报进度通过对应 `speak` 动作的 `action.updated` 到达。

语音状态由真实录音、消息与动作状态推导为 Idle、Recording、Transcribing、Processing、Speaking、Failed。

## 5. 图像

摄像头使用 `GET /camera/snapshot?max_width=960` 的 JPEG，按 3 fps 轮询，不假设 MJPEG、RTSP 或 WebRTC。观察证据使用 `frame_evidence_id` 单独读取。没有检测框字段，因此 UI 不绘制任何目标框。

## 6. 实时事件

SSE：`GET /tasks/{id}/events`，以快照 `event_cursor` 作为 `Last-Event-ID`。事件按 `sequence` 去重；收到 `stream.reset` 或连接断开后重新读取快照，再继续订阅。心跳注释忽略。

## 7. 后端未提供、因此 UI 不实现的功能

- 硬件急停：没有对应安全控制接口，界面只提供任务停止。
- 连续高频运动遥测：没有该接口，Monitor 不绘制实时运动曲线。
- 检测框或目标坐标：观察结果只有标签与相对方位，Vision 不绘制伪框。
- 历史任务列表：接口只允许按编号读取，UI 不伪造归档列表。
- 自动重放播报：合同没有提供播报重播接口。

## 8. 实际部署差异

当前 Jetson 后端由用户手动启动，环境为 `ROBOT_ADAPTER=car`、`ROBOT_PERCEPTION=live`、`ROBOT_AUDIO=real`、`ROBOT_API_HOST=0.0.0.0`、`ROBOT_API_PORT=8090`。因此板端使用 `config/jetson.json`；默认配置仍遵守合同的 `127.0.0.1:8000`。
