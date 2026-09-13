#!/usr/bin/env python3
"""错题本 CLI：录入 / 分流 / 关联 / 掌握验证。

逻辑同样拆开了：
    mastery.py  掌握三关状态机（纯函数）
    cards.py    卡片查找与写回
    paths.py    路径    errors.py  领域异常    vault.py  front-matter

设计依据见 PLAN.md 第 3 节：
  · Metcalfe 2017：从错误学习需要「察觉到错」+「意外感」+ 注意力被调动
    → 所以错题笔记强制有「先自己重做」与「我当时怎么想的」两段
  · Butterfield & Metcalfe 2001 / Butler et al. 2011：高信心错误最好纠正，但一周后会复发
    → 所以必填「答题信心」
  · Holmes et al. 2013：概念性错误要「概念转变」，不是重做
    → 所以错因分流决定走哪个流程
"""
import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.cards import all_cards, find  # type: ignore[import]  # noqa: E402
from pipeline.errors import (  # type: ignore[import]  # noqa: E402
    AmbiguousCard, CardNotFound, InvalidState, KaogongError)
from pipeline.mastery import STEPS, advance, from_card, normalize, resolve, to_card  # type: ignore[import]  # noqa: E402
from pipeline.paths import MISTAKE_DIR, VAULT_ROOT  # type: ignore[import]  # noqa: E402
from pipeline.vault import read, safe_float, write, yaml_value  # type: ignore[import]  # noqa: E402

CAUSES = {
    "概念性": "概念转变：反例打脸 → 重构规则 → 举正例 → 变式验证",
    "程序性": "进题型交错队列（判别对比），别按题型块刷",
    "粗心": "不进复习队列，进「限时正确率」指标单独盯",
    "超纲": "归档，低优先",
}
CONFIDENCE = ("高", "中", "低")

BODY = """## 题干

{stem}

## ① 先自己重做（不看答案）

<!-- 跳过这一步，后面的学习基本不会发生（Metcalfe 2017） -->

## ② 我当时为什么会这么想

<!-- 这一步产生「意外感」，是纠错的关键（Metcalfe 条件二） -->

## ③ 正确解法

## ④ 错因归类 + 下次怎么防

**{cause}** —— {advice}

## ⑤ 关联

- 所属小节：{lecture}
- 同知识点错题：
- 变式训练：
"""


def cmd_new(args):
    if args.confidence not in CONFIDENCE:
        raise KaogongError("答题信心只能是 高/中/低，收到 %r" % args.confidence)
    if args.cause not in CAUSES:
        raise KaogongError("错因只能是 %s，收到 %r" % ("/".join(CAUSES), args.cause))
    slug = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in args.stem)
    slug = slug[:28].strip("-") or "错题"
    MISTAKE_DIR.mkdir(parents=True, exist_ok=True)
    target = MISTAKE_DIR / ("%s-%s-%s.md" % (date.today(), args.module.replace("/", "-"), slug))
    counter = 2
    base_target = target
    while target.exists() and counter <= 20:
        target = base_target.with_name("%s-%d%s" % (base_target.stem, counter, base_target.suffix))
        counter += 1
    if target.exists():
        raise KaogongError("同名错题太多了（%s…），换个更具体的题干摘要" % base_target.name)
    points = [p.strip() for p in (args.points or "").split(",") if p.strip()]
    front_matter = {
        "type": "错题",
        "科目": args.subject,
        "模块": yaml_value(args.module),
        "知识点": "[%s]" % ", ".join('"%s"' % p for p in points) if points else "[]",
        "来源": yaml_value(args.source or ""),
        "日期": str(date.today()),
        "我的答案": args.mine or "",
        "正确答案": args.correct or "",
        "答题信心": args.confidence,
        "错因": args.cause,
        "状态": "未掌握",      # 写合法值：以前写「待巩固」，靠 normalize 的兜底才映射过来
    }
    # 用 replace 而不是 str.format：题干里出现 {x|x>0} 这类花括号时 format 会抛 KeyError
    body = BODY
    for token, value in (("{stem}", args.stem), ("{cause}", args.cause),
                         ("{advice}", CAUSES[args.cause]),
                         ("{lecture}", ("[[%s]]" % args.lecture) if args.lecture else "")):
        body = body.replace(token, value)
    # 走 vault.write：原子写 + 写前快照，不要裸 write_text
    write(target, front_matter, body, "")
    print("  新建 %s" % target.relative_to(VAULT_ROOT))
    print("  分流：%s → %s" % (args.cause, CAUSES[args.cause]))
    if args.confidence == "高" and args.cause == "概念性":
        print("  ⚠️ 高信心 + 概念性错误 = 最高优先级"
              "（纠正效果最好，但一周后会复发 → 间隔要更短、多轮复测）")


