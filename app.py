#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, threading, logging, time
from flask import Flask, request, jsonify
from position_supervisor_binance import (
    get_supervisor,
    get_supervisor_for_payload,
    SUPERVISORS,
    BINANCE_VPS_VERSION,
    bootstrap_supervisors,
)
from webhook_parser import (
    parse_webhook_request,
    normalize_tv_payload,
    format_webhook_log,
    TV_STRATEGY_VERSION,
)
from symbol_config import active_binance_symbols, resolve_binance_symbol
from console_api import init_console
from account_profiles import bootstrap_from_env, get_webhook_secret, get_active_sizing, get_symbol_settings
import webhook_log

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Flask-Binance: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
init_console(app)
# gunicorn / systemd 入口也会加载本模块：启动时灌入档案并绑 API
try:
    bootstrap_from_env()
except Exception as _e:
    logger.warning(f"account_profiles bootstrap: {_e}")


def process_webhook_payload(raw_bytes, content_type, headers, remote_addr, ticker=None, as_json=None):
    """
    /webhook 的实际处理逻辑，独立于 Flask 的 request 全局对象，可以直接被
    Console 的重放/手动发单在同进程内调用（不经过网络）。

    2026-08-17 发现：B/C/D 三账户的 gunicorn 都是 `-w 1 --threads 1`（单进程
    单线程，sync worker），Console 那两个路由原本是"回环 POST 自己的
    127.0.0.1:$PORT/webhook"——同一个请求处理线程等自己发给自己的 HTTP 请求
    的响应，而唯一能接这个请求的正好是它自己，直接死锁（连最简单的 PING 都
    10 秒超时）。改成同进程直接调用这个函数，不再有任何网络往返，也就没有
    死锁的余地；Flask 路由本身仍然是唯一对外暴露的入口，鉴权/解析/派发逻辑
    原样保留，一步没少。
    """
    headers = headers or {}
    _t0 = time.time()
    _source = str(headers.get("X-TV-Source") or "tv_direct").strip() or "tv_direct"
    _remote_addr = str(remote_addr or "")
    _replay_of = headers.get("X-TV-Replay-Of")
    try:
        _replay_of = int(_replay_of) if _replay_of else None
    except (TypeError, ValueError):
        _replay_of = None
    data = {}

    def _log(status, msg, sig_data=None):
        try:
            webhook_log.record_signal(
                sig_data if isinstance(sig_data, dict) else data,
                source=_source, remote_addr=_remote_addr,
                http_status=status, http_message=str(msg)[:200],
                dispatch_ms=(time.time() - _t0) * 1000.0,
                replay_of=_replay_of,
            )
        except Exception:
            pass

    try:
        _, data = parse_webhook_request(raw_bytes, content_type or "", as_json=as_json)
        data = normalize_tv_payload(data)
    except ValueError as e:
        logger.warning(f"[Webhook] 解析失败: {e}")
        _log(400, f"parse_error: {e}")
        return {"status": "error", "message": str(e)}, 400

    if not data:
        _log(400, "empty_payload")
        return {"status": "error", "message": "Empty payload"}, 400
    # 鉴权：优先 secret（TV v6.5.6+）；兼容旧字段 token；值来自 Console/档案或 .env
    auth = str(
        data.get("secret") or data.get("token") or ""
    ).strip()
    # 2026-08-16修正：原来这里还有一层"528586"硬编码兜底，跟
    # get_webhook_secret()内部那层重复——两处都写死了实盘真实密钥。
    # 现在密钥读取失败/未配置一律返回空字符串，空字符串直接拒绝所有
    # 请求(fail-closed)，不再有任何硬编码密钥兜底值。
    expected = str(get_webhook_secret() or "").strip()
    if not expected:
        logger.error("[Webhook] WEBHOOK_SECRET 未配置 → 拒绝所有请求(fail-closed)")
        _log(500, "server_misconfigured")
        return {"status": "error", "message": "Server misconfigured"}, 500
    if auth != expected:
        _log(403, "invalid_secret")
        return {"status": "error", "message": "Invalid secret"}, 403
    if not data.get("_parse_ok"):
        _log(400, "missing_or_invalid_action")
        return {"status": "error", "message": "Missing or invalid action"}, 400

    # URL 路径品种优先（/webhook/XAUUSDT），否则读 payload ticker
    if ticker:
        data["ticker"] = ticker
        data["symbol"] = ticker

    raw_action = data.get("action", "UNKNOWN")
    if raw_action == "PING":
        _log(200, "pong")
        return {
            "status": "success",
            "message": "pong",
            "action": "PING",
            "schema": TV_STRATEGY_VERSION,
            "symbols": active_binance_symbols(),
        }, 200

    if raw_action == "HEARTBEAT":
        # 2026-08-20：TV心跳持仓——每根收盘K线TV都会发一次自己当前的持仓状态，
        # 独立于开平仓警报，用于VPS核对"TV持仓、实盘漏单"这类现有日志比对
        # 天然看不见的情况(webhook从没送达，本地journalctl压根没留痕)。
        # 只写状态，不进 handle_signal 的完整交易流水线，不可能误触发下单。
        hb_supervisor, hb_sym = get_supervisor_for_payload(data)
        if hb_supervisor is not None:
            try:
                hb_supervisor.record_tv_heartbeat(data)
            except Exception as e:
                logger.warning(f"[Heartbeat] [{hb_sym}] 记录失败: {e}")
        _log(200, "heartbeat_recorded")
        return {
            "status": "success",
            "message": "heartbeat_recorded",
            "action": "HEARTBEAT",
            "symbol": hb_sym,
            "schema": TV_STRATEGY_VERSION,
        }, 200

    supervisor, sym = get_supervisor_for_payload(data)
    if supervisor is None:
        logger.warning(f"[Webhook] 不支持的品种: {sym}")
        _log(400, f"unsupported_symbol: {sym}")
        return {
            "status": "error",
            "message": f"Unsupported or missing symbol: {sym}",
            "hint": "TV JSON must include symbol/ticker e.g. ETHUSDT.P or XAUUSDT.P",
            "allowed": active_binance_symbols(),
        }, 400

    logger.info(f"[Webhook] [{sym}] {format_webhook_log(data)}")

    # 开仓必要字段：仅 price（ATR/ADX 由 VPS 行情引擎自算；webhook atr 仅调试比对）
    if raw_action in ("LONG", "SHORT"):
        # 2026-08-26：账户级品种开关——例如该账户尚未在交易所端接受某品种
        # 的前置协议（如币安代币化股票永续的TradFi-Perps agreement，
        # 下单会直接被交易所拒-4411），下到这一步再报错只会一直产生失败
        # 订单和噪音。这里提前拦截，Console里关掉即生效，不需要改代码/
        # 重新部署，也不影响同一份代码部署的其它账户（各账户各自的
        # data/symbol_settings.json独立）。
        if not get_symbol_settings(sym).get("trading_enabled", True):
            logger.warning(f"[Webhook] [{sym}] 该账户此品种交易已被手动关闭，跳过开仓")
            _log(200, "symbol_trading_disabled")
            return {
                "status": "skipped",
                "message": f"Trading disabled for {sym} on this account",
                "symbol": sym,
                "action": raw_action,
            }, 200
        px = data.get("price")
        try:
            px_ok = px is not None and float(px) > 0
        except (TypeError, ValueError):
            px_ok = False
        if not px_ok:
            _log(400, "missing_or_invalid_price")
            return {
                "status": "error",
                "message": "LONG/SHORT require valid price (ATR/ADX computed on VPS)",
                "got": {"price": px},
            }, 400
        # stop_loss → 永久硬止损距离输入（与 atr 一并进入 v15.7.8+ 唯一公式）；亦可作 sizing 收紧 / 调试对比
        sl = data.get("stop_loss") or data.get("tv_sl")
        if sl is not None:
            data["_tv_sl_ref"] = sl

    try:
        threading.Thread(
            target=supervisor.handle_signal, args=(data,), daemon=True,
            name=f"tv-{sym}",
        ).start()
    except Exception as e:
        logger.error(f"启动线程失败 [{sym}]: {e}")
        _log(500, f"thread_start_failed: {e}")
        return {
            "status": "error",
            "message": f"Failed to start processing: {e}",
            "symbol": sym,
        }, 500

    _sig_id = None
    try:
        _sig_id = webhook_log.record_signal(
            data, source=_source, remote_addr=_remote_addr,
            http_status=200, http_message="dispatched",
            dispatch_ms=(time.time() - _t0) * 1000.0, replay_of=_replay_of,
        )
        webhook_log.start_finalizer(_sig_id, sym)
    except Exception:
        pass

    return {
        "status": "success",
        "message": "Signal received and processing started",
        "action": raw_action,
        "symbol": sym,
        "schema": TV_STRATEGY_VERSION,
    }, 200


