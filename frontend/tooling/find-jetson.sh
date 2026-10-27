#!/bin/bash
# frontend/tooling/find-jetson.sh
#
# 局域网自动发现 Jetson 设备端服务，并把地址写进课堂 App 的配置。
#
# 用法：
#   ./find-jetson.sh                  # 自动发现 + 校验 + 写配置
#   ./find-jetson.sh --restart        # 写完顺手重启 App
#   ./find-jetson.sh --json           # 机器可读输出
#   ./find-jetson.sh --no-write       # 只看结果，不改配置
#   ./find-jetson.sh --server 1.2.3.4 # 顺带测服务器 SSH 连通性
#
# 换内网后直接跑一次：地址会自动更新，App 里不用再手填。
# 依赖：系统自带 nc / curl / ipconfig / route（macOS 默认都有）。

set -u

PORT=8090
TIMEOUT=1
IFACE=""
APP_PORT_SSH=""
NO_WRITE=0
RESTART=0
JSON=0
USE_MDNS=1
PREFER=ip
SERVER_SPEC=""

# 沙箱版 App 只能读容器内路径；非沙箱构建读用户目录，两个都写一份。
APP_ID="com.scmu.ailab.teachingrobot"
CONTAINER_CONF="$HOME/Library/Containers/$APP_ID/Data/Library/Application Support/TeachingRobot"
PLAIN_CONF="$HOME/Library/Application Support/TeachingRobot"
SERVER_CONF="$HOME/.config/teachingrobot/server.json"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --interface) IFACE="$2"; shift 2 ;;
    --server) SERVER_SPEC="$2"; shift 2 ;;
    --ssh-port) APP_PORT_SSH="$2"; shift 2 ;;
    --no-write) NO_WRITE=1; shift ;;
    --no-mdns) USE_MDNS=0; shift ;;
    --prefer) PREFER="$2"; shift 2 ;;
    --restart) RESTART=1; shift ;;
    --json) JSON=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "未知参数：$1" >&2; usage 1 ;;
  esac
done

log() { [ "$JSON" -eq 1 ] || echo "$@"; }

# ========== Step1: 预处理 ==========

# 本机局域网地址与网段：只扫直连网卡的 /24，不碰 VPN 与回环。
if [ -z "$IFACE" ]; then
  IFACE=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')
fi
if [ -z "$IFACE" ]; then
  echo "找不到默认网络接口，请用 --interface 指定（ifconfig 可见）" >&2
  exit 1
fi

MY_IP=$(ipconfig getifaddr "$IFACE" 2>/dev/null)
if [ -z "$MY_IP" ]; then
  echo "网卡 $IFACE 没有 IPv4 地址，检查 Wi-Fi / 网线是否连上" >&2
  exit 1
fi
SUBNET="${MY_IP%.*}"

# ========== Step2: 校验一个候选地址 ==========

# 返回 0 表示对方确实是设备端服务。
# 判定依据：/api/v1/system 返回 code=0，且 data 里有 instance_id 与 camera。
probe_device() {
  local host="$1"
  local body
  body=$(curl -s --noproxy '*' -m 3 "http://$host:$PORT/api/v1/system" 2>/dev/null)
  case "$body" in
    *'"code": 0'*) ;;
    *) return 1 ;;
  esac
  case "$body" in
    *'"instance_id"'*'"camera"'*) ;;
    *) return 1 ;;
  esac
  printf '%s\n' "$body"
}

# 把 mDNS 名字解析成 IPv4，解析不到返回空。
resolve_name() {
  dscacheutil -q host -a name "$1" 2>/dev/null | awk '/ip_address:/{print $2; exit}'
}

# 从 JSON 里抠字段，避免依赖 jq。
json_field() {
  printf '%s' "$1" | sed -n "s/.*\"$2\": *\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" | head -1
}

# ========== Step3: mDNS 先试（同网段时几乎瞬间命中） ==========

