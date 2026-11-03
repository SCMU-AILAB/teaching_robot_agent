#!/bin/sh
# deploy/jetson/robot_screen.sh
# 车载屏幕一键启动（桌面图标调用，也可在终端运行）：
#   1. 后台执行 robot.sh start：检查设备、设置音频、连 3080、启动后端（已在运行则跳过）；
#   2. 立即全屏打开 robot-ui，后端就绪前界面显示离线，就绪后自动连上。
# 关掉界面不会停后端；停止用 ./deploy/jetson/robot.sh stop。
# 安装桌面图标：./deploy/jetson/robot_screen.sh install

set -u
ROOT="$HOME/teaching_robot_agent"
UI_DIR="$ROOT/robot-ui"
LOG="$HOME/teaching-robot/screen.log"

install_icon() {
    desktop="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
    entry="$desktop/robot-screen.desktop"
    cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Name=机器人界面
Comment=启动机器人后端并全屏显示车载界面
Exec=$ROOT/deploy/jetson/robot_screen.sh
Icon=video-display
Terminal=false
Categories=Utility;
EOF
    chmod +x "$entry" "$ROOT/deploy/jetson/robot_screen.sh"
    # GNOME 桌面图标须标记为可信才能双击运行
    gio set "$entry" metadata::trusted true 2>/dev/null || true
    echo "已创建 $entry"
}

case "${1:-}" in
    install)
        install_icon
        exit 0
        ;;
esac

mkdir -p "$(dirname "$LOG")"
# 界面已开着就不再开第二个
if pgrep -x robot_ui >/dev/null; then
    notify-send "机器人界面已在运行" 2>/dev/null || true
    exit 0
fi

{
    echo "===== $(date '+%F %T') 启动 ====="
    if ! "$ROOT/deploy/jetson/robot.sh" start; then
        notify-send -u critical "机器人后端没有启动" "有设备没接好或连不上 3080，详情见 $LOG" 2>/dev/null || true
    fi
} >>"$LOG" 2>&1 &

cd "$UI_DIR" || exit 1
exec ./build/robot_ui --config config/jetson.json --fullscreen >>"$LOG" 2>&1
