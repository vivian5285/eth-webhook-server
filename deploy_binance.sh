#!/usr/bin/env bash
# DEPLOY_BINANCE_SHELL_MARKER — 本文件必须是 Bash 部署脚本，勿用 dingtalk.py 等内容覆盖
# ==========================================
# 币安 Binance — 工业级干净重部署脚本
# 版本: v13.1-daemon2  (使用 gunicorn --daemon，勿用 nohup)
# 流程: 强制核武清场 → 确认端口空闲 → 依赖 → 启动 → 多重健康审计
# ==========================================

set -uo pipefail

if ! head -1 "$0" | grep -q bash; then
    echo "❌ deploy_binance.sh 已损坏（首行不是 bash shebang），请恢复正确 Shell 脚本"
    exit 1
fi
if ! grep -q 'DEPLOY_BINANCE_SHELL_MARKER' "$0"; then
    echo "❌ deploy_binance.sh 内容异常（缺少部署标记），可能被 dingtalk.py 误覆盖"
    exit 1
fi

DEPLOY_SCRIPT_VERSION="v13.76-deploy-pid-health-robust"
# 接受 v13.4.6+、v13.5~9、v13.10+（含 -tv-pure-sl / always-close-then-open 等后缀）
# 注意：ERE 不用 (?:...)，否则部分 grep 会报 "? at start of expression"
MIN_SUPERVISOR_VERSION_RE='v13\.(4\.[6-9]|([5-9]|[1-9][0-9]+)\.)'

PORT=5003
WORKERS=1
THREADS=10
BIND_HOST="0.0.0.0"
MAX_CLEANUP_ROUNDS=5
HEALTH_WAIT_SEC=5
HEALTH_RETRIES=6
# 启动后轮询 PID/端口（daemon 写 pid 与 fork 有延迟，勿只 sleep 2 就判失败）
STARTUP_WAIT_SEC=12

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

