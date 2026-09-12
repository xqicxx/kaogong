#!/usr/bin/env python3
"""考点卡片的间隔重复调度（FSRS 核心）+ 每日 Telegram 推送。

实现的是 **FSRS-4.5 核心公式**（17 参数，Anki 早期 FSRS 版本用的那套，公式完全公开且稳定）：
    R(t,S) = (1 + FACTOR·t/S)^DECAY        DECAY=-0.5, FACTOR=0.9^(1/DECAY)-1
    S0(G)  = w[G-1]                        首次评分稳定度
    D0(G)  = w4 - (G-3)·w5                 初始难度，夹在 [1,10]
    D'     = w7·D0(4) + (1-w7)·(D - w6·(G-3))
    S'_r   = S·(1 + e^w8·(11-D)·S^-w9·(e^{w10·(1-R)}-1)·hard·easy)
    S'_f   = w11·D^-w12·((S+1)^w13 - 1)·e^{w14·(1-R)}          遗忘后
    间隔   = S / FACTOR · (requestR^(1/DECAY) - 1)     → requestR=90% 时间隔 == S（天）

为什么没上 FSRS-6：6 版多的 w17-w20 主要改「同日复习」和「可训练衰减」两项，
日频复习场景几乎用不到，而我没拿到足够的公式细节，宁可用完全确定的 4.5。
升级路径：换 W 参数表 + 两处公式。

状态就写在卡片自己的 front-matter 里（不另建数据库）：
  Obsidian 里直接看得到到期日、Dataview 能查、vault 的 git 能记录复习历史。
"""
import argparse
import json
import math
import sys
import urllib.request

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import vault  # type: ignore[import]  # noqa: E402
# 上面这行 pyright 解析不了：LSP 的工作区根不在仓库根，运行期靠上面的 sys.path 插入了。
# 同目录的 front-matter 读写，review/mistake 共用。
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path.home() / "Documents" / "Obsidian Vault" / "考公"
# 上次推送的卡片顺序。推送里说“回分：编号 评分”，没有这份映射就没法按编号回分。
PUSH_STATE = Path.home() / ".pi" / "kaogong" / "last-push.json"
CARD_DIR = ROOT / "考点"
TG = Path.home() / ".pi" / "agent" / "telegram.json"

# FSRS-4.5 默认参数（w0..w16）
W = [
    0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031,
    1.6474, 0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755,
]
DECAY = -0.5
FACTOR = 0.9 ** (1 / DECAY) - 1

# 考试锚点：Cepeda 2008 说最优间隔 ≈ 保持期的 10-20%。
# 2026-12-05 考试、83 天窗口 → 首轮 ≈ 12 天。想让首轮落在别的天数，改这里。
FIRST_INTERVAL_DAYS = 12.0
W_ANCHORED = list(W)
W_ANCHORED[0:3] = [v * FIRST_INTERVAL_DAYS / W[2] for v in W[0:3]]

KEYS = {"due": "到期", "s": "稳定度", "d": "难度", "reps": "复习次数", "last": "上次复习"}


def r_of(t, s):
    return (1 + FACTOR * t / max(s, 0.1)) ** DECAY


def interval_days(s, request_r=0.9):
    return max(1.0, s / FACTOR * (request_r ** (1 / DECAY) - 1))


