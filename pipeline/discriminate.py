#!/usr/bin/env python3
"""题目区分度 —— IRT 2PL 的可用子集。

PLAN 3.6 的「额外」那条要求「用答题数据回归题目难度（IRT 2PL 简化版），
把无效题剔出验证集」。这里要说清楚一件事：

    真正的 IRT 需要**一群人**答题才能估出「难度」和「区分度」两个参数。
    我们只有一个人、几十道题 —— 直接套 2PL 是把噪声当参数，
    参数看着很像样，结论全是假的。

所以这里只做可解释的那一半：一道题对「你到底会不会」有没有鉴别力。
判据全部来自真实的作答结果，不含任何拟合出来的系数：

    无鉴别力  一路过（连胜够长且已掌握）→ 它已经测不出你会不会，剔出验证集
    太难      反复被它绊住（连胜为 0、还没掌握、复习次数不少）→ 该拆小或归档
    有鉴别力  有对有错 → 留在验证集
    样本不足  复习次数太少，不下结论

宁可不下结论也不猜 —— 这道判断错了会把好题剔掉。
"""

# 判据阈值都放在一起，好调也好审
WIN_STREAK = 4          # 连胜到这个数：基本不会错了
TOO_HARD_REPS = 3       # 复习到这个次数还没过变式关：太难
MIN_REPS = 1            # 至少复习过一次才有资格下结论

无鉴别力 = "无鉴别力"
太难 = "太难"
有鉴别力 = "有鉴别力"
样本不足 = "样本不足"


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def item_stats(item):
    """把一条作答记录整理成判据需要的几个数。

    item 是扁平的 dict，字段名就是前端 matter 的键，
    这样调用方（mistake.py）不用先转换一层。
    """
    stage = str(item.get("状态") or "").strip()
    return {
        "name": item.get("name") or item.get("卡名") or "?",
        "stage": stage,
        "streak": _int(item.get("变式连胜")),
        "reps": _int(item.get("复习次数")),
        "cause": str(item.get("错因") or "").strip(),
    }


def classify(stats):
    """给一条答题记录判鉴别力。返回上面四个常量之一。

    判定顺序有讲究：先判「太难」再判「无鉴别力」——
    一道题可能既复习了很多次又已经掌握（熬过去了），那种情况其实是有鉴别力的。
    """
    if stats["reps"] < MIN_REPS:
        return 样本不足
    if stats["stage"] == "已掌握" and stats["streak"] >= WIN_STREAK:
        return 无鉴别力
    if stats["stage"] == "未掌握" and stats["streak"] == 0 and stats["reps"] >= TOO_HARD_REPS:
        return 太难
    return 有鉴别力


def summarize(items):
    """一批作答记录 → 分组结果。用于决定验证集里留哪些题。

    返回 {'buckets': {判据: [stats, ...]}, 'keep': [...], 'drop': [...]}
    drop 只含「无鉴别力」—— 「太难」的题不剔，它可能只是需要拆小，
    剔掉就再也看不见了（宁可留着提醒自己）。
    """
    buckets = {无鉴别力: [], 太难: [], 有鉴别力: [], 样本不足: []}
    for item in items:
        stats = item_stats(item)
        buckets[classify(stats)].append(stats)
    keep = buckets[有鉴别力] + buckets[样本不足] + buckets[太难]
    return {"buckets": buckets, "keep": keep, "drop": list(buckets[无鉴别力])}
