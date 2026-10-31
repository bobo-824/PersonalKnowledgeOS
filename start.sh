#!/usr/bin/env bash
# Personal Knowledge OS 启动脚本（Git Bash / Linux / macOS）
#
#   ./start.sh              单端口模式，浏览器访问 http://127.0.0.1:8000
#   ./start.sh --dev        开发模式，额外启动 Vite，浏览器访问 http://127.0.0.1:5173
#   ./start.sh --no-browser 启动但不自动打开浏览器
#   ./start.sh --skip-doctor 跳过环境自检
#   ./start.sh --help       显示本帮助
#
# 注意：本文件必须保持 LF 换行。CRLF 会让 bash 报
# "bad interpreter: /usr/bin/env bash^M" 而无法启动。

set -euo pipefail

# 子进程统一 UTF-8，避免中文 Windows 的 GBK 控制台输出乱码
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

UNAME="$(uname -s)"
is_windows() { case "$UNAME" in MINGW*|MSYS*|CYGWIN*) return 0 ;; *) return 1 ;; esac }

DEV=false
OPEN_BROWSER=true
RUN_DOCTOR=true

for arg in "$@"; do
    case "$arg" in
        --dev|-dev)      DEV=true ;;
        --no-browser)    OPEN_BROWSER=false ;;
        --skip-doctor)   RUN_DOCTOR=false ;;
        -h|--help)
            sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "[XX] 无法识别的参数：$arg"
            echo "     可用参数：--dev  --no-browser  --skip-doctor  --help"
            exit 1
            ;;
    esac
done

step() { printf '\n==> %s\n' "$1"; }

fail() {
    printf '\n[XX] %s\n' "$1"
    [ -n "${2:-}" ] && printf '     怎么办：%s\n' "$2"
    printf '\n'
    exit 1
}

need() { command -v "$1" >/dev/null 2>&1 || fail "找不到 $1 命令" "$2"; }

need python "安装 Python 3.13 或更新版本：https://www.python.org/downloads/"
need uv 'PowerShell 执行：powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'