def schedule(state, grade, today=None):
    """返回新状态。state 为 None 表示新卡。grade: 1 again / 2 hard / 3 good / 4 easy"""
    today = today or date.today()
    if state is None:
        s = W_ANCHORED[grade - 1]
        d = min(max(W[4] - (grade - 3) * W[5], 1), 10)
        reps = 1
        r = 0.9
    else:
        s, d, reps, last = state["s"], state["d"], state["reps"], state["last"]
        elapsed = max(0, (today - last).days) if last else 0
        r = r_of(elapsed, s)
        d0_4 = min(max(W[4] - (4 - 3) * W[5], 1), 10)
        d = min(max(W[7] * d0_4 + (1 - W[7]) * (d - W[6] * (grade - 3)), 1), 10)
        if grade == 1:
            s = W[11] * d ** -W[12] * ((s + 1) ** W[13] - 1) * math.exp(W[14] * (1 - r))
        else:
            hard = W[15] if grade == 2 else 1.0
            easy = W[16] if grade == 4 else 1.0
            s = s * (1 + math.exp(W[8]) * (11 - d) * s ** -W[9]
                     * (math.exp(W[10] * (1 - r)) - 1) * hard * easy)
        reps += 1
    s = min(max(s, 0.1), 36500.0)
    ivl = interval_days(s)
    if grade == 1:
        ivl = max(1.0, ivl * 0.5)      # 忘了：间隔砍一半，但至少 1 天
    return {"s": round(s, 3), "d": round(d, 3), "reps": reps,
            "last": today, "due": today + timedelta(days=round(ivl))}


