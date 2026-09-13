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
import tempfile
from datetime import date, timedelta
from pathlib import Path

# 约定：
#   · 用例函数定义在 main() 内部（共享 CHECKS/FAILURES 计数器），调用紧跟在定义之后
#   · 断言的失败消息先算成变量再 assert（下面那种 "msg" % (a, b) 的写法会被静态检查
#     误读成 assert 的元组字面量）
#   · 检查数少于 MIN_CHECKS 一律算失败 —— 套件曾被缩进错误静默截断过
#
# 这套自检靠 assert 表达不变量，而 python -O 会把 assert 全剥掉 —— 那就成了空跑
if not __debug__:
    print("自检依赖 assert，不能用 python -O 运行", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import (classify, discriminate, errors, fsrs, interleave, mastery,  # noqa: E402
                      mistake, mistakes, panel, review, split, vault)


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
# 套件曾被缩进错误静默截断：只跑了 4 项却退出码 0。
# 这道护栏让「检查数明显变少」直接算失败。
MIN_CHECKS = 30

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
def main():
    """跑完全部检查并汇总。包在 main 里：导入这个模块不该执行子进程。"""
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

        # 回归：遗忘绝不能让稳定度变大。
        # 原始 4.5 公式在低稳定度时会算出 S_f > S，"又忘了"反而把间隔拉长 ——
        # 用官方 py-fsrs 函数级对照时发现（180 组网格 8 组病态）。
        today = date(2026, 9, 1)
        for s in (0.3, 0.5, 1.0, 3.7, 12.0):
            for elapsed in (1, 2, 3, 30):
                state = {"s": s, "d": 9.5, "reps": 2, "last": today}
                after = fsrs.schedule(state, 1, today + timedelta(days=elapsed))
                msg = "忘掉之后稳定度不能变大：S=%.2f 隔 %d 天忘掉 → %.3f" % (s, elapsed, after["s"])
                assert after["s"] <= s + 1e-9, msg


    def _mastery_gates():
        today = date(2026, 9, 13)
        for kind in ("迁移", "保持"):
            # 必须是领域异常：以前抛 ValueError，CLI 的 except KaogongError 抓不到，
            # 用户看到的是 traceback 而不是一句人话
            expect_raises(errors.InvalidState,
                          lambda k=kind: mistake.advance({"状态": "未掌握"}, k, True, today),
                          "未掌握不能直接考 %s 关" % kind)
        # 领域层不许把 ValueError 漏出去
        for bad in (lambda: mistake.advance({"状态": "未掌握"}, "瞎写的关", True, today),
                    lambda: fsrs.schedule(None, 9)):
            try:
                bad()
                raise AssertionError("本该报错")
            except errors.KaogongError:
                pass
            except Exception as exc:
                raise AssertionError("领域层漏了 %s，CLI 抓不到，用户会看到 traceback"
                                     % type(exc).__name__)
        state = mistake.advance({"状态": "未掌握"}, "变式", True, today)
        expect_raises(errors.InvalidState, lambda: mistake.advance(state, "迁移", True, today),
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
        msg = "应当抽到 2 条，得到 %d：%r" % (len(items), [x[2] for x in items])
        assert len(items) == 2, msg
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
        msg = "匹配结果不对：%r" % (match,)
        assert match and match[0] == "老页.md", msg
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
        msg = "页码相同的那页应当被捞回来，实际 %r" % (hit,)
        assert hit and hit[0] == "老的.md", msg




    def _split_smoke():
        """光 import 不够。

        split.py 曾因为一个三元组解包写错而完全跑不起来，
        而当时的门禁只查语法和导入，照样全绿。所以这里必须真的执行一次。
        """
        import subprocess, sys, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        page = tmp / "样例-p001.md"
        page_text = chr(10).join([
            "---", "source: 样例", "page: 1", "---", "",
            "### 三种削弱（质疑）方式", "",
            "①另有他因：引入题干未提及的其他影响因素，降低原有因果关系的确定性。", "",
            "②因果倒置：颠倒原因与结果的先后顺序，直接否定题干因果关系，削弱力度极强。", "",
        ])
        page.write_text(page_text, encoding="utf-8")
        command = [sys.executable, "pipeline/split.py", "--module", "行测/判断推理",
                   "--lecture", "冒烟", "--source", "冒烟", "--dry", str(page)]
        result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT, timeout=60)
        output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 0, "split.py 跑不起来：%s" % output[-400:]
        assert "卡片" in output, "split.py 没输出卡片数：%s" % output[-200:]


    def _review_smoke():
        """复习链路的只读命令也要真跑（--dry，不推送）。"""
        import subprocess, sys
        for args in (["push", "--dry"], ["due"], ["stats"]):
            result = subprocess.run([sys.executable, "pipeline/review.py"] + args,
                                    capture_output=True, text=True, cwd=ROOT, timeout=60)
            msg = "review.py %s 失败：%s" % (args, (result.stderr or "")[-300:])
            assert result.returncode == 0, msg




    def _card_health():
        """坏卡必须能被认出来。

        背景：state_health / broken_cards 一度是死代码（load() 忘了填 health 字段），
        于是“字段坏了”的卡照样被当成新卡重新排期，真实复习历史被覆盖。
        这个用例同时钉住 state_health 与 load() 的字段连线。
        """
        import tempfile
        from pathlib import Path
        from pipeline import cards, vault
        good = "---\ntype: 考点\n到期: 2026-09-25\n稳定度: 12\n难度: 5\n复习次数: 1\n上次复习: 2026-09-13\n---\n\n正文\n"
        broken = "---\ntype: 考点\n到期: 不是日期\n稳定度: abc\n难度: 5\n---\n\n正文\n"
        fresh = "---\ntype: 考点\n状态: 未掌握\n---\n\n正文\n"
        tmp = Path(tempfile.mkdtemp())
        results = {}
        for name, text in (("好卡", good), ("坏卡", broken), ("新卡", fresh)):
            target = tmp / (name + ".md")
            target.write_text(text, encoding="utf-8")
            front_matter, _body, _raw = vault.read(target)
            results[name] = cards.state_health(front_matter)
        assert results["好卡"] == "ok", results
        assert results["坏卡"] == "broken", "坏卡没被认出来：%r" % results
        assert results["新卡"] == "none", results
        # load() 必须真的把 health 带上，否则 broken_cards() 永远是空
        target = tmp / "坏卡.md"
        loaded = cards.load(target)
        assert loaded.get("health") == "broken", "load() 没带 health：%r" % loaded.get("health")


    check("卡片健康度", _card_health)


    PAGE_QUESTIONS = chr(10).join([
        "1. 甲伤害乙，乙冠心病发作死亡。甲的行为与死亡结果之间：",
        "A. 无因果关系    B. 有因果关系",
        "C. 视情况而定    D. 无法判断",
        "",
        "2. 关于介入因素，下列说法正确的是：",
        "A. 介入因素必然中断因果关系",
        "B. 介入因素异常且独立引起结果时中断",
    ])
    PAGE_HANDOUT = chr(10).join([
        "第一章 逻辑论证之归因论证",
        "一、归因论证定义",
        "归因论证的核心，是对已发生的既定事实进行原因探究。",
        "（一）三类常规结构通用方法",
        "本部分内容适用于对比实验归因。",
    ])
    PAGE_ANSWERS = chr(10).join([
        "参考答案与解析",
        "1. B",
        "2. D",
        "3. ABD",
    ])


    def _classify_pages():
        from pipeline import classify
        assert classify.classify_page(PAGE_QUESTIONS) == classify.QUESTIONS, "题目页没认出来"
        assert classify.classify_page(PAGE_HANDOUT) == classify.HANDOUT, "讲义页没认出来"
        assert classify.classify_page(PAGE_ANSWERS) == classify.ANSWERS, "答案页没认出来"
        assert classify.classify_page("随便一段话") == classify.UNKNOWN, "乱内容应当判 unknown"
        # 全角是 OCR 的常态，不能因此漏整页
        fullwidth = "１． 题干一" + chr(10) + "Ａ． 选项甲" + chr(10) + "２． 题干二" + chr(10) + "Ｂ． 选项乙"
        assert classify.classify_page(fullwidth) == classify.QUESTIONS, "全角题目页没认出来"


    def _split_questions():
        from pipeline import classify
        questions = classify.split_questions(PAGE_QUESTIONS)
        assert len(questions) == 2, "应当切出 2 道题，得到 %d" % len(questions)
        assert questions[0]["number"] == 1 and questions[1]["number"] == 2
        # 题干不能丢字：题号模式里多一个 \S 就会把首字吃掉
        assert questions[0]["stem"].startswith("甲伤害乙"), "题干首字被吃了：%r" % questions[0]["stem"][:6]
        # 同一行四个选项也要全抽出来
        assert len(questions[0]["options"]) == 4, "同行选项没抽全：%r" % questions[0]["options"]
        assert questions[1]["options"][1].startswith("介入因素异常"), questions[1]["options"]


    def _answer_key_formats():
        from pipeline import classify
        expected = {1: "B", 2: "C", 3: "D", 4: "A", 5: "B"}
        for text in ("1-5 BCDAB", "1~5 BCDAB", "1—5 BCDAB", "１－５ ＢＣＤＡＢ"):
            msg = "%r 解析不对：%s" % (text, classify.parse_answer_key(text))
            assert classify.parse_answer_key(text) == expected, msg
        single = classify.parse_answer_key("1. B" + chr(10) + "2．C" + chr(10) + "3、D")
        assert single == {1: "B", 2: "C", 3: "D"}, single
        assert classify.parse_answer_key("") == {}, "空文本该返回空表"


    def _judge_answers():
        from pipeline import answers
        cases = [
            ("B", "B", answers.RIGHT),
            ("b", "Ｂ", answers.RIGHT),          # 全角/大小写不算错
            ("选 B", "B", answers.RIGHT),        # 手写常见噪声
            ("DBA", "ABD", answers.RIGHT),        # 多选顺序无关
            ("AB", "ABD", answers.WRONG),         # 少选算错
            ("B", "D", answers.WRONG),
            ("√", "正确", answers.RIGHT),         # 判断题
            ("×", "错误", answers.RIGHT),
            ("", "B", answers.UNKNOWN),          # 没作答 → 待定，不能猜
            ("不会", "B", answers.UNKNOWN),
            ("B", "", answers.UNKNOWN),
        ]
        for student, correct, want in cases:
            got = answers.judge(student, correct)
            msg = "judge(%r, %r) = %s，应为 %s" % (student, correct, got, want)
            assert got == want, msg


    def _suspect_lines_test():
        """黑笔分不开颜色，只能靠「像不像正常文字」找手写碎片。"""
        import json as _json
        import pathlib as _pathlib
        import tempfile
        from pipeline import classify
        page = {"lines": [
            {"text": "①排除他因：剔除其他潜在影响因素，强化题干因果关系的唯一性。", "conf": 0.5, "y": 0.2},
            {"text": "开、四", "conf": 0.5, "y": 0.85},
            {"text": "第2页", "conf": 0.5, "y": 0.95},
        ]}
        target = _pathlib.Path(tempfile.mkdtemp()) / "page.json"
        try:
            target.write_text(_json.dumps(page, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            raise AssertionError("写临时 OCR 结果失败：%s" % exc)
        path = str(target)
        texts = [item["text"] for item in classify.suspect_lines(path)]
        assert "开、四" in texts, "短碎片应当被列为可疑：%r" % texts
        assert not any("排除他因" in text for text in texts), "长正文不该进可疑清单：%r" % texts
        assert not any("第2页" in text for text in texts), "页码是有意义文字，不该进可疑清单：%r" % texts

    check("可疑手写行", _suspect_lines_test)
    check("页面分类", _classify_pages)
    check("题目切分", _split_questions)
    check("答案表解析", _answer_key_formats)
    check("判对错", _judge_answers)
    check("split 冒烟", _split_smoke)
    check("复习链路冒烟", _review_smoke)
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
        except errors.KaogongError:
            pass
        else:
            raise AssertionError("非法评分要抛 KaogongError（不是 ValueError —— CLI 抓不到它）")


    check("领域异常", _domain_errors)
    check("Markdown 转义", _md_escape)
    check("排期边界", _schedule_edges)


    def _classification_guards():
        # 编号很长却没有选项 —— 选项 OCR 丢失的题目页就是这个形态，
        # 必须报 unknown 让用户确认，不能硬塞成讲义（本文件的硬规矩）
        # 注意：点号后面**不能有空格** —— HANDOUT_MARK 要求 数字+点+紧跟非空白。
        # 写成「1. 甲…」就匹配不上，这条用例会空转成假测试（真踩过）
        no_options = ("1.甲持刀抢劫乙，乙趁机夺刀将甲刺伤，乙的行为如何认定\n"
                      "2.下列关于犯罪未遂的说法正确的是\n")
        assert classify.classify_page(no_options) == classify.UNKNOWN, \
            "编号长又没选项的页面不能判成讲义"
        # 讲义那种短小标题仍要认出来（这几行取自真实讲义页）
        handout = "## 二、刑法的基本原则\n### （一）罪刑法定原则\n### 1.含义\n### 2.基本内容\n"
        assert classify.classify_page(handout) == classify.HANDOUT, "讲义页必须认成 handout"
        # 有选项的才是题目
        questions = ("1. 下列说法正确的是\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n"
                     "2. 下列说法错误的是\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n")
        assert classify.classify_page(questions) == classify.QUESTIONS, "有选项的要认成题目"


    check("分类判据", _classification_guards)


    def _new_mechanisms():
        # 交错练习：相邻两题必须不同型 —— 这正是逼出判别的那一步
        items = [{"题型": "判断推理", "标题": "A"}, {"题型": "判断推理", "标题": "B"},
                 {"题型": "常识判断", "标题": "C"}]
        picked, why = interleave.pick(items, limit=3)
        assert len(picked) == 3, "三题够挑满"
        assert interleave.is_interleaved(picked), "交错后相邻题不能同型"
        # 只有一种题型时不该硬凑 —— 那会退化成分块练习，正是要避免的
        single, why = interleave.pick([{"题型": "判断推理", "标题": "A"}], limit=3)
        assert single == [] and why, "只有一种题型时交错不成立，要给出理由"

        # 区分度：一路过的要剔；太难的留着提醒（剔了就再也看不见）
        sample = [{"name": "一路过", "状态": "已掌握", "变式连胜": 6, "复习次数": 5},
                  {"name": "反复绊", "状态": "未掌握", "变式连胜": 0, "复习次数": 4},
                  {"name": "有对有错", "状态": "变式中", "变式连胜": 1, "复习次数": 2}]
        verdict = discriminate.summarize(sample)
        assert [s["name"] for s in verdict["drop"]] == ["一路过"], "一路过的题要剔出验证集"
        kept = [s["name"] for s in verdict["keep"]]
        assert "反复绊" in kept and "有对有错" in kept, "太难的不剔，有鉴别力的留"

        # 面板：正文不带 front-matter（曾经和 vault.write 自己包的那层叠成两道）
        body = panel.build([("卡A", "判断推理", "逾期 2 天")], [], [], [], [])
        assert not body.lstrip().startswith("---"), "面板正文不能自带 front-matter"
        assert "# 复习面板" in body and "卡A" in body
        assert "今天到期" in body and "无鉴别力" in body

        # 交错契约：题量不均衡时也不能连出同型（曾经轮转会退化成 AAA）
        lopsided = ([{"题型": "判断推理", "标题": "A%d" % i} for i in range(5)]
                    + [{"题型": "常识判断", "标题": "B"}])
        picked2, _ = interleave.pick(lopsided, limit=6)
        assert interleave.is_interleaved(picked2), "某个题型取完后不能连出同型（那就成块练习了）"

        # 坏笔记要跳过并上报，不能让整批读崩掉（以前只 catch OSError，
        # UnicodeDecodeError 会把整天的推送搞挂）
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "坏.md"
            bad.write_bytes(b"\xff\xfe bad")
            original, mistakes.paths = mistakes.paths, lambda: [bad]
            try:
                got, skipped = mistakes.read_all()
            finally:
                mistakes.paths = original
            assert got == [] and skipped == [bad], "坏笔记要跳过并原样上报"

        # 「勉强」= 当天重来（PLAN 2.2 ③ successive relearning）
        planned, redo = review.plan_for(None, 2, today=date(2026, 9, 13))
        assert redo and planned["due"] == date(2026, 9, 13), "评「勉强」必须留在今天重来"
        planned, redo = review.plan_for(None, 3, today=date(2026, 9, 13))
        assert not redo and planned["due"] > date(2026, 9, 13), "评「对了」不该留在今天"


    check("新机制", _new_mechanisms)


    def _link_target_helper():
        # 双链目标提取。以前这个函数**根本不存在** —— 注释写了一年，代码从没跑通过：
        # 考点目录里已有同名卡片且没加 --force 时，split.py 直接 NameError 崩掉。
        assert split._link_target('[[判断推理-加强]]') == '判断推理-加强'
        assert split._link_target('"[[含有 空格的名字]]"') == '含有 空格的名字'
        # 空/缺失都要给空串，不能抛 —— 调用方靠它判「没写所属小节」
        for empty in ('', None, '没有双链'):
            msg = "%r 应返回空串" % (empty,)
            assert split._link_target(empty) == '', msg
        # 必须比双链目标而不是子串：「判断」会命中「[[判断推理-加强]]」，
        # 那样不同讲义的卡片会被当成同一张、静默跳过
        assert split._link_target('[[判断推理-加强]]') != '判断'


    check("双链目标", _link_target_helper)


    def _cli_error_boundary():
        # CLI 的错误边界：领域错误要以「✗ 一句话」出现，不能是 traceback。
        # 这条覆盖了整轮修的东西：库函数抛 KaogongError、只有 main() 翻退出码。
        cases = ((["pipeline/classify.py"], "要一个页面文件"),
                 (["pipeline/classify.py", "--suspects", "/tmp/没有这个文件.json"], "读不到 OCR 结果"),
                 (["pipeline/review.py", "grade", "根本没有这张卡xyz", "1"], "✗"),
                 (["pipeline/mistake.py", "list", "--stage", "瞎写的状态"], "✗"))
        for argv, want in cases:
            done = subprocess.run([sys.executable] + argv, cwd=str(ROOT),
                                  capture_output=True, text=True)
            blob = done.stdout + done.stderr
            msg_code = "%s 应以退出码 1 结束，实际 %d" % (argv, done.returncode)
            assert done.returncode == 1, msg_code
            msg_want = "%s 的错误信息里没有 %r：%s" % (argv, want, blob[:120])
            assert want in blob, msg_want
            msg_trace = "%s 漏成了 traceback：%s" % (argv, blob[:160])
            assert "Traceback" not in blob, msg_trace


    check("CLI 错误边界", _cli_error_boundary)


    def _verify_accepts_both_kinds():
        # 冒烟测试抓到的：verify 只认考点卡，指向错题卡会报「没找到卡片」。
        # 而错题卡同样带那四个字段，三关对哪类都成立 —— 更糟的是，
        # 错题卡的「状态」字段于是永远不变，`list --stage 变式中` 变成死筛选。
        # 这条用例不依赖库里有什么卡：只验找不到时的行为。
        missing = "根本不存在的卡xyz"
        expect_raises(errors.CardNotFound, lambda: mistake._find_any(missing),
                      "找不到时要抛 CardNotFound")
        try:
            mistake._find_any(missing)
        except errors.CardNotFound as exc:
            text = str(exc)
            assert "考点" in text, "找不到时的提示要提到考点，实际：%s" % text
            assert "错题" in text, "找不到时的提示要提到错题，实际：%s" % text


    check("verify 认两类卡", _verify_accepts_both_kinds)


    def _ocr_layout_signals():
        if vision2md is None:
            return
        # ── 页码必须取自文件名 ──
        # shell glob 是字典序：jc2-p10 排在 jc2-p8 前面，
        # 于是「输入里的第几张」不是书上的页码，卡片来源会全错。
        assert vision2md.page_from_name("jc2-p10.json") == 10
        assert vision2md.page_from_name("讲义-p005.json") == 5
        # 裸 197-abc 落不到任何规则 → 0（宁可没有页码，也不猜错）
        assert vision2md.page_from_name("197-abc.json") == 0
        # 手机图的 id 不能被当页码（老写法会取前三位得到 197）
        assert vision2md.page_from_name("19743.json") == 0
        assert vision2md.page_from_name("19743-mark.json") == 0
        # 真实管线会给输入加序号前缀（kaogong-ocr.sh 把输入重命名成 "001-原名"）。
        # 不剥前缀的话 PAGE_PREFIX 会命中**序号**：实测 001-197-abc → 1、001-IMG_19743 → 1。
        # （最早只测裸名，所以没抓到 —— 这组用例就是补这个缺口。）
        assert vision2md.page_from_name("001-197-abc.json") == 197
        assert vision2md.page_from_name("002-198-def.json") == 198
        assert vision2md.page_from_name("001-IMG_19743.json") == 0
        assert vision2md.page_from_name("003-讲义-p005.json") == 5

        # ── 表格检测：判据与坐标都取自真页面 jc2-p10（刑法分则·单位犯罪那页）──
        # 表格行 = 左侧短标签 + 右侧长内容、y 区间重叠
        def row(text, x, y, w, h=0.02):
            return {"text": text, "x": x, "y": y, "w": w, "h": h, "conf": 1.0}

        table = [row("特征", 0.131, 0.215, 0.043), row("单位犯罪一般表现为…", 0.222, 0.208, 0.678),
                 row("形式", 0.131, 0.343, 0.043), row("可以是故意犯罪…", 0.224, 0.330, 0.674),
                 row("处罚制度", 0.113, 0.438, 0.079), row("双罚制：单位犯罪的…", 0.224, 0.427, 0.674)]
        is_table, rows = vision2md.detect_table(table)
        msg_table = "真表格页的坐标应判为表格，实际 %s（行 %s）" % (is_table, rows)
        assert is_table and rows >= 3, msg_table

        # 普通段落：左边界相同、宽度接近 → 不是表格
        prose = [row("犯罪主体是单位，即依法成立的公司。", 0.1, 0.10, 0.8),
                 row("单位犯罪是由单位的决策机构决定的。", 0.1, 0.13, 0.85),
                 row("单位犯罪以刑法明文规定为前提。", 0.1, 0.16, 0.7),
                 row("只有当刑法明确规定单位可以成为主体时。", 0.1, 0.19, 0.9)]
        is_table2, rows2 = vision2md.detect_table(prose)
        assert not is_table2, "普通段落不该判成表格，实际 %s 行" % rows2


    check("OCR 版面信号", _ocr_layout_signals)


    def _stage_resolution():
        # 用户输入的非法状态必须报错，不能静默当「未掌握」。
        # 踩过：校验写在过滤循环体里，库为空时循环一次都不跑 → 非法值被当成「0 道」。
        for bad in ("瞎写的", "已保持xyz"):
            expect_raises(errors.KaogongError,
                          lambda b=bad: mastery.resolve(b),
                          "非法状态 %r 必须拒绝" % bad)
        for alias, want in (("待巩固", "未掌握"), ("已保持", "已掌握"),
                            ("变式", "变式中"), ("未掌握", "未掌握")):
            msg = "%s 应解析成 %s" % (alias, want)
            assert mastery.resolve(alias) == want, msg


    check("状态解析", _stage_resolution)
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
            assert module.heading_level(chapter, 0.02) == 1, "章标题应为 1 级"
            long_body = {"text": "这是一段很长的正文，写了很多字，不应该是标题也不该被当成标题处理", "h": 0.02, "w": 0.9}
            assert module.heading_level(long_body, 0.02) == 0, "长正文不能判成标题"

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
    # 套件曾被缩进错误静默截断：只跑了 4 项却退出码 0。检查数明显变少就算失败。
    if CHECKS[0] < MIN_CHECKS:
        print("  ✗ 只跑了 %d 项检查（至少应有 %d 项）—— 套件可能被截断了"
              % (CHECKS[0], MIN_CHECKS), file=sys.stderr)
        sys.exit(1)
    print("  全部通过：%d 项" % CHECKS[0])


if __name__ == "__main__":
    main()

