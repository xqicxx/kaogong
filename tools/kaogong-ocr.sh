#!/bin/bash
# 考公：拍照 → 矫正 → 去红笔 → mac Vision OCR → markdown
#
#   kaogong-ocr.sh --source "逻辑论证-归因论证" [--module 行测/判断推理] [--start-page N] 图1.jpg 图2.jpg ...
#
# 机器产出统一落到 考公/_raw/<模块>/（proofread: false）；
# 校对后的版本由人/模型写到 考公/<模块>/。
set -euo pipefail
BIN="${HOME}/.pi/bin"
VAULT="${HOME}/Documents/Obsidian Vault/考公"
SOURCE="未命名讲义"
MODULE="_inbox"
START=1
KEEP_TMP=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
  --source)
    [ $# -ge 2 ] || {
      echo "--source 后面要跟讲义名" >&2
      exit 2
    }
    SOURCE="$2"
    shift 2
    ;;
  --module)
    [ $# -ge 2 ] || {
      echo "--module 后面要跟模块，如 行测/判断推理" >&2
      exit 2
    }
    MODULE="$2"
    shift 2
    ;;
  --start-page)
    [ $# -ge 2 ] || {
      echo "--start-page 后面要跟页码" >&2
      exit 2
    }
    START="$2"
    shift 2
    ;;
  --keep-tmp)
    KEEP_TMP=1
    shift
    ;;
  -h | --help)
    sed -n '2,8p' "$0"
    exit 0
    ;;
  --*)
    echo "未知参数：$1" >&2
    exit 2
    ;;
  *)
    ARGS+=("$1")
    shift
    ;;
  esac
done
[ ${#ARGS[@]} -eq 0 ] && {
  echo "没给图片" >&2
  exit 1
}

# 模块合法性：行测/<五大模块> 或 申论/<五大题型> 或 _inbox
if [ "$MODULE" != "_inbox" ]; then
  case "$MODULE" in
  行测/常识判断 | 行测/言语理解与表达 | 行测/数量关系 | 行测/判断推理 | 行测/资料分析) ;;
  申论/归纳概括 | 申论/综合分析 | 申论/提出对策 | 申论/贯彻执行 | 申论/申发论述) ;;
  *)
    echo "非法模块: $MODULE" >&2
    echo "  行测: 常识判断 言语理解与表达 数量关系 判断推理 资料分析" >&2
    echo "  申论: 归纳概括 综合分析 提出对策 贯彻执行 申发论述" >&2
    exit 2
    ;;
  esac
fi
OUT="$VAULT/_raw/$MODULE"
mkdir -p "$OUT"

TMP="$(mktemp -d)" || {
  echo "mktemp 失败" >&2
  exit 3
}
# mktemp 失败时 TMP 可能为空，rm -rf "" 会报错，所以两个条件都要判
trap 'if [ -z "$KEEP_TMP" ] && [ -n "$TMP" ]; then rm -rf "$TMP"; fi' EXIT
[ -n "$KEEP_TMP" ] && echo "  临时目录保留在：$TMP"

JSONS=()
FAILED=0
t_rect=0
t_ocr=0
for f in "${ARGS[@]}"; do
  [ -f "$f" ] || {
    echo "  ✗ 找不到文件：$f" >&2
    FAILED=$((FAILED + 1))
    continue
  }
  name="$(basename "${f%.*}")"
  s=$(date +%s%N)
  "$BIN/rectify" "$f" "$TMP/$name.jpg" >/dev/null 2>"/tmp/rect-$name.err" || cp "$f" "$TMP/$name.jpg"
  t_rect=$((t_rect + ($(date +%s%N) - s) / 1000000))
  "$BIN/deink" "$TMP/$name.jpg" "$TMP/$name-clean.jpg" >/dev/null 2>&1 || cp "$TMP/$name.jpg" "$TMP/$name-clean.jpg"
  s=$(date +%s%N)
  if "$BIN/vision-ocr" "$TMP/$name-clean.jpg" --json >"$TMP/$name.json" 2>"$TMP/$name.err"; then
    JSONS+=("$TMP/$name.json")
  else
    # 过去这里吞掉 stderr、还把坏 JSON 塞进列表，最后报“成功”
    echo "  ✗ 识别失败：$f" >&2
    [ -s "$TMP/$name.err" ] && echo "    $(head -1 "$TMP/$name.err")" >&2
    FAILED=$((FAILED + 1))
  fi
  t_ocr=$((t_ocr + ($(date +%s%N) - s) / 1000000))
done

if [ ${#JSONS[@]} -eq 0 ]; then
  echo "  全部失败，什么都没产出" >&2
  exit 4
fi
[ "$FAILED" -gt 0 ] && echo "  ⚠️ 有 $FAILED 张失败，其余继续" >&2

echo "  源: $SOURCE   模块: $MODULE   成功 ${#JSONS[@]} 张"
echo "  矫正 ${t_rect}ms  OCR ${t_ocr}ms"
python3 "$BIN/vision2md.py" --source "$SOURCE" --out-dir "$OUT" --start-page "$START" "${JSONS[@]}"
echo "  机器产出 → $OUT   （proofread: false；校对后写到 $VAULT/$MODULE/）"
