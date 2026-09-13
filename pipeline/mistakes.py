#!/usr/bin/env python3
"""错题的领域读取 —— review 与 mistake 共用一份。

为什么单独一个模块：读错题这件事本来有两处各自实现（复习推送要用来做
交错练习与区分度，错题命令要用来看状态），字段名一改就得改两遍。
这里把「读成什么形状」定下来，两边都从这里取。

容错策略写在一处：
    · 目录不存在 → 空列表（还没录过错题是正常状态，不是错误）
    · 单个文件读不动（权限、编码、YAML 坏了）→ 跳过它，不让一条坏笔记
      把今天的推送整个搞挂；坏在哪儿由调用方决定要不要告诉用户
"""
from pipeline.paths import MISTAKE_DIR  # type: ignore[import]
from pipeline.vault import read  # type: ignore[import]

# 记录里出现的键。多加一个字段就改这里，两边同时生效。
# 直接从 front-matter 抄过来的键。
# 题型 不在其中：它是从「模块」推出来的，不是原名；标题 也不在：它就是文件名。
FIELDS = ("来源", "状态", "错因", "答题信心",
          "我的答案", "正确答案", "变式连胜", "迁移通过", "复习次数")


def paths():
    """所有错题文件的路径（排序保证可复现）。"""
    if not MISTAKE_DIR.exists():
        return []
    return sorted(MISTAKE_DIR.glob("*.md"))


def read_all():
    """读全部错题 → (记录列表, 读不动的文件列表)。

    容错但不静默：一条坏笔记不该让整天的推送挂掉（所以跳过），
    可也**不能悄悄消失** —— 静默跳过的卡会从复习队列里凭空少掉，没人发现。
    跳过的路径原样返回，由调用方决定怎么告诉用户（推送里已经有一块是报坏卡的）。

    捕获范围是 (OSError, UnicodeDecodeError, ValueError)：
    前两个是权限/编码；ValueError 是 front-matter 坏了 —— vault.split 对
    不成对的 `---` 之类会抛它，只 catch OSError 会漏掉这类而让整批读崩掉（真踩过）。
    """
    out, skipped = [], []
    for path in paths():
        try:
            fm, _body, _raw = read(path)
        except (OSError, UnicodeDecodeError, ValueError):
            skipped.append(path)
            continue
        item = {"name": path.stem, "path": path}
        item["标题"] = path.stem
        item["题型"] = str(fm.get("模块") or "").strip() or "未分模块"
        for key in FIELDS:
            item[key] = fm.get(key)
        out.append(item)
    return out, skipped


def records():
    """只要记录。需要知道跳过了哪些文件就用 read_all()。"""
    return read_all()[0]
