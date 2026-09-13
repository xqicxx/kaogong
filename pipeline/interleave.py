#!/usr/bin/env python3
"""题型交错 —— 别按题型块刷题。

PLAN 2.2 ⑤ 的机制：题型靠的是**判别能力**（认得出「这题该用哪一招」），不是记忆。
按题型块刷属于分块练习（blocked practice），块内不需要判别、机械重复就能过，
效果显著差于交错练习（Rohrer & Taylor；Foster 2019）。

实现上用轮转选取：按题型分组后轮流各取一道，
于是**相邻两道题必然不同型** —— 这正是逼出判别的那一步。

纯逻辑：输入是普通 dict 列表，不碰文件、不碰网络，能单独测。
"""


def is_interleaved(items):
    """相邻两题题型是否都不同。交错的定义就是这个。"""
    types = [str(item.get("题型") or "") for item in items]
    return all(types[i] != types[i + 1] for i in range(len(types) - 1))


def pick(items, limit=3):
    """轮转挑选，返回 (挑中的列表, 说明)。

    limit 是本次最多出几道。不足两种题型就返回空 ——
    交错练习的意义在于「型与型之间做判别」，只有一种题型时这个机制不成立，
    硬凑成一块反而退化成分块练习。
    """
    usable = [item for item in items if str(item.get("题型") or "").strip()]
    groups = {}
    for item in usable:
        groups.setdefault(str(item["题型"]).strip(), []).append(item)
    if len(groups) < 2:
        return [], ("只有 %d 种题型，交错不成立（交错的意义在于型间判别）" % len(groups))

    # 顺序固定：按题型名排序，保证同样的输入得到同样的输出（可复现、好测）
    queues = [groups[name] for name in sorted(groups)]
    picked, index = [], 0
    while len(picked) < limit and any(queues):
        queue = queues[index % len(queues)]
        if queue:
            picked.append(queue.pop(0))
        index += 1
        if index > limit * len(queues) * 2:      # 防御：不该发生，但不让它转死
            break
    return picked, ""
