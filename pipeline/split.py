#!/usr/bin/env python3
"""校准后的讲义 md → 骨架层（小节长文）+ 卡片层（考点）。

折中两层（2026-09-13 定）：
  骨架  <vault>/考公/<科目>/<模块>/<讲义>.md     按页码拼接，保留全部内容，查阅用
  卡片  <vault>/考公/考点/<考点名>.md            一条一个考点，记忆用

卡片怎么抽：靠行首标记（①②③ / （1） / 1.）切成条目，再用第一个冒号把
“术语”和“解释”分开 —— 术语当卡面提示，解释当卡背。机械切法约 70% 能直接用，
剩下的必须人工/模型过一遍（这是设计的一部分，不是缺陷）。
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path.home() / "Documents" / "Obsidian Vault" / "考公"

ITEM = re.compile(r"^([①-⑳]|\d+[.、]|[（(]\d+[）)]|\(\d+\))\s*")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
TERM = re.compile(r"^\s*[①-⑳\d（()）.、]*\s*([^：:，。]{2,24})[：:]\s*(.*)$")


def read_front_matter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, text[end + 4:].lstrip("\n")


def parse_pages(paths):
    pages = []
    for p in paths:
        # 路径来自命令行参数（使用者自己的文件），不是不可信输入
        try:
            text = Path(p).read_text(encoding="utf-8")
        except OSError as exc:
            print("  跳过 %s（读不到：%s）" % (p, exc), file=sys.stderr)
            continue
        fm, body = read_front_matter(text)
        try:
            page = int(fm.get("page", 0) or 0)
        except ValueError:
            page = 0
        pages.append({"file": Path(p).name, "page": page, "body": body, "fm": fm})
    pages.sort(key=lambda x: x["page"])
    return pages


def collect_items(pages):
    """返回 [(heading_path, marker, term, body, page)]"""
    items = []
    for pg in pages:
        path = []
        for raw in pg["body"].splitlines():
            line = raw.strip()
            if not line:
                continue
            m = HEADING.match(line)
            if m:
                lvl, title = len(m.group(1)), m.group(2).strip()
                path = path[: lvl - 1] + [title]
                continue
            if not ITEM.match(line):
                continue
            mm = TERM.match(line)
            if not mm:
                continue
            term, rest = mm.group(1).strip(), mm.group(2).strip()
            if len(term) < 2 or len(rest) < 6:
                continue
            items.append((list(path), line, term, rest, pg["page"]))
    return items


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|#\[\]]', "", s).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="+")
    ap.add_argument("--module", required=True, help="如 行测/判断推理")
    ap.add_argument("--lecture", required=True, help="讲义名，如 逻辑论证-归因论证")
    ap.add_argument("--source", default="")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    subject, _, mod = args.module.partition("/")
    pages = parse_pages(args.pages)
    items = collect_items(pages)

    # ---- 骨架层 ----
    skel = [
        "---",
        "type: 骨架",
        "科目: %s" % subject,
        "模块: %s" % mod,
        "讲义: %s" % args.lecture,
        "页码: %s" % ("%d-%d" % (pages[0]["page"], pages[-1]["page"]) if pages else "-"),
        "tags: [考公/%s, %s]" % (subject, args.lecture),
        "---",
        "",
        "# %s" % args.lecture,
        "",
    ]
    for pg in pages:
        skel.append("<!-- p%d  %s -->" % (pg["page"], pg["file"]))
        skel.append("")
        skel.append(pg["body"].rstrip())
        skel.append("")
    skel_path = ROOT / subject / mod / (safe_name(args.lecture) + ".md")

    # ---- 卡片层 ----
    cards = []
    seen = {}
    for path, line, term, rest, page in items:
        group = path[-1] if path else ""
        base = safe_name(term)
        if base in seen:
            seen[base] += 1
            base = "%s-%d" % (base, seen[base])
        else:
            seen[base] = 1
        # 分组标题里可能已经有括号（如「三种削弱（质疑）方式」），再套一层括号会变成
        # 「另有他因（三种削弱（质疑）方式）是什么？」—— 难读，所以把内层括号换成「」
        # 只换「内层」括号：行首的（二）（三）是标题编号，不能动
        g = re.sub(r"(?<!^)[（(]([^（()）]{1,12})[）)]", r"「\1」", group)
        prompt = "%s 是什么？%s" % (term, ("\n\n> 分组：%s" % g) if g else "")
        card = [
            "---",
            "type: 考点",
            "科目: %s" % subject,
            "模块: %s" % mod,
            "所属小节: \"[[%s]]\"" % safe_name(args.lecture),
            "分组: %s" % group,
            "来源: %s p%d" % (args.source or args.lecture, page),
            "tags: [考点, %s/%s]" % (subject, mod),
            "状态: 未掌握",
            "---",
            "",
            "# %s" % term,
            "",
            "## 卡面",
            "",
            prompt,
            "",
            "## 卡背",
            "",
            rest,
            "",
            "## 原文",
            "",
            "> %s" % line,
            "",
        ]
        cards.append((base + ".md", "\n".join(card)))

    if args.dry:
        print("  骨架 → %s（%d 页）" % (skel_path, len(pages)))
        print("  卡片 → %d 张" % len(cards))
        for name, _ in cards[:12]:
            print("    %s" % name)
        return

    skel_path.parent.mkdir(parents=True, exist_ok=True)
    skel_path.write_text("\n".join(skel), encoding="utf-8")
    card_dir = ROOT / "考点"
    card_dir.mkdir(parents=True, exist_ok=True)
    for name, text in cards:
        (card_dir / name).write_text(text, encoding="utf-8")
    print("  骨架 %s  （%d 页）" % (skel_path.relative_to(ROOT), len(pages)))
    print("  卡片 %d 张 → %s" % (len(cards), card_dir.relative_to(ROOT)))


if __name__ == "__main__":
    main()
