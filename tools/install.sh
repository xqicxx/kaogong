#!/bin/bash
# 编译 Swift 工具 + 安装脚本到 ~/.pi/bin
#   ./install.sh [目标目录]
set -euo pipefail
cd "$(dirname "$0")"
BIN="${1:-$HOME/.pi/bin}"
mkdir -p "$BIN"

for s in rectify vision-ocr deink crop; do
  if swiftc -O "$s.swift" -o "$BIN/$s" 2>/dev/null; then
    echo "  ✓ $s"
  else
    echo "  ✗ $s 编译失败（需要 Xcode 命令行工具：xcode-select --install）" >&2
  fi
done

install -m 755 vision2md.py kaogong-ocr.sh "$BIN/"
echo "  ✓ vision2md.py / kaogong-ocr.sh → $BIN"
echo
echo "  测试：$BIN/vision-ocr --help 2>/dev/null; echo ok"