LOG_DIR="$DIR/logs"
LOG_FILE="$LOG_DIR/supervisor_binance.log"
BRAIN_LOG="$LOG_DIR/binance_brain.log"
PID_FILE="$LOG_DIR/gunicorn_binance.pid"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[1;36m'
NC='\033[0m'

DEPLOY_OK=1

log_step() { echo -e "${YELLOW}$1${NC}"; }
log_ok()   { echo -e "  ${GREEN}✅ $1${NC}"; }
log_warn() { echo -e "  ${YELLOW}⚠️  $1${NC}"; }
log_fail() { echo -e "  ${RED}❌ $1${NC}"; DEPLOY_OK=0; }

load_env() {
    if [ -f "$DIR/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        source "$DIR/.env"
        set +a
        log_ok "已加载 .env 配置"
    else
        log_warn "未找到 .env，将使用默认/环境变量"
    fi
    WEBHOOK_SECRET="${WEBHOOK_SECRET:-528586}"
}

kill_by_port() {
    local port=$1
    if command -v fuser >/dev/null 2>&1; then
        fuser -k -9 "${port}/tcp" 2>/dev/null || true
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -lptn "sport = :${port}" 2>/dev/null \
            | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
            | sort -u | xargs -r kill -9 2>/dev/null || true
    fi
}

kill_residual_processes() {
    pkill -9 -f "gunicorn.*:${PORT}"            2>/dev/null || true
    pkill -9 -f "gunicorn.*${PORT}"             2>/dev/null || true
    pkill -9 -f "gunicorn.*${DIR}.*app:app"     2>/dev/null || true
    pkill -9 -f "position_supervisor_binance"   2>/dev/null || true
    pkill -9 -f "position_supervisor.py"        2>/dev/null || true
    if [ -f "$PID_FILE" ]; then
        OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
        if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
            kill -9 "$OLD_PID" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
}

port_in_use() {
    if command -v lsof >/dev/null 2>&1 && lsof -Pi :"${PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
        return 0
    fi
    if command -v ss >/dev/null 2>&1 && ss -lnt "sport = :${PORT}" 2>/dev/null | grep -q LISTEN; then
        return 0
    fi
    if command -v netstat >/dev/null 2>&1 && netstat -tuln 2>/dev/null | grep -q ":${PORT} "; then
        return 0
    fi
    return 1
}

show_port_holders() {
    log_warn "端口 ${PORT} 仍被占用，当前监听进程:"
    if command -v lsof >/dev/null 2>&1; then
        lsof -Pi :"${PORT}" -sTCP:LISTEN 2>/dev/null || true
    elif command -v ss >/dev/null 2>&1; then
        ss -lptn "sport = :${PORT}" 2>/dev/null || true
    elif command -v netstat >/dev/null 2>&1; then
        netstat -tulnp 2>/dev/null | grep ":${PORT} " || true
    fi
}

force_cleanup() {
    log_step "[1/6] 强制核武清场：端口 ${PORT} + 全部币安残留进程..."
    local round=1
    while [ "$round" -le "$MAX_CLEANUP_ROUNDS" ]; do
        echo "  -> 清场第 ${round}/${MAX_CLEANUP_ROUNDS} 轮..."
        kill_residual_processes
        kill_by_port "$PORT"
        sleep 1.2
        if ! port_in_use; then
            log_ok "端口 ${PORT} 已完全释放，清场成功"
            rm -f "${DIR}/logs/.recover_singleton.lock" 2>/dev/null || true
            return 0
        fi
        round=$((round + 1))
    done
    show_port_holders
    log_fail "经过 ${MAX_CLEANUP_ROUNDS} 轮清场，端口 ${PORT} 仍被占用，部署中止"
    return 1
}

install_deps() {
    log_step "[2/6] 检查 Python 环境与依赖..."
    if [ -d "$DIR/venv" ]; then
        # shellcheck disable=SC1091
        source "$DIR/venv/bin/activate"
        log_ok "已激活 venv"
    else
        log_warn "未找到 venv，使用系统 Python"
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        log_fail "未找到 python3"
        return 1
    fi
    if ! command -v pip >/dev/null 2>&1 && ! command -v pip3 >/dev/null 2>&1; then
        log_fail "未找到 pip"
        return 1
    fi
    PIP_CMD="pip"
    command -v pip3 >/dev/null 2>&1 && PIP_CMD="pip3"
    $PIP_CMD install -q -r "$DIR/requirements.txt"
    log_ok "requirements.txt 依赖已就绪"

    find "$DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find "$DIR" -name "*.pyc" -delete 2>/dev/null || true

    SUPERVISOR_VER="$(grep 'BINANCE_VPS_VERSION' "$DIR/position_supervisor_binance.py" 2>/dev/null | head -1 || true)"
    if echo "$SUPERVISOR_VER" | grep -qE "BINANCE_VPS_VERSION.*\"${MIN_SUPERVISOR_VERSION_RE}"; then
        log_ok "position_supervisor_binance.py 版本已就绪 (${SUPERVISOR_VER})"
    else
        log_fail "position_supervisor_binance.py 版本异常！需要 v13.4.6+ / v13.10+ ，当前: ${SUPERVISOR_VER:-未找到 BINANCE_VPS_VERSION}"
        return 1
    fi

    if grep -q "report_tv_signal_received" "$DIR/dingtalk.py" 2>/dev/null \
        && grep -q "report_tv_position_add" "$DIR/dingtalk.py" 2>/dev/null \
        && grep -q "EXCHANGE_LEVERAGE" "$DIR/position_supervisor_binance.py" 2>/dev/null; then
        log_ok "v13.10+ TV比例/纯tv_sl/信号接收钉钉 已就绪"
    elif echo "$SUPERVISOR_VER" | grep -qE 'v13\.(10|11)\.'; then
        log_fail "v13.10+ 需 report_tv_signal_received + report_tv_position_add + EXCHANGE_LEVERAGE"
        return 1
    fi

    if grep -q "report_smart_same_dir_decision" "$DIR/dingtalk.py" 2>/dev/null \
        && grep -q "open_atr" "$DIR/dingtalk.py" 2>/dev/null \
        && grep -q "tv_atr" "$DIR/dingtalk.py" 2>/dev/null; then
        log_ok "dingtalk.py 智能同向筛选 (open_atr/tv_atr) 已就绪"
    elif echo "$SUPERVISOR_VER" | grep -qE 'v13\.5\.'; then
        log_fail "dingtalk.py 未同步！v13.5+ 需 report_smart_same_dir_decision(open_atr,tv_atr)，否则运行时报 TypeError"
        return 1
    elif grep -q "币安黄金" "$DIR/dingtalk.py" 2>/dev/null; then
        log_ok "dingtalk.py 金色主题已就绪"
    else
        log_warn "dingtalk.py 可能不是最新版"
    fi

    if grep -qE 'BINANCE_CLIENT_VERSION|v13\.(4[0-9]|[5-9][0-9]?)\.|Binance Client v13' "$DIR/binance_client.py" 2>/dev/null; then
        CLIENT_VER="$(grep 'BINANCE_CLIENT_VERSION' "$DIR/binance_client.py" 2>/dev/null | head -1 || true)"
        log_ok "binance_client.py 版本已就绪 (${CLIENT_VER:-ok})"
    else
        log_warn "binance_client.py 可能不是最新版（建议含 BINANCE_CLIENT_VERSION）"
    fi

    if grep -q -- '--daemon' "$DIR/deploy_binance.sh" 2>/dev/null; then
        log_ok "deploy_binance.sh ${DEPLOY_SCRIPT_VERSION}（daemon 模式）"
    else
        log_fail "deploy_binance.sh 仍是旧版（含 nohup）！请 git pull 最新代码"
        return 1
    fi

    python3 -m py_compile "$DIR/app.py" "$DIR/binance_client.py" \
        "$DIR/dingtalk.py" "$DIR/position_supervisor_binance.py" "$DIR/tv_seq.py" 2>/dev/null \
        && log_ok "核心 Python 文件语法检查通过" \
        || { log_fail "Python 语法检查失败（请检查 dingtalk.py / supervisor / tv_seq）"; return 1; }
}

get_gunicorn_master_pid() {
    local pid=""
    if [ -f "$PID_FILE" ]; then
        pid="$(tr -d '[:space:]' < "$PID_FILE" 2>/dev/null || true)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
    fi
    # 回退：按端口找监听进程（daemon 时 pid 文件偶发滞后）
    if command -v lsof >/dev/null 2>&1; then
        pid="$(lsof -t -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -1 || true)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
    fi
    if command -v ss >/dev/null 2>&1; then
        pid="$(ss -lptn "sport = :${PORT}" 2>/dev/null \
            | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
            | head -1 || true)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
    fi
    # 回退：本目录 gunicorn 进程
    pid="$(pgrep -f "gunicorn.*${PORT}.*app:app" 2>/dev/null | head -1 || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "$pid"
        return 0
    fi
    return 1
}

http_health_ok() {
    local body
    body="$(curl -sf --max-time 3 "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo "")"
    echo "$body" | grep -q "binance_webhook"
}

