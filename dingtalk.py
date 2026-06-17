#!/usr/bin/env python3
# dingtalk.py（V3.0 币安 15/30/50 固定差价止盈专属战报版）
import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")


def _generate_sign(secret: str) -> tuple:
    """生成加签 timestamp + sign"""
    timestamp = str(round(time.time() * 1000))
    secret_enc = secret.encode('utf-8')
    string_to_sign = f'{timestamp}\n{secret}'
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def _get_signed_url() -> str:
    if not DINGTALK_WEBHOOK: return ""
    if DINGTALK_SECRET:
        timestamp, sign = _generate_sign(DINGTALK_SECRET)
        return f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
    return DINGTALK_WEBHOOK


def send_markdown_message(title: str, text: str, is_at_all: bool = False):
    """发送极度美观的 Markdown 富文本消息"""
    if not DINGTALK_WEBHOOK:
        logger.warning("[DingTalk] 未配置 Webhook，跳过发送")
        return False

    try:
        url = _get_signed_url()
        if not url: return False

        # 统一注入头部时间戳与签名
        full_text = f"### {title}\n> **⏱ 汇报时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n{text}\n---\n*🤖 币安战神 V4 · 全域单向护城河与死咬机制*"

        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": full_text
            },
            "at": {"isAtAll": is_at_all}
        }

        resp = requests.post(url, json=data, timeout=8)
        if resp.status_code == 200 and resp.json().get("errcode") == 0:
            logger.info(f"[DingTalk] Markdown 报告发送成功: {title}")
            return True
        else:
            logger.error(f"[DingTalk] 发送失败: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"[DingTalk] 发送异常: {e}", exc_info=True)
        return False


# ==================== 监督层核实专用报告模板 ====================

def report_supervisor_open(side: str, entry_price: float, qty: float, tp_dict: dict, account_info: dict):
    """实盘开仓核实通过报告"""
    emoji = "🟩" if side == "LONG" else "🟥"
    action_text = "做多 (LONG)" if side == "LONG" else "做空 (SHORT)"
    
    text = f"""
**📍 实盘持仓核实通过 (单向一手)**
- **交易方向**：{emoji} **{action_text}**
- **实盘入场**：`{entry_price}` USDT
- **确认仓位**：`{qty}` ETH (50%本金 / 20x杠杆)

**🎯 固定止盈防线 (15/30/50U 刺客流)**
- **TP1 (40%)**：`{tp_dict.get('tp1')}`
- **TP2 (40%)**：`{tp_dict.get('tp2')}`
- **TP3 (20%)**：`{tp_dict.get('tp3')}`

**📊 当前账户与风控状态**
- **可用保证金**：{account_info.get('balance', 0):.2f} USDT
- **账户总权益**：{account_info.get('equity', 0):.2f} USDT
- **动态风险系数**：`{account_info.get('risk_mult', 1.0)}`
"""
    send_markdown_message("🚀 新开仓实盘核实报告", text)


def report_supervisor_close(side: str, reason: str, real_pnl: float, account_info: dict):
    """实盘平仓核实报告"""
    pnl_emoji = "🔥" if real_pnl > 0 else "🩸"
    
    text = f"""
**🔚 阵地清算核实完毕**
- **清场原因**：{reason}
- **原仓方向**：{side}
- **真实已实现盈亏**：{pnl_emoji} **{real_pnl:+.2f} USDT**

**📉 账户结算后状态**
- **账户总权益**：{account_info.get('equity', 0):.2f} USDT
- **当日累计盈亏**：{account_info.get('daily_pnl', 0):+.2f} USDT
- **最大回撤拦截**：{account_info.get('drawdown', 0):.2%}
"""
    send_markdown_message("🔚 信号平仓核实报告", text)


def report_supervisor_tp_trigger(level: str, trigger_price: float, real_pnl: float, next_action: str):
    """止盈触发核实报告"""
    text = f"""
**🎯 止盈防线被击穿**
- **触发级别**：**{level}**
- **触发现价**：`{trigger_price}` USDT
- **本段落袋盈亏**：🔥 **{real_pnl:+.2f} USDT**

**🛡️ 系统后续应对**
- {next_action}
"""
    send_markdown_message(f"🎯 {level} 止盈光速落袋", text)


def report_supervisor_intervention(old_qty: float, new_qty: float, new_tps: dict):
    """人工干预应对报告"""
    text = f"""
**⚠️ 检测到外部仓位变更 (人工干预)**
- **系统原计仓位**：`{old_qty}` ETH
- **实盘侦测仓位**：`{new_qty}` ETH
- **应对决策**：已接管新仓位，并基于当前入场价重新锚定止盈！

**🔄 重新锚定的 15/30/50 止盈防线**
- **新 TP1 (40%)**：`{new_tps.get('tp1')}`
- **新 TP2 (40%)**：`{new_tps.get('tp2')}`
- **新 TP3 (20%)**：`{new_tps.get('tp3')}`
"""
    send_markdown_message("🚨 人工干预动态纠正报告", text, is_at_all=True)


def report_force_align(old_side: str, new_side: str):
    """防逆向操作强制重置"""
    text = f"""
**☠️ 触发单向持仓底线保护**
- **实盘违规方向**：{old_side}
- **TV最高权威方向**：{new_side}
- **应对决策**：已强行抹平违规仓位，强制洗盘重开对齐 TV！
- **警告**：系统已锁定单向持仓模式，请勿人为挂单反向对冲！
"""
    send_markdown_message("⚔️ 强制对齐纠正报告", text, is_at_all=True)


def report_anomaly(message: str):
    text = f"**🚨 运行时拦截日志**\n\n{message}\n\n请管理员立刻登入服务器排查！"
    send_markdown_message("🚨 系统异常熔断警报", text, is_at_all=True)
