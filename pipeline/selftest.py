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

from pipeline import mistake, review, split, vault  # noqa: E402


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
    result = subprocess.run([sys.executable] + args, capture_output=True, text=True,
                            cwd=ROOT, timeout=60)
    if result.returncode == 0:
        raise AssertionError("本该非零退出却成功了：%s" % " ".join(args))
    return (result.stdout or "") + (result.stderr or "")


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
    state = review.schedule(None, 3, today)
    for grade in (1, 2, 3, 4):
        nxt = review.schedule(state, grade, state["due"])
        assert 1 <= nxt["d"] <= 10, "难度必须夹在 [1,10]，得到 %s" % nxt["d"]
        assert nxt["due"] > state["due"], "下次到期必须往后走"
        assert nxt["reps"] == state["reps"] + 1
    assert review.r_of(0, 10) > review.r_of(100, 10), "R 必须随时间下降"


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


check("vault 边界", _vault_edges)
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
