#!/bin/sh
# deploy/jetson/robot.sh
# 一键启动 / 停止机器人（在 Jetson 上运行，不设开机自启）：
#
#   ./deploy/jetson/robot.sh start    检查设备 → 设置扬声器与麦克风 → 建 Jetson 到 3080 的隧道 → 启动后端
#   ./deploy/jetson/robot.sh stop     停止后端（先停车）与隧道
#   ./deploy/jetson/robot.sh status   查看隧道、后端、3080 模型服务与设备
#   ./deploy/jetson/robot.sh logs     跟踪后端日志（Ctrl-C 退出）
#
# 隧道与后端以临时用户服务运行：断线自动重连，Jetson 重启后不会自己起来。
# 前提：~/teaching-robot/robot.env 已配置；Jetson 能用密钥登录 MODEL_SERVER_SSH；
# 3080 上的 Ollama 与 GPT-SoVITS 已用 model_server.sh start 启动。

set -u
ROOT="$HOME/teaching_robot_agent"
ENV_FILE="$HOME/teaching-robot/robot.env"
TUNNEL=teaching-robot-tunnel
BACKEND=teaching-robot
UV="$HOME/.local/bin/uv"
API=http://127.0.0.1:8090/api/v1

[ -f "$ENV_FILE" ] || { echo "缺少配置文件 $ENV_FILE" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

ok() { printf '  [好] %s\n' "$1"; }
bad() { printf '  [缺] %s\n' "$1"; }

check_devices() {
    # 返回缺少的设备数
    missing=0
    if [ -e "${CAR_SERIAL_PORT:-/dev/ttyACM0}" ]; then ok "底盘串口 ${CAR_SERIAL_PORT:-/dev/ttyACM0}"; else bad "底盘串口 ${CAR_SERIAL_PORT:-/dev/ttyACM0}"; missing=$((missing + 1)); fi
    if grep -qs RealSense /sys/class/video4linux/video*/name; then ok "RealSense 摄像头"; else bad "RealSense 摄像头（检查 USB 扩展坞）"; missing=$((missing + 1)); fi
    if [ "${PERCEPTION_CAMERA:-}" = "usb" ] && [ "${PERCEPTION_DEVICE:-0}" != "realsense" ] && [ ! -e "/dev/video${PERCEPTION_DEVICE:-0}" ]; then bad "彩色画面节点 /dev/video${PERCEPTION_DEVICE:-0}"; missing=$((missing + 1)); fi
    if grep -qsi earpods /proc/asound/cards; then ok "Earpods 麦克风"; else bad "Earpods 麦克风"; missing=$((missing + 1)); fi
    if grep -qs "USB Audio Device" /proc/asound/cards; then ok "C-Media 扬声器"; else bad "C-Media 扬声器"; missing=$((missing + 1)); fi
    return "$missing"
}

node_id() {
    # $1: Sinks 或 Sources；$2: 名称子串。输出 wpctl status 中的节点编号。
    wpctl status | sed -n '/^Audio/,/^Video/p' | sed -n "/ $1:/,/ endpoints:/p" \
        | grep "$2" | head -n 1 | sed -E 's/[^0-9]*([0-9]+)\..*/\1/'
}

setup_audio() {
    # 开机后插入的 USB 声卡可能没有输出配置，PipeWire 也会把音量复原、默认设备可能选成 Earpods
    systemctl --user restart wireplumber
    sleep 3
    sink=$(node_id Sinks "USB Audio Device")
    src=$(node_id Sources "Earpods")
    if [ -n "$sink" ]; then
        wpctl set-default "$sink" && wpctl set-volume "$sink" 1.0 && ok "扬声器设为默认输出，音量 100%"
    else
        bad "PipeWire 中没有 USB Audio Device 输出"
    fi
    if [ -n "$src" ]; then
        wpctl set-default "$src" && ok "Earpods 设为默认输入"
    else
        bad "PipeWire 中没有 Earpods 输入"
    fi
    for card in /sys/class/sound/card*; do
        [ "$(cat "$card/id" 2>/dev/null)" = "Device" ] && amixer -q -c "${card##*card}" sset PCM 100%
    done
}

start_tunnel() {
    if systemctl --user is-active -q "$TUNNEL"; then ok "隧道已在运行"; return 0; fi
    systemctl --user reset-failed "$TUNNEL" 2>/dev/null
    systemd-run --user --quiet --unit="$TUNNEL" --property=Restart=always --property=RestartSec=5 \
        /usr/bin/ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
        -L 127.0.0.1:21435:127.0.0.1:11435 -L 127.0.0.1:29881:127.0.0.1:9881 \
        "$MODEL_SERVER_SSH"
    # 3080 的 sshd 常因公网扫描丢弃新连接，重试由 Restart=always 完成
    for _ in $(seq 1 30); do
        if curl -s -m 2 http://127.0.0.1:21435/api/version >/dev/null; then ok "隧道已连通 3080"; return 0; fi
        sleep 2
    done
    bad "隧道 60 秒内未连通，查看：journalctl --user -u $TUNNEL"
    return 1
}

check_models() {
    if curl -s -m 3 http://127.0.0.1:21435/api/version >/dev/null; then ok "Ollama 可用"; else bad "Ollama 不可用"; fi
    # GPT-SoVITS 根路径返回 404 表示服务在线
    code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:29881/)
    if [ "$code" = "404" ] || [ "$code" = "200" ]; then ok "GPT-SoVITS 可用"; else bad "GPT-SoVITS 不可用（在 3080 上运行 model_server.sh start）"; fi
}

