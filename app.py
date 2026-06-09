@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")
        reason = data.get("reason", "")

        client = get_client(account)

        # ========== 开仓 ==========
        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            side = "LONG" if signal == "OPEN_LONG" else "SHORT"
            # 这里可以调用你之前的 smart_open_position 方法
            send_pretty_dingtalk(client, f"{side} 开仓", f"收到 {signal} 信号")
            return jsonify({"status": "success", "action": signal}), 200

        # ========== 部分止盈（TP1 / TP2） ==========
        if signal == "TP_PARTIAL":
            if reason not in ["tp1", "tp2"]:
                return jsonify({"status": "ignored"}), 200

            close_percent = 0.30
            result = client.close_partial_position(symbol, close_percent)

            if result.get("status") == "success":
                send_pretty_dingtalk(client, f"部分止盈 {reason.upper()}", 
                                     f"平仓 {close_percent*100}%")
            elif result.get("status") == "skipped":
                logging.info(f"[TP_PARTIAL 跳过] {symbol} - {result.get('reason')}")
            else:
                send_pretty_dingtalk(client, f"部分止盈失败 {reason.upper()}", 
                                     "执行失败", is_warning=True)

            return jsonify({"status": result.get("status"), "action": signal}), 200

        # ========== 全平（含 TP3 和反转保护） ==========
        if signal == "CLOSE_ALL":
            # TP3 最终全平
            if reason == "tp3_full_close":
                result = client.close_all_positions(symbol)
                if result.get("status") == "success":
                    send_pretty_dingtalk(client, "TP3 最终全平", "已全平剩余仓位")
                return jsonify({"status": "success", "action": "TP3_FULL_CLOSE"}), 200

            # 其他全平（反转、时间止损、快速平仓等）—— 智能化静默处理
            position = client.get_current_position(symbol)
            if float(position.get("positionAmt", 0)) == 0:
                logging.info(f"[静默跳过] {symbol} 当前无持仓，忽略 {reason} 的 CLOSE_ALL 信号")
                return jsonify({"status": "skipped", "reason": "position_already_closed"}), 200

            # 有持仓才执行全平
            result = client.close_all_positions(symbol)
            if result.get("status") == "success":
                send_pretty_dingtalk(client, "全平完成", reason)
            return jsonify({"status": "success", "action": "CLOSE_ALL"}), 200

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}", exc_info=True)
        return jsonify({"status": "error"}), 500
