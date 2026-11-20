#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${XDG_RUNTIME_DIR:-/tmp}/aalc-isolated-display.env"
LAUNCH_GAME_SCRIPT="$REPO_DIR/scripts/launch_limbus_isolated.sh"

USE_DEV=0
WITH_GAME=0
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev)
            USE_DEV=1
            shift
            ;;
        --with-game)
            WITH_GAME=1
            shift
            ;;
        -h|--help)
            cat <<'EOF'
用法: ./scripts/start_isolated.sh [--dev] [--with-game] [附加启动参数...]

基于独立 rootful Xwayland 启动隔离后台挂机模式：
  - Limbus Company 与键鼠模拟运行在独立的隔离 X11 显示内
  - AALC 图形界面继续运行在宿主 Wayland 桌面，方便随时操作/查看日志
  - 宿主鼠标、键盘与当前工作窗口焦点不会被脚本抢占

选项:
  --dev        使用 main_dev.py（开发热重载模式）启动
  --with-game  启动 AALC 前立即在隔离显示内预启动 Limbus Company
  -h, --help   显示本帮助信息
EOF
            exit 0
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

if ! command -v Xwayland >/dev/null 2>&1; then
    echo "[AALC-隔离模式] 错误：未找到 Xwayland，请先安装 xwayland 软件包。" >&2
    exit 1
fi

HOST_WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}"
if [[ -z "$HOST_WAYLAND_DISPLAY" && -n "${XDG_RUNTIME_DIR:-}" ]]; then
    for sock in "$XDG_RUNTIME_DIR"/wayland-[0-9]*; do
        if [[ -S "$sock" ]]; then
            HOST_WAYLAND_DISPLAY="$(basename "$sock")"
            break
        fi
    done
fi
if [[ -z "$HOST_WAYLAND_DISPLAY" ]]; then
    echo "[AALC-隔离模式] 错误：未检测到 WAYLAND_DISPLAY。请在 Wayland 桌面会话中运行本脚本。" >&2
    exit 1
fi

