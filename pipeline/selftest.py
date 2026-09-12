#!/usr/bin/env python3
"""一条命令跑完所有自检与边界用例 —— 上线前跑这个。

    python3 pipeline/selftest.py

三层：
  1. 模块内部自检（vault / review 排期 / mistake 状态机）
  2. 纯函数边界用例（解析、命名、噪声过滤、条目续行…）
  3. CLI 冒烟（非法参数、不存在的卡片 —— 都必须报错退出，不能静默成功）

任何一项失败 → 汇总后非零退出。
"""
import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import errors, fsrs, mistake, review, split, vault  # noqa: E402


def load_vision2md():
    """tools/ 不是包，按路径直接加载，别写成 import vision2md"""
    path = ROOT / "tools" / "vision2md.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("vision2md", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vision2md = load_vision2md()
FAILURES = []
CHECKS = [0]


def check(name, fn):
    CHECKS[0] += 1
    try:
        fn()
        print("  ✓ %s" % name)
    except Exception as exc:                     # noqa: BLE001 —— 收集器要兜住一切，不能因为一个用例挂掉整轮
        FAILURES.append((name, "%s: %s" % (type(exc).__name__, exc)))
        print("  ✗ %s\n      %s: %s" % (name, type(exc).__name__, exc))


def expect_raises(exc_types, fn, what):
    try:
        fn()
    except exc_types:
        return
    raise AssertionError("本该报错却没有：%s" % what)


def run_cli(args):
    """跑一条命令行；非零退出才算通过（这些用例都是“应该被拦住”的情形）。"""
    # shell 脚本不能用 python 解释器跑 —— 以前这样写，永远只得到 SyntaxError，
    # 于是“非零退出”这条断言躺赢（测的不是脚本本身的行为）
    runner = ["bash"] if args[0].endswith(".sh") else [sys.executable]
    result = subprocess.run(runner + args, capture_output=True, text=True,
                            cwd=ROOT, timeout=60)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0:
        raise AssertionError("本该非零退出却成功了：%s" % " ".join(args))
    if "SyntaxError" in output or "Traceback" in output:
        raise AssertionError(
            "不该以解释器/语法错误的形式失败：%s / %s" % (" ".join(args), output[:180]))
    return output


# ---------- 1. 模块内部自检 ----------
print("模块自检")
check("vault 解析/写入", vault.selftest)
check("review 排期", lambda: review.cmd_selftest(None))
check("mistake 状态机", lambda: mistake.cmd_selftest(None))


# ---------- 2. 纯函数边界用例 ----------
print("\n边界用例")


def _vault_edges():
    assert vault.merge_front_matter("", {"a": "1"}) == "a: 1"
    _, body, raw = vault.split("---\na: 1\n---\n\n正文")
    assert body == "正文" and "a: 1" in raw
    assert vault.split("没有 front-matter")[2] == "", "没有 front-matter 时 raw 必须是空字符串"
    assert vault.split("---\na: 1\n")[0] == {}, "缺闭合分隔符应当作没有 front-matter"
    assert vault.safe_int("x", 7) == 7 and vault.safe_float(None, 2.5) == 2.5
    lines = vault.merge_front_matter("\n\na: 1\n\n", {"a": "2"}).splitlines()
    assert len(lines) == 1, "首尾空行不该带进来，得到 %r" % lines


def _md_escape():
    escaped = review.md_safe("a_b*c[d]e`f")
    for ch in "_*[]":
        assert review.md_safe(ch) == chr(92) + ch, "%s 必须转义" % ch
    assert chr(92) + chr(96) in escaped, "反引号也要转义"


def _schedule_edges():
    today = date(2026, 9, 13)
    state = fsrs.schedule(None, 3, today)
    for grade in (1, 2, 3, 4):
        nxt = fsrs.schedule(state, grade, state["due"])
        assert 1 <= nxt["d"] <= 10, "难度必须夹在 [1,10]，得到 %s" % nxt["d"]
        assert nxt["due"] > state["due"], "下次到期必须往后走"
        assert nxt["reps"] == state["reps"] + 1
    assert fsrs.r_of(0, 10) > fsrs.r_of(100, 10), "R 必须随时间下降"


