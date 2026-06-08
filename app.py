@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True)  # silent=True 更安全
        if not data:
            logging.warning("[Webhook] 收到非JSON或空请求")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        account = data.get("account", "main")

        logging.info(f"[收到信号] signal={signal}, symbol={symbol}, account={account}, raw={data}")

        if not signal:
            return jsonify({"status": "error", "message": "Missing signal"}), 400

        client = get_client(account)

        if signal == "OPEN_LONG":
            result = client.open_position(symbol, "LONG")
        elif signal == "OPEN_SHORT":
            result = client.open_position(symbol, "SHORT")
        elif signal == "CLOSE_ALL":
            result = client.close_all_positions(symbol)
        else:
            logging.warning(f"[未知信号] {signal}")
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

        return jsonify(result)

    except Exception as e:
        logging.error(f"[Webhook异常] {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
