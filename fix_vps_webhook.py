#!/usr/bin/env python3
"""Fix VPS app.py: 添加 /binance/webhook 路由"""
import sys

# 读取原文件
with open('/home/trading/binance-engine/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 检查是否已有 /binance/webhook 路由
if '/binance/webhook' in content:
    print('OK /binance/webhook 路由已存在')
    sys.exit(0)

# 找到 webhook 函数定义，添加 /binance/webhook 路由
old_line = """@app.route('/webhook', methods=['POST'])
@app.route('/webhook/<path:ticker>', methods=['POST'])"""
new_line = old_line + """
@app.route('/binance/webhook', methods=['POST'])
@app.route('/binance/webhook/<path:ticker>', methods=['POST'])"""

if old_line not in content:
    print('ERROR: 找不到原始路由定义')
    sys.exit(1)

content = content.replace(old_line, new_line)

# 写回
with open('/home/trading/binance-engine/app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('OK 已添加 /binance/webhook 路由')

# 验证
with open('/home/trading/binance-engine/app.py', 'r', encoding='utf-8') as f:
    check = f.read()
    if '/binance/webhook' in check:
        print('OK 验证成功')
    else:
        print('ERROR: 验证失败')
        sys.exit(1)
