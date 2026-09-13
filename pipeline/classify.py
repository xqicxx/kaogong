#!/usr/bin/env python3
"""页面分类与题目切分 —— 纯函数，不做 I/O。

试卷/错题本的照片进来以后要分三路：
    讲义页  → 知识点（骨架 + 卡片）
    题目页  → 逐题判对错 → 错的进错题本，对的丢弃
    答案页  → 标准答案表，用来判对错

分类只在**黑字层**的文本上做；红笔层不参与分类（那是判对错与错因的依据）。
"""
# 包内相对导入需要先把仓库根放进 sys.path —— 直接 `python3 pipeline/classify.py`
# 也能跑。以前这行在 main() 里，于是模块顶层的库函数没法用领域异常，
# 只好抛 SystemExit（库函数抛它违反项目约定：只有 main() 翻退出码）。
import argparse
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import answers as judge_module  # type: ignore[import]  # noqa: E402
from pipeline.errors import KaogongError  # type: ignore[import]  # noqa: E402

HANDOUT = "handout"
QUESTIONS = "questions"
ANSWERS = "answers"
UNKNOWN = "unknown"

# 讲义的结构标记：第X章 / 第X节 / 一、 / （一） / 1.1
HANDOUT_MARK = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百\d]+[章节篇讲]"     # 第二章 / 第3节
    r"|[一二三四五六七八九十]+[、.]"                        # 一、 / 三.
    r"|（[一二三四五六七八九十]+）"                          # （一）
    r"|\d+\.\d+"                                         # 1.1
    r"|\d+[.、]\S)\s*\S"                                # 1.含义（讲义小标题）
)
# 题号：1. / 1、 / 1． / 1) / 第1题
QUESTION_START = re.compile(r"^\s*(?:第\s*(\d{1,3})\s*题|(\d{1,3})\s*[.、．)）])(?=\s*\S)")
# 选项行：A. / A、 / A． / A)
OPTION_LINE = re.compile(r"^\s*([A-Da-dＡ-Ｄａ-ｄ])\s*[.、．)）](?=\s*\S)")
# 同一行排四个选项（行测常态）时用来切开：只切位置，标记文本留给 OPTION_LINE 剥
OPTION_SPLIT = re.compile(r"(?:^|\s)(?=[A-Da-dＡ-Ｄａ-ｄ]\s*[.、．)）])")
# 页脚页码（第1页 / 1 / 一）—— 属于已知噪声，不是手写批注
PAGE_NUM = re.compile(r"^第?\s*[0-9０-９一二三四五六七八九十]{1,4}\s*[页頁]?$")
# 答案标语
ANSWER_MARK = re.compile(r"参考?答案|答案与解析|答案：|解析：|标准答案")
# 答案表：1-5 BCDAB / 1~5 BCDAB
ANSWER_RANGE = re.compile(r"([\d０-９]{1,3})\s*[-–~—－〜]\s*([\d０-９]{1,3})\s*[：:、.．]?\s*([A-Da-dＡ-Ｄａ-ｄ]{2,})")
# 答案表：1. B / 1、B / 1B
ANSWER_SINGLE = re.compile(r"(?:^|[\s,，、;；])([\d０-９]{1,3})\s*[.、．)）]?\s*([A-Da-dＡ-Ｄａ-ｄ]{1,4})(?![A-Za-z\d])")


# OCR 读中文材料时经常把题号/选项给成全角（１２３ / ＡＢＣＤ），
# 直接匹配会漏掉整页，所以数字与字母都做一次全角→半角归一
FULLWIDTH_DIGITS = {ord("０") + offset: ord("0") + offset for offset in range(10)}
FULLWIDTH_LETTERS = {ord("Ａ") + offset: ord("A") + offset for offset in range(26)}
FULLWIDTH_MAP = {**FULLWIDTH_DIGITS, **FULLWIDTH_LETTERS}


def narrow(text):
    """全角数字/字母 → 半角。"""
    return str(text).translate(FULLWIDTH_MAP)


