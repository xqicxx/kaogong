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
import subprocess
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.cards import all_cards, find  # type: ignore[import]  # noqa: E402
from pipeline.errors import (  # type: ignore[import]  # noqa: E402
    AmbiguousCard, CardNotFound, InvalidState, KaogongError)
from pipeline.mastery import STEPS, advance, from_card, normalize, to_card  # type: ignore[import]  # noqa: E402
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


def find_mistake(name):
    """按名字找错题；模糊命中多张就报错，绝不自己挑一张。"""
    if not MISTAKE_DIR.exists():
        raise CardNotFound("错题目录还不存在：%s" % MISTAKE_DIR)
    exact = sorted(MISTAKE_DIR.glob("%s.md" % name))
    if len(exact) == 1:
        return exact[0]
    fuzzy = sorted(MISTAKE_DIR.glob("%s*.md" % name))
    if not fuzzy:
        raise CardNotFound("没找到错题：%s" % name)
    if len(fuzzy) > 1:
        raise AmbiguousCard("「%s」匹配到 %d 道错题：%s"
                                   % (name, len(fuzzy), "、".join(p.stem for p in fuzzy[:5])))
    return fuzzy[0]


def cmd_new(args):
    if args.confidence not in CONFIDENCE:
        raise KaogongError("答题信心只能是 高/中/低，收到 %r" % args.confidence)
    if args.cause not in CAUSES:
        raise KaogongError("错因只能是 %s，收到 %r" % ("/".join(CAUSES), args.cause))
    slug = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in args.stem)
    slug = slug[:28].strip("-") or "错题"
    MISTAKE_DIR.mkdir(parents=True, exist_ok=True)
    target = MISTAKE_DIR / ("%s-%s-%s.md" % (date.today(), args.module.replace("/", "-"), slug))
    if target.exists():
        raise KaogongError("已存在：%s" % target)
    points = [p.strip() for p in (args.points or "").split(",") if p.strip()]
    front_matter = {
        "type": "错题",
        "科目": args.subject,
        "模块": args.module,
        "知识点": "[%s]" % ", ".join('"%s"' % p for p in points) if points else "[]",
        "来源": yaml_value(args.source or ""),
        "日期": str(date.today()),
        "我的答案": args.mine or "",
        "正确答案": args.correct or "",
        "答题信心": args.confidence,
        "错因": args.cause,
        "状态": "待巩固",
    }
    body = BODY.format(stem=args.stem, cause=args.cause, advice=CAUSES[args.cause],
                       lecture=("[[%s]]" % args.lecture) if args.lecture else "")
    target.write_text("---\n" + "\n".join("%s: %s" % kv for kv in front_matter.items())
                      + "\n---\n\n" + body, encoding="utf-8")
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
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    args = parser.parse_args()
    try:
        args.fn(args)
    except KaogongError as exc:
        print("  ✗ %s" % exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