def cmd_verify(args):
    card = find(args.card)
    stability = safe_float(card["fm"].get("稳定度"), 12.0)
    before = normalize(card["fm"].get("状态"))
    try:
        after = advance(from_card(card["fm"]), args.kind,
                                args.result == "对", stability=stability)
    except ValueError as exc:
        raise InvalidState(str(exc))
    write(card["path"], to_card(after), card["body"], card["raw"])
    arrow = "→" if after["状态"] != before else "（不变）"
    print("  %s  %s %s %s  %s" % (card["path"].stem, args.kind, args.result, arrow, after["状态"]))
    if after["状态"] == "待保持" and after["保持测试"]:
        print("    保持测试安排在 %s（按稳定度 %.0f 天）" % (after["保持测试"], stability))


def cmd_queue(_args):
    pending = []
    for card in all_cards():
        stage = normalize(card["fm"].get("状态"))
        if stage in ("变式中", "迁移中", "待保持"):
            pending.append((stage, card))
    if not pending:
        print("  没有待验证的考点（都在未掌握或已掌握）")
        return
    today = date.today()
    for stage, card in pending:
        extra = ""
        keep = card["fm"].get("保持测试") or ""
        if stage == "待保持" and keep:
            try:
                days = (date.fromisoformat(keep) - today).days
                extra = "  保持测试 %s（%s）" % (keep, "%d 天后" % days if days > 0 else "已到期")
            except (ValueError, TypeError):
                extra = "  保持测试日期无法解析：%r（手改过？）" % keep
        print("  [%s] %-22s %s%s" % (stage, card["path"].stem, card["fm"].get("模块", ""), extra))


def _read_card(name):
    """按文件名（可省 .md）找一张错题，找不到就抛出可读的错。"""
    stem = name[:-3] if name.endswith(".md") else name
    path = MISTAKE_DIR / (stem + ".md")
    if not path.exists():
        matches = [p for p in MISTAKE_DIR.glob("*.md") if stem in p.stem]
        if len(matches) == 1:
            path = matches[0]
        elif not matches:
            raise KaogongError("找不到错题 %r（在 %s 下）" % (name, MISTAKE_DIR))
        else:
            raise KaogongError("%r 匹配到 %d 道，写全一点：%s"
                               % (name, len(matches), "、".join(p.stem for p in matches[:3])))
    fm, body, _ = read(path)
    return path, fm, body


def _stem_of(body):
    """取出「## 题干」和「## ①」之间的内容。"""
    match = re.search(r"##[ \t]*题干[ \t]*\n(.*?)(?=\n##[ \t]|\Z)", body, re.S)
    return (match.group(1).strip() if match else body.strip())[:600]


def cmd_variant(args):
    """给「变式」关备一份出题简报。

    生成由 agent 做，CLI 只把料备齐。为什么要生成而不是复用原题：
    变式关考的是「换个情境还认不认得」，原题做对可能只是记住了答案。
    """
    path, fm, body = _read_card(args.card)
    points = fm.get("知识点") or []
    if isinstance(points, str):
        points = [p.strip().strip(chr(34)) for p in points.strip("[]").split(",") if p.strip()]
    print("  -- 出题简报：%s --" % path.stem)
    print("  科目/模块   %s / %s" % (fm.get("科目", ""), fm.get("模块", "")))
    print("  关联考点    %s" % ("、".join(str(p) for p in points) if points else "（没填，靠题干自判）"))
    print("  原错因      %s  <- 新题要专门戳这个点" % fm.get("错因", ""))
    print("  我当时选 %s   正确答案 %s" % (fm.get("我的答案", "") or "?",
                                            fm.get("正确答案", "") or "?"))
    print()
    print("  原题题干：")
    for line in _stem_of(body).splitlines()[:12]:
        print("    " + line[:100])
    print()
    print("  -- 要求 --")
    print("    1. 同一考点，换情境/换材料，别改数字复用原题")
    print("    2. 干扰项要像样：错误选项得对应真会犯的错，别一眼假")
    print("    3. 只出一道")
    print("    4. 出完先别给答案，等作答")
    print()
    print("  用户答完后：")
    print("    python3 ~/code/kaogong/pipeline/mistake.py verify %s --kind 变式 --result 对" % path.stem)


