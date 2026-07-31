#!/bin/bash
# 本地清理脚本
cd "$(dirname "$0")"

echo "清理临时文件..."
rm -f _diff_*.txt
rm -f *.bak
rm -f *_orig.py
rm -f .git_commit_msg.txt
rm -f _webhook_parser_new.py

echo "清理完成！"