start_backend() {
    if systemctl --user is-active -q "$BACKEND"; then ok "后端已在运行"; return 0; fi
    systemctl --user reset-failed "$BACKEND" 2>/dev/null
    systemd-run --user --quiet --unit="$BACKEND" --property=EnvironmentFile="$ENV_FILE" \
        --property=TimeoutStopSec=20 --working-directory="$ROOT" \
        "$UV" run --extra speech --extra car --extra depth python -m app.robot_server
    echo "  等待后端就绪（GPT-SoVITS 预热，首次可能需要一两分钟）…"
    for _ in $(seq 1 90); do
        if curl -s -m 2 "$API/system" >/dev/null; then ok "后端已就绪：http://$(hostname -I | cut -d' ' -f1):${ROBOT_API_PORT:-8090}"; return 0; fi
        if ! systemctl --user is-active -q "$BACKEND"; then break; fi
        sleep 2
    done
    bad "后端未能启动，最近日志："
    journalctl --user -u "$BACKEND" --no-pager -n 15
    return 1
}

case "${1:-}" in
    start)
        echo "检查设备"
        if ! check_devices; then
            echo "有设备未接好，先检查连线再启动（强制启动：$0 start-anyway）" >&2
            exit 1
        fi
        echo "设置音频"
        setup_audio
        echo "连接 3080"
        start_tunnel || exit 1
        check_models
        echo "启动后端"
        start_backend
        ;;
    start-anyway)
        setup_audio
        start_tunnel || exit 1
        check_models
        start_backend
        ;;
    stop)
        # 后端退出时先停车并释放设备
        systemctl --user stop "$BACKEND" 2>/dev/null && echo "后端已停止"
        systemctl --user stop "$TUNNEL" 2>/dev/null && echo "隧道已停止"
        systemctl --user reset-failed "$BACKEND" "$TUNNEL" 2>/dev/null
        true
        ;;
    status)
        echo "服务"
        for unit in "$TUNNEL" "$BACKEND"; do
            printf '  %-22s %s\n' "$unit" "$(systemctl --user is-active "$unit")"
        done
        echo "3080 模型"
        check_models
        echo "设备"
        check_devices
        echo "后端"
        curl -s -m 3 "$API/system" || echo "  未运行"
        echo
        ;;
    logs)
        journalctl --user -u "$BACKEND" -f
        ;;
    *)
        echo "用法：$0 start|stop|status|logs" >&2
        exit 2
        ;;
esac
