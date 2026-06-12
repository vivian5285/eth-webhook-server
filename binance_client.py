# ==================== 加强版 send_position_open_report ====================

def send_position_open_report(self, signal: str, qty: float, entry_price: float,
                              tp1: float = 0, tp2: float = 0, tp3: float = 0):
    """
    开仓成功报告（由监督层调用）
    """
    try:
        logging.info(f"[报告] 开始生成开仓报告: {signal}")

        is_long = signal == "OPEN_LONG"
        direction = "开多 🟢" if is_long else "开空 🔴"

        # 空单止盈价格修正保护
        if not is_long:
            if tp1 > entry_price:
                tp1 = round(entry_price - (tp1 - entry_price), 2)
            if tp2 > entry_price:
                tp2 = round(entry_price - (tp2 - entry_price), 2)
            if tp3 > entry_price:
                tp3 = round(entry_price - (tp3 - entry_price), 2)

        # 获取账户余额（带保护）
        try:
            balance = self.get_account_balance()
            total_balance = balance.get("totalWalletBalance", 0)
            available_balance = balance.get("availableBalance", 0)
        except Exception as e:
            logging.warning(f"[报告] 获取余额失败，使用默认值: {e}")
            total_balance = 0
            available_balance = 0

        content = f"""### {direction} 成功

**数量**: {qty} 张  
**开仓价**: {entry_price} USDT

**止盈目标**
- 止盈1: {tp1} USDT
- 止盈2: {tp2} USDT  
- 止盈3: {tp3} USDT

**账户详情**
- 账户权益: {total_balance} USDT
- 可用余额: {available_balance} USDT
"""

        self._send_dingtalk(f"{signal} 成功", content)
        logging.info(f"[报告] {signal} 钉钉报告已发送")

    except Exception as e:
        logging.error(f"[报告] send_position_open_report 异常: {e}")


# ==================== 加强版 _send_dingtalk ====================

def _send_dingtalk(self, title: str, content: str):
    """
    钉钉发送（带加签 + 详细日志）
    """
    if not DINGTALK_WEBHOOK:
        logging.error("[钉钉] DINGTALK_WEBHOOK 未配置")
        return

    try:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
        hmac_code = hmac.new(
            DINGTALK_SECRET.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": content
            }
        }

        resp = requests.post(url, json=data, timeout=8)
        logging.info(f"[钉钉] 发送完成 | 状态码: {resp.status_code} | 标题: {title}")

        if resp.status_code != 200:
            logging.warning(f"[钉钉] 发送异常响应: {resp.text}")

    except Exception as e:
        logging.error(f"[钉钉] 发送失败: {e}")


# ==================== 补充 send_close_all_report（加强版） ====================

def send_close_all_report(self, reason: str = ""):
    try:
        logging.info(f"[报告] 开始生成全平报告，原因: {reason}")

        try:
            balance = self.get_account_balance()
            total_balance = balance.get("totalWalletBalance", 0)
        except Exception:
            total_balance = 0

        content = f"""### 🔴 全平完成

**原因**: {reason}

**账户权益**: {total_balance} USDT
"""
        self._send_dingtalk("全平完成", content)
        logging.info("[报告] 全平报告已发送")

    except Exception as e:
        logging.error(f"[报告] send_close_all_report 异常: {e}")


# ==================== 补充 send_tp_trigger_report（可选加强） ====================

def send_tp_trigger_report(self, level: str, closed_qty: float, remaining_qty: float):
    try:
        content = f"""### ✅ 系统止盈触发

**触发级别**: {level.upper()}  
**本次平仓数量**: {closed_qty}  
**剩余仓位**: {remaining_qty}
"""
        self._send_dingtalk(f"止盈 {level.upper()} 触发", content)
        logging.info(f"[报告] {level} 止盈报告已发送")
    except Exception as e:
        logging.error(f"[报告] send_tp_trigger_report 异常: {e}")
