#!/usr/bin/env python3
"""Obsidian 笔记的 front-matter 读写（review.py 与 mistake.py 共用）。

为什么单独抽出来：两个脚本各写了一份 front-matter 重写逻辑，而且**两份都错**：
  · review.py 版：只有白名单字段（到期/稳定度…）会被改写，其它字段即使传了新值也保持原样
    → reset 命令静默失效过（把卡片重置了，但「状态」还停在「待保持」）
  · mistake.py 版：文件里有、但 update 字典里没有的键会被**直接删掉**

正确做法很简单：**逐行重写，只替换 update 里给出的键，其余原样保留，新键追加到末尾。**
"""
import re
from pathlib import Path


def split(text):
    """拆成 (fm 字典, 正文, 原始 front-matter 文本)。没有 front-matter 时 fm 为空。"""
    if not text.startswith("---"):
        return {}, text, ""
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text, ""
    raw = text[3:end]
    fm = {}
    for line in raw.splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, text[end + 4:].lstrip("\n"), raw


def read(path):
    return split(Path(path).read_text(encoding="utf-8"))


def merge_front_matter(raw_fm, updates):
    """逐行重写：原文件的键保留，updates 里的键改值/追加，未提及的键不动。"""
    seen = set()
    out = []
    for line in raw_fm.splitlines():
        if ":" not in line:
            out.append(line)
            continue
        k = line.split(":", 1)[0].strip()
        if k in updates:
            out.append("%s: %s" % (k, updates[k]))
            seen.add(k)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append("%s: %s" % (k, v))
    return "\n".join(out)


def write(path, updates, body, raw_fm):
    Path(path).write_text(
        "---\n" + merge_front_matter(raw_fm, updates) + "\n---\n\n" + body,
        encoding="utf-8")


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
    assert "状态: 已掌握" in out, "要改的字段必须被改写：%s" % out
    assert "tags: [a]" in out, "没提到的字段必须原样保留：%s" % out
    assert "type: 考点" in out, "第一行不能丢：%s" % out
    assert out.rstrip().endswith("新增: 1"), "新字段要追加到末尾：%s" % out
    assert merge_front_matter(raw, {}) == raw, "空更新不该改动任何东西"
    fm, body, _ = split("---\na: 1\n---\n\n正文")
    parsed_ok = fm == {"a": "1"} and body == "正文"
    msg = "解析结果不对：%r / %r" % (fm, body)
    assert parsed_ok, msg
    fm2, body2, _ = split("没有 front-matter")
    assert fm2 == {} and body2 == "没有 front-matter"
    print("  自检通过：改值 / 保留未提及字段 / 追加新字段 / 空更新 / 有无 front-matter")


if __name__ == "__main__":
    selftest()