start_service() {
    log_step "[3/6] 启动 Gunicorn 网关 (workers=${WORKERS}, threads=${THREADS})..."
    mkdir -p "$LOG_DIR"
    touch "$BRAIN_LOG" 2>/dev/null || true
    chmod 664 "$BRAIN_LOG" 2>/dev/null || true
    chmod 775 "$LOG_DIR" 2>/dev/null || true
    : > "$LOG_FILE"
    rm -f "$PID_FILE"

    # 使用 --daemon 正式脱离终端，避免 nohup & 被 shell 作业控制 SIGKILL
    if ! gunicorn \
        --workers "$WORKERS" \
        --threads "$THREADS" \
        --timeout 120 \
        --graceful-timeout 30 \
        --bind "${BIND_HOST}:${PORT}" \
        --pid "$PID_FILE" \
        --log-file "$LOG_FILE" \
        --access-logfile "$LOG_DIR/gunicorn_access.log" \
        --error-logfile "$LOG_DIR/gunicorn_error.log" \
        --capture-output \
        --daemon \
        app:app
    then
        log_fail "gunicorn 命令本身退出非零"
        tail -n 40 "$LOG_FILE" 2>/dev/null || true
        tail -n 20 "$LOG_DIR/gunicorn_error.log" 2>/dev/null || true
        return 1
    fi

    # 轮询最多 ~12s：PID 文件 / 端口 LISTEN / /health —— 任一成功即视为启动成功
    # （旧逻辑 sleep 2 只查 PID，daemon 写 pid 滞后时会误报失败并 exit，尽管服务已起来）
    local i=0
    local max_i=24
    local found_pid=""
    while [ "$i" -lt "$max_i" ]; do
        found_pid="$(get_gunicorn_master_pid || true)"
        if [ -n "$found_pid" ]; then
            log_ok "Gunicorn 已启动 PID=${found_pid}（轮询 $((i + 1))/${max_i}）"
            return 0
        fi
        if port_in_use; then
            found_pid="$(get_gunicorn_master_pid || true)"
            log_ok "端口 ${PORT} 已监听${found_pid:+ · PID=${found_pid}}（轮询 $((i + 1))/${max_i}）"
            return 0
        fi
        if http_health_ok; then
            log_ok "GET /health 已通（轮询 $((i + 1))/${max_i}）→ 服务正常"
            return 0
        fi
        sleep 0.5
        i=$((i + 1))
    done

    log_fail "Gunicorn 启动失败：${STARTUP_WAIT_SEC}s 内无 PID / 端口 /health"
    tail -n 40 "$LOG_FILE" 2>/dev/null || true
    tail -n 20 "$LOG_DIR/gunicorn_error.log" 2>/dev/null || true
    return 1
}
wait_for_listen() {
    log_step "[4/6] 等待端口 ${PORT} 进入 LISTEN 状态..."
    local i=1
    while [ "$i" -le "$HEALTH_RETRIES" ]; do
        if port_in_use; then
            log_ok "端口 ${PORT} 已开始监听 (第 ${i} 次检测)"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    log_fail "Gunicorn 进程存在但端口 ${PORT} 未监听"
    tail -n 20 "$LOG_FILE" 2>/dev/null || true
    return 1
}

