#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
广播网关：单条TV webhook → 同时转发给B/C/D三个账户的/webhook。

背景：TV订阅上限20条警报，每个品种若要覆盖3个账户需要3条独立TV警报
（分别指向/binance-b/webhook、/binance-c/webhook、/binance-d/webhook），
品种一多就超限。这个网关让TV对这些品种只需配1条警报指向本网关
（/binance-all/webhook），网关收到后原样转发给三个账户端口，各自走
自己已有的secret校验/解析/去重/风控逻辑不变——网关本身不做任何交易
业务判断，纯转发，不持有任何API密钥、不import任何账户代码。

三个账户并发转发、各自独立超时，其中一个慢/挂（比如正在deploy重启）
不阻塞另外两个正常接收信号。

不需要单独走"每品种"的网关：哪些品种直连账户、哪些品种走本网关，
由用户在TradingView自己那端配置警报URL决定，本网关对payload内容
完全无感知，谁发来就转发给谁。

2026-08-16：用户把全部TV警报都切到本网关后，它成了唯一入口，单点风险
明显升高——这次加固：
  1. 部分/全部转发失败时立即钉钉告警（原来只写日志，不推送，容易错过）
  2. HTTP server 加 socket 级超时，防止慢速/不完整请求把处理线程挂住
     （systemd 的 Restart= 只能抓崩溃，抓不住"进程还活着但不响应"这种挂死）
  3. 配合独立的 gateway-heartbeat.timer（60s一次）做快速探活+自愈重启，
     不必等主监督狗10分钟一轮的周期才发现网关挂了
纯标准库实现，不引入requests等第三方依赖，保持网关自身零依赖、开箱即用。

2026-08-18：do_POST 收到请求后立刻回TV「已收到」，转发到B/C改成后台
线程异步做，不再让TV等账户端处理完（TV自己的等待窗口很短，账户端
稍慢——比如正在重启——TV就会先报"timed out"，即使实际后来还是转发
成功了，也容易让人误以为丢单去手动补发，反而造成重复开平仓）。转发
是否真的送达，继续靠原有的钉钉告警机制单独通知。

2026-08-24：上面这个"先回TV、后台线程异步转发"的设计有个没堵上的口子——
后台线程是daemon线程，进程一退出就被直接砍断，不会等它转发完。而
gateway-heartbeat.timer每60秒探活一次，失败就会自愈式`systemctl
restart`——如果探活失败的同时正好有一笔TV信号已经回了200但还卡在后台
线程里没转发完，这次restart会把它连同转发结果一起悄悄抹掉：TV那边看
到的是200成功，不会重试；后台线程被杀死，_alert_if_needed也没机会跑，
钉钉不会报警。整条链路谁都不知道这笔信号丢了。现在补上优雅关闭：收到
SIGTERM先停止接受新连接，再限时等所有还在跑的转发线程收尾（等不完也
不无限拖，到点强制退出，不能让优雅关闭本身变成新的"进程还活着但不
响应"）。
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("gateway")

PORT = 5006
# 2026-08-15：D账户暂停使用（未接TV、未放资金），先从广播目标里去掉，
# 避免空账户白收信号产生日志噪音；D重新启用时把这行取消注释即可，
# 不用改其它任何逻辑。
# 2026-08-22：新增E(MARIO客户账户)，跟B/C同一批品种/同一规则实盘，
# 走跟自己账户完全相同的网关广播路径，不需要用户在TV那端额外加警报。
BACKENDS = [
    ("B", "http://127.0.0.1:5007/webhook"),
    ("C", "http://127.0.0.1:5008/webhook"),
    # ("D", "http://127.0.0.1:5009/webhook"),
    ("E", "http://127.0.0.1:5010/webhook"),
]
BACKEND_TIMEOUT_SEC = 8.0
_SKIP_HEADERS = {"host", "content-length", "connection"}
# 优雅关闭最多等这么久——留够BACKEND_TIMEOUT_SEC+2(单笔转发的join超时)一轮
# 的余量，真等不完也不能让"优雅关闭"本身变成新的挂死，到点直接退出。
GRACEFUL_SHUTDOWN_TIMEOUT_SEC = 12.0