CANDIDATES=""
if [ "$USE_MDNS" -eq 1 ]; then
  for name in "qwq.local" "jetson.local" "orin.local" "jetson-orin.local"; do
    if body=$(probe_device "$name"); then
      CANDIDATES="$name"
      FOUND_BODY="$body"
      MDNS_NAME="$name"
      log "mDNS 命中：$name"
      break
    fi
  done
fi

# ========== Step4: 扫 /24 的 8090 端口 ==========

if [ -z "$CANDIDATES" ]; then
  log "mDNS 未命中，开始扫描 $SUBNET.0/24 的 $PORT 端口（约 3–6 秒）…"
  HITS=$(mktemp)
  trap 'rm -f "$HITS"' EXIT
  i=1
  while [ "$i" -le 254 ]; do
    (
      if nc -z -G "$TIMEOUT" -w "$TIMEOUT" "$SUBNET.$i" "$PORT" >/dev/null 2>&1; then
        echo "$i" >> "$HITS"
      fi
    ) &
    if [ $((i % 64)) -eq 0 ]; then wait; fi
    i=$((i + 1))
  done
  wait
  # 端口开着不等于就是设备端服务，逐个调 /system 确认。
  for octet in $(sort -n "$HITS"); do
    if body=$(probe_device "$SUBNET.$octet"); then
      CANDIDATES="$CANDIDATES $SUBNET.$octet"
      [ -z "${FOUND_BODY:-}" ] && FOUND_BODY="$body"
    fi
  done
  CANDIDATES=$(printf '%s\n' $CANDIDATES | tr '\n' ' ')
fi

if [ -z "$(printf '%s' "$CANDIDATES" | tr -d ' ')" ]; then
  echo "没有在 $SUBNET.0/24 找到设备端服务（端口 $PORT）" >&2
  echo "检查：① 板子是否开机且与 Mac 同网段 ② device_api.py 是否在跑 ③ 端口是否改成别的" >&2
  [ "$JSON" -eq 1 ] && echo '{"found":false}'
  exit 1
fi

# 多个候选时优先 instance_id 以 jetson 开头的那个（同网段可能还有别人的服务）。
DEVICE_HOST=""
DEVICE_BODY=""
for host in $CANDIDATES; do
  body=$(probe_device "$host") || continue
  instance=$(json_field "$body" instance_id)
  case "$instance" in
    jetson*|'') DEVICE_HOST="$host"; DEVICE_BODY="$body"; break ;;
  esac
  [ -z "$DEVICE_HOST" ] && { DEVICE_HOST="$host"; DEVICE_BODY="$body"; }
done

# 默认写 IPv4：比赛现场 AP 可能禁掉组播，IP 比 .local 更可预期；
# 想跟着 DHCP 走就用 --prefer name，写 qwq.local，换 IP 也不用重跑。
DEVICE_ALIAS=""
if [ "$PREFER" = "name" ] && [ -n "${MDNS_NAME:-}" ]; then
  DEVICE_HOST="$MDNS_NAME"
elif [ -n "${MDNS_NAME:-}" ] && [ "$DEVICE_HOST" = "$MDNS_NAME" ]; then
  resolved=$(resolve_name "$MDNS_NAME")
  if [ -n "$resolved" ]; then
    DEVICE_HOST="$resolved"
    DEVICE_ALIAS="$MDNS_NAME"
  fi
fi

INSTANCE=$(json_field "$DEVICE_BODY" instance_id)
CAMERA_UP=$(printf '%s' "$DEVICE_BODY" | grep -o '"available": true' | head -1)
[ -n "$CAMERA_UP" ] && CAMERA_STATE="就绪" || CAMERA_STATE="未就绪"
DEVICE_BASE="http://$DEVICE_HOST:$PORT"
log ""
[ -n "$DEVICE_ALIAS" ] && DEVICE_BASE="http://$DEVICE_HOST:$PORT"
log "设备端：${DEVICE_BASE}（${INSTANCE}，摄像头${CAMERA_STATE}）"
[ -n "$DEVICE_ALIAS" ] && log "mDNS 别名：${DEVICE_ALIAS}（如需跟随 DHCP，可加 --prefer name）"