health_check() {
    log_step "[5/6] 多重健康审计..."
    sleep "$HEALTH_WAIT_SEC"

    HEALTH_BODY="$(curl -sf "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo "")"
    HEALTH_OK=0
    if echo "$HEALTH_BODY" | grep -q "binance_webhook"; then
        HEALTH_OK=1
        log_ok "GET /health 正常 → ${HEALTH_BODY}"
    else
        log_fail "GET /health 异常 → ${HEALTH_BODY:-无响应}"
    fi

    HTTP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "http://127.0.0.1:${PORT}/webhook" \
        -H "Content-Type: application/json" \
        -d "{\"secret\":\"${WEBHOOK_SECRET}\",\"action\":\"PING\"}" 2>/dev/null || echo "000")"
    if [ "$HTTP_STATUS" = "200" ]; then
        log_ok "POST /webhook 回路 200 OK（secret 校验通过）"
    else
        log_fail "POST /webhook 异常 HTTP=${HTTP_STATUS}"
    fi

    GUNICORN_PID="$(get_gunicorn_master_pid || true)"
    if [ -n "$GUNICORN_PID" ] && kill -0 "$GUNICORN_PID" 2>/dev/null; then
        log_ok "Gunicorn 主进程存活 PID=${GUNICORN_PID}"
    elif [ "$HEALTH_OK" -eq 1 ] && [ "$HTTP_STATUS" = "200" ]; then
        log_warn "PID 文件进程已变，但 HTTP 健康检查全部通过（服务正常运行）"
    else
        log_fail "Gunicorn 主进程已退出且 HTTP 检查未通过"
    fi

    sleep 2
    if grep -qE 'v13\.(4\.[6-9]|([5-9]|[1-9][0-9]+))' "$BRAIN_LOG" 2>/dev/null; then
        log_ok "VPS 大脑 v13.4.6+ / v13.10+ 已成功加载"
    elif grep -q "币安 VPS" "$BRAIN_LOG" 2>/dev/null || grep -q "军师托管版" "$BRAIN_LOG" 2>/dev/null; then
        log_warn "大脑已加载但版本可能过旧（日志中无 v13.4.6+）"
    elif grep -q "系统重启点火" "$BRAIN_LOG" 2>/dev/null; then
        log_ok "闪电接管已执行（binance_brain.log）"
    else
        log_warn "日志中暂未看到大脑/雷达启动字样（请 tail -f logs/binance_brain.log 确认）"
    fi

    if grep -q "哨兵" "$BRAIN_LOG" 2>/dev/null || grep -q "monitoring" "$BRAIN_LOG" 2>/dev/null; then
        log_ok "雷达哨兵监控已启动或待命"
    fi

    echo -e "  ${CYAN}→ 当前本目录 Gunicorn 进程:${NC}"
    ps -ef 2>/dev/null | grep "${DIR}" | grep gunicorn | grep -v grep \
        | awk '{print "     PID="$2" CMD="$8" "$9" "$10}' || true

    # HTTP 全通过则视为部署成功（不因 PID 文件瞬变误报失败）
    if [ "$HEALTH_OK" -eq 1 ] && [ "$HTTP_STATUS" = "200" ]; then
        DEPLOY_OK=1
    fi
}

print_summary() {
    log_step "[6/6] 部署结果汇总"
    echo ""
    if [ "$DEPLOY_OK" -eq 1 ]; then
        echo -e "${GREEN}=== 🔶 币安(Binance) 干净重部署成功 ===${NC}"
        echo -e "  网关地址: http://${BIND_HOST}:${PORT}/webhook"
        echo -e "  健康检查: http://127.0.0.1:${PORT}/health"
        echo -e "  大脑日志: tail -f ${BRAIN_LOG}"
        echo -e "  (勿直接输入文件名；须用 tail -f 查看)"
        echo -e "  访问日志: tail -f ${LOG_DIR}/gunicorn_access.log"
        echo -e "  错误日志: tail -f ${LOG_DIR}/gunicorn_error.log"
    else
        echo -e "${RED}=== ❌ 币安部署未完全通过，请排查上述失败项 ===${NC}"
        echo -e "  最近日志:"
        tail -n 15 "$LOG_FILE" 2>/dev/null || true
        exit 1
    fi
    echo ""
}

echo -e "\n${CYAN}=== 币安系统 · 干净重部署开始 [${DEPLOY_SCRIPT_VERSION}] ===${NC}"
echo -e "  工作目录: ${DIR}"
echo -e "  目标端口: ${PORT}"
echo ""

load_env
force_cleanup || exit 1
install_deps || exit 1
start_service || exit 1
wait_for_listen || exit 1
health_check
print_summary
