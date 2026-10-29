#!/usr/bin/env bash
# model_server.sh
# 按需启停 3080 上的模型服务（GPT-SoVITS 与 Ollama），并在本机打开 SSH 隧道。
# 服务不设开机自启：用机器人前 start，用完 stop。
#
# 用法：
#   export MODEL_SERVER_SSH=<用户>@<3080 地址>     # 必填，使用 SSH 密钥登录
#   ./model_server.sh start     启动 Ollama 与 GPT-SoVITS，等待端口就绪
#   ./model_server.sh stop      卸载 qwen 并停止 GPT-SoVITS，释放显存（Ollama 服务保留，供其他人使用）
#   ./model_server.sh status    服务状态、开机自启、显存与已加载模型
#   ./model_server.sh tunnel    前台打开本机隧道（Ctrl-C 结束）：本机 21435 → Ollama，29881 → GPT-SoVITS
#
# sudo 密码：在终端运行时由 sudo 提示输入；非交互运行时从标准输入读取一行，例如
#   printf '%s\n' "$SUDO_PASSWORD" | ./model_server.sh start
set -euo pipefail

case "${1:-}" in
    start | stop | status | tunnel) ;;
    *)
        sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
        exit 2
        ;;
esac
: "${MODEL_SERVER_SSH:?请先设置 MODEL_SERVER_SSH，例如 user@host}"
OLLAMA_REMOTE_PORT="${OLLAMA_REMOTE_PORT:-11435}"
SOVITS_REMOTE_PORT="${SOVITS_REMOTE_PORT:-9881}"
OLLAMA_LOCAL_PORT="${OLLAMA_LOCAL_PORT:-21435}"
SOVITS_LOCAL_PORT="${SOVITS_LOCAL_PORT:-29881}"
AGENT_MODEL="${AGENT_MODEL:-qwen3.5:9b}"
# GPT-SoVITS 冷启动加载模型实测约 6 分钟（内存紧张时更久）。
READY_TIMEOUT_S="${READY_TIMEOUT_S:-600}"

# 不读标准输入（-n），以免吞掉随后 sudo 要读的密码。
remote() {
    ssh -n -o ConnectTimeout=10 "$MODEL_SERVER_SSH" "$@"
}

# 在 3080 上执行 sudo 命令：终端里交互输入密码，否则从标准输入读密码。
remote_sudo() {
    if [ -t 0 ]; then
        ssh -t -o ConnectTimeout=10 "$MODEL_SERVER_SSH" "sudo $*"
    else
        local password
        IFS= read -r password
        printf '%s\n' "$password" | ssh -o ConnectTimeout=10 "$MODEL_SERVER_SSH" "sudo -S -p '' $*"
    fi
}

wait_port() {
    local port="$1" name="$2" waited=0
    until remote "ss -ltn | grep -q ':${port} '"; do
        if [ "$waited" -ge "$READY_TIMEOUT_S" ]; then
            echo "等待 ${name} 超时（${READY_TIMEOUT_S} 秒），查看日志：ssh $MODEL_SERVER_SSH journalctl -u sovits-api -n 50" >&2
            return 1
        fi
        sleep 10
        waited=$((waited + 10))
        echo "  等待 ${name} 就绪…（${waited} 秒）"
    done
    echo "${name} 已就绪（端口 ${port}）"
}

cmd_start() {
    echo "启动 3080 上的 Ollama 与 GPT-SoVITS（只启动，不设开机自启）"
    remote_sudo systemctl start ollama sovits-api
    wait_port "$OLLAMA_REMOTE_PORT" "Ollama"
    wait_port "$SOVITS_REMOTE_PORT" "GPT-SoVITS"
    cmd_status
}

cmd_stop() {
    echo "卸载 ${AGENT_MODEL} 并停止 GPT-SoVITS"
    remote "curl -s -m 30 http://127.0.0.1:${OLLAMA_REMOTE_PORT}/api/generate -d '{\"model\":\"${AGENT_MODEL}\",\"keep_alive\":0}' >/dev/null" || true
    remote_sudo systemctl stop sovits-api
    cmd_status
}

cmd_status() {
    remote "for s in ollama sovits-api; do printf '%-11s %-9s 开机自启 %s\n' \"\$s\" \"\$(systemctl is-active \$s)\" \"\$(systemctl is-enabled \$s)\"; done
        echo '显存：'; nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
        echo '已加载模型：'; OLLAMA_HOST=127.0.0.1:${OLLAMA_REMOTE_PORT} ollama ps 2>/dev/null | tail -n +2 || true"
}

cmd_tunnel() {
    echo "隧道：本机 ${OLLAMA_LOCAL_PORT} → Ollama，${SOVITS_LOCAL_PORT} → GPT-SoVITS（Ctrl-C 结束）"
    echo "后端配置：OLLAMA_BASE_URL=http://127.0.0.1:${OLLAMA_LOCAL_PORT}  SPEECH_GPTSOVITS_URL=http://127.0.0.1:${SOVITS_LOCAL_PORT}"
    exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
        -L "127.0.0.1:${OLLAMA_LOCAL_PORT}:127.0.0.1:${OLLAMA_REMOTE_PORT}" \
        -L "127.0.0.1:${SOVITS_LOCAL_PORT}:127.0.0.1:${SOVITS_REMOTE_PORT}" \
        "$MODEL_SERVER_SSH"
}

case "$1" in
    start) cmd_start ;;
    stop) cmd_stop ;;
    status) cmd_status ;;
    tunnel) cmd_tunnel ;;
esac
