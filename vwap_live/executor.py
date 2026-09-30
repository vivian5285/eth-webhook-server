#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 strategy 信号落成真实下单(武装时)或纯观察日志(未武装时)。两条路径
共用同一个 db 记账，观察模式下的"仓位"是本地记的假想仓，用最新标记价
算浮盈——这样即使还没给 key/没开 LIVE_TRADING，也能看到"如果现在是真的
会怎么走"，攒出一段可信的track record再决定要不要真开。"""
import logging
import time

import binance_futures as bf
import db
import market_data as md
import notifier
import risk

logger = logging.getLogger(__name__)


def _pnl_usd(side: str, entry: float, exit_: float, qty: float) -> float:
    d = 1.0 if side == "LONG" else -1.0
    return round(d * (exit_ - entry) * qty, 4)


def _current_equity(cfg) -> float:
    """实时权益——照抄真实 TV 引擎的 RISK20 思路(webhook_parser.py::
    compute_fixed_order_qty 用的 principal 就是每次开仓现查的账户余额，
    不是写死的数)。没接真实账户(纯观察)时用 fallback_equity_usd 只为了
    让日志数字有意义，不影响下单(那时候本来就不会下单)。"""
    if cfg.api_key_present:
        eq = bf.get_balance_usdt(cfg)
        if eq is not None and eq > 0:
            return eq
    return cfg.fallback_equity_usd