def cmd_export(args):
    """导出可打印的重做卷：题目在前、答案与解析附末尾。

    那些在线错题本的「导出打印」，本地版就这么简单：一份 markdown，
    用现成的 pdf 流程转一下就能打印。
    """
    stage_filter = resolve(args.stage) if args.stage else ""
    rows = []
    for path in (sorted(MISTAKE_DIR.glob("*.md")) if MISTAKE_DIR.exists() else []):
        fm, body, _ = read(path)
        if stage_filter and normalize(fm.get("状态")) != stage_filter:
            continue
        rows.append((str(fm.get("日期", "")), path.stem, fm, body))
    rows.sort(reverse=True)
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("  没有符合条件的错题可导出")
        return
    lines = ["# 错题重做 · %s" % date.today(), "",
             "> 共 %d 道。先自己做，答案与解析在最后。" % len(rows), "", "---", ""]
    answers = ["---", "", "## 答案与解析", ""]
    for i, (when, name, fm, body) in enumerate(rows, 1):
        raw_points = fm.get("知识点") or "[]"
        if isinstance(raw_points, list):
            raw_points = "、".join(str(p) for p in raw_points)
        clean = re.sub(r"[*_`>#\[\]]", "", str(raw_points)).replace(chr(34), "")
        lines += ["### %d. %s | %s" % (i, fm.get("模块", ""), fm.get("来源", "") or when), "",
                  _stem_of(body), "",
                  "我的答案 ______   信心 %s" % fm.get("答题信心", ""), "",
                  "<br><br><br>", ""]
        answers += ["**%d. 正确答案 %s**（我当时 %s）" % (
            i, fm.get("正确答案", "") or "?", fm.get("我的答案", "") or "未记"), "",
            "错因：%s | 关联：%s" % (fm.get("错因", ""), clean or "未填"), ""]
    text = "\n".join(lines + answers)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)   # 路径写错时给个干净的错，别裸崩
        target.write_text(text, encoding="utf-8")
        print("  已写出 %s（%d 道）" % (args.out, len(rows)))
    else:
        print(text)



def cmd_list(args):
    """按状态 / 来源 / 时间筛错题。

    等价于 wrong-notebook 那几个筛选视图，但不用装 Dataview —— 零依赖，
    手机上从 Telegram 也能跑。三个条件可以叠加。
    """
    from datetime import date as _date
    # 校验必须在循环**外**做：「库是空的」时循环体一次都不执行，
    # 非法状态永远碰不到 resolve()，用户看到的是「0 道」而不是报错（真实踩到过）
    stage_filter = resolve(args.stage) if args.stage else ""
    rows = []
    skipped = 0
    for path in (sorted(MISTAKE_DIR.glob("*.md")) if MISTAKE_DIR.exists() else []):
        fm = read(path)[0]          # read() 返回 (front_matter, body, raw)
        stage = normalize(fm.get("状态"))
        if stage_filter and stage != stage_filter:
            continue
        if args.source and args.source not in str(fm.get("来源", "")):
            continue
        if args.days:
            try:
                when = _date.fromisoformat(str(fm.get("日期", "")))
            except (ValueError, TypeError):
                skipped += 1          # 没日期或日期被手改坏：不猜，单独报数
                continue
            if (_date.today() - when).days > args.days:
                continue
        rows.append((str(fm.get("日期", "")), stage, path.stem, fm))
    if args.cause:
        rows = [r for r in rows if str(r[3].get("错因", "")) == args.cause]

    rows.sort(reverse=True)
    conds = []
    if args.stage:
        conds.append("状态=%s" % args.stage)
    if args.source:
        conds.append("来源含「%s」" % args.source)
    if args.days:
        conds.append("最近 %d 天" % args.days)
    if args.cause:
        conds.append("错因=%s" % args.cause)
    print("  %s  →  %d 道" % ("，".join(conds) if conds else "全部错题", len(rows)))
    if skipped:
        print("    （%d 道因日期缺失/无效被跳过，可能被手改过）" % skipped)
    if not rows:
        return
    print()
    print("  日期        状态      科目/模块        信心  错因      题目")
    for when, stage, name, fm in rows:
        print("  %-10s  %-6s  %-14s  %-3s  %-6s  %s" % (
            when, stage,
            str(fm.get("模块", ""))[:14], str(fm.get("答题信心", ""))[:3],
            str(fm.get("错因", ""))[:6], name[:34]))


