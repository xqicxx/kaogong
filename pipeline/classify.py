#!/usr/bin/env python3
"""页面分类与题目切分 —— 纯函数，不做 I/O。

试卷/错题本的照片进来以后要分三路：
    讲义页  → 知识点（骨架 + 卡片）
    题目页  → 逐题判对错 → 错的进错题本，对的丢弃
    答案页  → 标准答案表，用来判对错

分类只在**黑字层**的文本上做；红笔层不参与分类（那是判对错与错因的依据）。
"""
import re

HANDOUT = "handout"
QUESTIONS = "questions"
ANSWERS = "answers"
UNKNOWN = "unknown"

# 讲义的结构标记：第X章 / 第X节 / 一、 / （一） / 1.1
HANDOUT_MARK = re.compile(r"^\s*(?:第[一二三四五六七八九十百\d]+[章节篇讲]|[一二三四五六七八九十]+[、.]|（[一二三四五六七八九十]+）|\d+\.\d+)\s*\S")
# 题号：1. / 1、 / 1． / 1) / 第1题
QUESTION_START = re.compile(r"^\s*(?:第\s*(\d{1,3})\s*题|(\d{1,3})\s*[.、．)）])(?=\s*\S)")
# 选项行：A. / A、 / A． / A)
OPTION_LINE = re.compile(r"^\s*([A-Da-dＡ-Ｄａ-ｄ])\s*[.、．)）](?=\s*\S)")
# 同一行排四个选项（行测常态）时用来切开：只切位置，标记文本留给 OPTION_LINE 剥
OPTION_SPLIT = re.compile(r"(?:^|\s)(?=[A-Da-dＡ-Ｄａ-ｄ]\s*[.、．)）])")
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


def safe_int(value, default=0):
    """把题号/页码转成 int；全角数字先归一，转不动就退回默认值。

    这类值来自 OCR 文本，不该因为一个字符不认识就整页失败。
    """
    try:
        return int(str(value).translate(FULLWIDTH_DIGITS).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def _lines(text):
    return [line.rstrip() for line in (text or "").splitlines()]


def classify_page(text):
    """返回 handout / questions / answers / unknown。

    判据按优先级：答案标语 → 题目结构 → 讲义结构 → unknown。
    题目要求「有题号」且「有选项」，只看题号会把讲义里的编号清单误判成题目。
    """
    lines = _lines(text)
    body = "\n".join(lines)
    question_count = sum(1 for line in lines if QUESTION_START.match(line))
    option_count = sum(1 for line in lines if OPTION_LINE.match(line))
    handout_count = sum(1 for line in lines if HANDOUT_MARK.match(line))

    if ANSWER_MARK.search(body[:400]) and question_count <= max(2, option_count):
        return ANSWERS
    # 纯答案表：短行里大量「数字+字母」
    if len(parse_answer_key(body)) >= 3 and option_count == 0:
        return ANSWERS
    if question_count >= 2 and option_count >= 2:
        return QUESTIONS
    if handout_count >= 2 and question_count == 0:
        return HANDOUT
    if handout_count >= 1 and option_count == 0 and question_count <= 1:
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
    import argparse
    import json
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from pipeline import answers as judge_module

    parser = argparse.ArgumentParser(description="页面分类 + 逐题判对错（只读，不改 vault）")
    parser.add_argument("page", help="页面 markdown")
    parser.add_argument("--key", help="标准答案页 markdown")
    parser.add_argument("--student", default="", help="学生作答，形如 1:B,2:D")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    text = pathlib.Path(args.page).read_text(encoding="utf-8")
    page_type = classify_page(text)
    questions = split_questions(text) if page_type in (QUESTIONS, UNKNOWN) else []
    answer_key = parse_answer_key(pathlib.Path(args.key).read_text(encoding="utf-8")) if args.key else {}
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
