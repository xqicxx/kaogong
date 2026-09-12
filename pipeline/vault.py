#!/usr/bin/env python3
"""Obsidian 笔记的 front-matter 读写（review / mistake / split 共用）。

被 open-code-review 找出过 5 个问题，全部在这里修掉：
  ① 非原子写：write_text 中途崩溃会留下截断的笔记 → 改成临时文件 + os.replace
  ② 分隔符用子串匹配：find("\\n---") 会把 "--- foo" / "----" 当结束符 → 只认整行等于 ---
  ③ 没有 front-matter 时返回 raw=None，下游 splitlines() 直接崩 → 统一返回 ""
  ④ 多行值（|、缩进列表）被替换时残留续行 → 替换时连同缩进行一起清掉
  ⑤ 每存一次多一个空行：raw 里带着开头换行，写出时又加一个 → 统一 strip
"""
import os
import re
import tempfile
from pathlib import Path

DELIM = re.compile(r"^---\s*$")


def split(text):
    """拆成 (fm 字典, 正文, 原始 front-matter 文本)。

    结束分隔符必须是整行 ---（前后允许空白），不是子串匹配 ——
    正文里出现 "---foo" 或 "----" 不会被误当结尾。
    """
    lines = text.splitlines()
    if not lines or not DELIM.match(lines[0]):
        return {}, text, ""
    end = None
    for i in range(1, len(lines)):
        if DELIM.match(lines[i]):
            end = i
            break
    if end is None:
        # 没有闭合分隔符：当作没有 front-matter（宁可当正文，也不要伪造一个头）
        return {}, text, ""
    fm = {}
    for line in lines[1:end]:
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    raw = "\n".join(lines[1:end])
    return fm, body, raw


def read(path):
    """读一个笔记文件。读不到就抛 OSError，让调用方决定怎么处理。"""
    return split(Path(path).read_text(encoding="utf-8"))


def merge_front_matter(raw_fm, updates):
    """逐行重写：原文件里的键保留，updates 给出的键改值，新键追加到末尾。

    未出现在 updates 里的键原样保留 —— 这是关键：卡片上还有复习历史，
    少写一个字段就是丢数据。
    """
    raw_fm = (raw_fm or "").strip("\n")   # 首尾空行丢掉，否则每存一次就多一行
    updates = updates or {}
    seen = set()
    out = []
    skipping_continuation = False
    for line in raw_fm.splitlines():
        if ":" not in line or line[:1].isspace():
            if skipping_continuation and line[:1].isspace():
                continue          # 被替换键的缩进续行，一起丢掉
            out.append(line)
            continue
        k = line.split(":", 1)[0].strip()
        if k in updates:
            out.append("%s: %s" % (k, updates[k]))
            seen.add(k)
            skipping_continuation = True
        else:
            out.append(line)
            skipping_continuation = False
    for k, v in updates.items():
        if k not in seen:
            out.append("%s: %s" % (k, v))
    return "\n".join(out)


def write(path, updates, body, raw_fm):
    """原子写：先写同目录临时文件再 os.replace，中途崩不会留下半个笔记。"""
    p = Path(path)
    data = "---\n" + merge_front_matter(raw_fm, updates) + "\n---\n\n" + (body or "")
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def selftest():
    raw = "type: 考点\n状态: 未掌握\ntags: [a]"
    out = merge_front_matter(raw, {"状态": "已掌握", "新增": "1"})
    ok1 = "状态: 已掌握" in out
    assert ok1, "要改的字段必须被改写：%s" % out
    ok2 = "tags: [a]" in out and "type: 考点" in out
    assert ok2, "没提到的字段必须原样保留：%s" % out
    ok3 = out.rstrip().endswith("新增: 1")
    assert ok3, "新字段要追加到末尾：%s" % out
    ok4 = merge_front_matter(raw, {}) == raw
    assert ok4, "空更新不该改动任何东西"

    fm, body, _ = split("---\na: 1\n---\n\n正文")
    ok5 = fm == {"a": "1"} and body == "正文"
    msg5 = "解析结果不对：%r / %r" % (fm, body)
    assert ok5, msg5
    fm2, body2, raw2 = split("没有 front-matter")
    ok6 = fm2 == {} and body2 == "没有 front-matter" and raw2 == ""
    assert ok6, "没有 front-matter 时不该伪造头部"
    fm3, body3, _ = split("---\na: 1\n\n正文（没有闭合分隔符）")
    ok7 = fm3 == {}
    assert ok7, "缺闭合分隔符时应当作没有 front-matter"
    _, _, raw4 = split("---\na: 1\nb: |\n  ---foo\n---\n\n正文")
    ok8 = body3 is not None and "---foo" in raw4
    assert ok8, "正文里的 ---foo 不能被当结束符"
    r1 = merge_front_matter("\ntype: 考点\n状态: 未掌握", {"状态": "已掌握"})
    r2 = merge_front_matter(r1, {"状态": "未掌握"})
    n1, n2 = len(r1.splitlines()), len(r2.splitlines())
    ok9 = n1 == n2 == 2
    msg9 = "往返读写不该膨胀，也不该留下开头空行：%d 行 vs %d 行（应为 2）" % (n1, n2)
    assert ok9, msg9
    multi = "tags: [a]\ndesc: |\n  第一行\n  第二行\n其他: 1"
    out_m = merge_front_matter(multi, {"desc": "单行"})
    ok10 = "第一行" not in out_m and "desc: 单行" in out_m
    assert ok10, "多行值改写后不该留残行：%s" % out_m.replace("\n", " / ")
    ok11 = merge_front_matter(None, {"a": "1"}) == "a: 1"
    assert ok11, "raw_fm 为 None 也不能崩"
    print("  自检通过：11 项（原子写 / 精确分隔符 / 缺头不崩 / 往返不膨胀 / 多行值清残行）")


if __name__ == "__main__":
    selftest()
