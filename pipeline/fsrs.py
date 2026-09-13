#!/usr/bin/env python3
"""间隔重复排期算法 —— 纯函数，不做任何 I/O。

实现的是 **FSRS-4.5 核心公式**（17 参数，Anki 早期 FSRS 用的那套，公式公开且稳定）：
    R(t,S) = (1 + FACTOR·t/S)^DECAY        DECAY=-0.5, FACTOR=0.9^(1/DECAY)-1
    S0(G)  = w[G-1]                        首次评分稳定度
    D0(G)  = w4 - (G-3)·w5                 初始难度，夹在 [1,10]
    D'     = w7·D0(4) + (1-w7)·(D - w6·(G-3))
    S'_r   = S·(1 + e^w8·(11-D)·S^-w9·(e^{w10·(1-R)}-1)·hard·easy)
    S'_f   = w11·D^-w12·((S+1)^w13 - 1)·e^{w14·(1-R)}          遗忘后
    间隔   = S / FACTOR · (requestR^(1/DECAY) - 1)     → requestR=90% 时间隔 == S（天）

为什么没上 FSRS-6：6 版多的 w17-w20 主要改「同日复习」和「可训练衰减」，
日频场景几乎用不到，而我没拿到足够公式细节 —— 宁可用完全确定的 4.5。
升级路径：换 W 参数表 + 两处公式。

抽成独立模块的理由：这是全项目最该被单独验证的一段逻辑，
不带文件读写就能跑断言（见 selftest）。
"""
import math
from datetime import date, timedelta

from pipeline.errors import KaogongError  # type: ignore[import]

# FSRS-4.5 默认参数（w0..w16）
W = [
    0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031,
    1.6474, 0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755,
]
DECAY = -0.5
FACTOR = 0.9 ** (1 / DECAY) - 1
DEFAULT_REQUEST_RETENTION = 0.9

# 考试锚点：Cepeda 2008 说最优间隔 ≈ 保持期的 10-20%。
# 2026-12-05 考试、83 天窗口 → 首轮 ≈ 12 天。改这一个数就能重锚。
FIRST_INTERVAL_DAYS = 12.0
W_ANCHORED = list(W)
# 四个首评档位都要等比缩到同一个基准，只缩前三个会让 Easy 保持原值：
# Good 被锚到 12 天，Easy 还是 13.8 天 —— 两者几乎没差别，Easy 就没有意义了
W_ANCHORED[0:4] = [v * FIRST_INTERVAL_DAYS / W[2] for v in W[0:4]]

# 卡片 front-matter 里存状态用的字段名
KEYS = {"due": "到期", "s": "稳定度", "d": "难度",
        "reps": "复习次数", "last": "上次复习"}

MIN_STABILITY = 0.1
MAX_STABILITY = 36500.0


def safe_number(value, default):
    """把卡片里的数值字段转成 float；缺失/非法/NaN/无限就退回默认值。"""
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except (TypeError, ValueError):
        return float(default)


def safe_count(value, default=0):
    """把复习次数转成 int；NaN/inf 会让 int() 抛错。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def r_of(elapsed_days, stability):
    """t 天后的可提取度。"""
    return (1 + FACTOR * max(elapsed_days, 0) / max(stability, MIN_STABILITY)) ** DECAY


def interval_days(stability, request_r=DEFAULT_REQUEST_RETENTION):
    """给定稳定度与目标保持率，算间隔天数。"""
    return max(1.0, stability / FACTOR * (request_r ** (1 / DECAY) - 1))


def clamp_stability(value):
    return min(max(value, MIN_STABILITY), MAX_STABILITY)


def schedule(state, grade, today=None):
    """评分后的新状态。state 为 None 表示新卡；grade: 1 忘了 / 2 勉强 / 3 对了 / 4 太简单。"""
    if grade not in (1, 2, 3, 4):
        # 抛领域异常而不是 ValueError：本项目的约定是库函数抛 KaogongError，
        # 只有 main() 把它翻成退出码 —— 抛 ValueError 的话 CLI 的 except 抓不到，
        # 用户拿到的是 traceback（CLI 层虽然用 choices 拦住了，但库不该指望调用方兜底）
        raise KaogongError("评分只能是 1-4，收到 %r" % (grade,))
    today = today or date.today()
    # last 为空也按新卡处理：否则 elapsed=0 → R=1 → 稳定度一点都不涨，
    # 白白浪费掉第一个 12 天周期（init 出来的卡就是这种“有状态但没复习过”的形态）
    if state is None or not state.get("last"):
        stability = W_ANCHORED[grade - 1]
        difficulty = min(max(W[4] - (grade - 3) * W[5], 1), 10)
        reps = 1
        retrievability = DEFAULT_REQUEST_RETENTION
    else:
        # 卡片字段可能被手改坏：s<=0 会让下面的 s**-w9 变成除零，缺字段会 KeyError。
        # 先夹到合法区间、缺的取默认值，坏数据不该让整条复习链路崩掉。
        stability = clamp_stability(safe_number(state.get("s"), W_ANCHORED[grade - 1]))
        difficulty = min(max(safe_number(state.get("d"), W[4]), 1), 10)
        reps = safe_count(safe_number(state.get("reps"), 0))
        elapsed = max(0, (today - state["last"]).days) if state.get("last") else 0
        retrievability = r_of(elapsed, stability)
        # 这里的 (4 - 3) 不是笔误：难度回归的目标是 D0(4)（即"太简单"档的初始难度），
        # 而不是 D0(grade) —— 与官方 4.5 参考实现一致
        base = min(max(W[4] - (4 - 3) * W[5], 1), 10)
        difficulty = min(max(W[7] * base + (1 - W[7]) * (difficulty - W[6] * (grade - 3)), 1), 10)
        if grade == 1:
            forgotten = (W[11] * difficulty ** -W[12] * ((stability + 1) ** W[13] - 1)
                         * math.exp(W[14] * (1 - retrievability)))
            # 遗忘绝不能让稳定度变大。原始公式在低稳定度（S<1 天）时会算出 S_f > S，
            # 于是"又忘了"反而把间隔拉长 —— 官方实现也有这道闸（min(S_f, S/…)，见 py-fsrs）。
            # 用官方 py-fsrs 做函数级对照时发现的，180 组网格里 8 组病态。
            stability = min(forgotten, stability)
        else:
            hard = W[15] if grade == 2 else 1.0
            easy = W[16] if grade == 4 else 1.0
            stability = stability * (1 + math.exp(W[8]) * (11 - difficulty) * stability ** -W[9]
                                     * (math.exp(W[10] * (1 - retrievability)) - 1) * hard * easy)
        reps += 1
    stability = clamp_stability(stability)
    interval = interval_days(stability)
    if grade == 1:
        interval = max(1.0, interval * 0.5)     # 忘了：间隔砍半，但至少一天
    return {"s": round(stability, 3), "d": round(difficulty, 3), "reps": reps,
            "last": today, "due": today + timedelta(days=round(interval))}