if [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$REPO_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "[AALC-隔离模式] 错误：未找到可用的 Python 解释器（.venv/bin/python 或 python3）。" >&2
    exit 1
fi

# 检查是否已有 AALC 实例占用了单实例通信端口 62333
if "$PYTHON_BIN" - <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(0.5)
try:
    rc = s.connect_ex(("127.0.0.1", 62333))
finally:
    s.close()
sys.exit(0 if rc == 0 else 1)
PY
then
    echo "[AALC-隔离模式] 错误：检测到已有 AALC 实例正在运行（端口 62333 已被占用）。" >&2
    echo "请先关闭现有的 AALC 窗口，再运行 ./scripts/start_isolated.sh。" >&2
    exit 1
fi

if pgrep -fi 'LimbusCompany\.exe' >/dev/null 2>&1; then
    echo "[AALC-隔离模式] 提醒：检测到系统中已有 LimbusCompany.exe 正在运行。" >&2
    echo "              若该窗口运行在宿主桌面，请先手动关闭它，再点击 AALC 的开始任务（或重启本脚本）。" >&2
fi

# 从 config.yaml 读取目标分辨率，设置隔离屏尺寸略大于游戏客户区，避免全屏误判及 XGetImage 越界
TARGET_H="$(awk '/^[[:space:]]*set_win_size:[[:space:]]*[0-9]+/{sub(/#.*/, ""); sub(/^[^:]*:[[:space:]]*/, ""); sub(/[^0-9].*/, ""); if ($0 != "") {print $0; exit}}' "$REPO_DIR/config.yaml" 2>/dev/null || true)"
if [[ ! "$TARGET_H" =~ ^[0-9]+$ ]] || (( TARGET_H < 360 || TARGET_H > 4320 )); then
    TARGET_H=1080
fi
TARGET_W="$((TARGET_H * 16 / 9))"
GEOMETRY="${AALC_ISOLATED_GEOMETRY:-$((TARGET_W + 40))x$((TARGET_H + 80))}"

# 查找 :2 .. :50 中第一个空闲 X 显示号
DISP_NUM=""
for n in $(seq 2 50); do
    if [[ ! -e "/tmp/.X11-unix/X$n" && ! -e "/tmp/.X$n-lock" ]]; then
        DISP_NUM="$n"
        break
    fi
done
if [[ -z "$DISP_NUM" ]]; then
    echo "[AALC-隔离模式] 错误：未找到空闲的 X11 显示号（:2..:50 均被占用）。" >&2
    exit 1
fi

XWAYLAND_LOG="${XDG_RUNTIME_DIR:-/tmp}/aalc-xwayland-${DISP_NUM}.log"
Xwayland ":$DISP_NUM" -geometry "$GEOMETRY" -decorate -noreset -nolisten tcp >"$XWAYLAND_LOG" 2>&1 &
XWAYLAND_PID=$!
APP_PID=""
CLEANED=0

_stop_pid() {
    local pid="${1:-}"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
        kill -TERM "$pid" >/dev/null 2>&1 || true
        for _ in $(seq 1 20); do
            if ! kill -0 "$pid" >/dev/null 2>&1; then
                return 0
            fi
            sleep 0.1
        done
        if kill -0 "$pid" >/dev/null 2>&1; then
            kill -9 "$pid" >/dev/null 2>&1 || true
        fi
    fi
}

cleanup() {
    local rc=$?
    if [[ "$CLEANED" -eq 1 ]]; then
        exit "$rc"
    fi
    CLEANED=1
    trap - EXIT INT TERM
    _stop_pid "$APP_PID"
    if [[ -f "$ENV_FILE" ]] && grep -q "XWAYLAND_PID=\"$XWAYLAND_PID\"" "$ENV_FILE" 2>/dev/null; then
        rm -f "$ENV_FILE"
    fi
    _stop_pid "$XWAYLAND_PID"
    exit "$rc"
}
trap cleanup EXIT INT TERM

XWAYLAND_READY=0
for _ in $(seq 1 50); do
    if ! kill -0 "$XWAYLAND_PID" >/dev/null 2>&1; then
        echo "[AALC-隔离模式] 错误：Xwayland :${DISP_NUM} 启动失败，日志如下：" >&2
        cat "$XWAYLAND_LOG" >&2 || true
        exit 1
    fi
    if [[ -S "/tmp/.X11-unix/X${DISP_NUM}" ]] && "$PYTHON_BIN" - ":$DISP_NUM" <<'PY' >/dev/null 2>&1
import sys
from Xlib import display
d = display.Display(sys.argv[1])
d.close()
PY
    then
        XWAYLAND_READY=1
        break
    fi
    sleep 0.1
done

if [[ "$XWAYLAND_READY" -ne 1 ]]; then
    echo "[AALC-隔离模式] 错误：等待 Xwayland :${DISP_NUM} 就绪超时，日志如下：" >&2
    cat "$XWAYLAND_LOG" >&2 || true
    exit 1
fi

cat >"$ENV_FILE" <<EOF
ISOLATED_DISPLAY=":$DISP_NUM"
XWAYLAND_PID="$XWAYLAND_PID"
TARGET_W="$TARGET_W"
TARGET_H="$TARGET_H"
EOF

echo "============================================================"
echo "AALC 隔离后台挂机模式已启动"
echo "  隔离显示: :${DISP_NUM} (分辨率 ${GEOMETRY})"
echo "  Xwayland PID: ${XWAYLAND_PID}"
echo "  用法提示:"
echo "    1. 游戏将作为独立窗口运行在隔离显示 :${DISP_NUM} 内。"
echo "    2. 你可以将 Xwayland 窗口切到后台、被其他应用遮挡或移到其他工作区，"
echo "       不要最小化或关闭该 Xwayland 窗口。"
echo "    3. 宿主鼠标、键盘和当前窗口焦点完全归你使用，脚本不会抢占。"
echo "============================================================"

if [[ "$WITH_GAME" -eq 1 ]]; then
    echo "[AALC-隔离模式] 正在隔离显示 :${DISP_NUM} 中预启动 Limbus Company..."
    DISPLAY=":$DISP_NUM" AALC_ISOLATED_DISPLAY=":$DISP_NUM" "$LAUNCH_GAME_SCRIPT" >/dev/null 2>&1 &
fi

ENTRY_SCRIPT="$REPO_DIR/main.py"
if [[ "$USE_DEV" -eq 1 ]]; then
    ENTRY_SCRIPT="$REPO_DIR/main_dev.py"
fi

cd "$REPO_DIR"
QT_QPA_PLATFORM=wayland \
WAYLAND_DISPLAY="$HOST_WAYLAND_DISPLAY" \
DISPLAY=":$DISP_NUM" \
AALC_ISOLATED_DISPLAY=":$DISP_NUM" \
XDG_SESSION_TYPE=x11 \
AALC_GAME_LAUNCH_SCRIPT="$LAUNCH_GAME_SCRIPT" \
"$PYTHON_BIN" "$ENTRY_SCRIPT" "${EXTRA_ARGS[@]}" &
APP_PID=$!
wait "$APP_PID"