def cmd_link(args):
    """关联检索：vault 已建索引（qmd 的 notes collection）。"""
    print("  qmd 检索：%s（collection=%s）" % (args.query, args.collection))
    try:
        result = subprocess.run(["qmd", "search", args.query, "-c", args.collection],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise KaogongError("qmd 调用失败：%s" % exc)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        print("    qmd 退出码 %s%s" % (result.returncode, ("：" + detail[0]) if detail else ""))
    text = (result.stdout or "").strip()
    if not text:
        print("    没搜到 —— 新写的笔记可能还没进索引，跑一下 qmd update")
        return
    for line in text.splitlines()[:12]:
        print("    %s" % line[:110])


def cmd_stats(_args):
    stage_count = Counter(normalize(card["fm"].get("状态")) for card in all_cards())
    mistakes = sorted(MISTAKE_DIR.glob("*.md")) if MISTAKE_DIR.exists() else []
    cause_count = Counter(read(p)[0].get("错因", "未填") for p in mistakes)
    summary = "  ".join("%s %d" % (step, stage_count[step]) for step in STEPS if stage_count[step])
    causes = "  ".join("%s %d" % kv for kv in cause_count.items()) or "—"
    print("  考点 %d 张：%s" % (sum(stage_count.values()), summary))
    print("  错题 %d 道：%s" % (len(mistakes), causes))


def cmd_selftest(_args):
    """状态机自检（逻辑本身在 mastery.py，这里验它真按规矩走）。"""
    today = date(2026, 9, 13)
    for kind in ("迁移", "保持"):
        try:
            advance({"状态": "未掌握"}, kind, True, today)
        except ValueError:
            continue
        raise AssertionError("未掌握竟然能直接过 %s 关" % kind)
    state = advance({"状态": "未掌握"}, "变式", True, today)
    assert state["状态"] == "变式中" and state["变式连胜"] == 1, state
    try:
        advance(state, "迁移", True, today)
    except ValueError:
        pass
    else:
        raise AssertionError("变式只赢 1 次不该能考迁移关")
    state = advance(state, "变式", True, today)
    assert state["状态"] == "迁移中", state
    state = advance(state, "迁移", True, today, stability=20)
    assert state["状态"] == "待保持", state
    failed = advance(state, "保持", False, today)
    assert failed["状态"] == "迁移中" and failed["迁移通过"] == "false", failed
    done = advance(state, "保持", True, today)
    assert done["状态"] == "已掌握" and done["保持测试"] == "", done
    assert advance(done, "变式", False, today)["状态"] == "变式中", "已掌握之后又错要退回去"
    assert normalize("乱填的") == "未掌握", "非法状态要归一"
    print("  自检通过：变式×2 → 迁移 → 待保持 → 保持 → 已掌握；任一步失败都退一级")


def main():
    parser = argparse.ArgumentParser(description="错题本：录入 / 分流 / 关联 / 掌握验证")
    sub = parser.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new")
    new.add_argument("--subject", default="行测")
    new.add_argument("--module", required=True)
    new.add_argument("--stem", required=True, help="题干摘要，用作文件名")
    new.add_argument("--mine", default="")
    new.add_argument("--correct", default="")
    new.add_argument("--confidence", default="中", help="高/中/低")
    new.add_argument("--cause", default="概念性", help="/".join(CAUSES))
    new.add_argument("--points", default="", help="关联考点，逗号分隔")
    new.add_argument("--lecture", default="", help="所属小节笔记名")
    new.add_argument("--source", default="")
    new.set_defaults(fn=cmd_new)
    verify = sub.add_parser("verify")
    verify.add_argument("card")
    verify.add_argument("--kind", required=True, choices=["变式", "迁移", "保持"])
    verify.add_argument("--result", required=True, choices=["对", "错"])
    verify.set_defaults(fn=cmd_verify)
    sub.add_parser("queue").set_defaults(fn=cmd_queue)
    link = sub.add_parser("link")
    link.add_argument("query")
    link.add_argument("--collection", default="notes")
    link.set_defaults(fn=cmd_link)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    listing = sub.add_parser("list", help="按状态/来源/时间筛错题")
    listing.add_argument("--stage", default="", help="待巩固/变式中/迁移中/待保持/已保持")
    listing.add_argument("--source", default="", help="来源包含这段文字")
    listing.add_argument("--days", type=int, default=0, help="只看最近 N 天")
    listing.add_argument("--cause", default="", help="/".join(CAUSES))
    listing.set_defaults(fn=cmd_list)
    variant = sub.add_parser("variant", help="给变式关备一份出题简报")
    variant.add_argument("card")
    variant.set_defaults(fn=cmd_variant)
    export = sub.add_parser("export", help="导出可打印的重做卷，答案附末尾")
    export.add_argument("--stage", default="", help="只导某个状态")
    export.add_argument("--limit", type=int, default=0, help="最多几道")
    export.add_argument("--out", default="", help="写到文件；不给就打到屏幕")
    export.set_defaults(fn=cmd_export)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    args = parser.parse_args()
    try:
        args.fn(args)
    except KaogongError as exc:
        print("  ✗ %s" % exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
