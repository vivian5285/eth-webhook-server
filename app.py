from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)  # 允许跨域请求，方便测试

# 获取 Railway 分配的端口，没有则默认 5000
PORT = int(os.environ.get("PORT", 5000))

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        print(f"[收到信号] {signal} | Symbol: {symbol}")

        if signal in ["OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL"]:
            # 这里后面会接入真实交易逻辑
            return jsonify({
                "status": "success",
                "signal": signal,
                "symbol": symbol,
                "message": "信号已接收（测试模式）"
            })
        else:
            return jsonify({"status": "error", "message": "Unknown signal"}), 400

    except Exception as e:
        print(f"[错误] {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    print(f"服务器启动中，监听端口: {PORT}")
    app.run(host="0.0.0.0", port=PORT)
