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
W_ANCHORED[0:3] = [v * FIRST_INTERVAL_DAYS / W[2] for v in W[0:3]]

# 卡片 front-matter 里存状态用的字段名
KEYS = {"due": "到期", "s": "稳定度", "d": "难度",
        "reps": "复习次数", "last": "上次复习"}

MIN_STABILITY = 0.1
MAX_STABILITY = 36500.0


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
        raise ValueError("评分只能是 1-4，收到 %r" % (grade,))
    today = today or date.today()
    if state is None:
        stability = W_ANCHORED[grade - 1]
        difficulty = min(max(W[4] - (grade - 3) * W[5], 1), 10)
        reps = 1
        retrievability = DEFAULT_REQUEST_RETENTION
    else:
        stability = state["s"]
        difficulty = state["d"]
        reps = state["reps"]
        elapsed = max(0, (today - state["last"]).days) if state.get("last") else 0
        retrievability = r_of(elapsed, stability)
        base = min(max(W[4] - (4 - 3) * W[5], 1), 10)
        difficulty = min(max(W[7] * base + (1 - W[7]) * (difficulty - W[6] * (grade - 3)), 1), 10)
        if grade == 1:
            stability = (W[11] * difficulty ** -W[12] * ((stability + 1) ** W[13] - 1)
                         * math.exp(W[14] * (1 - retrievability)))
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