def open_position(cfg, symbol: str, sig: dict):
    ok, why = risk.check_can_open(cfg, symbol)
    if not ok:
        logger.info("%s 开仓被风控拒绝: %s", symbol, why)
        db.record_decision({"symbol": symbol, "bar_time": sig["bar_time"], "action": "SKIP",
                             "side": sig["side"], "price": sig["price"], "reason": why,
                             "armed": cfg.is_armed})
        return None

    price = sig["price"]
    equity = _current_equity(cfg)
    margin_usd = equity * cfg.position_size_pct
    try:
        qty = bf.quantity_for_usd(cfg, symbol, price, margin_usd)
    except Exception as e:
        logger.warning("%s 精度查询失败(%s)，用近似数量", symbol, e)
        qty = round((margin_usd * cfg.leverage) / price, 4)
    if qty <= 0:
        logger.warning("%s 计算数量<=0，跳过", symbol)
        return None

    # 2026-09-12 宝贝反馈修正：均值回归的信号必须立即开仓——每一笔都有自己
    # 的止损保护本金，不能因为组合总敞口的考虑就把满足信号的品种晾在一边
    # 不下单，那样会真的丢掉这次机会(均值回归不会"过会儿再补开一次同样
    # 的仓")。所以总敞口上限**只做监控、不做拦截**——照样按 position_size_
    # pct 全额开仓，只在日志/通知里如实报出"这笔之后总敞口是多少、占权益
    # 几倍"，方便宝贝在面板上看、自己判断要不要手动调 position_size_pct，
    # 而不是让程序替宝贝丢单。真正会拦截开仓的只有：品种白名单、单日亏损
    # 熔断、单品种防重复开仓、最大并发仓位数、跟交易所实际持仓的一致性
    # 核对——这几条不影响"信号来了就开"，是防重复/防账本错乱，不是缩权重。
    new_notional = qty * price
    existing_notional = bf.get_total_notional(cfg) if cfg.api_key_present else 0.0
    notional_cap = equity * cfg.max_total_notional_mult
    total_after = existing_notional + new_notional
    if cfg.api_key_present and total_after > notional_cap:
        logger.warning("%s 开仓后总敞口 $%.2f 会超过监控上限 $%.2f(权益×%.0f)——仍然照常开仓，"
                        "只是提醒，请自行关注", symbol, total_after, notional_cap, cfg.max_total_notional_mult)

    entry_price = price
    order_result = None
    stop_confirmed = True
    tp_confirmed = True
    if cfg.is_armed:
        try:
            bf.ensure_isolated_margin(cfg, symbol)  # 逐仓隔离——跟账户上可能同时存在的其它仓位不共享保证金池
            bf.set_leverage(cfg, symbol)
            order = bf.open_market(cfg, symbol, sig["side"], qty)
        except bf.NotArmedError as e:
            logger.error("下单前风控二次检查失败: %s（不应该发生，已拦截）", e)
            return None
        except Exception as e:
            # 这里还没有任何真实仓位(入场单本身没成交)，账本不记录是对的
            logger.error("%s 入场下单失败: %s，不记录仓位（尚未产生真实持仓）", symbol, e)
            notifier.send(cfg, f"<b>{cfg.label}</b> ⚠️ {symbol} 入场下单失败: {e}")
            return None

        filled = float(order.get("avgPrice") or 0)
        entry_price = filled if filled > 0 else price
        order_result = f"order_id={order.get('orderId')}"
        # 2026-09-12 事故修复：入场单一旦成交，交易所上就已经是真实持仓——
        # 从这一刻起，不管后面止损单是否成功，都必须先落本地账本，否则
        # 会重演 SOLUSDT/BCHUSDT 那次事故(止损单报错导致整个 try 块被
        # except 吞掉，入场单已经真实成交但账本/面板对此一无所知)。
        stop_confirmed = False
        try:
            bf.place_stop_market(cfg, symbol, sig["side"], sig["stop_loss"])
            stop_confirmed = True
            logger.info("🟢 [真实下单] %s %s @ %.6f qty=%.6f stop=%.6f", symbol, sig["side"], entry_price, qty, sig["stop_loss"])
        except Exception as e:
            logger.error("🚨 %s 入场已成交(qty=%.6f @ %.6f)但止损单失败: %s —— 已按无止损状态记入本地账本，"
                         "下一轮循环会自动重试挂止损，在此之前仓位无保护！", symbol, qty, entry_price, e)
            notifier.send(cfg, f"<b>{cfg.label}</b> 🚨🚨 {symbol} 入场已成交但止损单失败: {e}\n"
                               f"仓位当前无保护，系统会自动重试，请人工同步核实交易所")

        # 2026-09-12 新增：止盈也挂一张交易所侧兜底单(跟止损对称)——正常
        # 平仓仍然靠主循环每轮动态判断 VWAP(更准，目标价会随时间移动)，
        # 这张单只防"进程/VPS 整个挂掉"时利润无法自动落袋。失败不影响
        # 本金安全，降级成 WARNING，交给重试队列慢慢补，不用像止损那样
        # 大声报警。
        if sig.get("target"):
            tp_confirmed = False
            try:
                bf.place_take_profit_market(cfg, symbol, sig["side"], sig["target"])
                tp_confirmed = True
            except Exception as e:
                logger.warning("%s 止盈兜底单失败: %s（不影响本金安全，止损仍在，下一轮重试）", symbol, e)
    else:
        logger.info("[观察模式] %s 将开 %s @ %.6f qty=%.6f stop=%.6f （不下单，only记账）",
                    symbol, sig["side"], price, qty, sig["stop_loss"])

    pid = db.create_position({"symbol": symbol, "side": sig["side"], "entry_price": entry_price,
                               "qty": qty, "stop_price": sig["stop_loss"], "armed": cfg.is_armed,
                               "stop_confirmed": stop_confirmed,
                               "tp_price": sig.get("target"), "tp_confirmed": tp_confirmed})
    db.record_decision({"symbol": symbol, "bar_time": sig["bar_time"], "action": "OPEN",
                         "side": sig["side"], "price": entry_price, "reason": sig.get("reason"),
                         "armed": cfg.is_armed, "order_result": order_result})
    tag = "🔴 已下真实单" if cfg.is_armed else "🟡 观察模式(未下单)"
    if cfg.is_armed and not stop_confirmed:
        tag += "（⚠️止损待重试）"
    if cfg.is_armed and not tp_confirmed:
        tag += "（止盈兜底单待重试）"
    over_flag = " ⚠️超监控线" if (cfg.api_key_present and total_after > notional_cap) else ""
    notifier.send(cfg, f"<b>{cfg.label}</b> {tag}\n{symbol} {sig['side']} @ {entry_price:.6f}\n"
                       f"止损 {sig['stop_loss']:.6f}  目标(VWAP) {sig.get('target', 0):.6f}\n"
                       f"仓位 权益${equity:.2f}×{cfg.position_size_pct:.0%}=${margin_usd:.2f}保证金×{cfg.leverage:.0f}x"
                       f"≈${new_notional:.2f}名义(qty={qty:.6g})\n"
                       f"账户总敞口 ${total_after:.2f}/${notional_cap:.2f}{over_flag}\n{sig.get('reason', '')}")
    return pid