def parse_card(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text, None
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text, None
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, text[end + 4:].lstrip("\n"), text[3:end]


def write_card(path, fm, body, raw_fm):
    """改 front-matter 里的指定字段，其余行原样保留。

    旧实现有个真跑出来的 bug：只有 KEYS 里的字段会被改写，其它字段（状态/变式连胜…）
    即使用新值传进来也保持原样 —— reset 命令因此静默失效过（另有他因 一直停在「待保持」）。
    现在统一走 vault.rewrite_front_matter。
    """
    return vault.write(path, fm, body, raw_fm)


def read_state(fm):
    if not fm.get(KEYS["due"]) or KEYS["due"] not in fm:
        return None
    try:
        last = datetime.strptime(fm[KEYS["last"]], "%Y-%m-%d").date() if fm.get(KEYS["last"]) else None
        return {"s": float(fm[KEYS["s"]]), "d": float(fm[KEYS["d"]]),
                "reps": int(fm.get(KEYS["reps"], 0)), "last": last,
                "due": datetime.strptime(fm[KEYS["due"]], "%Y-%m-%d").date()}
    except (ValueError, KeyError):
        return None


def all_cards():
    if not CARD_DIR.exists():
        return []
    out = []
    for p in sorted(CARD_DIR.glob("*.md")):
        fm, body, raw = parse_card(p)
        out.append({"path": p, "fm": fm, "body": body, "raw": raw, "state": read_state(fm)})
    return out


def cmd_init(args):
    n = 0
    for c in all_cards():
        if c["state"]:
            continue
        st = schedule(None, 3)          # 新卡按“对”起步，首轮到 FIRST_INTERVAL_DAYS
        fm, raw, body = c["fm"], c["raw"], c["body"]
        fm[KEYS["s"]], fm[KEYS["d"]] = st["s"], st["d"]
        fm[KEYS["reps"]], fm[KEYS["last"]] = 0, ""
        fm[KEYS["due"]] = str(st["due"])
        write_card(c["path"], fm, body, raw)
        n += 1
    print("  初始化 %d 张（首轮间隔 %.0f 天）" % (n, FIRST_INTERVAL_DAYS))
    print("  已有状态的 %d 张" % (len(all_cards()) - n))


def due_cards(today=None):
    today = today or date.today()
    due = [c for c in all_cards() if c["state"] and c["state"]["due"] <= today]
    # 逾期最狠 + 最可能忘（R 低）排前面
    def key(c):
        st = c["state"]
        el = max(0, (today - (st["last"] or today)).days)
        return (r_of(el, st["s"]), st["due"])
    return sorted(due, key=key)


def cmd_due(args):
    d = due_cards()
    print("  到期 %d 张：" % len(d))
    today = date.today()
    for c in d[: args.limit]:
        st = c["state"]
        # 「已排期但从未复习过」的卡片 last 是空的（init 之后就是这个状态），
        # 直接相减会 TypeError —— 到期那天必然崩，所以这里必须兜底
        elapsed = max(0, (today - (st["last"] or today)).days)
        print("    %-24s 到期 %s（逾期 %s 天）  R=%.2f" % (
            c["path"].stem, st["due"], (today - st["due"]).days, r_of(elapsed, st["s"])))


# Telegram 的 Markdown 里，卡片名带 _ * [ ] 会让整条消息 400 发不出去。
# 名字是我们自己的数据，但用户完全可能给卡片起个带下划线的名字。
MD_ESCAPE = str.maketrans({c: chr(92) + c for c in "_*[]()"})


def md_safe(value):
    return str(value).translate(MD_ESCAPE)


def build_message(limit):
    today = date.today()
    cards = all_cards()          # 整个 vault 只扫一次（原来扫了三遍）
    newt, review = [], []
    for c in cards:
        st = c["state"]
        if not st or st["reps"] == 0:
            newt.append(c)
        elif st["due"] <= today:
            review.append(c)
    review.sort(key=lambda c: c["state"]["due"])

    lines = ["📚 今天该复习 **%d** 张" % (len(review) + len(newt[:3])), ""]
    if review:
        lines.append("**逾期最狠（先做这些）**")
        lines.append("")
        for i, c in enumerate(review[:limit], 1):
            st = c["state"]
            late = (today - st["due"]).days
            tag = "逾期 %d 天" % late if late > 0 else "今天到期"
            lines.append("%d. %s · %s（%s）" % (i, md_safe(c["path"].stem), md_safe(c["fm"].get("模块", "")), tag))
            lines.append("")
    if newt:
        lines.append("**新学（最多 3 张）**")
        lines.append("")
        for i, c in enumerate(newt[:3], len(review[:limit]) + 1):
            lines.append("%d. %s · %s" % (i, md_safe(c["path"].stem), md_safe(c["fm"].get("模块", ""))))
            lines.append("")
    verify = [c for c in cards
              if (c["fm"].get("状态") or "").strip() in ("变式中", "迁移中", "待保持")]
    if verify:
        lines.append("**🧪 待验证（掌握三关）**")
        lines.append("")
        for c in verify[:5]:
            s = (c["fm"].get("状态") or "").strip()
            keep = c["fm"].get("保持测试") or ""
            tail = "　保持测试 %s" % keep if s == "待保持" and keep else ""
            lines.append("• %s　[%s]%s" % (md_safe(c["path"].stem), s, tail))
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("回分：`编号 评分`　1=忘了　2=勉强　3=对了　4=太简单")
    lines.append("")
    lines.append("例：`3 4` 表示第 3 张给“太简单”")
    return "\n".join(lines), review[:limit] + newt[:3]


def load_tg():
    """读 telegram.json 拿 botToken / allowedUserId，出错就说清哪一步坏了。"""
    try:
        cfg = json.loads(TG.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit("读不到 %s：%s" % (TG, exc))
    except ValueError as exc:
        raise SystemExit("%s 不是合法 JSON：%s" % (TG, exc))
    prof = (cfg.get("profiles") or {}).get(cfg.get("defaultProfile", "default")) or {}
    token, chat = prof.get("botToken"), prof.get("allowedUserId")
    if not token or not chat:
        raise SystemExit("%s 里缺 botToken / allowedUserId" % TG)
    return token, chat


def send_tg(text):
    token, chat = load_tg()
    data = json.dumps({"chat_id": chat, "text": text, "parse_mode": "Markdown"}).encode()
    # URL 里的 token 来自本机配置文件（使用者自己的），不是外部输入
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token,
                                 data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status
    except OSError as exc:                      # 网络不通 / token 失效都从这里出来
        raise SystemExit("发 Telegram 失败：%s" % exc)


def cmd_push(args):
    msg, cards = build_message(args.limit)
    if args.dry:
        print(msg)
        print("\n  （dry run，未发送；本次会推 %d 张）" % len(cards))
        return
    # 先发再存编号表：反过来的话，发送失败也会留下“已推送”的记录，
    # 之后按编号回分就会对到用户根本没看到的卡片
    status = send_tg(msg)
    PUSH_STATE.parent.mkdir(parents=True, exist_ok=True)
    PUSH_STATE.write_text(json.dumps(
        {"date": str(date.today()), "cards": [c["path"].stem for c in cards]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("  发送退出码 %s（%d 张，编号表已存 %s）" % (status, len(cards), PUSH_STATE))


def resolve_card(spec):
    """把「编号」或「卡片名」解析成一张卡。

    两条硬规则（都是 review 指出的）：
      · 数字按上次推送的编号解析，别猜
      · 名字只允许唯一匹配 —— 模糊命中多张就报错，绝不能默默改错卡
    """
    cards = all_cards()
    if spec.isdigit():
        try:
            saved = json.loads(PUSH_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SystemExit("读不到上次推送的编号表（%s），请用卡片名" % PUSH_STATE)
        if str(saved.get("date")) != str(date.today()):
            # 昨天的编号今天含义不同，照着改会改错卡
            raise SystemExit("编号表是 %s 推的，不是今天 —— 用卡片名，或先跑一次 push"
                             % saved.get("date"))
        names = saved.get("cards") or []
        try:
            i = int(spec) - 1
        except ValueError:                      # isdigit() 已挡一层，这里再兜一次
            raise SystemExit("编号得是数字：%r" % spec)
        if not 0 <= i < len(names):
            raise SystemExit("编号 %s 超出上次推送范围（共 %d 张）" % (spec, len(names)))
        hit = [c for c in cards if c["path"].stem == names[i]]
        if not hit:
            raise SystemExit("上次推送的第 %s 张「%s」现在找不到了" % (spec, names[i]))
        return hit[0]
    exact = [c for c in cards if c["path"].stem == spec]
    if len(exact) == 1:
        return exact[0]
    fuzzy = [c for c in cards if spec in c["path"].stem]
    if not fuzzy:
        raise SystemExit("没找到卡片：%s" % spec)
    if len(fuzzy) > 1:
        raise SystemExit("「%s」匹配到 %d 张卡，说清楚是哪张：%s"
                         % (spec, len(fuzzy), "、".join(x["path"].stem for x in fuzzy[:6])))
    return fuzzy[0]


def cmd_grade(args):
    """grade <编号|卡片名> <1-4>。编号来自最近一次 push 的顺序。"""
    c = resolve_card(args.card)
    if c["path"].stem != args.card and not args.card.isdigit():
        print("  （按「%s」匹配到 %s）" % (args.card, c["path"].stem))
    # FSRS-4.5 里同日重复评分几乎不改变稳定度（R(0)=1 → 增量因子为 0），
    # FSRS-6 才专门处理同日复习。一天评一次是正常用法，但重复评要提醒，免得误以为“评了有用”。
    same_day = (c["state"] or {}).get("last") == date.today()
    st = schedule(c["state"], args.rating)
    fm, raw, body = c["fm"], c["raw"], c["body"]
    fm[KEYS["s"]], fm[KEYS["d"]] = st["s"], st["d"]
    fm[KEYS["reps"]], fm[KEYS["last"]] = st["reps"], str(st["last"])
    fm[KEYS["due"]] = str(st["due"])
    write_card(c["path"], fm, body, raw)
    print("  %s  评分 %d  S=%.1f D=%.2f  下次 %s（%.0f 天后）" % (
        c["path"].stem, args.rating, st["s"], st["d"], st["due"],
        (st["due"] - date.today()).days))
    if same_day:
        print("    ⚠️ 今天已经评过这张卡了 —— 同日重复评分不改变稳定度"
              "（FSRS-4.5 的 R(0)=1，增量因子为 0），间隔不会因此拉长")


def cmd_reset(args):
    """把所有卡片的状态清回出厂值。会丢复习历史，所以要 --yes。"""
    if not args.yes:
        raise SystemExit("这会清掉全部复习历史。确认就加 --yes")
    cleared = {"状态": "未掌握", "变式连胜": "0", "迁移通过": "false", "保持测试": ""}
    n = 0
    for c in all_cards():
        st = schedule(None, 3)                 # 重新起一张新卡
        fm, raw, body = c["fm"], c["raw"], c["body"]
        fm.update(cleared)
        fm[KEYS["s"]], fm[KEYS["d"]] = st["s"], st["d"]
        fm[KEYS["reps"]], fm[KEYS["last"]] = 0, ""
        fm[KEYS["due"]] = str(st["due"])
        write_card(c["path"], fm, body, raw)
        n += 1
    print("  已重置 %d 张卡（状态/排期/掌握字段回到出厂，首轮 %.0f 天）" % (n, FIRST_INTERVAL_DAYS))


def cmd_stats(args):
    cards = all_cards()
    today = date.today()
    withstate = [c for c in cards if c["state"]]
    print("  卡片总数 %d，已排期 %d" % (len(cards), len(withstate)))
    if withstate:
        d = due_cards()
        print("  今天到期 %d" % len(d))
        fut = sorted((c["state"]["due"] - today).days for c in withstate)
        print("  最近到期：%s" % ("%d 天后" % fut[0]))
        print("  最远到期：%s" % ("%d 天后" % fut[-1]))
        print("  平均稳定度 %.1f 天" % (sum(c["state"]["s"] for c in withstate) / len(withstate)))


def cmd_selftest(args):
    """排期逻辑的自检：这些断言挂了，说明间隔算错了。"""
    today = date(2026, 9, 13)
    st = schedule(None, 3, today)
    ivl = (st["due"] - today).days
    msg = "首轮间隔应≈%.0f 天，实际 %d" % (FIRST_INTERVAL_DAYS, ivl)
    assert abs(ivl - FIRST_INTERVAL_DAYS) <= 1, msg
    again = schedule(st, 1, st["due"])
    good = schedule(st, 3, st["due"])
    easy = schedule(st, 4, st["due"])
    hard = schedule(st, 2, st["due"])
    assert again["due"] <= st["due"] + timedelta(days=2), "忘了之后必须很快再见到"
    ordered = st["due"] < hard["due"] < good["due"] < easy["due"]
    assert ordered, "间隔必须随评分单调：hard<good<easy"
    assert again["s"] < st["s"], "忘了应降低稳定度"
    assert easy["s"] > good["s"] > st["s"], "稳定度应随表现上升"
    assert 1 <= again["d"] <= 10 and 1 <= easy["d"] <= 10, "难度必须夹在 [1,10]"
    r_now, r_later = r_of(0, st["s"]), r_of(30, st["s"])
    assert r_now > r_later, "可提取度必须随时间下降"
    # S 的定义就是 R 掉到 90% 的那个时间点：R(t=S, S) == 0.9
    assert abs(r_of(st["s"], st["s"]) - 0.9) < 1e-9, "稳定度定义不满足 R(S,S)=90%%"
    assert schedule(st, 3, st["due"])["reps"] == st["reps"] + 1, "复习次数要累加"
    print("  自检通过：首轮 %d 天 / 忘了 %d 天 / 勉强 %d 天 / 对 %d 天 / 太简单 %d 天" % (
        ivl, (again["due"] - st["due"]).days, (hard["due"] - st["due"]).days,
        (good["due"] - st["due"]).days, (easy["due"] - st["due"]).days))


def main():
    ap = argparse.ArgumentParser(description="考点卡片 FSRS 调度")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("due"); p.add_argument("--limit", type=int, default=10); p.set_defaults(fn=cmd_due)
    p = sub.add_parser("push"); p.add_argument("--limit", type=int, default=8)
    p.add_argument("--dry", action="store_true"); p.set_defaults(fn=cmd_push)
    p = sub.add_parser("grade"); p.add_argument("card"); p.add_argument("rating", type=int, choices=[1, 2, 3, 4])
    p.set_defaults(fn=cmd_grade)
    p = sub.add_parser("stats"); p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("selftest"); p.set_defaults(fn=cmd_selftest)
    p = sub.add_parser("reset"); p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_reset)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
