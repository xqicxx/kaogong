#!/usr/bin/env python3
"""页面去重 —— 纯函数，不碰文件系统。

为什么要它：同一页讲义会被拍好几次（换角度、补拍、手抖重拍）。
不去重就会出现重复的页 → 重复的卡片 → 同一个考点被排期多次，
复习负担翻倍、并且“两张卡”互相干扰。

判据用字符 3-gram 的 Jaccard 相似度，不用整串哈希：
  · 同一页两次拍摄 OCR 结果会有细微差异（标点、个别字），哈希对不上
  · 换角度拍还会让页眉页脚的行序略有不同
3-gram 对这类噪声鲁棒，而且不需要任何依赖。
"""
import re

# 正文归一：只留中日韩与字母数字，丢掉空白、标点、水印式符号
NOISE = re.compile(r"[^\u4e00-\u9fff\u3040-\u30ffA-Za-z0-9]")
CJK = re.compile(r"[\u4e00-\u9fff]")

DEFAULT_THRESHOLD = 0.45      # 实测：同页重拍 ≈ 0.61，相邻页 ≈ 0.04 —— 中间大片空白区
PAGE_LABEL_THRESHOLD = 0.25   # 页脚页码相同时放宽：重拍糊得厉害也可能只有 0.3
STRONG_THRESHOLD = 0.85      # 高到可以无视页码不一致（那种情况只可能是页码被 OCR 读错）
SIBLING_MIN_CHARS = 20        # 文本兜底要求正文至少这么长 —— 太短的文本相似度没有意义
                              # （真实卡片正文 30-80 字，短于 20 字的相似度基本是噪声）
SIBLING_THRESHOLD = 0.30      # 卡片之间：超过它算“近义兄弟”，排期时要错开。
                              # 主判据是「分组」字段；文本只是兜底（实测同组 0.13-0.30）
# ⚠️ 卡片**不做去重**：兄弟条目（同一组①②③用同一套措辞）相似度天然就高，
# 实测「因果倒置 ↔ 否定此因」0.61、「支持原观点或质疑反对者 ↔ 支持反对者或质疑原观点」0.77。
# 它们是完全不同的知识点，合并或删除就是丢内容 —— 只能错开排期，不能当重复处理。


def normalize(text):
    return NOISE.sub("", text or "")


def shingles(text, size=3):
    """字符 size-gram 集合。中文按字切，正好对上“一段话大体相同”的直觉。"""
    body = normalize(text)
    if len(body) <= size:
        return {body} if body else set()
    return {body[i:i + size] for i in range(len(body) - size + 1)}


def similarity(left, right):
    """两段文本的相似度 0..1（3-gram Jaccard）。太短的文本按长度比给个保守值。"""
    first, second = shingles(left), shingles(right)
    if not first or not second:
        return 0.0
    if len(first) < 8 or len(second) < 8:
        # 短文本不能用长度比："abc" 与 "xyz" 长度一样，长度比会给 1.0（完全错）。
        # 拆成字符集合后看交集：完全不同就是 0，完全相同才是 1。
        left_chars = set("".join(first))
        right_chars = set("".join(second))
        if not left_chars or not right_chars:
            return 0.0
        return len(left_chars & right_chars) / len(left_chars | right_chars)
    return len(first & second) / len(first | second)



PAGE_LABEL = re.compile(r"第\s*([0-9０-９一二三四五六七八九十]{1,3})\s*[页頁]")


def page_label(text):
    """抠出页脚里的「第N页」—— 给去重再加一道独立旁证。"""
    found = PAGE_LABEL.findall(text or "")
    return found[-1] if found else ""


def split_siblings(cards, text_of, already=(), threshold=SIBLING_THRESHOLD, group_of=None):
    """把卡片分成「本批可做」与「因近义暂缓」两组。

    主判据是**分组**：同一分组里的条目本来就是同一套措辞的并列项
    （「三种削弱方式」下的 另有他因 / 因果倒置 / 否定此因），放同一天最容易记混。
    没有分组信息时才退到文本相似度。

    ⚠️ 只影响排期，**绝不合并或删除卡片** —— 删了就是丢知识点。
    """
    picked = list(already)
    deferred = []
    for card in cards:
        group = (group_of(card) if group_of else "") or ""
        same_group = False
        if group:
            same_group = any(((group_of(other) if group_of else "") or "") == group
                             for other in picked)
        body = text_of(card)
        similar = False
        if len(normalize(body)) >= SIBLING_MIN_CHARS:
            similar = any(len(normalize(text_of(other))) >= SIBLING_MIN_CHARS
                          and similarity(body, text_of(other)) >= threshold
                          for other in picked)
        if same_group or similar:
            deferred.append(card)
        else:
            picked.append(card)
    return picked[len(already):], deferred


def is_same_page(left, right, threshold=DEFAULT_THRESHOLD):
    """同一页的判据。

    三条，按优先级：
      1. 两个页脚的页码明确不同 且 文字也没到「几乎一样」→ **不是同一页**
         （页码是硬证据；只有 OCR 把页码读错了才会出现「同页不同号」）
      2. 文字相似度 >= threshold → 是同一页
      3. 页码相同 + 相似度 >= PAGE_LABEL_THRESHOLD → 是同一页（重拍糊了的旁证）
    """
    score = similarity(left, right)
    left_label, right_label = page_label(left), page_label(right)
    if left_label and right_label and left_label != right_label and score < STRONG_THRESHOLD:
        return False
    if score >= threshold:
        return True
    return bool(left_label) and left_label == right_label and score >= PAGE_LABEL_THRESHOLD


def find_match(candidate_text, existing, threshold=DEFAULT_THRESHOLD):
    """在 existing（[(名字, 文本)]）里找与候选页匹配的那一页。

    返回 (名字, 相似度) 或 None。

    ⚠️ 不能只验“相似度最高的那个”：重拍糊了的页可能相似度只有 0.3，
    但它和已有页的**页码相同**，按 PAGE_LABEL_THRESHOLD 应当被判为同一页。
    所以：按相似度从高到低逐个用 is_same_page 验，第一个通过的就返回。
    """
    target = len(CJK.findall(candidate_text))
    scored = []
    for name, text in existing:
        size = len(CJK.findall(text))
        ratio = (max(target, size) / max(1, min(target, size))) if target and size else 1.0
        # 长度差太多通常不是同一页；但两边页脚页码一样时仍要给页码旁证一次机会
        # （重拍糊了的那页 OCR 可能少一大截，长度预筛会把它直接判死）
        if ratio > 1.6 and not (page_label(candidate_text)
                                and page_label(candidate_text) == page_label(text)):
            continue
        scored.append((similarity(candidate_text, text), name, text))
    for score, name, text in sorted(scored, key=lambda item: -item[0]):
        if is_same_page(candidate_text, text, threshold):
            return (name, score)
    return None
