#!/usr/bin/env python3
"""错题本：录入 → 错因分流 → 关联 → 掌握验证（变式/迁移/保持三关）。

设计依据（见 PLAN.md 第 3 节）：
  · Metcalfe 2017：从错误学习需要「察觉到自己错了」+「意外感」+ 注意力被调动
    → 所以错题笔记强制有「先自己重做」和「我当时怎么想的」两段
  · Butterfield & Metcalfe 2001 / Butler et al. 2011：高信心错误最容易被纠正，但一周后会复发
    → 所以必填「答题信心」，高信心错误间隔更短
  · Holmes et al. 2013：概念性错误要「概念转变」，不是重做
    → 所以错因分流决定走哪个流程

掌握三关（卡片的 状态 字段）：
  未掌握 → 变式中 → 迁移中 → 待保持 → 已掌握
  任何一关失败 → 退一级（不清零，FSRS 那边会自己缩短间隔）
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import vault  # type: ignore[import]  # noqa: E402
# front-matter 读写统一走 vault：之前这份自带一份实现，会把文件里有、update 里没有的键删掉

VAULT = Path.home() / "Documents" / "Obsidian Vault" / "考公"
CARD_DIR = VAULT / "考点"
ERR_DIR = VAULT / "错题"

CAUSES = {
    "概念性": "概念转变：反例打脸 → 重构规则 → 举正例 → 变式验证",
    "程序性": "进题型交错队列（判别对比），别按题型块刷",
    "粗心":   "不进复习队列，进「限时正确率」指标单独盯",
    "超纲":   "归档，低优先",
}
CONFIDENCE = {"高", "中", "低"}
MASTER_STEPS = ["未掌握", "变式中", "迁移中", "待保持", "已掌握"]


read_card = vault.read


def write_card(path, fm, body, raw_fm):
    return vault.write(path, fm, body, raw_fm)


def safe_int(v, default=0):
    """卡片字段可能被手改坏，解析失败就退回默认值，别让整条流程挂掉。"""
    return vault.safe_int(v, default)


def safe_float(v, default=12.0):
    f = vault.safe_float(v, default)
    return f if -1e9 < f < 1e9 else default


def norm_state(v):
    v = (v or "").strip()
    return v if v in MASTER_STEPS else "未掌握"


def advance(state, kind, ok, today=None, stability=12.0):
    """掌握状态机。state 为 dict：{状态, 变式连胜, 迁移通过, 保持测试}"""
    today = today or date.today()
    st = dict(state)
    st["变式连胜"] = safe_int(st.get("变式连胜"), 0)
    st["迁移通过"] = str(st.get("迁移通过", "false")).lower() == "true"
    if kind == "变式":
        if ok:
            st["变式连胜"] += 1
            if st["变式连胜"] >= 2:
                st["状态"] = "迁移中"
                st["变式连胜"] = 0
            else:
                st["状态"] = "变式中"
        else:
            st["变式连胜"] = 0
            st["状态"] = "变式中"
    elif kind == "迁移":
        if ok:
            st["迁移通过"] = True
            st["状态"] = "待保持"
            days = max(1, safe_int(round(safe_float(stability)), 12))
            st["保持测试"] = str(today + timedelta(days=days))
        else:
            st["迁移通过"] = False
            st["状态"] = "变式中"
            st["变式连胜"] = 0
            st["保持测试"] = ""
    elif kind == "保持":
        if ok:
            st["状态"] = "已掌握"
            st["保持测试"] = ""
        else:
            st["状态"] = "迁移中"
            st["迁移通过"] = False
            st["保持测试"] = ""
    else:
        raise ValueError("kind 只能是 变式/迁移/保持，收到 %r" % kind)
    st["迁移通过"] = "true" if st["迁移通过"] else "false"
    return st


def find_card(name):
    hit = [p for p in CARD_DIR.glob("*.md") if p.stem == name]
    if not hit:
        hit = [p for p in CARD_DIR.glob("*.md") if name in p.stem]
    return hit[0] if hit else None


def cmd_new(args):
    if args.confidence not in CONFIDENCE:
        raise SystemExit("答题信心只能是 高/中/低，收到 %r" % args.confidence)
    if args.cause not in CAUSES:
        raise SystemExit("错因只能是 %s，收到 %r" % ("/".join(CAUSES), args.cause))
    ERR_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", args.stem)[:28].strip("-")
    name = "%s-%s-%s.md" % (date.today(), args.module.replace("/", "-"), slug or "错题")
    path = ERR_DIR / name
    if path.exists():
        raise SystemExit("已存在：%s" % path)
    points = ["[[%s]]" % p.strip() for p in (args.points or "").split(",") if p.strip()]
    fm = {
        "type": "错题", "科目": args.subject, "模块": args.module,
        "知识点": "[%s]" % ", ".join('"%s"' % p for p in points) if points else "[]",
        "来源": args.source or "", "日期": str(date.today()),
        "我的答案": args.mine or "", "正确答案": args.correct or "",
        "答题信心": args.confidence, "错因": args.cause,
        "状态": "待巩固",
    }
    body = """## 题干

