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
DEDUP=""
WITH_MARKS=""
MARKS_COLOR="any"
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
  --no-dedup)
    DEDUP="--no-dedup"
    shift
    ;;
  --with-marks)
    # 额外产出笔迹层图与文字（判对错、推错因的依据）
    WITH_MARKS=1
    shift
    ;;
  --marks-color)
    [ $# -ge 2 ] || {
      echo "--marks-color 后面要跟 red / blue / any" >&2
      exit 2
    }
    MARKS_COLOR="$2"
    shift 2
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
# --source 会变成文件名/路径片段，先清掉分隔符与控制字符
SOURCE_SAFE=$(printf %s "$SOURCE" | tr -d "\000-\037" | tr "/:" "--")
[ -n "$SOURCE_SAFE" ] || SOURCE_SAFE="未命名讲义"

FAILED=0
t_rect=0
t_ocr=0
index=0
for f in "${ARGS[@]}"; do
  index=$((index + 1))
  [ -f "$f" ] || {
    echo "  ✗ 找不到文件：$f" >&2
    FAILED=$((FAILED + 1))
    continue
  }
  # 加序号前缀：不同目录下的同名图片（a/图1.jpg 与 b/图1.jpg）否则会互相覆盖，
  # 结果是悄悄少页或者串页
  name="$(printf '%03d-%s' "$index" "$(basename "${f%.*}")")"
  s=$(date +%s%N)
  if ! "$BIN/rectify" "$f" "$TMP/$name.jpg" >/dev/null 2>"$TMP/$name-rect.err"; then
    # 检测不到文档时 rectify 本来就该原样输出；真失败才走这个兜底，但要报出来
    echo "  ! 矫正没成功，用原图继续：$f" >&2
    [ -s "$TMP/$name-rect.err" ] && echo "    $(head -1 "$TMP/$name-rect.err")" >&2
    cp "$f" "$TMP/$name.jpg"
  fi
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
  if [ -n "$WITH_MARKS" ]; then
    # 红笔层：把手写批注单独抠出来。印刷体留给 OCR，手写体留给我判
    # 默认 any：红笔、蓝笔、任何彩色笔都算；黑白印刷体是灰阶，不会被选中
    if "$BIN/deink" "$TMP/$name.jpg" "$TMP/$name-marks.jpg" --marks "$MARKS_COLOR" \
      >"$TMP/$name-marks.log" 2>&1; then
      "$BIN/vision-ocr" "$TMP/$name-marks.jpg" >"$TMP/$name-marks.txt" 2>/dev/null || true
    fi
  fi
done

if [ ${#JSONS[@]} -eq 0 ]; then
  echo "  全部失败，什么都没产出" >&2
  exit 4
fi
[ "$FAILED" -gt 0 ] && echo "  ⚠️ 有 $FAILED 张失败，其余继续" >&2

echo "  源: $SOURCE   模块: $MODULE   成功 ${#JSONS[@]} 张"
echo "  矫正 ${t_rect}ms  OCR ${t_ocr}ms"
if ! python3 "$BIN/vision2md.py" --source "$SOURCE_SAFE" --out-dir "$OUT" --start-page "$START" $DEDUP "${JSONS[@]}"; then
  echo "  ✗ 版面转换失败（vision2md），产物可能不完整" >&2
  exit 6
fi

# 红笔层产物放在成功分支之后 —— 之前误插进失败分支，只有报错时才拷贝
if [ -n "$WITH_MARKS" ]; then
  mkdir -p "$OUT/marks"
  for extra in "$TMP"/*-marks.jpg "$TMP"/*-marks.txt; do
    [ -f "$extra" ] && cp "$extra" "$OUT/marks/"
  done
  echo "  笔迹层（红/蓝/彩色） → $OUT/marks/"
fi
echo "  机器产出 → $OUT   （proofread: false；校对后写到 $VAULT/$MODULE/）"
# 有页面失败时不要报成功：调用方（脚本/agent）要能感知到
if [ "$FAILED" -gt 0 ]; then
  echo "  ⚠️ $FAILED 张失败，产物不完整" >&2
  exit 5
fi