def close_position(cfg, pos: dict, reason: str, ref_price: float, already_flat: bool = False):
    """already_flat=True：调用方(对账逻辑)已经用 bf.get_position 确认过
    交易所这个品种真的没仓位了(大概率止损/止盈兜底单其中一张已经把它
    平掉)——这种情况**不能**再发一次真实平仓单，交易所根本没仓位可平，
    close_market会直接被拒(2026-09-12实测 OPENAIUSDT 复现：每轮循环都
    收到HTTP 400，本地账本因为走的还是"平仓失败就不落账"这条老路径，
    永远卡在OPEN，账本跟交易所各说各话)。已确认空仓时跳过下单，直接
    走本地记账，只清理一下可能残留的挂单(另一张没触发的兜底单)。"""
    exit_price = ref_price
    if already_flat:
        try:
            bf.cancel_all_open_orders(cfg, pos["symbol"])
        except Exception as e:
            logger.warning("%s 已确认空仓，清理残留挂单失败: %s（不影响账本更新）", pos["symbol"], e)
    elif cfg.is_armed and pos.get("armed"):
        try:
            bf.cancel_all_open_orders(cfg, pos["symbol"])
            order = bf.close_market(cfg, pos["symbol"], pos["side"], pos["qty"])
            filled = float(order.get("avgPrice") or 0)
            exit_price = filled if filled > 0 else ref_price
            logger.info("🔴 [真实平仓] %s @ %.6f", pos["symbol"], exit_price)
        except Exception as e:
            logger.error("%s 平仓下单失败: %s —— 仓位可能仍挂在交易所，需要人工核实！", pos["symbol"], e)
            notifier.send(cfg, f"<b>{cfg.label}</b> 🚨 {pos['symbol']} 平仓失败: {e}，请人工核实交易所持仓")
            return
    pnl = _pnl_usd(pos["side"], pos["entry_price"], exit_price, pos["qty"])
    db.close_position(pos["id"], exit_price, reason, pnl)
    sign = "✅" if pnl >= 0 else "❌"
    notifier.send(cfg, f"<b>{cfg.label}</b> {sign} {pos['symbol']} 平仓 @ {exit_price:.6f}\n"
                       f"{pos['side']} {pos['entry_price']:.6f}→{exit_price:.6f}  盈亏 {pnl:+.2f} USDT\n{reason}")
    if db.today_pnl() <= -abs(cfg.daily_loss_limit_usd):
        db.set_halted_today(True)
        notifier.send(cfg, f"<b>{cfg.label}</b> 🛑 今日实现亏损 ${db.today_pnl():.2f} 触及熔断线，今日停止开新仓")


