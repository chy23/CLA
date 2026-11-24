#!/bin/bash
cd "$(dirname "$0")"
python3 scripts/build_data.py
echo ""
echo "按任意鍵關閉視窗..."
read -n 1 -s