# --------------------------------------------------------------------------
# 读取 .env 里的端口，与 backend/config.py 保持一致，不硬编码
# --------------------------------------------------------------------------
env_value() {
    local key="$1" default="$2" line value
    if [ -f .env ]; then
        line="$(grep -E "^[[:space:]]*${key}=" .env | tail -1 || true)"
        if [ -n "$line" ]; then
            value="${line#*=}"
            value="${value#"${value%%[![:space:]]*}"}"   # 去前导空白
            value="${value%"${value##*[![:space:]]}"}"   # 去尾随空白
            value="${value#\"}"; value="${value%\"}"
            value="${value#\'}"; value="${value%\'}"
            if [ -n "$value" ]; then printf '%s' "$value"; return; fi
        fi
    fi
    printf '%s' "$default"
}

PORT="$(env_value PKOS_PORT 8000)"
HOST="$(env_value PKOS_HOST 127.0.0.1)"
FRONTEND_PORT="$(env_value PKOS_FRONTEND_PORT 5173)"

BACKEND_URL="http://${HOST}:${PORT}"
VITE_URL="http://${HOST}:${FRONTEND_PORT}"

# --------------------------------------------------------------------------
# 退出时清理子进程。
# npm/vite 会派生 node 子进程，只杀父进程会留下孤儿继续占着端口。
# Windows(Git Bash) 上没有真正的进程组，用 taskkill /T 杀整棵树。
# --------------------------------------------------------------------------
CHILD_PIDS=()

kill_tree() {
    local pid="$1"
    kill -0 "$pid" 2>/dev/null || return 0
    if is_windows; then
        taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    else
        kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    fi
}

cleanup() {
    local code=$? pid
    trap - EXIT INT TERM
    if [ ${#CHILD_PIDS[@]} -gt 0 ]; then
        printf '\n==> 正在关闭后台进程…\n'
        for pid in "${CHILD_PIDS[@]}"; do kill_tree "$pid"; done
        sleep 1
        for pid in "${CHILD_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                if is_windows; then
                    taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
                else
                    kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
                fi
            fi
        done
    fi
    exit $code
}
trap cleanup INT TERM EXIT

open_browser() {
    local url="$1"
    if is_windows; then
        cmd //c start "" "$url" >/dev/null 2>&1 || true
    elif [ "$UNAME" = "Darwin" ]; then
        open "$url" >/dev/null 2>&1 || true
    else
        xdg-open "$url" >/dev/null 2>&1 || true
    fi
}

# 参数通过 argv 传给 python，不把 shell 变量拼进源码里
port_open() {
    python - "$HOST" "$1" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
s.settimeout(0.4)
try:
    sys.exit(0 if s.connect_ex((host, port)) == 0 else 1)
finally:
    s.close()
PY
}

wait_for_port() {
    local port="$1" tries="${2:-60}"
    for _ in $(seq 1 "$tries"); do
        port_open "$port" && return 0
        sleep 0.5
    done
    return 1
}

# --------------------------------------------------------------------------
# 1. 环境自检
# --------------------------------------------------------------------------
if $RUN_DOCTOR; then
    step "环境自检（想跳过请加 --skip-doctor）"
    python scripts/doctor.py || fail \
        "环境自检发现必须先解决的问题（标记为 [XX] 的项）" \
        "按上面每一项的[怎么办]提示处理后，重新运行 ./start.sh"
fi

# --------------------------------------------------------------------------
# 2. 数据库迁移
# --------------------------------------------------------------------------
step "检查数据库迁移"
uv run alembic upgrade head || fail "数据库迁移失败" \
    "先运行 python scripts/setup.py 完成安装；或看上面的报错定位问题"
echo "[OK] 数据库结构已是最新"

# --------------------------------------------------------------------------
# 3. 前端构建产物（单端口模式需要）
# --------------------------------------------------------------------------
if ! $DEV && [ ! -f frontend/dist/index.html ]; then
    step "前端还没有构建，现在构建（只影响首次启动）"
    need npm "安装 Node.js LTS：https://nodejs.org/"
    # 用 pushd 而不是子 shell：子 shell 里的 fail 只会退出子 shell，
    # 报错信息会被吞掉，用户看不到真正的原因。
    pushd frontend >/dev/null
    if [ ! -d node_modules ]; then
        npm ci || { popd >/dev/null; fail "npm ci 失败" \
            "网络问题可重试；或配置镜像：npm config set registry https://registry.npmmirror.com"; }
    fi
    npm run build || { popd >/dev/null; fail "npm run build 失败" \
        "可以改用开发模式：./start.sh --dev（不需要构建）"; }
    popd >/dev/null
    echo "[OK] 前端构建完成"
fi

# --------------------------------------------------------------------------
# 4. 启动服务
# --------------------------------------------------------------------------
if $DEV; then
    need npm "安装 Node.js LTS：https://nodejs.org/"
    step "启动 Vite 开发服务器（端口 ${FRONTEND_PORT}）"
    pushd frontend >/dev/null
    npm run dev &
    CHILD_PIDS+=($!)
    popd >/dev/null

    step "启动后端（端口 ${PORT}，热重载已开启）"
    echo "    浏览器地址：${VITE_URL}"
    echo "    改后端代码会自动重启，改前端代码会自动刷新。"
    echo "    按 Ctrl+C 停止（会自动关掉 Vite）。"
    echo

    if $OPEN_BROWSER; then
        wait_for_port "$FRONTEND_PORT" 60 && open_browser "$VITE_URL"
    fi

    uv run uvicorn backend.main:app --host "$HOST" --port "$PORT" --reload
else
    step "启动应用（单端口模式）"
    echo "    浏览器地址：${BACKEND_URL}"
    echo "    这一个进程同时提供页面和 API。按 Ctrl+C 停止。"
    echo

    if $OPEN_BROWSER; then
        # 后端在下面前台启动，这里先起一个后台等待者负责开浏览器
        ( wait_for_port "$PORT" 60 && open_browser "$BACKEND_URL" ) &
        CHILD_PIDS+=($!)
    fi

    uv run uvicorn backend.main:app --host "$HOST" --port "$PORT"
fi