@app.route('/webhook', methods=['POST'])
@app.route('/webhook/<path:ticker>', methods=['POST'])
def webhook(ticker=None):
    resp, code = process_webhook_payload(
        request.get_data(), request.content_type, request.headers,
        request.remote_addr, ticker=ticker,
        as_json=request.get_json(silent=True),
    )
    return jsonify(resp), code


@app.route('/admin/resume/<path:symbol>', methods=['POST'])
def admin_resume(symbol):
    """人工确认后解除交易暂停；同时清掉持仓期误累计的 ATR 降级污染。"""
    meta = resolve_binance_symbol(symbol, default="")
    sym = meta.get("symbol") or ""
    if not sym or sym not in set(active_binance_symbols()):
        return jsonify({
            "status": "error",
            "message": f"Unknown symbol: {symbol}",
            "allowed": active_binance_symbols(),
        }), 400
    # IP 冷却期内禁止 resume（避免立刻 REST 雪崩再撞 -1003）
    force = str(
        (request.get_json(silent=True) or {}).get("force")
        or request.args.get("force")
        or ""
    ).strip().lower() in ("1", "true", "yes")
    try:
        from binance_client import binance_client as _bc_rl
        rem = float(_bc_rl.ip_rate_limit_remaining() or 0)
        if rem > 0 and not force:
            return jsonify({
                "status": "blocked",
                "message": (
                    f"IP rate-limit cooldown remaining {rem:.0f}s; "
                    f"resume rejected (pass force=1 to override)"
                ),
                "symbol": sym,
                "ip_rate_limit_remaining": rem,
            }), 429
    except Exception as e:
        logger.warning(f"[admin/resume] rate-limit gate skip: {e}")
    # resume 保持本地运维兼容（不强制 secret）；smoke_arm 才强制鉴权
    sup = get_supervisor(sym)
    prev = str(getattr(sup, "trading_pause_reason", "") or "")
    was = bool(getattr(sup, "trading_paused", False))
    was_mon = bool(getattr(sup, "api_monitor_only", False))
    sup.trading_paused = False
    sup.trading_pause_reason = ""
    try:
        if hasattr(sup, "_exit_api_monitor_only") and (
            was_mon or str(prev).startswith("api_monitor_only")
        ):
            # 人工 resume：清仅监控标志，但不强制再走 probe 对账路径的钉钉轰炸
            from binance_client import binance_client as _bc
            getattr(sup, "api_monitor_only", None)
            sup.api_monitor_only = False
            _bc.set_monitor_only(sym, False)
        elif was_mon:
            from binance_client import binance_client as _bc
            sup.api_monitor_only = False
            _bc.set_monitor_only(sym, False)
    except Exception as e:
        logger.warning(f"[admin/resume] 清仅监控跳过: {e}")
    # 持仓期误跑开仓sizing留下的污染（假 ATR 降级）一并清掉
    try:
        sup._atr_div_streak = 0
        sup.atr_degraded = False
        if str(getattr(sup, "atr_source", "") or "").startswith("tv_implied"):
            sup.atr_source = "vps"
        sup._pending_atr_degrade = None
    except Exception as e:
        logger.warning(f"[admin/resume] ATR污染清理跳过: {e}")
    try:
        sup._save_state()
    except Exception as e:
        logger.warning(f"[admin/resume] 状态持久化跳过: {e}")
    # sticky 曾触激活线 / 恢复后立刻脉冲武装（pause 期无法 REST 挂单）
    try:
        if float(getattr(sup, "watched_qty", 0) or 0) > 0:
            sup._post_recover_radar_pulse = True
    except Exception as e:
        logger.warning(f"[admin/resume] radar pulse 跳过: {e}")
    logger.info(f"✅ [admin/resume] {sym} 解除暂停 | was={was} reason={prev or '—'}")
    return jsonify({
        "status": "success",
        "symbol": sym,
        "was_paused": was,
        "previous_reason": prev,
        "trading_paused": False,
        "atr_div_streak": 0,
        "atr_degraded": False,
    }), 200