def _mastery_gates():
    today = date(2026, 9, 13)
    for kind in ("迁移", "保持"):
        expect_raises(ValueError,
                      lambda k=kind: mistake.advance({"状态": "未掌握"}, k, True, today),
                      "未掌握不能直接考 %s 关" % kind)
    state = mistake.advance({"状态": "未掌握"}, "变式", True, today)
    expect_raises(ValueError, lambda: mistake.advance(state, "迁移", True, today),
                  "变式只赢 1 次不能考迁移关")
    state = mistake.advance(state, "变式", True, today)
    assert state["状态"] == "迁移中", state
    assert mistake.advance(state, "迁移", True, today)["状态"] == "待保持"
    # 失败要退级，且不留下旧的已通过标记
    failed = mistake.advance(state, "变式", False, today)
    assert failed["状态"] == "变式中" and failed["保持测试"] == "", failed


def _naming():
    assert split.safe_name("") == "未命名" and split.safe_name("///") == "未命名"
    assert "/" not in split.safe_name("A/B") and ":" not in split.safe_name("A:B")


def _item_continuation():
    body = ("### 三种削弱（质疑）方式\n\n"
            "①另有他因：引入题干未提及的其他影响因素，\n"
            "降低原有因果关系的确定性\n\n"
            "②因果倒置：颠倒原因与结果的先后顺序，削弱力度极强\n")
    items = split.collect_items([{"page": 1, "body": body, "fm": {}}])
    assert len(items) == 2, "应当抽到 2 条，得到 %d：%r" % (len(items), [x[2] for x in items])
    assert "降低原有因果关系的确定性" in items[0][3], \
        "紧邻的续行必须并进同一条，实际：%r" % items[0][3]




def _page_dedup():
    from pipeline import dedup
    one = "①排除他因：剔除其他潜在影响因素，强化题干因果关系的唯一性，降低不确定性。第2页"
    worse = "①排除他因：剔除其他潜在因素，强化题干因果关系的唯一性，降低不确定性。第2页"
    other = "第一章 逻辑论证之归因论证 1.1 归因论证整体概述 一、归因论证定义 第1页"
    same_score = dedup.similarity(one, worse)
    assert same_score > 0.75, "同一页重拍应当高度相似，实测 %.3f" % same_score
    assert dedup.is_same_page(one, worse), "同一页要判为重复"
    assert not dedup.is_same_page(one, other), "不同页不能判为重复"
    assert dedup.page_label(one) == "2" and dedup.page_label(other) == "1"
    match = dedup.find_match(one, [("老页.md", worse), ("别的页.md", other)])
    assert match and match[0] == "老页.md", "匹配结果不对：%r" % (match,)
    # 页码相同可以放宽阈值（重拍糊了 OCR 出人较多）


def _siblings():
    """近义考点必须错开排期，但**绝不能被删掉**。

    主判据是「分组」：同一组的条目本来就是同一套措辞的并列项，
    实测卡背相似度 0.13-0.30；不同组只有 0.04-0.13。
    """
    from pipeline import dedup
    cards = [
        {"path": "因果倒置", "分组": "三种削弱（质疑）方式",
         "body": "②因果倒置：颠倒原因与结果的先后顺序，直接否定题干因果关系，削弱力度极强。"},
        {"path": "否定此因", "分组": "三种削弱（质疑）方式",
         "body": "③否定此因：直接表明题干给出的原因不成立，切断原有因果关联，削弱力度极强。"},
        {"path": "增长率比较", "分组": "资料分析",
         "body": "资料分析：增长率比较要用两期比重差，先算基期量再比较，注意单位换算与量级。"},
    ]
    group_of = lambda card: card["分组"]
    kept, deferred = dedup.split_siblings(cards, lambda c: c["body"], group_of=group_of)
    kept_names = [c["path"] for c in kept]
    deferred_names = [c["path"] for c in deferred]
    assert len(kept) + len(deferred) == len(cards), "错开只能暂缓，不能丢卡片"
    assert deferred_names == ["否定此因"], "同组第二张要暂缓：%r" % deferred_names
    assert "增长率比较" in kept_names, "不同组的必须留下：%r" % kept_names

    # 没有分组信息时，文本相似度兜底
    twin_a = {"path": "A", "body": "①排除他因：剔除其他潜在影响因素，强化题干因果关系的唯一性，削弱力度极强。"}
    twin_b = {"path": "B", "body": "②排除他因：剔除其他潜在影响因素，强化题干因果关系的唯一性，削弱力度极强。"}
    kept2, deferred2 = dedup.split_siblings([twin_a, twin_b], lambda c: c["body"])
    assert [c["path"] for c in deferred2] == ["B"],         "没分组时相似文本也要错开：相似度 %.3f" % dedup.similarity(twin_a["body"], twin_b["body"])




