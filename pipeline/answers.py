#!/usr/bin/env python3
"""学生作答 vs 标准答案 —— 纯函数，不做 I/O。

这一步刻意做成精确比对，不用向量相似度：答案是 A/B/C/D 或对错，
属于「相等就是相等」的场景；向量会把「同一话题的不同选项」算得很近
（B 和 D 常常是同一主题的不同侧重），拿它判对错就是选错了工具。

判不出来时返回 unknown，**绝不猜** —— 猜错的代价是复习了不该复习的、
跳过了真正不会的。
"""

RIGHT = "right"
WRONG = "wrong"
UNKNOWN = "unknown"

TRUE_WORDS = ("正确", "对", "是", "√", "✓", "T", "TRUE", "Y")
FALSE_WORDS = ("错误", "错", "否", "×", "✗", "F", "FALSE", "N")

FULLWIDTH = {ord("Ａ") + offset: ord("A") + offset for offset in range(26)}
FULLWIDTH.update({ord("ａ") + offset: ord("a") + offset for offset in range(26)})
FULLWIDTH.update({ord("０") + offset: ord("0") + offset for offset in range(10)})

DROP = " \t　,，、;；:：.。" + chr(39) + chr(0x201C) + chr(0x201D) + "（）()[]【】"


def normalize(text):
    """归一：全角→半角、去空白与标点、字母大写、判断题统一成 T/F。

    学生手写的答案常带噪声（「选 B」「B．」「答案是B」），归一后才好比。
    """
    if text is None:
        return ""
    body = str(text).translate(FULLWIDTH)
    body = "".join(ch for ch in body if ch not in DROP)
    upper = body.upper()
    for word in TRUE_WORDS:
        if upper == word:
            return "T"
    for word in FALSE_WORDS:
        if upper == word:
            return "F"
    # 只留 ASCII 字母数字：中文 isalnum() 也为真，"选 B" 会被归一成「选B」而判错。
    # 判断题的「正确/错误」在上面按词表处理过了，这里剩下的只应是选项字母或数字。
    return "".join(ch for ch in upper if ch.isascii() and ch.isalnum())


def judge(student, correct):
    """返回 right / wrong / unknown。

    多选按集合比（ABD 与 DBA 是同一个答案）；
    一方是另一方的真子集算错（只选了 A 而答案是 ABD）。
    """
    mine, key = normalize(student), normalize(correct)
    if not mine or not key:
        return UNKNOWN
    # 长散文不算作答：「根据《刑法》第A条」不该被判成选了 A
    # 上限取 8：手写作答通常是「选B」「答案是B」；超过 8 个字的多半是散文
    # （「根据《刑法》第A条的规定」），从散文里抠一个字母当答案会误判
    if len(str(student or "").strip()) > 8:
        return UNKNOWN
    if mine == key:
        return RIGHT
    # 纯数字答案按字符串比：12 与 21 是不同答案，不能当集合看
    if mine.isdigit() and key.isdigit():
        return WRONG
    if len(key) > 1 and len(mine) > 1 and set(mine) == set(key):
        return RIGHT
    return WRONG


def judge_all(questions, answer_key, student_answers):
    """批量判题。

    questions       classify.split_questions 的输出
    answer_key      {题号: 'B'}
    student_answers {题号: 'B'} —— 从红笔层/作答痕迹读出来的

    返回 (逐题结果, 汇总)。判不出的进 unknown，不参与对错统计。
    """
    results = []
    summary = {"total": len(questions), "right": 0, "wrong": 0, "unknown": 0}
    for question in questions:
        number = question["number"]
        mine = (student_answers or {}).get(number, "")
        key = (answer_key or {}).get(number, "")
        verdict = judge(mine, key)
        summary[verdict] += 1
        results.append({"number": number, "student": mine, "correct": key,
                        "verdict": verdict, "stem": question.get("stem", "")})
    return results, summary