@app.route('/admin/smoke_arm_radar/<path:symbol>', methods=['POST'])
def admin_smoke_arm_radar(symbol):
    """
    烟测专用：在主进程军师上强制越过激活线挂雷达 STOP。
    仅当 monitoring 且有持仓、雷达仍休眠时生效；需 WEBHOOK_SECRET。
    """
    # 2026-08-16：同一批修正，去掉硬编码"528586"兜底——这条路由能直接
    # 越过激活线强挂雷达STOP，比普通webhook更敏感，之前"if not expected"
    # 这层fail-closed保护形同虚设(硬编码兜底让expected永远不会真的为空)。
    expected = str(os.getenv("WEBHOOK_SECRET") or "").strip()
    data = request.get_json(silent=True) or {}
    auth = str(
        data.get("secret")
        or request.form.get("secret")
        or request.args.get("secret")
        or request.headers.get("X-Webhook-Secret")
        or ""
    ).strip()
    if not expected or auth != expected:
        return jsonify({"status": "error", "message": "Invalid secret"}), 403
    meta = resolve_binance_symbol(symbol, default="")
    sym = meta.get("symbol") or ""
    if not sym or sym not in set(active_binance_symbols()):
        return jsonify({
            "status": "error",
            "message": f"Unknown symbol: {symbol}",
            "allowed": active_binance_symbols(),
        }), 400
    sup = get_supervisor(sym)
    if not bool(getattr(sup, "monitoring", False)):
        return jsonify({"status": "error", "message": "not monitoring"}), 400
    live = float(getattr(sup, "watched_qty", 0) or 0)
    if live <= 0:
        return jsonify({"status": "error", "message": "no live qty"}), 400
    if bool(getattr(sup, "radar_activated", False)):
        return jsonify({
            "status": "ok",
            "symbol": sym,
            "already_activated": True,
            "radar_activated": True,
        }), 200
    try:
        gate = float(sup._radar_activation_price() or 0)
    except Exception as e:
        return jsonify({"status": "error", "message": f"gate_err:{e}"}), 500
    side = str(getattr(sup, "current_side", "") or "").upper()
    if side == "SHORT":
        fake = gate * 0.998 if gate > 0 else 0.0
    else:
        fake = gate * 1.002 if gate > 0 else 0.0
    if fake <= 0:
        return jsonify({"status": "error", "message": "bad fake px"}), 500
    ok = bool(
        sup._maybe_arm_radar_on_activation(live, fake, source="admin_smoke_arm")
    )
    logger.info(
        f"[admin/smoke_arm_radar] {sym} ok={ok} gate={gate} fake={fake} "
        f"activated={getattr(sup, 'radar_activated', False)}"
    )
    return jsonify({
        "status": "success" if ok else "error",
        "symbol": sym,
        "armed": ok,
        "gate": gate,
        "fake_px": fake,
        "radar_activated": bool(getattr(sup, "radar_activated", False)),
        "radar_pending_arm": bool(getattr(sup, "radar_pending_arm", False)),
        "initial_stop": float(getattr(sup, "initial_stop", 0) or 0),
    }), 200 if ok else 500