def _dedup_edges():
    """页码是硬证据；短文本不该拿相似度说事。"""
    from pipeline import dedup

    # 共用一大段版式文字、但页脚页码不同的两页 —— 相似度中等（0.45-0.85 之间）
    shared = ("归因论证的核心是对已发生的既定事实进行原因探究，文段的最终目的是分析这件事为什么会发生的"
              "真正原因所在。做题时先看题干的分组方式，再看选项有没有回到同一组里作比较。")
    first = "第2页 " + shared + "本页讲对比实验归因，以分组对照实验为载体推导差异产生的原因。"
    second = "第3页 " + shared + "本页讲时间对比归因，以过去和现在两个时间维度对照状态变化。"
    score = dedup.similarity(first, second)
    assert 0.45 <= score < dedup.STRONG_THRESHOLD,         "这条用例要覆盖“页码否决”的那一段，当前相似度 %.3f 不在区间内" % score
    assert not dedup.is_same_page(first, second),         "页码明确不同且文字只是中等相似（%.3f）时不能判为同一页" % score

    # 页码被 OCR 读错、但文字几乎一致 → 仍应判为同一页
    long_same = "第2页 " + "①排除他因：剔除其他潜在影响因素，强化题干因果关系的唯一性。" * 3
    assert dedup.is_same_page(long_same, long_same.replace("第2页", "第！页")),         "页码读错但文字几乎一致，仍应判为同一页"

    short = [{"body": "完全不同的内容 A"}, {"body": "完全不同的内容 B"}]
    _kept, deferred = dedup.split_siblings(short, lambda c: c["body"])
    assert not deferred, "太短的正文不该由相似度触发错开"

    assert dedup.page_label("第 12 页") == "12" and dedup.page_label("没有页码") == ""




def _dedup_rescue():
    """相似度不高、但页码相同的候选，也要被捞回来。

    起因：旧实现只验“相似度最高的那个候选”，于是重拍糊了（相似度 0.3）
    但页码对得上的那页会被丢掉，结果同一页被当成新页重新录入。
    """
    from pipeline import dedup
    base = ("归因论证的核心是对已发生的既定事实进行原因探究，文段的最终目的是分析这件事的"
            "真正原因，做题时要先看题干的分组方式再回到同一组里比较选项。")
    candidate = "第2页 " + base
    # 同样的页码，但文字被 OCR 弄花了一部分 → 相似度中等
    noisy = "第2页 " + "归因论证核心对已发生事实进行原因探究，文段目的是分析真正原因，做题先看分组方式再回到同组比较选项。"
    score = dedup.similarity(candidate, noisy)
    assert score < dedup.DEFAULT_THRESHOLD, "这条用例需要相似度低于主阈值，实测 %.3f" % score
    assert score >= dedup.PAGE_LABEL_THRESHOLD, "又要够到页码旁证阈值，实测 %.3f" % score
    # 另一个候选分数更高，但它不是同一页（页码不同、文字也不同）
    decoy = "第7页 " + "四、归因论证常见正误选项判定标准 ①话题紧扣原因：选项始终围绕题干成因展开。"
    hit = dedup.find_match(candidate, [("诱饵.md", decoy), ("老的.md", noisy)])
    assert hit and hit[0] == "老的.md", "页码相同的那页应当被捞回来，实际 %r" % (hit,)


check("去重捞回", _dedup_rescue)
check("去重边界", _dedup_edges)
check("页面去重", _page_dedup)
check("近义错开", _siblings)