def _retry_missing_stops(cfg):
    """2026-09-12 事故修复：入场成交但止损单当时失败的仓位，每轮循环
    在这里自动重试，直到确认挂上为止——不依赖人工发现，也不用重启
    服务；成功一次就标记 stop_confirmed，之后不会再重试。"""
    if not cfg.is_armed:
        return
    for pos in db.get_positions_missing_stop():
        try:
            live = bf.get_position(cfg, pos["symbol"])
        except Exception as e:
            logger.warning("%s 止损重试前查仓失败: %s，本轮跳过", pos["symbol"], e)
            continue
        if live is None:
            # 仓位在交易所已经不在了(比如已被别的路径平掉)，止损自然不需要再挂
            logger.info("%s 止损待重试，但交易所已无持仓，跳过(留给对账逻辑处理)", pos["symbol"])
            continue
        try:
            bf.place_stop_market(cfg, pos["symbol"], pos["side"], pos["stop_price"])
            db.mark_stop_confirmed(pos["id"])
            logger.info("✅ %s 止损单重试成功 @ %.6f", pos["symbol"], pos["stop_price"])
            notifier.send(cfg, f"<b>{cfg.label}</b> ✅ {pos['symbol']} 补挂止损单成功 @ {pos['stop_price']:.6f}")
        except Exception as e:
            body = getattr(e, "body", "") or str(e)
            if "-4130" in body:
                # -4130=同向已有 closePosition 止损单——上一次尝试其实成功
                # 挂到交易所了，只是我们这边没收到/没处理成功响应(比如网络
                # 在收到应答前断了)，把它误判成"失败"进了重试队列。真实情况
                # 是仓位本来就有保护，不是继续裸奔，标记确认、别再天天报警。
                db.mark_stop_confirmed(pos["id"])
                logger.info("%s 止损重试收到-4130(已有同向止损单)，说明上次其实已经挂成功，标记确认", pos["symbol"])
            else:
                logger.error("🚨 %s 止损单重试仍然失败: %s，仓位继续无保护，下一轮再试", pos["symbol"], e)


def _retry_missing_tps(cfg):
    """止盈兜底单重试——跟 _retry_missing_stops 对称，但失败不算本金
    风险(止损仍然保护着)，日志/通知力度低一档。"""
    if not cfg.is_armed:
        return
    for pos in db.get_positions_missing_tp():
        try:
            live = bf.get_position(cfg, pos["symbol"])
        except Exception as e:
            logger.warning("%s 止盈重试前查仓失败: %s，本轮跳过", pos["symbol"], e)
            continue
        if live is None:
            continue  # 仓位已不在，留给下面的对账逻辑处理
        try:
            bf.place_take_profit_market(cfg, pos["symbol"], pos["side"], pos["tp_price"])
            db.mark_tp_confirmed(pos["id"])
            logger.info("✅ %s 止盈兜底单重试成功 @ %.6f", pos["symbol"], pos["tp_price"])
        except Exception as e:
            body = getattr(e, "body", "") or str(e)
            if "-4130" in body:
                db.mark_tp_confirmed(pos["id"])
                logger.info("%s 止盈重试收到-4130(已有同向条件单)，标记确认", pos["symbol"])
            else:
                logger.warning("%s 止盈兜底单重试仍然失败: %s（止损仍在保护本金，下一轮再试）", pos["symbol"], e)


def check_exits_and_reconcile(cfg):
    """每轮：① 止损/止盈兜底单待重试的仓位自动重挂 ② 超时强平安全网
    ③ 武装模式下跟交易所对账(仓位在交易所端已经消失=大概率是止损/止盈
    兜底单其中一张被触发平的仓，本地也标记平仓，用当前标记价近似成交
    价，写清楚 exit_reason 供人工复核，并清掉另一张还挂着的僵尸条件单
    ——两张单同时挂着、只有一张会真正成交，成交那张不会自动帮忙撤掉
    没成交的那张，不清理的话会一直挂在交易所上指向一个已经不存在的
    仓位)。"""
    _retry_missing_stops(cfg)
    _retry_missing_tps(cfg)
    for pos in db.get_open_positions():
        held_h = (time.time() - pos["opened_at"]) / 3600.0
        if held_h >= cfg.max_hold_hours:
            px = md.mark_price(pos["symbol"], cfg.binance_base_url) or pos["entry_price"]
            close_position(cfg, pos, f"持有超过{cfg.max_hold_hours:.0f}h强制平仓(安全网)", px)
            continue
        if cfg.is_armed and pos.get("armed"):
            try:
                live = bf.get_position(cfg, pos["symbol"])
            except Exception as e:
                logger.warning("%s 对账查询失败: %s，本轮跳过", pos["symbol"], e)
                continue
            if live is None:
                px = md.mark_price(pos["symbol"], cfg.binance_base_url) or pos["stop_price"] or pos["entry_price"]
                close_position(cfg, pos, "交易所端持仓已消失(推测止损/止盈兜底单其中一张被触发，"
                                          "成交价为近似值，请核实)", px, already_flat=True)