@app.route('/admin/abort_catchup/<path:symbol>', methods=['POST'])
def admin_abort_catchup(symbol):
    """
    人工中止TV心跳漏单追回周期——撤掉追回限价/市价单(如果还挂着)、清
    catchup_*状态、标记本次episode已消耗(同一episode不会重新触发；TV
    真发一条新OPEN信号、或心跳先转FLAT再变LONG/SHORT形成全新episode，
    仍会正常评估追回，不影响未来)。2026-08-29新增：宝贝手动平仓PAXG
    (看支撑位判断，怕反弹回吐)后，TV自己从没发过CLOSE，心跳追回机制
    照常判定成"疑似漏单"自动挂单想追回——这是系统按自己既定逻辑正确
    运行，但撞上了用户主动的判断，需要一个人工干预口子直接中止，不用等
    重启。跟/admin/smoke_arm_radar同款WEBHOOK_SECRET鉴权，避免被外部误触发
    (这条路由能撤掉真实追回挂单，影响真实资金路径)。

    2026-08-29修复：最初写成"catchup_active已经是False就直接提前返回，
    什么都不做"——实盘发现这样不够：常规重启对账本身就可能已经把
    catchup_active清成False(先于人工调用这条路由)，但_catchup_episode_
    resolved从来没被设过，_maybe_start_tv_heartbeat_catchup的同一episode
    判定没被打上，只要心跳还是同一个方向/entry、下一轮多周期动量一旦
    确认，会重新武装一次一模一样的追回——完全没达到"人工中止"的效果。
    改成不管catchup_active当前是不是True都无条件标记episode已消耗
    (用当前心跳的side/entry，心跳已转FLAT则退回原来挂单时冻结的
    catchup_side/catchup_tv_entry_frozen)，真正做到"这一段TV持仓不会
    再被追回"，而不是"只不多这一次"。
    """
    expected = str(os.getenv("WEBHOOK_SECRET") or "").strip()
    data = request.get_json(silent=True) or {}
    auth = str(
        data.get("secret")
        or request.form.get("secret")
        or request.args.get("secret")
        or request.headers.get("X-Webhook-Secret")
        or ""
    ).strip()
    if not expected or auth != expected:
        return jsonify({"status": "error", "message": "Invalid secret"}), 403
    meta = resolve_binance_symbol(symbol, default="")
    sym = meta.get("symbol") or ""
    if not sym or sym not in set(active_binance_symbols()):
        return jsonify({
            "status": "error",
            "message": f"Unknown symbol: {symbol}",
            "allowed": active_binance_symbols(),
        }), 400
    sup = get_supervisor(sym)
    was_active = bool(getattr(sup, "catchup_active", False))
    reason = str(data.get("reason") or request.args.get("reason") or "人工中止(admin_abort_catchup)")
    try:
        if was_active:
            sup._abort_tv_catchup_cycle(reason=reason)
        else:
            hb_side = str(getattr(sup, "tv_heartbeat_side", "FLAT") or "FLAT").upper()
            if hb_side in ("LONG", "SHORT"):
                ep_side = hb_side
                ep_entry = float(getattr(sup, "tv_heartbeat_entry", 0) or 0)
            else:
                ep_side = str(getattr(sup, "catchup_side", "") or "").upper()
                ep_entry = float(getattr(sup, "catchup_tv_entry_frozen", 0) or 0)
            sup._catchup_episode_side = ep_side
            sup._catchup_episode_entry = ep_entry
            sup._catchup_episode_resolved = True
            sup._save_state()
            logger.warning(
                f"🛑 [{sym}] TV心跳追回episode人工标记已消耗(未武装状态) "
                f"side={ep_side} entry={ep_entry} | {reason}"
            )
    except Exception as e:
        logger.error(f"[admin/abort_catchup] {sym} 中止失败: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"abort_err:{e}"}), 500
    logger.info(f"[admin/abort_catchup] {sym} 已人工中止追回周期 | was_active={was_active} | {reason}")
    return jsonify({
        "status": "success",
        "symbol": sym,
        "was_active": was_active,
        "catchup_active": bool(getattr(sup, "catchup_active", False)),
        "episode_side": str(getattr(sup, "_catchup_episode_side", "") or ""),
        "episode_entry": float(getattr(sup, "_catchup_episode_entry", 0) or 0),
        "episode_resolved": bool(getattr(sup, "_catchup_episode_resolved", False)),
        "reason": reason,
    }), 200


@app.route('/admin/cancel_chase_watch/<path:symbol>', methods=['POST'])
def admin_cancel_chase_watch(symbol):
    """
    人工中止追单确认观察窗(_chase_watch_active)——撤掉已挂的追单限价(如果
    有)、清chase_watch_*状态。2026-08-29新增：XAUUSDT实盘场景——宝贝看
    4H KDJ见底+周末无量，主动平仓锁盈，平仓价刚好贴近雷达自己的止损线
    附近，被_resolve_exit_source的价格贴近判定误归类成"疑似雷达提前出局"，
    自动武装了3小时追单确认观察窗(不是心跳追回，是追单确认chase-watch，
    两套完全独立的机制)——虽然chase-watch本身有多周期EMA+动量确认+反转
    检测两道门槛，大概率会在宝贝判断对的情况下自然确认失败、超时放弃，
    但宝贝明确要求直接尊重自己的判断、不留这个不确定性。

    跟/admin/abort_catchup不同：chase-watch只在"检测到那一次qualifying
    退出"时才会武装一次，不是心跳追回那种每轮idle-patrol都会重新评估
    触发条件——清掉_chase_watch_active后不会重新武装同一次退出事件，
    不需要额外标记"episode已消耗"这层概念。
    """
    expected = str(os.getenv("WEBHOOK_SECRET") or "").strip()
    data = request.get_json(silent=True) or {}
    auth = str(
        data.get("secret")
        or request.form.get("secret")
        or request.args.get("secret")
        or request.headers.get("X-Webhook-Secret")
        or ""
    ).strip()
    if not expected or auth != expected:
        return jsonify({"status": "error", "message": "Invalid secret"}), 403
    meta = resolve_binance_symbol(symbol, default="")
    sym = meta.get("symbol") or ""
    if not sym or sym not in set(active_binance_symbols()):
        return jsonify({
            "status": "error",
            "message": f"Unknown symbol: {symbol}",
            "allowed": active_binance_symbols(),
        }), 400
    sup = get_supervisor(sym)
    was_active = bool(getattr(sup, "_chase_watch_active", False))
    reason = str(data.get("reason") or request.args.get("reason") or "人工中止(admin_cancel_chase_watch)")
    try:
        sup._clear_chase_watch(reason=reason, save=True)
    except Exception as e:
        logger.error(f"[admin/cancel_chase_watch] {sym} 中止失败: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"cancel_err:{e}"}), 500
    logger.info(f"[admin/cancel_chase_watch] {sym} 已人工中止追单确认窗口 | was_active={was_active} | {reason}")
    return jsonify({
        "status": "success",
        "symbol": sym,
        "was_active": was_active,
        "chase_watch_active": bool(getattr(sup, "_chase_watch_active", False)),
        "reason": reason,
    }), 200


@app.route('/admin/reload_notify', methods=['POST'])
def admin_reload_notify():
    """热加载 Telegram/钉钉环境变量，无需重启交易服务。"""
    try:
        import dingtalk
        status = dingtalk.reload_notify_config()
        return jsonify({"status": "ok", "notify": status}), 200
    except Exception as e:
        logger.error(f"[admin/reload_notify] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/admin/notify_test', methods=['POST'])
def admin_notify_test():
    """
    验收双通道：
      {"level": 1} → 仅 TG
      {"level": 2} → TG + 钉钉
    """
    try:
        import dingtalk
        body = request.get_json(silent=True) or {}
        lvl = int(body.get("level") or 1)
        title = str(body.get("title") or f"双通道自检 L{lvl}")
        detail = str(body.get("detail") or "notify_test from /admin/notify_test")
        dingtalk.send_alert(
            title,
            {"📝 说明": detail, "🧪 level": str(lvl)},
            immediate=True,
            level=lvl,
        )
        return jsonify({
            "status": "ok",
            "level": lvl,
            "notify": dingtalk.notify_config_status(),
        }), 200
    except Exception as e:
        logger.error(f"[admin/notify_test] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    from webhook_parser import SIZING_MODE
    from binance_client import binance_client as _bc
    try:
        import dingtalk
        notify = dingtalk.notify_config_status()
    except Exception:
        notify = {"telegram_configured": False, "dingtalk_configured": False}
    try:
        risk, lev = get_active_sizing()
    except Exception:
        # 2026-09-05：杠杆假设从5降到3，兜底值同步更新，否则get_active_
        # sizing()异常时/health会展示错误的旧杠杆。
        risk, lev = 0.20, 3.0
    return jsonify({
        "service": "binance_webhook",
        "status": "ok",
        "version": BINANCE_VPS_VERSION,
        "tv_strategy": TV_STRATEGY_VERSION,
        "sizing": SIZING_MODE,  # RISK20_NOTIONAL3 公式骨架；数值来自生效档案
        "leverage": float(lev),
        "risk_pct": float(risk),
        "notional_mult": float(lev),
        "console": "/console",
        "radar": "breath_dual_eth_xau",
        "notify": notify,
        "symbols": list(SUPERVISORS.keys()) or active_binance_symbols(),
        "monitoring": {
            s: bool(getattr(sup, "monitoring", False))
            for s, sup in SUPERVISORS.items()
        },
        "trading_paused": {
            s: bool(getattr(sup, "trading_paused", False))
            for s, sup in SUPERVISORS.items()
        },
        "api_monitor_only": {
            s: bool(getattr(sup, "api_monitor_only", False))
            or bool(_bc.is_monitor_only(s))
            for s, sup in SUPERVISORS.items()
        },
        "trading_pause_reason": {
            s: str(getattr(sup, "trading_pause_reason", "") or "")
            for s, sup in SUPERVISORS.items()
        },
        "pipeline": {
            s: (
                (getattr(sup, "_pipeline_state_blob", lambda: {})() or {}).get("phase")
                if callable(getattr(sup, "_pipeline_state_blob", None))
                else None
            )
            for s, sup in SUPERVISORS.items()
        },
        # v16.16.0：权重配额健康（主动预判数据）
        "weight_stats": (
            getattr(_bc, "_weighted_session", None)
            and _bc._weighted_session.get_weight_stats()
        ) or None,
        "ip_rate_limit_remaining": float(_bc.ip_rate_limit_remaining()),
        # 部署安全阀：任一品种正在开仓执行中(_open_in_progress)时不应重启，
        # 否则会撞上"市价单已成交但仓位查询/TP绑定尚未走完"的窗口，重启会
        # 把这笔仓位打成孤儿仓，靠闪电接管兜底而非正常TV关联流程。
        # 2026-08-10：BNBUSDT开仓中途被部署重启命中过一次，接管虽然兜住了
        # 但走的是应急通道，故加这道显式的可轮询安全阀。
        "open_in_progress": {
            s: bool(getattr(sup, "_open_in_progress", False))
            for s, sup in SUPERVISORS.items()
        },
        # 2026-08-22新增：超强趋势/追单确认这几套新状态机之前只能靠SSH进去
        # 读原始state json才能看，日常排查很不方便。这里只暴露"当前是什么
        # 状态"这几个轻量字段，不暴露任何下单相关的敏感细节。
        "radar_mega_strong": {
            s: bool(getattr(sup, "radar_mega_strong", False))
            for s, sup in SUPERVISORS.items()
        },
        "chase_watch_active": {
            s: bool(getattr(sup, "_chase_watch_active", False))
            for s, sup in SUPERVISORS.items()
        },
        "chase_watch_phase": {
            s: str(getattr(sup, "_chase_watch_phase", "") or "")
            for s, sup in SUPERVISORS.items()
        },
        "catchup_active": {
            s: bool(getattr(sup, "catchup_active", False))
            for s, sup in SUPERVISORS.items()
        },
        "deploy_safe": not any(
            bool(getattr(sup, "_open_in_progress", False))
            for sup in SUPERVISORS.values()
        ),
    }), 200


if __name__ == '__main__':
    bootstrap_from_env()
    bootstrap_supervisors()
    # 对外 IP:端口访问 Console（仍建议仅自己用；设 CONSOLE_PASSWORD）
    app.run(host='0.0.0.0', port=5003, debug=False, threaded=True)
