#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${XDG_RUNTIME_DIR:-/tmp}/aalc-isolated-display.env"

# 读取目标隔离显示号：优先显式指定的 AALC_ISOLATED_DISPLAY，其次状态文件，最后非 :0 的 DISPLAY
TARGET_DISPLAY="${AALC_ISOLATED_DISPLAY:-}"
if [[ -z "$TARGET_DISPLAY" && -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    TARGET_DISPLAY="${ISOLATED_DISPLAY:-}"
fi
if [[ -z "$TARGET_DISPLAY" && -n "${DISPLAY:-}" && "${DISPLAY}" != ":0" && "${DISPLAY}" != ":0.0" ]]; then
    TARGET_DISPLAY="$DISPLAY"
fi

if [[ -z "$TARGET_DISPLAY" ]]; then
    echo "[AALC-隔离启动] 错误：未找到隔离 X11 显示号。请先通过 scripts/start_isolated.sh 启动。" >&2
    exit 1
fi

DISP_NUM="${TARGET_DISPLAY#:}"
DISP_NUM="${DISP_NUM%%.*}"
if [[ ! -S "/tmp/.X11-unix/X${DISP_NUM}" ]]; then
    echo "[AALC-隔离启动] 错误：隔离显示 :${DISP_NUM} 尚未就绪（缺少 /tmp/.X11-unix/X${DISP_NUM}）。" >&2
    exit 1
fi

# 从 config.yaml 读取 set_win_size（默认 1080）
TARGET_H="$(awk '/^[[:space:]]*set_win_size:[[:space:]]*[0-9]+/{sub(/#.*/, ""); sub(/^[^:]*:[[:space:]]*/, ""); sub(/[^0-9].*/, ""); if ($0 != "") {print $0; exit}}' "$REPO_DIR/config.yaml" 2>/dev/null || true)"
if [[ ! "$TARGET_H" =~ ^[0-9]+$ ]] || (( TARGET_H < 360 || TARGET_H > 4320 )); then
    TARGET_H=1080
fi
TARGET_W="$((TARGET_H * 16 / 9))"

# 定位 Steam 安装目录、游戏目录与 compatdata
STEAM_ROOT="${STEAM_ROOT:-}"
if [[ -z "$STEAM_ROOT" || ! -d "$STEAM_ROOT/steamapps" ]]; then
    STEAM_ROOT=""
    for candidate in \
        "$HOME/.local/share/Steam" \
        "$HOME/.steam/debian-installation" \
        "$HOME/.steam/root" \
        "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam"
    do
        if [[ -d "$candidate/steamapps" ]]; then
            STEAM_ROOT="$candidate"
            break
        fi
    done
fi

if [[ -z "$STEAM_ROOT" ]]; then
    echo "[AALC-隔离启动] 错误：未找到 Steam 目录（可通过 STEAM_ROOT 显式指定）。" >&2
    exit 1
fi

GAME_DIR="${AALC_GAME_DIR:-$STEAM_ROOT/steamapps/common/Limbus Company}"
GAME_EXE="$GAME_DIR/LimbusCompany.exe"
COMPAT_DATA="${AALC_COMPAT_DATA:-$STEAM_ROOT/steamapps/compatdata/1973530}"
SLR_ENTRY="$STEAM_ROOT/steamapps/common/SteamLinuxRuntime_4/_v2-entry-point"

# 自动探测 Proton：优先 AALC_PROTON_BIN，其次 compatdata/1973530/config_info，最后常见 Proton 目录
PROTON_BIN="${AALC_PROTON_BIN:-}"
if [[ -z "$PROTON_BIN" && -f "$COMPAT_DATA/config_info" ]]; then
    config_proton_line="$(sed -n '2p' "$COMPAT_DATA/config_info" 2>/dev/null || true)"
    if [[ "$config_proton_line" == */files/* ]]; then
        candidate_proton="${config_proton_line%%/files/*}/proton"
        if [[ -x "$candidate_proton" ]]; then
            PROTON_BIN="$candidate_proton"
        fi
    fi
fi
if [[ -z "$PROTON_BIN" ]]; then
    for candidate_proton in \
        "$STEAM_ROOT/steamapps/common/Proton - Experimental/proton" \
        "$STEAM_ROOT"/steamapps/common/Proton*/proton
    do
        if [[ -x "$candidate_proton" ]]; then
            PROTON_BIN="$candidate_proton"
            break
        fi
    done
fi

if [[ ! -f "$GAME_EXE" ]]; then
    echo "[AALC-隔离启动] 错误：未找到游戏可执行文件：$GAME_EXE（可通过 AALC_GAME_DIR 指定）" >&2
    exit 1
fi
if [[ -z "$PROTON_BIN" || ! -x "$PROTON_BIN" ]]; then
    echo "[AALC-隔离启动] 错误：未找到可执行的 Proton（可通过 AALC_PROTON_BIN 指定）" >&2
    exit 1
fi
if [[ ! -d "$COMPAT_DATA" ]]; then
    mkdir -p "$COMPAT_DATA"
fi

if ! pgrep -x steam >/dev/null 2>&1; then
    echo "[AALC-隔离启动] 警告：未检测到运行中的 Steam 客户端，若游戏提示无法连接 Steam 请先启动 Steam。" >&2
fi

WINESERVER_BIN="$(dirname "$PROTON_BIN")/files/bin/wineserver"
if [[ -x "$WINESERVER_BIN" ]] && ! ps -eo comm=,args= | awk '/LimbusCompany\.exe/ && $1 !~ /^(python|bash|sh)/ {found=1} END {exit !found}'; then
    # 清理上次异常退出或旧 Xwayland 关闭后残留的孤儿 wineserver，避免新进程卡在失效的 DISPLAY 上
    WINEPREFIX="$COMPAT_DATA/pfx" "$WINESERVER_BIN" -k >/dev/null 2>&1 || true
    sleep 0.5
fi

unset WAYLAND_DISPLAY || true
unset LD_PRELOAD || true
export DISPLAY=":$DISP_NUM"
export SDL_VIDEODRIVER=x11
export SteamAppId=1973530
export SteamGameId=1973530
export STEAM_COMPAT_APP_ID=1973530
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$COMPAT_DATA"
export STEAM_COMPAT_INSTALL_PATH="$GAME_DIR"
export STEAM_COMPAT_LIBRARY_PATHS="$STEAM_ROOT/steamapps"
export DXVK_CONFIG="${DXVK_CONFIG:-dxgi.syncInterval=0}"

cd "$GAME_DIR"

GAME_ARGS=(
    "-screen-fullscreen" "0"
    "-screen-width" "$TARGET_W"
    "-screen-height" "$TARGET_H"
)
if [[ "${AALC_FORCE_D3D11:-1}" == "1" ]]; then
    GAME_ARGS=("-force-d3d11" "${GAME_ARGS[@]}")
fi
if [[ -n "${AALC_GAME_EXTRA_ARGS:-}" ]]; then
    read -r -a extra_args <<< "$AALC_GAME_EXTRA_ARGS"
    GAME_ARGS+=("${extra_args[@]}")
fi

# Ubuntu 24.04+ 默认开启 kernel.apparmor_restrict_unprivileged_userns=1，
# 普通终端调用 SteamLinuxRuntime_4 的 srt-bwrap 会报 uid map Permission denied；
# 仅在当前进程允许创建 userns 时才经由 SteamLinuxRuntime_4 启动，否则直接调用 Proton。
if [[ -x "$SLR_ENTRY" ]] && command -v unshare >/dev/null 2>&1 && unshare -Ur true >/dev/null 2>&1; then
    exec "$SLR_ENTRY" --verb=waitforexitandrun -- "$PROTON_BIN" waitforexitandrun "$GAME_EXE" "${GAME_ARGS[@]}"
else
    exec "$PROTON_BIN" waitforexitandrun "$GAME_EXE" "${GAME_ARGS[@]}"
fi
