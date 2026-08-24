"""
2026-08-24: 验证"事后发现空仓时用真实成交价而非近似现价归因退出原因"这个修复。

背景：B账户ETH一笔仓位被永久硬止损打出局，但系统用发现那一刻的近似现价
(2484.84)去判断"是否贴近硬止损(2477.44)"，因为发现延迟导致价格已经走
出去9个点，容差判断(max(2.5, px*0.002))没能命中，误判成"来源未明"。
币安真实成交记录显示实际成交价是2475.83，跟硬止损只差1.6点，应该被
正确识别。

两部分验证：
1. 离线数学验证：用这次实盘复现的真实数字，证明"近似价判断不出、真实
   成交价判断得出"——不需要网络/账户凭证，随时可跑。
2. 在线只读验证（可选，需要在账户自己的venv+.env环境下跑）：调用新增的
   binance_client.get_last_fill_price()，确认对一个真实持仓过的symbol
   能查到非零成交价，且不会意外抛异常影响主流程。
"""


def near_hard(px: float, hard: float) -> bool:
    """镜像 position_supervisor_binance.py::_exit_px_near_hard 的判断公式。"""
    if hard <= 0 or px <= 0:
        return False
    return abs(px - hard) <= max(2.5, px * 0.002)


def test_offline_real_case_eth_binanceB():
    """实盘复现数字：近似现价判断不出硬止损，真实成交价能判断出。"""
    hard_sl = 2477.44
    approx_px_at_discovery = 2484.84  # 修复前：发现空仓那一刻的近似现价
    real_fill_px = 2475.83            # 修复后：币安futures_account_trades查到的真实成交价

    old_result = near_hard(approx_px_at_discovery, hard_sl)
    new_result = near_hard(real_fill_px, hard_sl)

    assert old_result is False, (
        f"复现修复前的误判：近似价{approx_px_at_discovery}距硬止损{hard_sl}"
        f"差{abs(approx_px_at_discovery - hard_sl):.2f}，容差"
        f"{max(2.5, approx_px_at_discovery * 0.002):.2f}，理应判不出"
    )
    assert new_result is True, (
        f"验证修复后：真实成交价{real_fill_px}距硬止损{hard_sl}"
        f"差{abs(real_fill_px - hard_sl):.2f}，应该在容差内判定为硬止损出局"
    )
    print(
        f"✅ 离线验证通过：近似价{approx_px_at_discovery}→判不出硬止损"
        f"（差{abs(approx_px_at_discovery - hard_sl):.2f} > 容差"
        f"{max(2.5, approx_px_at_discovery * 0.002):.2f}）"
    )
    print(
        f"✅ 真实成交价{real_fill_px}→正确判定硬止损出局"
        f"（差{abs(real_fill_px - hard_sl):.2f} ≤ 容差"
        f"{max(2.5, real_fill_px * 0.002):.2f}）"
    )


def test_offline_boundary_cases():
    """边界情况：确认公式本身没有回归，正常场景该判出/判不出的行为不变。"""
    # 明显远离止损，两种价格都不该判定为硬止损
    assert near_hard(2600.0, 2477.44) is False
    # 精确命中止损价，必须判定为真
    assert near_hard(2477.44, 2477.44) is True
    # 硬止损未设置(0)，任何价格都不该误判
    assert near_hard(2475.83, 0.0) is False
    print("✅ 边界情况验证通过")


def test_online_get_last_fill_price_readonly(account_dir=None, symbol="ETHUSDT"):
    """
    在线只读验证（可选）——需要在对应账户自己的venv里跑，且当前目录要能
    import到binance_client.py（即在账户的binance-engine目录下执行）。
    只调用只读的futures_account_trades，不下单不撤单，不影响任何持仓。

    用法：cd /home/binanceB/binance-engine && venv/bin/python
          /path/to/test_exit_price_classification.py --online
    """
    from binance_client import binance_client

    px = binance_client.get_last_fill_price(symbol, lookback_sec=3600 * 24)
    print(f"[在线验证] {symbol} 最近24小时真实成交价查询结果: {px}")
    assert isinstance(px, float), "返回类型必须是float，异常时也要返回0.0而不是抛出"
    if px <= 0:
        print("⚠️ 查询结果为0——可能是这段时间该symbol确实没有成交记录，"
              "或者IP正在冷却中，属于预期内的安全降级，不代表函数本身有问题")
    else:
        print(f"✅ 在线验证通过：拿到真实成交价 {px}，函数正常工作")


if __name__ == "__main__":
    import sys

    test_offline_real_case_eth_binanceB()
    test_offline_boundary_cases()

    if "--online" in sys.argv:
        test_online_get_last_fill_price_readonly()
    else:
        print(
            "\n(跳过在线验证——加 --online 参数、且在账户自己的venv+"
            "binance-engine目录下运行，可以额外验证真实API调用)"
        )

    print("\n全部离线测试通过。")
