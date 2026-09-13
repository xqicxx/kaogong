#!/bin/bash
# 考公系统压力测试：量级 + 边界，全程在临时库跑，绝不碰真库。
#
#   tools/stress-test.sh <讲义.pdf> [页数] [起始页]
#
# 为什么要有它：这套系统的毛病几乎都是「拿真材料一跑才暴露」——
# 分类器太严、表格页漏内容、shell 脚本硬编码 vault 路径、跨页断裂……
# 改完任何东西跑一遍，能挡住大部分回归。
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd -P)"

PDF="${1:-}"
PAGES="${2:-20}"
START="${3:-20}"
KEEP="${KEEP:-}"
if [ -z "$PDF" ] || [ ! -f "$PDF" ]; then
  echo "用法: tools/stress-test.sh <讲义.pdf> [页数] [起始页]" >&2
  exit 2
fi

TMP=/tmp/kg-stress-$$
export KAOGONG_VAULT="$TMP/vault"
export KAOGONG_STATE="$TMP/state"
mkdir -p "$KAOGONG_VAULT/考点" "$KAOGONG_VAULT/错题" "$KAOGONG_VAULT/_raw" "$KAOGONG_STATE"

pass=0; fail=0
ok()   { printf '    ✓ %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '    ✗ %s\n' "$1" >&2; fail=$((fail+1)); }
step() { printf '\n  ── %s ──\n' "$1"; }
cleanup() { [ -n "$KEEP" ] || rm -rf "$TMP"; }
trap cleanup EXIT

step "0. 基线：真库现状"
REAL="${HOME}/Documents/Obsidian Vault/考公"
before=$(ls -R "$REAL/_raw" 2>/dev/null | wc -l | tr -d ' ')
printf '     真库 _raw 条目: %s\n' "$before"

step "1. 栅格化 $PAGES 页"
if ~/.pi/bin/pdf2img "$PDF" "$TMP/pg" "$((START-1))" "$PAGES" >"$TMP/pdf2img.log" 2>&1; then
  n=$(ls "$TMP"/pg-*.png 2>/dev/null | wc -l | tr -d ' ')
  ok "栅格化 $n 页"
else
  bad "栅格化失败（见 $TMP/pdf2img.log）"
fi

step "2. 造边界输入"
python3 - "$TMP/blank.png" <<'PY'
import struct, sys, zlib
w = h = 200
raw = b''.join(b'\x00' + b'\xff' * (w * 3) for _ in range(h))
def chunk(t, d):
    c = struct.pack('>I', len(d)) + t + d
    return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
open(sys.argv[1], 'wb').write(png)
PY
first=$(ls "$TMP"/pg-*.png | head -1)
cp "$first" "$TMP/dup.png"
printf 'not a png' > "$TMP/broken.png"
ok '空白图 / 重复图 / 坏文件 已造好'

step "3. 批量 OCR（含边界）"
START_TS=$(date +%s)
set +e
~/.pi/bin/kaogong-ocr.sh --source "压测" --module 行测/常识判断 "$TMP"/pg-*.png "$TMP/blank.png" "$TMP/dup.png" "$TMP/broken.png" >"$TMP/ocr.log" 2>&1
ocr_code=$?
set -e
END_TS=$(date +%s); elapsed=$((END_TS - START_TS))
# || true 不能省：set -e + pipefail 下 ls 空 glob 会返回非零，
# 而「零产出」正是这里要上报的情形（否则脚本死在这一行，报不出问题）
produced=$(ls "$KAOGONG_VAULT/_raw/行测/常识判断/"*.md 2>/dev/null | wc -l | tr -d ' ' || true)
[ "$produced" -gt 0 ] && ok "产出 $produced 页，耗时 ${elapsed}s" || bad '一页都没产出'
grep -q '识别失败\|读不到图片' "$TMP/ocr.log" && ok '坏文件被识别并报错' || bad '坏文件没被识别'
[ "$ocr_code" -gt 0 ] && ok "退出码 $ocr_code（有失败时不为 0）" || bad '有失败却退出 0'

step "4. 隔离验证：真库有没有被动"
after=$(ls -R "$REAL/_raw" 2>/dev/null | wc -l | tr -d ' ')
if [ "$before" = "$after" ]; then
  ok '真库 _raw 条目数未变'
else
  bad "真库被写了！$before → $after"
fi

step "5. 切卡 + 排期"
python3 pipeline/split.py --module 行测/常识判断 --lecture 压测讲义 --source 压测 "$KAOGONG_VAULT/_raw/行测/常识判断/"*.md >"$TMP/split.log" 2>&1
cards=$(ls "$KAOGONG_VAULT/考点/"*.md 2>/dev/null | wc -l | tr -d ' ')
[ "$cards" -gt 0 ] && ok "切出 $cards 张卡" || bad '没切出卡'
python3 pipeline/review.py init >/dev/null 2>&1 && ok '排期初始化成功' || bad '排期失败'

step "6. 推送（长度与转义）"
python3 pipeline/review.py push --dry --limit 200 >"$TMP/push.log" 2>&1
chars=$(python3 -c "print(len(open('$TMP/push.log').read()))")
[ "$chars" -lt 4096 ] && ok "推送 $chars 字（Telegram 上限 4096）" || bad "推送超限：$chars 字"

step "7. 错题本量级"
for i in $(seq 1 10); do
  python3 pipeline/mistake.py new --module 判断推理 --stem "压测错题$i" --cause 概念性 --confidence 高 >/dev/null 2>&1
done
m=$(ls "$KAOGONG_VAULT/错题/"*.md 2>/dev/null | wc -l | tr -d ' ')
[ "$m" -eq 10 ] && ok '录入 10 道错题' || bad "错题数不对：$m"
python3 pipeline/mistake.py list --stage 未掌握 >/dev/null 2>&1 && ok '筛选可用' || bad '筛选失败'
python3 pipeline/mistake.py export --out "$TMP/卷.md" >/dev/null 2>&1 && ok '导出可用' || bad '导出失败'

printf '\n  ══ 结果：%d 通过 / %d 失败 ══\n' "$pass" "$fail"
[ -n "$KEEP" ] && echo "  临时库保留在 $TMP（KEEP=1）"
[ "$fail" -eq 0 ] || exit 1
