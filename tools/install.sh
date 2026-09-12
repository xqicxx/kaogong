#!/bin/bash
# 编译 Swift 工具 + 安装脚本到 ~/.pi/bin
#   ./install.sh [目标目录]
#
# 原来这里会把 swiftc 的报错丢掉、编译失败也 exit 0、冒烟测试只 echo 不跑 ——
# 现在：旧产物先删、错误照实打印、失败计数、真跑一次冒烟。
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd -P)" # 认符号链接，也扛得住路径里的空格
BIN="${1:-$HOME/.pi/bin}"
mkdir -p "$BIN"

failed=0
for tool in rectify vision-ocr deink crop; do
  rm -f "$BIN/$tool" # 失败时别留着旧二进制冒充新版本
  if swiftc -O "$tool.swift" -o "$BIN/$tool"; then
    # 编译过 ≠ 能跑：架构不匹配、dylib 缺失、Gatekeeper 拦截都是运行期才暴露。
    # 这几个工具无参时都会打印用法并非零退出，正好当冒烟测试。
    if "$BIN/$tool" >/dev/null 2>&1; then
      echo "  ✗ $tool 编译成功但运行异常（无参时本该非零退出并给用法）" >&2
      failed=$(( failed + 1 ))
    else
      echo "  ✓ $tool"
    fi
  else
    echo "  ✗ $tool 编译失败（需要 Xcode 命令行工具：xcode-select --install）" >&2
    failed=$((failed + 1))
  fi
done

# 这两个是脚本，直接软链过去 —— 否则改了仓库里的版本，跑的还是 ~/.pi/bin 里的旧副本
# （这个坑真踩过：修了参数校验，测出来还是老行为，因为跑的是没更新的拷贝）
for script in vision2md.py kaogong-ocr.sh; do
  rm -f "$BIN/$script"
  ln -s "$(pwd)/$script" "$BIN/$script"
done
echo "  ✓ vision2md.py / kaogong-ocr.sh → $BIN（软链，改了立即生效）"

# 真跑一遍，不只看文件在不在
if "$BIN/vision2md.py" --help >/dev/null 2>&1; then
  echo "  ✓ vision2md.py 可执行"
else
  echo "  ✗ vision2md.py 跑不起来（python3 在吗？）" >&2
  failed=$((failed + 1))
fi
if bash -n "$BIN/kaogong-ocr.sh"; then
  echo "  ✓ kaogong-ocr.sh 语法通过"
else
  echo "  ✗ kaogong-ocr.sh 语法错误" >&2
  failed=$((failed + 1))
fi

if [ "$failed" -gt 0 ]; then
  echo "  $failed 项失败 —— 上面有原因" >&2
  exit 1
fi
echo "  全部就绪：$BIN"