%s

## ① 先自己重做（不看答案）

<!-- 跳过这一步，后面的学习基本不会发生（Metcalfe 2017） -->

## ② 我当时为什么会这么想

<!-- 这一步产生「意外感」，是纠错的关键（Metcalfe 条件二） -->

## ③ 正确解法

## ④ 错因归类 + 下次怎么防

**%s** —— %s

## ⑤ 关联

- 所属小节：%s
- 同知识点错题：
- 变式训练：
""" % (args.stem, args.cause, CAUSES[args.cause],
       ("[[%s]]" % args.lecture) if args.lecture else "")
    path.write_text("---\n" + "\n".join("%s: %s" % (k, v) for k, v in fm.items())
                    + "\n---\n\n" + body, encoding="utf-8")
    print("  新建 %s" % path.relative_to(VAULT))
    print("  分流：%s → %s" % (args.cause, CAUSES[args.cause]))
    if args.confidence == "高" and args.cause == "概念性":
        print("  ⚠️ 高信心 + 概念性错误 = 最高优先级（纠正效果最好，但一周后会复发 → 间隔要更短、多轮复测）")


def cmd_verify(args):
    p = find_card(args.card)
    if not p:
        raise SystemExit("没找到考点卡：%s" % args.card)
    fm, body, raw = read_card(p)
    stability = safe_float(fm.get("稳定度"), 12.0)
    before = norm_state(fm.get("状态"))
    st = advance({"状态": fm.get("状态"), "变式连胜": fm.get("变式连胜"),
                  "迁移通过": fm.get("迁移通过"), "保持测试": fm.get("保持测试")},
                 args.kind, args.result == "对", stability=stability)
    fm.update({"状态": st["状态"], "变式连胜": st["变式连胜"],
               "迁移通过": st["迁移通过"], "保持测试": st["保持测试"]})
    write_card(p, fm, body, raw)
    arrow = "→" if st["状态"] != before else "（不变）"
    print("  %s  %s %s %s  %s" % (p.stem, args.kind, args.result, arrow, st["状态"]))
    if st["状态"] == "待保持" and st["保持测试"]:
        print("    保持测试安排在 %s（按稳定度 %.0f 天）" % (st["保持测试"], stability))


def cmd_queue(args):
    rows = []
    for p in sorted(CARD_DIR.glob("*.md")):
        fm, _, _ = read_card(p)
        s = norm_state(fm.get("状态"))
        if s in ("变式中", "迁移中", "待保持"):
            rows.append((s, p.stem, fm.get("保持测试") or "", fm.get("模块") or ""))
    if not rows:
        print("  没有待验证的考点（都在未掌握或已掌握）")
        return
    today = date.today()
    for s, name, keep, mod in rows:
        extra = ""
        if s == "待保持" and keep:
            d = (date.fromisoformat(keep) - today).days
            extra = "  保持测试 %s（%s）" % (keep, "%d 天后" % d if d > 0 else "已到期")
        print("  [%s] %-22s %s%s" % (s, name, mod, extra))


def cmd_link(args):
    """关联：qmd 全文检索（vault = notes collection）。

    两个坑（都踩过）：
      · 必须带 -c notes —— 不带就是搜 pi-memory，永远搜不到笔记
      · 新建笔记后要先 qmd update，否则索引里没有它们
    """
    q = args.query
    print("  qmd 检索：%s（collection=%s）" % (q, args.collection))
    try:
        out = subprocess.run(["qmd", "search", q, "-c", args.collection],
                             capture_output=True, text=True, timeout=60)
        text = (out.stdout or "").strip()
        if text:
            for line in text.splitlines()[:12]:
                print("    %s" % line[:110])
        else:
            print("    （qmd 没返回结果）")
        if "No results" in text:
            print("    没搜到 —— 新写的笔记可能还没进索引，跑一下 qmd update")
    except (OSError, subprocess.SubprocessError) as exc:
        print("    qmd 调用失败：%s" % exc)


def cmd_stats(args):
    cards = [read_card(p)[0] for p in sorted(CARD_DIR.glob("*.md"))]
    from collections import Counter
    c = Counter(norm_state(fm.get("状态")) for fm in cards)
    errs = sorted(ERR_DIR.glob("*.md")) if ERR_DIR.exists() else []
    causes = Counter(read_card(p)[0].get("错因", "未填") for p in errs)
    print("  考点 %d 张：%s" % (len(cards), "  ".join("%s %d" % (k, c[k]) for k in MASTER_STEPS if c[k])))
    print("  错题 %d 道：%s" % (len(errs), "  ".join("%s %d" % (k, v) for k, v in causes.items()) or "—"))


def cmd_selftest(args):
    today = date(2026, 9, 13)
    s = {"状态": "未掌握"}
    s = advance(s, "变式", True, today)
    assert s["状态"] == "变式中" and s["变式连胜"] == 1, s
    s = advance(s, "变式", True, today)
    assert s["状态"] == "迁移中", "连续 2 次变式正确必须进迁移关：%s" % s
    s = advance(s, "变式", False, today)
    assert s["状态"] == "变式中" and s["变式连胜"] == 0, "变式失败要清零并退一级：%s" % s
    s = advance(s, "变式", True, today); s = advance(s, "变式", True, today)
    s = advance(s, "迁移", True, today, stability=20)
    assert s["状态"] == "待保持" and s["保持测试"] == str(today + timedelta(days=20)), s
    s2 = advance(s, "保持", False, today)
    assert s2["状态"] == "迁移中" and s2["迁移通过"] == "false", "保持失败要退回迁移关：%s" % s2
    s3 = advance(s, "保持", True, today)
    assert s3["状态"] == "已掌握" and s3["保持测试"] == "", "保持通过才算掌握：%s" % s3
    s4 = advance(s3, "变式", False, today)
    assert s4["状态"] == "变式中", "已掌握之后又错，要退回去重练：%s" % s4
    assert norm_state("乱填的") == "未掌握", "非法状态要归一成未掌握"
    print("  自检通过：变式×2 → 迁移 → 待保持 → 保持 → 已掌握；任一步失败都退一级")


def main():
    ap = argparse.ArgumentParser(description="错题本：录入 / 分流 / 关联 / 掌握验证")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("--subject", default="行测"); p.add_argument("--module", required=True)
    p.add_argument("--stem", required=True, help="题干摘要，用作文件名")
    p.add_argument("--mine", default=""); p.add_argument("--correct", default="")
    p.add_argument("--confidence", default="中", help="高/中/低")
    p.add_argument("--cause", default="概念性", help="/".join(CAUSES))
    p.add_argument("--points", default="", help="关联考点，逗号分隔")
    p.add_argument("--lecture", default="", help="所属小节笔记名")
    p.add_argument("--source", default="")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("verify"); p.add_argument("card")
    p.add_argument("--kind", required=True, choices=["变式", "迁移", "保持"])
    p.add_argument("--result", required=True, choices=["对", "错"]); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("queue"); p.set_defaults(fn=cmd_queue)
    p = sub.add_parser("link"); p.add_argument("query")
    p.add_argument("--collection", default="notes"); p.set_defaults(fn=cmd_link)
    p = sub.add_parser("stats"); p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("selftest"); p.set_defaults(fn=cmd_selftest)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