def safe_float(value, default=0.0):
    """把 OCR 给的置信度转成 float；转不动就退回默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError, AttributeError):
        return default


def safe_int(value, default=0):
    """把题号/页码转成 int；全角数字先归一，转不动就退回默认值。

    这类值来自 OCR 文本，不该因为一个字符不认识就整页失败。
    """
    try:
        return int(str(value).translate(FULLWIDTH_DIGITS).strip())
    except (TypeError, ValueError, AttributeError):
        return default


# 流水线产出的是 markdown，标题带 ## 前缀；分类只看正文结构，
# 匹配前先把 # 剥掉（否则「## 二、xxx」一条也认不出来 —— 拿真讲义跑才暴露的）
HEADING_PREFIX = re.compile(r"^\s*#{1,6}\s*")


def _lines(text):
    return [line.rstrip() for line in (text or "").splitlines()]


def _plain(line):
    """剥掉 markdown 标题前缀，便于用纯文本模式匹配。"""
    return HEADING_PREFIX.sub("", line)


def classify_page(text):
    """返回 handout / questions / answers / unknown。

    判据按优先级：答案标语 → 题目结构 → 讲义结构 → unknown。
    题目要求「有题号」且「有选项」，只看题号会把讲义里的编号清单误判成题目。
    """
    lines = _lines(text)
    body = "\n".join(lines)
    plain = [_plain(line) for line in lines]
    question_count = sum(1 for line in plain if QUESTION_START.match(line))
    option_count = sum(1 for line in plain if OPTION_LINE.match(line))
    handout_count = sum(1 for line in plain if HANDOUT_MARK.match(line))
    # 「1.含义」和「1.甲持刀抢劫乙…」都是编号行，光看编号分不出来。
    # 讲义小标题短、题干长 —— 用长度当第二判据，
    # 免得把「选项 OCR 丢了」的题目页错判成讲义（那批页面本该报 unknown 让用户确认）
    numbered = [line.strip() for line in plain if QUESTION_START.match(line) or HANDOUT_MARK.match(line)]
    heading_like = sum(1 for line in numbered if len(line) <= 20)

    if ANSWER_MARK.search(body[:400]) and question_count <= max(2, option_count):
        return ANSWERS
    # 纯答案表：短行里大量「数字+字母」
    if len(parse_answer_key(body)) >= 3 and option_count == 0:
        return ANSWERS
    # 关键判据是「有没有选项」——
    # 讲义的小标题（「1.含义」「2.基本内容」）和题号（「1. 甲伤害乙…」）长得一模一样，
    # 只看编号会把讲义误判成题目；而题目一定有 A/B/C/D 选项，讲义没有。
    if option_count >= 2 and question_count >= 2:
        return QUESTIONS
    # 讲义：编号行必须**全是**短标题（真讲义的层级标题都短）。
    # 只要有一条编号行长到像题干，就可能是「选项丢了」的题目页 →
    # 报 unknown 交给用户确认，不硬塞（本文件的硬规矩）。
    # ⚠️ 别只要求「有一条短标题」—— 题目里的短题干（「2.下列说法正确的是」）就能满足。
    all_headings = bool(numbered) and heading_like == len(numbered)
    if handout_count >= 2 and all_headings:
        return HANDOUT
    if handout_count >= 1 and option_count == 0 and all_headings:
        return HANDOUT
    return UNKNOWN


def split_questions(text):
    """按题号切分，返回 [{number, stem, options, raw, start_line}]。

    题干与选项分开：判对错要看选项，做卡片要题干。
    """
    questions = []
    current = None
    for index, line in enumerate(_lines(text), 1):
        matched = QUESTION_START.match(line)
        if matched:
            if current:
                questions.append(current)
            number = matched.group(1) or matched.group(2)
            stripped = QUESTION_START.sub("", line, count=1).strip()
            current = {"number": safe_int(number), "stem": stripped, "options": [],
                       "extra": [], "raw": [line], "start_line": index}
            continue
        if current is None:
            continue
        current["raw"].append(line)
        if OPTION_LINE.match(line):
            # 四个选项排在同一行是常态，按标记全部切开
            for piece in OPTION_SPLIT.split(line.strip()):
                text = OPTION_LINE.sub("", piece.strip(), count=1).strip()
                if text:
                    current["options"].append(text)
        elif line.strip():
            if current["options"]:
                # 选项之后的非选项行，多半是上一选项的续行
                current["options"][-1] += line.strip()
            else:
                current["extra"].append(line.strip())
                current["stem"] += line.strip()
    if current:
        questions.append(current)
    return questions


def parse_answer_key(text):
    """解析标准答案，返回 {题号: 'B'}。

    支持两种常见写法：
        连续表   1-5 BCDAB    1~5 BCDAB
        逐题式   1. B         1、B        1B
    """
    answers = {}
    body = text or ""
    for start, end, letters in ANSWER_RANGE.findall(body):
        first, last = safe_int(start), safe_int(end)
        if last - first + 1 == len(letters) and last - first < 200:
            for offset, letter in enumerate(letters.upper()):
                answers[first + offset] = narrow(letter).upper()
    for line in _lines(body):
        if ANSWER_RANGE.search(line):
            continue                     # 这一行已经按连续表解过了
        for number, letter in ANSWER_SINGLE.findall(line):
            item = safe_int(number)
            if 1 <= item <= 999:
                answers.setdefault(item, narrow(letter).upper())
    return answers





def suspect_lines(page_json, max_chars=10, symbol_ratio=0.5):
    """从黑字层 OCR 结果里挑出「可能是手写批注」的行。

    为什么需要它：**黑笔和印刷体同色，颜色分不开**（红笔蓝笔可以靠 --marks 分离）。

    ⚠️ 别拿置信度当判据：Vision 对中文常给 0.5 这个整值，
    实测按 conf<=0.5 筛会捞回整页正文（26 条里 24 条是正常印刷字）。
    能区分的是「像不像正常文字」：极短，或符号占比过半 ——
    手写的叉、勾、圈划在 OCR 输出里就长这样（例如「开、四」那种碎片）。

    这份清单是「该去原图哪里看」的索引，**不是结论** —— 手写字最终还得看图。
    """
    import json as _json
    import pathlib as _pathlib
    try:
        data = _json.loads(_pathlib.Path(page_json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KaogongError("读不到 OCR 结果 %s：%s" % (page_json, exc))
    suspects = []
    for line in data.get("lines", []):
        text = (line.get("text") or "").strip()
        if not text or len(text) > max_chars:
            continue
        if PAGE_NUM.match(text):
            continue                       # 页码是已知噪声，不是手写
        readable = sum(1 for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
        # 两条判据：极短（印刷正文不会只有三四个字），或符号占比过半
        if len(text) <= 4 or readable / len(text) < symbol_ratio:
            suspects.append({"text": text, "conf": safe_float(line.get("conf"), 1.0),
                             "y": line.get("y", 0)})
    return suspects


def parse_student_answers(text):
    """解析手工给的作答，形如 1:B,2:D,3:A（读红笔层之后填入）。"""
    answers = {}
    for chunk in (text or "").replace(chr(0xFF1B), ",").replace(chr(0xFF0C), ",").split(","):
        if ":" not in chunk and chr(0xFF1A) not in chunk:
            continue
        number, _, letter = chunk.replace(chr(0xFF1A), ":").partition(":")
        answers[safe_int(number)] = letter.strip()
    return answers


def main():
    parser = argparse.ArgumentParser(description="页面分类 + 逐题判对错（只读，不改 vault）")
    parser.add_argument("page", nargs="?", help="页面 markdown（--suspects 时可不给）")
    parser.add_argument("--key", help="标准答案页 markdown")
    parser.add_argument("--student", default="", help="学生作答，形如 1:B,2:D")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--suspects", help="黑字层 OCR 的 json，列出可疑的手写行")
    args = parser.parse_args()
    # 领域异常统一在这里翻成退出码 —— 与 review / mistake 两个 CLI 一致。
    # 少了这一层，KaogongError 也是以 traceback 的形式甩给用户，那就白抛了。
    try:
        run(args)
    except KaogongError as exc:
        print("  ✗ %s" % exc, file=sys.stderr)
        sys.exit(1)


def run(args):
    """执行一次分类/判对错。参数解析留在 main()，这一层能单独调用与测试。"""
    if args.suspects:
        # 只列可疑行，不需要 page
        found = suspect_lines(args.suspects)
        if args.json:
            print(json.dumps(found, ensure_ascii=False, indent=1))
        else:
            print("  可疑行 %d 条（手写批注的候选，需对着原图核）：" % len(found))
            for item in found[:20]:
                print("    conf %.2f  %s" % (item["conf"], item["text"][:40]))
        return

    if not args.page:
        # 以前不给页面会一路走到 Path(None).read_text() → TypeError traceback。
        # 不能用 parser.error：parser 是 main() 的局部变量，run() 里拿不到。
        raise KaogongError("要一个页面文件（或用 --suspects 指向 OCR 的 json）")
    try:
        text = pathlib.Path(args.page).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise KaogongError("读不到页面 %s：%s" % (args.page, exc))
    page_type = classify_page(text)
    questions = split_questions(text) if page_type in (QUESTIONS, UNKNOWN) else []
    answer_key = {}
    if args.key:
        try:
            answer_key = parse_answer_key(pathlib.Path(args.key).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise KaogongError("读不到答案页 %s：%s" % (args.key, exc))
    student = parse_student_answers(args.student)
    results, summary = judge_module.judge_all(questions, answer_key, student)

    if args.json:
        print(json.dumps({"page_type": page_type, "summary": summary, "results": results},
                         ensure_ascii=False, indent=1))
        return
    print("  页面类型: %s" % page_type)
    if not questions:
        print("  （不是题目页，无需判对错）")
        return
    print("  题目 %d 道：正确 %d / 错误 %d / 待定 %d"
          % (summary["total"], summary["right"], summary["wrong"], summary["unknown"]))
    wrong = [str(item["number"]) for item in results if item["verdict"] == judge_module.WRONG]
    pending = [str(item["number"]) for item in results if item["verdict"] == judge_module.UNKNOWN]
    if wrong:
        print("  错题：第 %s 题 → 进错题本" % "、".join(wrong))
    if pending:
        print("  待定：第 %s 题（作答或答案缺失，需要你确认）" % "、".join(pending))
    if not answer_key:
        print("  提示：没给 --key（标准答案页），无法判对错")


if __name__ == "__main__":
    main()