# ========== Step5: 服务器连通性 ==========

SERVER_HOST=""
SERVER_PORT=""
HEALTH_URL=""
if [ -n "$SERVER_SPEC" ]; then
  case "$SERVER_SPEC" in
    *:*) SERVER_HOST="${SERVER_SPEC%:*}"; SERVER_PORT="${SERVER_SPEC##*:}" ;;
    *) SERVER_HOST="$SERVER_SPEC"; SERVER_PORT="${APP_PORT_SSH:-22}" ;;
  esac
elif [ -f "$SERVER_CONF" ]; then
  raw=$(cat "$SERVER_CONF")
  SERVER_HOST=$(json_field "$raw" ssh_host)
  SERVER_PORT=$(json_field "$raw" ssh_port)
  HEALTH_URL=$(json_field "$raw" health_url)
  SERVER_PORT="${SERVER_PORT:-22}"
fi
SERVER_PORT="${SERVER_PORT:-${APP_PORT_SSH:-22}}"

SERVER_STATE="未配置"
SERVER_DETAIL="没有服务器地址，可在 $SERVER_CONF 写 {\"ssh_host\":\"1.2.3.4\",\"ssh_port\":22}"
if [ -n "$SERVER_HOST" ]; then
  if nc -z -G 3 -w 3 "$SERVER_HOST" "$SERVER_PORT" >/dev/null 2>&1; then
    SERVER_STATE="可达"
    SERVER_DETAIL="SSH $SERVER_HOST:$SERVER_PORT 端口开放"
  else
    SERVER_STATE="不可达"
    SERVER_DETAIL="SSH $SERVER_HOST:$SERVER_PORT 连不上（换网络后端口被挡 / 服务器未开机）"
  fi
  if [ -n "$HEALTH_URL" ] && [ "$SERVER_STATE" = "可达" ]; then
    code=$(curl -s --noproxy '*' -m 5 -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null)
    SERVER_DETAIL="$SERVER_DETAIL；$HEALTH_URL → $code"
  fi
fi
log "服务器：${SERVER_STATE}（${SERVER_DETAIL}）"

# ========== Step6: 写入 App 配置 ==========

WROTE=""
if [ "$NO_WRITE" -eq 0 ]; then
  payload="{\"device_api_base\":\"$DEVICE_BASE\"}"
  for dir in "$CONTAINER_CONF" "$PLAIN_CONF"; do
    mkdir -p "$dir" 2>/dev/null || continue
    if printf '%s' "$payload" > "$dir/config.json" 2>/dev/null; then
      WROTE="$WROTE $dir/config.json"
    fi
  done
  log "已写入：$(printf '%s' "$WROTE" | sed 's/^ //')"
else
  log "（--no-write：未修改 App 配置）"
fi

# ========== Step7: 按需重启 App ==========

if [ "$RESTART" -eq 1 ]; then
  if pgrep -f "TeachingRobot.app/Contents/MacOS/TeachingRobot" >/dev/null 2>&1; then
    osascript -e 'quit app "TeachingRobot"' >/dev/null 2>&1
    sleep 2
  fi
  APP_PATH=$(ls -d /Applications/TeachingRobot.app "$HOME/Applications/TeachingRobot.app" 2>/dev/null | head -1)
  if [ -n "$APP_PATH" ]; then
    open "$APP_PATH" && log "已重启 $APP_PATH"
  else
    log "没找到已安装的 TeachingRobot.app，跳过重启（新配置下次启动生效）"
  fi
fi

if [ "$JSON" -eq 1 ]; then
  printf '{"found":true,"device_api_base":"%s","instance_id":"%s","camera":"%s","server_state":"%s","server_detail":"%s"}\n' \
    "$DEVICE_BASE" "$INSTANCE" "$CAMERA_STATE" "$SERVER_STATE" "$SERVER_DETAIL"
fi
