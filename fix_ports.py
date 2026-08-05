#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端口配置文件修复工具

架构说明：
- 5003: /home/trading/binance-engine (Binance 主账户)
- 5007: /home/binanceB/binance-engine (Binance-B 对照账户)

用法: python3 fix_ports.py
"""
import re
import sys

FILES = [
    ('/home/trading/binance-engine/gunicorn.conf.py', '5003', 'trading-engine'),
    ('/home/binanceB/binance-engine/gunicorn.conf.py', '5007', 'binanceB-engine'),
]


def fix_gunicorn_config(fpath, port, name):
    try:
        with open(fpath) as f:
            c = f.read()
    except FileNotFoundError:
        print(f'SKIP (not found): {fpath}')
        return False

    original = c

    # 修正 bind
    c = re.sub(r'bind\s*=\s*"[^"]*"', f'bind = "0.0.0.0:{port}"', c)
    # 修正 proc_name
    c = re.sub(r'proc_name\s*=\s*"[^"]*"', f'proc_name = "{name}"', c)

    if c != original:
        with open(fpath, 'w') as f:
            f.write(c)
        print(f'UPDATED: {fpath} -> port={port}, name={name}')
        return True
    else:
        print(f'NO_CHANGE: {fpath} (already correct)')
        return True


def main():
    print('=== Binance Port Fix Tool ===')
    print()
    for fpath, port, name in FILES:
        fix_gunicorn_config(fpath, port, name)
    print()
    print('All done.')


if __name__ == '__main__':
    main()
