#!/usr/bin/env python3
"""考点卡片的间隔重复调度 —— 只管命令行与消息组装。

逻辑已经拆到各自的模块，这里只做“拼起来”：

    paths.py    所有路径的唯一来源（可用环境变量覆盖）
    errors.py   领域异常（库函数只抛错，退出口径统一在 main）
    fsrs.py     排期算法，纯函数无 I/O
    cards.py    卡片读取 / 查找 / 写回状态
    notify.py   推送到 Telegram（含被拒收的判定）
    vault.py    front-matter 读写、原子写、写前快照

状态就写在卡片自己的 front-matter 里（不另建数据库）：
Obsidian 里直接看得见到期日、Dataview 能查、vault 的 git 记得住历史。
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.cards import (all_cards, broken_cards, by_index, find,
                            save_state, state_fields)  # type: ignore[import]  # noqa: E402
from pipeline.dedup import split_siblings  # type: ignore[import]  # noqa: E402
from pipeline.discriminate import summarize as discrimination  # type: ignore[import]  # noqa: E402
from pipeline.errors import KaogongError  # type: ignore[import]  # noqa: E402
from pipeline.fsrs import FIRST_INTERVAL_DAYS, r_of, schedule  # type: ignore[import]  # noqa: E402
from pipeline.interleave import pick as pick_interleaved  # type: ignore[import]  # noqa: E402
from pipeline.mistakes import read_all as read_mistakes  # type: ignore[import]  # noqa: E402
from pipeline.notify import SAFE_LENGTH, send  # type: ignore[import]  # noqa: E402
from pipeline.panel import build as build_panel, refresh as refresh_panel  # type: ignore[import]  # noqa: E402
from pipeline.paths import PUSH_STATE, STATE_DIR  # type: ignore[import]  # noqa: E402
from pipeline.vault import write  # type: ignore[import]  # noqa: E402

# Telegram 的 Markdown 里，卡片名带 _ * [ ] ` 会让整条消息 400 发不出去。
# 名字是我们自己的数据，但用户完全可能给卡片起个带下划线的名字。
MD_ESCAPE = str.maketrans({c: chr(92) + c for c in "_*[]()" + chr(96)})


def md_safe(value):
    return str(value).translate(MD_ESCAPE)


def _elapsed(card, today):
    """距上次复习过了多久。没复习过的（init 之后）算 0。"""
    last = card["state"]["last"]
    return max(0, (today - (last or today)).days)




def build_message(limit, today=None):
    """返回 (消息文本, 本次推送的卡片列表)。列表顺序就是回分用的编号顺序。"""
    today = today or date.today()
    everything = all_cards()                 # 整个 vault 只扫一次
    fresh, due, broken = [], [], []
    for card in everything:
        state = card["state"]
        if card.get("health") == "broken":
            broken.append(card)          # 排期字段坏了：既不算新卡也不算到期
        elif not state or state["reps"] == 0:
            fresh.append(card)
        elif state["due"] <= today:
            due.append(card)
    # “逾期最狠”按可提取度排，不是按日期 —— 到期最久的未必最容易忘
    due.sort(key=lambda card: r_of(_elapsed(card, today), card["state"]["s"]))

    # 语义错开（LECTOR 2025 的发现）：同批里别放近义考点。
    # 例：「因果倒置」与「否定此因」文字相似度 0.61 —— 它们不是重复，
    # 是同一组的兄弟条目；放一起会“记混”而不是“忘记”。今天先放过其中一个。
    # 注意：这里只错开排期，**绝不合并或删除卡片** —— 删了就是丢知识点。
    body_of = lambda card: card["body"] or ""
    group_of = lambda card: (card["fm"].get("分组") or "").strip()
    due, deferred = split_siblings(due, body_of, group_of=group_of)
    # 新学也要错开：同一天引入两个近义考点，效果和“一起复习”一样糟
    fresh, fresh_deferred = split_siblings(fresh, body_of, already=due, group_of=group_of)
    deferred += fresh_deferred
    pending = [card for card in everything
               if (card["fm"].get("状态") or "").strip() in ("变式中", "迁移中", "待保持")]

    used = 0
    lines = ["📚 今天该复习 **%d** 张" % (len(due) + len(fresh[:3])), ""]
    shown_review = 0
    if due:
        lines += ["**逾期最狠（先做这些）**", ""]
        for position, card in enumerate(due[:limit], 1):
            late = (today - card["state"]["due"]).days
            tag = "逾期 %d 天" % late if late > 0 else "今天到期"
            entry = "%d. %s · %s（%s）" % (position, md_safe(card["path"].stem),
                                           md_safe(card["fm"].get("模块", "")), tag)
            if used + len(entry) > SAFE_LENGTH:
                break
            lines += [entry, ""]
            used += len(entry)
            shown_review += 1
    if fresh:
        lines += ["**新学（最多 3 张）**", ""]
        for position, card in enumerate(fresh[:3], shown_review + 1):
            entry = "%d. %s · %s" % (position, md_safe(card["path"].stem),
                                      md_safe(card["fm"].get("模块", "")))
            if used + len(entry) > SAFE_LENGTH:
                break
            lines += [entry, ""]
            used += len(entry)
    if shown_review < len(due):
        lines += ["…还有 %d 张没列出来，先做上面的" % (len(due) - shown_review), ""]
    if deferred:
        lines += ["（为防混淆，今日暂缓 %d 张近义考点：%s）"
                  % (len(deferred), "、".join(md_safe(c["path"].stem) for c in deferred[:3])), ""]
    if broken:
        lines += ["**⚠️ 排期字段坏了（已跳过，不影响今天的复习）**", ""]
        lines += ["  ".join(md_safe(c["path"].stem) for c in broken[:5]), ""]
    if pending:
        lines += ["**🧪 待验证（掌握三关）**", ""]
        for card in pending[:5]:
            stage = (card["fm"].get("状态") or "").strip()
            keep = card["fm"].get("保持测试") or ""
            tail = "　保持测试 %s" % keep if stage == "待保持" and keep else ""
            lines += ["• %s　[%s]%s" % (md_safe(card["path"].stem), stage, tail), ""]
    # 交错练习（PLAN 2.2 ⑤）：题型靠「判别」不靠记忆，
    # 按题型块刷属于分块练习，块内不需要判别、机械重复就能过。
    # 源是「错因=程序性」的错题 —— 概念性走概念转变流程，粗心不进队列（PLAN 3.3 分流）。
    records, skipped_mistakes = read_mistakes()
    picks, why = pick_interleaved(
        [r for r in records if r["错因"] == "程序性" and r["状态"] != "已掌握"], limit=3)
    if picks:
        lines += ["**🔀 交错练习（相邻不同型，练判别）**", ""]
        for position, item in enumerate(picks, 1):
            lines += ["%d. [%s] %s" % (position, md_safe(item["题型"]), md_safe(item["标题"])), ""]
    elif why and any(r["错因"] == "程序性" for r in records):
        # 有程序性错题却凑不出两种题型 —— 这本身就是该告诉用户的事
        lines += ["（交错练习：%s，先多录几道程序性错题）" % why, ""]
    if skipped_mistakes:
        # 静默跳过的卡会从复习队列里凭空少掉，必须让用户看见
        lines += ["**⚠️ %d 道错题读不出来（已跳过，不影响今天的复习）**" % len(skipped_mistakes), "",
                  "  ".join(md_safe(p.stem) for p in skipped_mistakes[:5]), ""]
    lines += ["---", "", "回分：`编号 评分`　1=忘了　2=勉强　3=对了　4=太简单", "",
              "例：`3 4` 表示第 3 张给“太简单”"]
    return "\n".join(lines), due[:shown_review] + fresh[:3], \
        _panel_data(due, fresh, pending, broken, records, today)


def _panel_data(due, fresh, pending, broken, records, today):
    """备好面板要的行。排版细节归 panel.py，这里只出数据 —— 换展示方式不用碰这里。"""
    def rows(cards, note_of=None):
        out = []
        for card in cards:
            fm = card["fm"]
            out.append((card["path"].stem, fm.get("模块", ""),
                        note_of(card) if note_of else ""))
        return out

    def late_note(card):
        late = (today - card["state"]["due"]).days
        return "逾期 %d 天" % late if late > 0 else "今天到期"

    def stage_note(card):
        return (card["fm"].get("状态") or "").strip()

    verdict = discrimination(records)
    drop = [(s["name"], s["cause"] or "—", "连胜 %d（已掌握）" % s["streak"])
            for s in verdict["drop"]]
    return {
        "due": rows(due, late_note),
        "fresh": rows(fresh),
        "pending": rows(pending, stage_note),
        "broken": [card["path"].stem for card in broken],
        "drop": drop,
    }


def cmd_init(_args):
    count = 0
    damaged = broken_cards()
    for card in all_cards():
        if card["state"] or card.get("health") == "broken":
            continue     # 坏卡不重排：当成新卡会把真实复习历史冲掉
        planned = schedule(None, 3)          # 新卡按“对”起步，首轮到 FIRST_INTERVAL_DAYS
        save_state(card, dict(planned, reps=0, last=None))
        count += 1
    print("  初始化 %d 张（首轮间隔 %.0f 天）" % (count, FIRST_INTERVAL_DAYS))
    print("  已有状态的 %d 张" % (len(all_cards()) - count))


def cmd_due(args):
    today = date.today()
    pending = [card for card in all_cards()
               if card["state"] and card["state"]["due"] <= today]
    pending.sort(key=lambda card: r_of(_elapsed(card, today), card["state"]["s"]))
    print("  到期 %d 张：" % len(pending))
    for card in pending[: args.limit]:
        state = card["state"]
        print("    %-24s 到期 %s（逾期 %s 天）  R=%.2f" % (
            card["path"].stem, state["due"], (today - state["due"]).days,
            r_of(_elapsed(card, today), state["s"])))


def cmd_push(args):
    message, sent, panel_data = build_message(args.limit)
    if args.dry:
        print(message)
        print("\n  （dry run，未发送；本次会推 %d 张）" % len(sent))
        return
    # 先发成功再落编号表：反过来的话，发送失败也会留下“已推送”的记录，
    # 之后按编号回分就会对到用户根本没看到的卡片
    status = send(message)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PUSH_STATE.write_text(json.dumps(
        {"date": str(date.today()), "cards": [card["path"].stem for card in sent]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("  发送退出码 %s（%d 张，编号表已存 %s）" % (status, len(sent), PUSH_STATE))
    # 面板是「视图」，写完推完才算数；但它坏掉不该让已经发出去的推送变成失败，
    # 所以只在大声报错的同时照常退出 0 —— 不能悄悄咽掉。
    try:
        panel_path = refresh_panel(build_panel(**panel_data))
        print("  面板已刷新：%s" % panel_path)
    except (KaogongError, OSError) as exc:
        print("  ⚠️ 推送已发出，但面板没刷新：%s" % exc)


def cmd_grade(args):
    card = by_index(args.card) if str(args.card).isdigit() else find(args.card)
    if card.get("health") == "broken":
        # 坏卡当新卡评分 = 用初始值覆盖真实复习历史
        raise KaogongError("这张卡的排期字段读不出来（手改坏过？），先修好再评分：%s"
                           % card["path"].stem)
    if card["path"].stem != args.card and not str(args.card).isdigit():
        print("  （按「%s」匹配到 %s）" % (args.card, card["path"].stem))
    # FSRS-4.5 里同日重复评分几乎不改变稳定度（R(0)=1 → 增量因子为 0），
    # 一天评一次是正常用法，但重复评要提醒，免得误以为“多评几次就记住了”
    same_day = (card["state"] or {}).get("last") == date.today()
    planned, same_day_redo = plan_for(card["state"], args.rating)
    save_state(card, planned)
    print("  %s  评分 %d  S=%.1f D=%.2f  下次 %s（%.0f 天后）" % (
        card["path"].stem, args.rating, planned["s"], planned["d"], planned["due"],
        (planned["due"] - date.today()).days))
    if same_day_redo:
        print("    ↻ 「勉强」不算过（successive relearning）：这张卡留在今天的到期队列里，"
              "请再完整回忆一次，别看一眼答案就算")
    if same_day:
        print("    ⚠️ 今天已经评过这张卡了 —— 同日重复评分不改变稳定度"
              "（FSRS-4.5 的 R(0)=1，增量因子为 0），间隔不会因此拉长")


def plan_for(state, rating, today=None):
    """评分 → 新计划。返回 (计划, 是否要求当天重来)。

    抽成纯函数是为了能单独测：这条「勉强不算过」的规矩要是哪天被改掉，
    测试会立刻红 —— 而它藏在 cmd_grade 里的时候只能靠手工试。

    PLAN 2.2 ③ successive relearning：「模糊想起」「看一眼答案」都不算过，当天重来。
    这里只把到期日拉回今天，**不动 S/D** —— 记忆强度归 FSRS 管，
    本函数改的只是「什么时候再考一次」。
    """
    today = today or date.today()
    planned = schedule(state, rating)
    if rating == 2:
        return dict(planned, due=min(planned["due"], today)), True
    return planned, False


def cmd_panel(_args):
    """单独刷新库内面板（不改任何排期状态，只重写展示用的那一页）。"""
    _message, _sent, panel_data = build_message(10)
    print("  已刷新：%s" % refresh_panel(build_panel(**panel_data)))


def cmd_reset(args):
    if not args.yes:
        raise KaogongError("这会清掉全部复习历史。确认就加 --yes")
    cleared = {"状态": "未掌握", "变式连胜": "0", "迁移通过": "false", "保持测试": ""}
    count = 0
    for card in all_cards():
        planned = schedule(None, 3)
        # 一次写两类字段：掌握状态（clear）与排期状态（state_fields）
        updates = dict(cleared)
        updates.update(state_fields(dict(planned, reps=0, last=None)))
        write(card["path"], updates, card["body"], card["raw"])
        count += 1
    print("  已重置 %d 张卡（状态/排期/掌握字段回到出厂，首轮 %.0f 天）"
          % (count, FIRST_INTERVAL_DAYS))


def cmd_stats(_args):
    everything = all_cards()
    scheduled = [card for card in everything if card["state"]]
    print("  卡片总数 %d，已排期 %d" % (len(everything), len(scheduled)))
    if not scheduled:
        return
    today = date.today()
    pending = [card for card in scheduled if card["state"]["due"] <= today]
    offsets = sorted((card["state"]["due"] - today).days for card in scheduled)
    print("  今天到期 %d" % len(pending))
    print("  最近到期：%d 天后" % offsets[0])
    print("  最远到期：%d 天后" % offsets[-1])
    print("  平均稳定度 %.1f 天" % (sum(card["state"]["s"] for card in scheduled) / len(scheduled)))


def cmd_selftest(_args):
    """排期逻辑自检：这些断言挂了，说明间隔算错了。"""
    today = date(2026, 9, 13)
    first = schedule(None, 3, today)
    interval = (first["due"] - today).days
    message = "首轮间隔应≈%.0f 天，实际 %d" % (FIRST_INTERVAL_DAYS, interval)
    assert abs(interval - FIRST_INTERVAL_DAYS) <= 1, message
    again = schedule(first, 1, first["due"])
    hard = schedule(first, 2, first["due"])
    good = schedule(first, 3, first["due"])
    easy = schedule(first, 4, first["due"])
    assert again["due"] <= first["due"] + __import__("datetime").timedelta(days=2), \
        "忘了之后必须很快再见到"
    ordered = first["due"] < hard["due"] < good["due"] < easy["due"]
    assert ordered, "间隔必须随评分单调：hard<good<easy"
    assert again["s"] < first["s"], "忘了应降低稳定度"
    assert easy["s"] > good["s"] > first["s"], "稳定度应随表现上升"
    assert 1 <= again["d"] <= 10 and 1 <= easy["d"] <= 10, "难度必须夹在 [1,10]"
    assert r_of(0, first["s"]) > r_of(30, first["s"]), "可提取度必须随时间下降"
    assert abs(r_of(first["s"], first["s"]) - 0.9) < 1e-9, "稳定度定义要满足 R(S,S)=90%"
    assert schedule(first, 3, first["due"])["reps"] == first["reps"] + 1, "复习次数要累加"
    print("  自检通过：首轮 %d 天 / 忘了 %d 天 / 勉强 %d 天 / 对 %d 天 / 太简单 %d 天" % (
        interval, (again["due"] - first["due"]).days, (hard["due"] - first["due"]).days,
        (good["due"] - first["due"]).days, (easy["due"] - first["due"]).days))


def main():
    parser = argparse.ArgumentParser(description="考点卡片 FSRS 调度")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("due").add_argument("--limit", type=int, default=10)
    sub.choices["due"].set_defaults(fn=cmd_due)
    push = sub.add_parser("push")
    push.add_argument("--limit", type=int, default=8)
    push.add_argument("--dry", action="store_true")
    push.set_defaults(fn=cmd_push)
    grade = sub.add_parser("grade")
    grade.add_argument("card")
    grade.add_argument("rating", type=int, choices=[1, 2, 3, 4])
    grade.set_defaults(fn=cmd_grade)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("panel").set_defaults(fn=cmd_panel)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    reset = sub.add_parser("reset")
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(fn=cmd_reset)
    args = parser.parse_args()
    try:
        args.fn(args)
    except KaogongError as exc:      # 领域异常统一在这里翻成退出码
        print("  ✗ %s" % exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