# ── 优雅关闭：跟踪还没跑完的_forward_and_report后台线程 ──────────────────
_inflight_lock = threading.Lock()
_inflight_threads = []
_server_ref = None  # main()里赋值，供信号处理函数调用shutdown()


def _track_inflight(t):
    with _inflight_lock:
        _inflight_threads.append(t)
        # 顺手清掉已经跑完的，避免列表无限增长
        _inflight_threads[:] = [x for x in _inflight_threads if x.is_alive()]


def _graceful_shutdown():
    logger.info("收到停止信号，停止接受新连接，等待在途转发收尾...")
    try:
        if _server_ref is not None:
            _server_ref.shutdown()
    except Exception as e:
        logger.warning(f"停止监听异常(不影响继续退出): {e}")
    deadline = time.time() + GRACEFUL_SHUTDOWN_TIMEOUT_SEC
    with _inflight_lock:
        threads = list(_inflight_threads)
    for t in threads:
        remaining = max(0.0, deadline - time.time())
        t.join(timeout=remaining)
    still_alive = sum(1 for t in threads if t.is_alive())
    if still_alive:
        logger.warning(f"优雅关闭超时，仍有 {still_alive} 笔转发未收尾就退出")
    else:
        logger.info("优雅关闭完成，在途转发已全部收尾")
    os._exit(0)


def _handle_term_signal(signum, frame):
    threading.Thread(target=_graceful_shutdown, daemon=True, name="graceful-shutdown").start()


