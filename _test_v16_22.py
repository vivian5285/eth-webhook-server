#!/usr/bin/env python3
"""v16.22 v2.0 验证测试"""
import sys
sys.path.insert(0, '/home/trading/binance-engine')

from reentry_profiles import radar_gate_price_from_tps, tier_coeffs, get_reentry_profile

print("=" * 60)
print("v16.22 v2.0 雷达激活验证测试")
print("=" * 60)

# 1. 测试TP2激活价
print("\n[1] 雷达激活价测试:")
gate = radar_gate_price_from_tps(1950.0, 1970.0, attempt=0)
print(f"    TP1=1950, TP2=1970, 首次开仓(attempt=0)")
print(f"    雷达激活价 = {gate}")
print(f"    期望: 1970 (TP2价格)")
assert gate == 1970.0, f"激活价错误: {gate}"
print(f"    ✅ 激活价正确!")

gate2 = radar_gate_price_from_tps(1950.0, 1970.0, attempt=1)
print(f"    重入开仓(attempt=1) 激活价 = {gate2}")
print(f"    期望: 1970 (TP2价格)")
assert gate2 == 1970.0, f"重入激活价错误: {gate2}"
print(f"    ✅ 重入激活价正确!")

# 2. 测试XAU强趋势参数
print("\n[2] XAU强趋势呼吸参数:")
params = tier_coeffs(2, get_reentry_profile("XAUUSDT"))
print(f"    breath_tp12 = {params['breath_tp12']} (期望: 3.0)")
print(f"    breath_tp23 = {params['breath_tp23']} (期望: 4.0)")
print(f"    min_mult = {params['min_mult']} (期望: 5.0)")
print(f"    max_mult = {params['max_mult']} (期望: 7.0)")
assert params['breath_tp12'] == 3.0, f"breath_tp12错误: {params['breath_tp12']}"
assert params['breath_tp23'] == 4.0, f"breath_tp23错误: {params['breath_tp23']}"
assert params['min_mult'] == 5.0, f"min_mult错误: {params['min_mult']}"
assert params['max_mult'] == 7.0, f"max_mult错误: {params['max_mult']}"
print(f"    ✅ XAU强趋势参数正确!")

# 3. 测试ETH强趋势参数
print("\n[3] ETH强趋势呼吸参数:")
params_eth = tier_coeffs(2, get_reentry_profile("ETHUSDT"))
print(f"    breath_tp12 = {params_eth['breath_tp12']} (期望: 2.5)")
print(f"    breath_tp23 = {params_eth['breath_tp23']} (期望: 3.5)")
print(f"    min_mult = {params_eth['min_mult']} (期望: 4.0)")
print(f"    max_mult = {params_eth['max_mult']} (期望: 6.0)")
assert params_eth['breath_tp12'] == 2.5, f"ETH breath_tp12错误"
assert params_eth['max_mult'] == 6.0, f"ETH max_mult错误"
print(f"    ✅ ETH强趋势参数正确!")

print("\n" + "=" * 60)
print("✅ 所有测试通过! v16.22 v2.0 验证完成")
print("=" * 60)
