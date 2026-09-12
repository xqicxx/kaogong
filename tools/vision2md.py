#!/usr/bin/env python3
"""mac Vision OCR 的 JSON → Obsidian markdown。

Vision 只给「文本行 + 坐标 + 置信度」，没有版面语义，所以版面全靠几何+模式推断：
  行高/缩进/间距  → 标题 vs 正文 vs 段落断点
  行首模式        → 章节标题、列表
  批内重复        → 页眉/页脚/水印（单页看不出重复，所以要成批跑）

用法:
  vision2md.py --source "2026高端班讲义-法律" --out-dir out p1.json p2.json ...
  vision2md.py --source X --stdout p1.json
"""
import argparse
import difflib
import json
import os
import re
import sys

# 必须用 realpath：~/.pi/bin/vision2md.py 是指向仓库的软链，
# abspath 不跟随软链，会算出 ~/.pi 而不是仓库根
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from pipeline import dedup  # type: ignore[import]  # noqa: E402


def safe_int(value, default):
    """把比例算出来的浮点取整；类型不对就退回默认值。

    这些值来自页数/行数，正常不会出错，但一批 OCR 结果里混进脏数据时，
    宁可退默认值也不要让整批转换挂掉。
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default
from collections import Counter
from pathlib import Path

# 讲义上印刷的水印/品牌字样（成批跑时还会自动识别重复出现的页眉页脚）
WATERMARK = ("请勿外传", "内部分享", "金标尺教育", "考编制金标尺", "严禁外传", "仅供内部")

H_CHAPTER = re.compile(r"^第[一二三四五六七八九十百零〇\d]+[章篇讲]")
H_SECTION = re.compile(r"^第[一二三四五六七八九十百零〇\d]+节")
H_CN_NUM = re.compile(r"^[一二三四五六七八九十]+[、.．]")
H_PAREN_CN = re.compile(r"^[（(][一二三四五六七八九十]+[）)]")
H_NUM = re.compile(r"^\d+(\.\d+)*[、.．]?\s*\S")
H_PAREN_NUM = re.compile(r"^[（(]\d+[）)]")
BULLET = re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳·•▪●○\-*]\s*")
TERMINAL = "。！？；…\"'）)」』】"
# 页脚页码（第1页 / 1 / 一）。必须配合"在页边带里"才丢，否则会误伤正文里的「一」
PAGE_NUM = re.compile(r"^第?\s*[0-9０-９一二三四五六七八九十]{1,4}\s*[页頁]?$")


def normalize_line(raw):
    """把一条 OCR 行规整成内部结构；缺字段/类型不对就丢掉这一行。

    直接 l["y"] 这种索引在脏数据上会 KeyError，一页坏行就把整批转换带崩。
    行级容错比整批失败划算：丢一行只是个错字，丢一批是白干。
    """
    try:
        text = str(raw.get("text", "")).strip()
        if not text:
            return None
        return {"text": text,
                "y": float(raw["y"]), "x": float(raw["x"]), "h": float(raw["h"]),
                "w": float(raw.get("w", 0.0)), "conf": float(raw.get("conf", 1.0))}
    except (TypeError, ValueError, KeyError):
        return None


def load(path):
    """读一页的 OCR JSON。读不动就报清楚是哪一步坏了。"""
    try:
        raw_text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit("读不到 OCR 结果 %s：%s" % (path, exc))
    try:
        parsed = json.loads(raw_text)
    except ValueError as exc:
        raise SystemExit("OCR 结果不是合法 JSON（%s）：%s —— 上游 vision-ocr 是不是失败了？"
                         % (path, exc))
    lines = [line for line in (normalize_line(item) for item in parsed.get("lines", []))
             if line]
    lines.sort(key=lambda item: (item["y"], item["x"]))
    return {"path": str(path), "seconds": parsed.get("seconds", 0), "lines": lines}


def batch_noise(pages, band=0.085, ratio=0.6, min_pages=2):
    """批内反复出现在页眉/页脚带的短行 → 页眉、页脚、水印。

    ⚠️ 必须保护标题：真实踩到过 —— 用同一页做多页测试时，
    「（一）刑法的空间效力」因为"每页都出现"被当成水印删掉了（丢内容）。
    所以：命中标题模式的行永不删，且要求重复率 ≥60%、长度 ≤18。
    """
    per_page = []
    for pg in pages:
        lines = pg["lines"]
        if not lines:
            continue
        hs = sorted(l["h"] for l in lines)
        med_h = hs[len(hs) // 2]
        seen = set()
        for l in lines:
            if not (l["y"] < band or l["y"] > 1 - band):
                continue
            t = l["text"].strip()
            if len(t) > 40 or heading_level(l, med_h, 1.0):
                continue
            seen.add(t)
        per_page.append(seen)
    # 同一个页眉在每页的识别结果会有细微出入（四海公考 / 四海公者 / 四海公麦），
    # 精确匹配抓不到，所以按相似度聚类
    groups = []
    for seen in per_page:
        for t in seen:
            for g in groups:
                if difflib.SequenceMatcher(None, t, g["rep"]).ratio() >= 0.72:
                    g["pages"].add(id(seen))
                    g["items"].add(t)
                    break
            else:
                groups.append({"rep": t, "pages": {id(seen)}, "items": {t}})
    page_count = max(1, len(pages))
    need = max(min_pages, safe_int(page_count * ratio, min_pages))
    out = set()
    for g in groups:
        if len(g["pages"]) >= need:
            out |= g["items"]
    return out


def is_noise(line, noise):
    t = line["text"].strip()
    if t in noise:
        return True
    if any(w in t for w in WATERMARK):
        return True
    # 页码：每页都有但各页数字不同，批内重复检测抓不到，只能按模式丢；
    # 限定在页边带里，避免误伤正文里孤零零的「一」
    if (line["y"] < 0.09 or line["y"] > 0.90) and len(t) <= 8 and PAGE_NUM.match(t):
        return True
    # 页眉里的商标英文行（DINAI OONG KAO / SIHAIGONG KAO），每页识别结果都不同，
    # 相似度聚类抓不稳 → 直接按"页边带里的纯大写拉丁行"丢
    if line["y"] < 0.09 and len(t) <= 24:
        letters = [c for c in t if c.isalpha()]
        if letters and all(c.isascii() and c.isupper() for c in letters):
            return True
    # 单页处理时批内重复检测帮不上忙，页眉里的品牌/公众号行只能按样式丢
    if line["y"] < 0.09 and ("公众号" in t or "微信" in t or t.startswith("关注")):
        return True
    if line["y"] < 0.08 and len(t) <= 8 and not PAGE_NUM.match(t):
        return True          # 页眉带里的短品牌名（如「四海公章」）
    return False


def heading_level(line, med_h, col_w):
    """返回 0（不是标题）或 1-4。模式为主，几何佐证。"""
    t = line["text"].strip()
    tall = line["h"] > med_h * 1.18
    bare = not t.endswith(("。", "！", "？", "；", "：", "，"))
    # 几何判标题必须短：长句只是"这一行偏高"，不是标题
    geom = tall and bare and len(t) <= 24
    if H_CHAPTER.match(t):
        return 1
    if H_SECTION.match(t):
        return 2
    if H_CN_NUM.match(t) and (geom or len(t) <= 24):
        return 2
    if H_PAREN_CN.match(t) and (geom or len(t) <= 30):
        return 3
    if H_NUM.match(t) and len(t) <= 30 and bare and (geom or len(t) <= 18):
        return 3
    if H_PAREN_NUM.match(t) and len(t) <= 34 and bare:
        return 4
    if geom and not BULLET.match(t):
        return 3
    return 0


def build_blocks(lines, med_h, col_w):
    """把行合并成块：标题 / 列表项 / 段落。"""
    blocks = []
    for ln in lines:
        t = ln["text"].strip()
        lvl = heading_level(ln, med_h, col_w)
        if lvl:
            blocks.append(["h", lvl, t])
            continue
        if BULLET.match(t) or H_PAREN_NUM.match(t):
            blocks.append(["li", 0, t])
            continue
        if not blocks:
            blocks.append(["p", 0, t])
            blocks[-1].append(ln)
            continue
        kind = blocks[-1][0]
        if kind == "li":
            # 列表项换行续接（中文不加空格）
            blocks[-1][2] += t
            blocks[-1].append(ln)
            continue
        if kind == "h":
            blocks.append(["p", 0, t, ln])
            continue
        # 段落续行判断
        prev_t = blocks[-1][2]
        prev_ln = blocks[-1][-1]
        gap = ln["y"] - (prev_ln["y"] + prev_ln["h"])
        med_gap = med_h * 0.45
        new_para = (
            (prev_t.endswith(tuple(TERMINAL)) and prev_ln.get("w", 0) < col_w * 0.90)
            or gap > med_gap * 1.9
            or ln["x"] - prev_ln["x"] > 0.035
        )
        if new_para:
            blocks.append(["p", 0, t, ln])
        else:
            sep = " " if (prev_t[-1:].isascii() and prev_t[-1:].isalnum()
                          and t[:1].isascii() and t[:1].isalnum()) else ""
            blocks[-1][2] = prev_t + sep + t
            blocks[-1].append(ln)
    return blocks


def page_to_md(pg, noise, source, page_no, low_conf):
    lines = [l for l in pg["lines"] if not is_noise(l, noise)]
    if not lines:
        return "", 0
    hs = sorted(l["h"] for l in lines)
    med_h = hs[len(hs) // 2]
    widths = sorted(l.get("w", 0) for l in lines)
    width_index = safe_int(len(widths) * 0.9, len(widths) - 1)
    col_w = widths[min(max(width_index, 0), len(widths) - 1)]     # 夹住下标，空列表也不会炸
    blocks = build_blocks(lines, med_h, col_w)
    body = []
    for b in blocks:
        if b[0] == "h":
            body.append("#" * b[1] + " " + b[2])
        elif b[0] == "li":
            body.append(b[2])
        else:
            body.append(b[2])
    chars = sum(len(b[2]) for b in blocks)
    low = [l for l in lines if l["conf"] < low_conf]
    uncertain = [l for l in lines if l["conf"] < 0.7]
    fm = [
        "---",
        "source: %s" % source,
        "page: %d" % page_no,
        "ocr: macos-vision",
        "ocr_lines: %d" % len(lines),
        "ocr_low_conf: %d" % len(low),
        "ocr_uncertain: %d/%d" % (len(uncertain), len(lines)),
        "characters: %d" % chars,
        "proofread: false",
        "---",
    ]
    out = "\n".join(fm) + "\n\n" + "\n\n".join(body)
    if low:
        out += "\n\n<!-- 待校对（置信度低于 %.2f）\n" % low_conf
        for l in low[:20]:
            out += "  conf %.2f  %s\n" % (l["conf"], l["text"][:70])
        out += "-->"
    return out, chars


PAGE_PREFIX = re.compile(r"^(\d{3})-")


def page_text(page):
    """把一页的行拼成用于比较的文本。"""
    return "\n".join(line["text"] for line in page["lines"])


def existing_pages(out_dir):
    """已经处理过的页（同一模块的 _raw 目录）—— 用于跨次运行去重。"""
    import json as _json
    from pathlib import Path
    found = []
    for md in sorted(Path(out_dir).glob("*.md")) if Path(out_dir).exists() else []:
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        found.append((md.name, text))
    return found


def dedup_pages(pages, out_dir, threshold):
    """批内去重 + 跨次运行去重。返回 (保留的页, [(被丢的, 原因)])。"""
    kept, dropped = [], []
    for page in pages:
        text = page_text(page)
        name = Path(page["path"]).name
        earlier = dedup.find_match(text, [(p["path"], page_text(p)) for p in kept], threshold)
        if earlier:
            dropped.append((name, "批内重复：与 %s 是同一页（相似度 %.2f）"
                            % (Path(earlier[0]).name, earlier[1])))
            continue
        before = dedup.find_match(text, existing_pages(out_dir), threshold)
        if before:
            dropped.append((name, "已处理过：与 %s 是同一页（相似度 %.2f）" % (before[0], before[1])))
            continue
        kept.append(page)
    return kept, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsons", nargs="+")
    ap.add_argument("--source", default="untitled")
    ap.add_argument("--out-dir")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--start-page", type=int, default=1)
    # Vision 的置信度是双峰分布（0.5 / 1.0），0.55 会误标一半的行；
    # 0.4 以下才是真的没底。整体"犹豫程度"放到 front-matter 的 ocr_uncertain 里做页级指标
    ap.add_argument("--low-conf", type=float, default=0.40)
    ap.add_argument("--no-dedup", action="store_true", help="关掉页面去重")
    ap.add_argument("--dedup-threshold", type=float, default=dedup.DEFAULT_THRESHOLD)
    args = ap.parse_args()

    pages = [load(p) for p in args.jsons]
    dropped = []
    if not args.no_dedup:
        pages, dropped = dedup_pages(pages, args.out_dir, args.dedup_threshold)
        for name, why in dropped:
            print("  跳过 %s —— %s" % (name, why), file=sys.stderr)
        if not pages:
            print("  全部是重复页，没有新内容需要写", file=sys.stderr)
    noise = batch_noise(pages)
    total = 0
    for i, pg in enumerate(pages):
        # 页号跟“输入里的第几张”走，而不是“成功处理的第几张”——
        # 中间有一张失败时，后者会把后面的页整体往前挪一位
        matched = PAGE_PREFIX.match(Path(pg["path"]).stem)
        page_no = (args.start_page + int(matched.group(1)) - 1) if matched else (args.start_page + i)
        md, chars = page_to_md(pg, noise, args.source, page_no, args.low_conf)
        total += chars
        if args.stdout:
            sys.stdout.write(md + "\n")
        elif args.out_dir:
            Path(args.out_dir).mkdir(parents=True, exist_ok=True)
            name = "%s-p%03d.md" % (args.source, args.start_page + i)
            (Path(args.out_dir) / name).write_text(md, encoding="utf-8")
            print("  %s  %d 字" % (name, chars), file=sys.stderr)
        else:
            sys.stdout.write(md + "\n")
    if noise:
        print("  批内识别为页眉/页脚/水印: %s" % ", ".join(sorted(noise)[:6]), file=sys.stderr)
    print("  合计 %d 字 / %d 页" % (total, len(pages)), file=sys.stderr)


if __name__ == "__main__":
    main()