check("vault 边界", _vault_edges)
def _domain_errors():
    from pipeline import cards
    try:
        cards.find("根本不存在的卡片xyz")
    except errors.CardNotFound:
        pass
    else:
        raise AssertionError("找不到卡片要抛 CardNotFound")
    try:
        cards.find("支持")
    except errors.AmbiguousCard:
        pass
    else:
        raise AssertionError("模糊命中多张要抛 AmbiguousCard")
    try:
        fsrs.schedule(None, 9)
    except ValueError:
        pass
    else:
        raise AssertionError("非法评分要抛 ValueError")


check("领域异常", _domain_errors)
check("Markdown 转义", _md_escape)
check("排期边界", _schedule_edges)
check("三关闸门", _mastery_gates)
check("文件命名", _naming)
check("条目续行", _item_continuation)

if vision2md is None:
    print("  - vision2md 未加载（跳过页面解析用例）")
else:
    module = vision2md

    def _noise():
        page_number = {"text": "第2页", "y": 0.95, "h": 0.02}
        header = {"text": "关注“花生十三”公众号，每日图推、类比、速算等", "y": 0.04, "h": 0.02}
        brand = {"text": "四海公章", "y": 0.05, "h": 0.02}
        logo = {"text": "CTHAIGONG KAO", "y": 0.06, "h": 0.02}
        body = {"text": "①另有他因：引入题干未提及的其他影响因素", "y": 0.5, "h": 0.02}
        short_body = {"text": "第2条规则", "y": 0.5, "h": 0.02}
        checks = [
            (module.is_noise(page_number, set()), "页码要当噪声"),
            (module.is_noise(header, {header["text"]}), "批内识别出的页眉要当噪声"),
            (module.is_noise(header, set()), "单页时页眉里的公众号行也要丢"),
            (module.is_noise(brand, set()), "页眉带里的短品牌名要丢"),
            (module.is_noise(logo, set()), "商标英文行要丢"),
            (not module.is_noise(body, set()), "正文不能被当噪声"),
            (not module.is_noise(short_body, set()), "正文里的“第X条”不能被误删"),
        ]
        for ok, why in checks:
            verdict = bool(ok)
            assert verdict, why

    def _heading():
        chapter = {"text": "第一章 逻辑论证之归因论证", "h": 0.03, "w": 0.5}
        assert module.heading_level(chapter, 0.02, 1.0) == 1, "章标题应为 1 级"
        long_body = {"text": "这是一段很长的正文，写了很多字，不应该是标题也不该被当成标题处理", "h": 0.02, "w": 0.9}
        assert module.heading_level(long_body, 0.02, 1.0) == 0, "长正文不能判成标题"

    check("vision2md 噪声过滤", _noise)
    check("vision2md 标题判定", _heading)


# ---------- 3. CLI 冒烟 ----------
print("\nCLI 冒烟（都必须报错退出）")
check("不存在的卡片不能静默改",
      lambda: run_cli(["pipeline/review.py", "grade", "根本不存在的卡片xyz", "3"]))
check("非法评分要拒绝", lambda: run_cli(["pipeline/review.py", "grade", "另有他因", "9"]))
check("不存在的考点不能验证",
      lambda: run_cli(["pipeline/mistake.py", "verify", "根本不存在的考点xyz", "--kind", "变式", "--result", "对"]))
check("非法错因要拒绝",
      lambda: run_cli(["pipeline/mistake.py", "new", "--module", "判断推理", "--stem", "x", "--cause", "瞎写"]))
check("非法模块要拒绝",
      lambda: run_cli(["pipeline/split.py", "--module", "判断推理", "--lecture", "X", "/dev/null"]))
check("缺页面文件要拒绝",
      lambda: run_cli(["pipeline/split.py", "--module", "行测/判断推理", "--lecture", "X", "/tmp/根本不存在.md"]))
check("shell 未知参数要拒绝", lambda: run_cli(["tools/kaogong-ocr.sh", "--bogus", "x.jpg"]))


# ---------- 汇总 ----------
print("\n" + "─" * 40)
if FAILURES:
    print("  %d/%d 项失败：" % (len(FAILURES), CHECKS[0]))
    for name, why in FAILURES:
        print("    ✗ %s — %s" % (name, why))
    sys.exit(1)
print("  全部通过：%d 项" % CHECKS[0])