# ── 钉钉告警（复用watchdog同一个群，纯stdlib实现，不依赖requests）────────
DINGTALK_WEBHOOK = os.getenv("WATCHDOG_DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("WATCHDOG_DINGTALK_SECRET", "")
_ALERT_DEDUPE_SEC = 60.0
_last_alert_ts = {"partial": 0.0, "full": 0.0}
_alert_lock = threading.Lock()


def _dingtalk_signed_url() -> str:
    if not DINGTALK_WEBHOOK:
        return ""
    if not DINGTALK_SECRET:
        return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{DINGTALK_SECRET}"
    hmac_code = hmac.new(
        DINGTALK_SECRET.encode("utf-8"), string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    sep = "&" if "?" in DINGTALK_WEBHOOK else "?"
    return f"{DINGTALK_WEBHOOK}{sep}timestamp={ts}&sign={sign}"


def _dingtalk_send(text: str) -> bool:
    url = _dingtalk_signed_url()
    if not url:
        logger.warning(f"[钉钉] 未配置webhook，跳过发送: {text[:80]}")
        return False
    payload = json.dumps({
        "msgtype": "text", "text": {"content": text}, "at": {"isAtAll": False},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("errcode") == 0:
            return True
        logger.warning(f"[钉钉] 发送失败: {data}")
        return False
    except Exception as e:
        logger.warning(f"[钉钉] 发送异常: {e}")
        return False


def _alert_if_needed(ok_count: int, total: int, results: list, preview: str) -> None:
    """转发不全 → 立即钉钉，60秒内同类型不重复刷屏。"""
    kind = "full" if ok_count == 0 else ("partial" if ok_count < total else None)
    if kind is None:
        return
    now = time.time()
    with _alert_lock:
        if now - _last_alert_ts.get(kind, 0.0) < _ALERT_DEDUPE_SEC:
            return
        _last_alert_ts[kind] = now
    label = "全部失败" if kind == "full" else "部分失败"
    detail = " | ".join(
        f"{r['account']}:{'OK' if r.get('ok') else 'FAIL(' + str(r.get('status')) + ')'}"
        for r in results if r
    )
    _dingtalk_send(
        f"【广播网关】转发{label} {ok_count}/{total} — {detail}\n"
        f"payload={preview[:150]}\n"
        f"（60秒内同类问题不重复推送）"
    )


def _forward(name, url, body, headers, results, idx):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        for k, v in headers.items():
            if k.lower() in _SKIP_HEADERS:
                continue
            req.add_header(k, v)
        # 给下游账户打来源标签，供其自己的信号日志区分"直连"还是"经网关转发"；
        # 网关依旧不解析/不判断 payload 内容，只加这一个身份标记。
        req.add_header("X-TV-Source", "gateway")
        with urllib.request.urlopen(req, timeout=BACKEND_TIMEOUT_SEC) as resp:
            code = resp.status
            resp.read(500)  # 排空但不需要内容
        results[idx] = {
            "account": name, "ok": 200 <= code < 300,
            "status": code, "elapsed": round(time.time() - t0, 3),
        }
    except urllib.error.HTTPError as e:
        results[idx] = {
            "account": name, "ok": False, "status": e.code,
            "elapsed": round(time.time() - t0, 3), "error": str(e),
        }
    except Exception as e:
        results[idx] = {
            "account": name, "ok": False, "status": 0,
            "elapsed": round(time.time() - t0, 3), "error": str(e),
        }


class Handler(BaseHTTPRequestHandler):
    # socket级读超时：防止慢速/不完整请求把处理线程挂住（systemd的Restart=
    # 只能抓进程崩溃，抓不住"活着但不响应"这种挂死，这里从源头堵住）。
    timeout = 15

    def log_message(self, fmt, *args):
        pass  # 交给logger统一格式，不用默认stderr输出

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {"status": "ok", "backends": [b[0] for b in BACKENDS]})
        else:
            self._reply(404, {"error": "not_found"})

    def do_POST(self):
        if self.path not in ("/webhook", "/webhook/"):
            self._reply(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        headers = dict(self.headers.items())

        # 2026-08-18：之前这里同步等两个账户都转发完（最长 BACKEND_TIMEOUT_
        # SEC+2=10s）才回TV，账户端稍微慢一点（比如正在重启、还没起来）TV
        # 自己的webhook等待窗口就先超时了，报"request took too long and
        # timed out"——但实际上账户端过一会儿还是收到并处理成功了，不是真
        # 丢单。TV这次"失败"提示反而误导人去手动补发，造成重复开平仓（当晚
        # 复现过一次：BNB在两边都被多平开了一轮，多付了一趟手续费）。
        # 现在改成：先立刻回TV「已收到」，转发到B/C这一步挪到后台线程异步
        # 做，不占用这次HTTP响应的时间。转发是否真的成功，走原有的钉钉
        # 告警机制单独通知（部分/全部失败都会推送），不再依赖TV这次响应
        # 是否超时来判断有没有送达。
        self._reply(200, {"ok": True, "queued": True})
        t = threading.Thread(
            target=self._forward_and_report, args=(body, headers), daemon=True,
        )
        _track_inflight(t)
        t.start()

    def _forward_and_report(self, body, headers):
        results = [None] * len(BACKENDS)
        threads = []
        for idx, (name, url) in enumerate(BACKENDS):
            t = threading.Thread(
                target=_forward, args=(name, url, body, headers, results, idx),
                daemon=True,
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=BACKEND_TIMEOUT_SEC + 2)

        ok_count = sum(1 for r in results if r and r.get("ok"))
        preview = body[:200].decode("utf-8", errors="replace")
        logger.info(
            f"[广播] {ok_count}/{len(BACKENDS)}成功 | payload={preview!r} | 明细={results}"
        )
        _alert_if_needed(ok_count, len(BACKENDS), results, preview)

    def _reply(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    global _server_ref
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    _server_ref = server
    signal.signal(signal.SIGTERM, _handle_term_signal)
    signal.signal(signal.SIGINT, _handle_term_signal)
    logger.info(
        f"广播网关启动，监听127.0.0.1:{PORT}，转发目标={BACKENDS}，"
        f"钉钉告警={'已配置' if DINGTALK_WEBHOOK else '未配置'}"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
