#!/bin/bash
# 考公：拍照 → 矫正 → 去红笔 → mac Vision OCR → markdown
#
#   kaogong-ocr.sh --source "逻辑论证-归因论证" --module 行测/判断推理 图1.jpg 图2.jpg ...
#
# 机器产出统一落到 考公/_raw/<模块>/（proofread: false）；
# 校对后的版本由人/模型写到 考公/<模块>/。
set -euo pipefail
BIN="$HOME/.pi/bin"
VAULT="$HOME/Documents/Obsidian Vault/考公"
SOURCE="未命名讲义"; MODULE="_inbox"; START=1; KEEP_TMP=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --module) MODULE="$2"; shift 2 ;;
    --start-page) START="$2"; shift 2 ;;
    --keep-tmp) KEEP_TMP=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
[ ${#ARGS[@]} -eq 0 ] && { echo "没给图片" >&2; exit 1; }

# 模块合法性：行测/<五大模块> 或 申论/<五大题型> 或 _inbox
if [ "$MODULE" != "_inbox" ]; then
  case "$MODULE" in
    行测/常识判断|行测/言语理解与表达|行测/数量关系|行测/判断推理|行测/资料分析) ;;
    申论/归纳概括|申论/综合分析|申论/提出对策|申论/贯彻执行|申论/申发论述) ;;
    *) echo "非法模块: $MODULE" >&2
       echo "  行测: 常识判断 言语理解与表达 数量关系 判断推理 资料分析" >&2
       echo "  申论: 归纳概括 综合分析 提出对策 贯彻执行 申发论述" >&2
       exit 2 ;;
  esac
fi
OUT="$VAULT/_raw/$MODULE"
mkdir -p "$OUT"

TMP="$(mktemp -d)"
trap '[ -n "$KEEP_TMP" ] || rm -rf "$TMP"' EXIT

JSONS=()
t_rect=0; t_ocr=0
for f in "${ARGS[@]}"; do
  name="$(basename "${f%.*}")"
  s=$(date +%s%N)
  "$BIN/rectify" "$f" "$TMP/$name.jpg" >/dev/null 2>&1 || cp "$f" "$TMP/$name.jpg"
  t_rect=$(( t_rect + ($(date +%s%N) - s) / 1000000 ))
  "$BIN/deink" "$TMP/$name.jpg" "$TMP/$name-clean.jpg" >/dev/null 2>&1 || cp "$TMP/$name.jpg" "$TMP/$name-clean.jpg"
  s=$(date +%s%N)
  "$BIN/vision-ocr" "$TMP/$name-clean.jpg" --json > "$TMP/$name.json" 2>/dev/null
  JSONS+=("$TMP/$name.json")
  t_ocr=$(( t_ocr + ($(date +%s%N) - s) / 1000000 ))
done
echo "  源: $SOURCE   模块: $MODULE   共 ${#ARGS[@]} 张"
echo "  矫正 ${t_rect}ms  OCR ${t_ocr}ms  （均摊 $(( (t_rect + t_ocr) / ${#ARGS[@]} ))ms/张）"
python3 "$BIN/vision2md.py" --source "$SOURCE" --out-dir "$OUT" --start-page "$START" "${JSONS[@]}"
echo "  机器产出 → $OUT   （proofread: false；校对后写到 $VAULT/$MODULE/）"
