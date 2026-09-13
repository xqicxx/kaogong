#!/usr/bin/env python3
"""掌握三关的状态机 —— 纯函数，不做 I/O。

    未掌握 → 变式中 → 迁移中 → 待保持 → 已掌握

关键规矩（都在 GATE 里）：**不能跳关**。
没这道闸门时，「未掌握」可以直接考「保持」关，三关就白设计了。

  · 迁移关只能从「迁移中」考 —— 变式连胜满 2 次才会进这个状态
  · 保持关只能从「待保持」考 —— 迁移过关并排好日期才会进

任一步失败 → 退一级（不清零：FSRS 那边自己会缩短间隔）。
"""
from datetime import date, timedelta

from pipeline.errors import InvalidState, KaogongError  # type: ignore[import]

STEPS = ["未掌握", "变式中", "迁移中", "待保持", "已掌握"]
GATE = {
    "变式": ("未掌握", "变式中", "迁移中", "待保持", "已掌握"),
    "迁移": ("迁移中",),
    "保持": ("待保持",),
}
FIELDS = {"状态": "状态", "连胜": "变式连胜", "迁移": "迁移通过", "保持测试": "保持测试"}
WINS_NEEDED = 2
MAX_KEEP_DAYS = 36500


def normalize(state_name):
    """把任意输入归一到合法状态（手改坏了就当未掌握）。"""
    value = (state_name or "").strip()
    return value if value in STEPS else "未掌握"


ALIASES = {
    "待巩固": "未掌握",
    "巩固中": "未掌握",
    "变式": "变式中",
    "迁移": "迁移中",
    "保持": "待保持",
    "已保持": "已掌握",
    "掌握": "已掌握",
}


def resolve(text):
    """把**用户输入**的状态名解析成合法状态；不认识就报错。

    和 normalize() 的分工：
      normalize  读文件，宽容 —— 手改坏了当未掌握，不让一条坏数据炸掉整条链路
      resolve    读输入，严格 —— 否则「已保持」这种不存在的状态会被悄悄当成
                 「未掌握」，筛出来的集合完全不是用户要的（真实踩到过）
    """
    value = ALIASES.get((text or "").strip(), (text or "").strip())
    if value not in STEPS:
        raise KaogongError("状态只能是 %s（也认 %s），收到 %r"
                           % ("/".join(STEPS), "、".join(sorted(ALIASES)), text))
    return value


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def from_card(front_matter):
    """从卡片 front-matter 拼出状态字典。"""
    return {
        "状态": front_matter.get(FIELDS["状态"]),
        "变式连胜": front_matter.get(FIELDS["连胜"]),
        "迁移通过": front_matter.get(FIELDS["迁移"]),
        "保持测试": front_matter.get(FIELDS["保持测试"]),
    }


def to_card(state):
    """状态字典 → front-matter 字段。"""
    return {FIELDS["状态"]: state["状态"],
            FIELDS["连胜"]: state["变式连胜"],
            FIELDS["迁移"]: state["迁移通过"],
            FIELDS["保持测试"]: state["保持测试"]}


def advance(state, kind, passed, today=None, stability=12.0):
    """过一关。kind: 变式/迁移/保持；passed: 对/错。

    非法转换抛 InvalidState（领域异常）。
    ⚠️ 以前这里抛 ValueError —— 那违反本项目的约定「库函数抛 KaogongError，
    只有 main() 把它翻成退出码」：CLI 层的 except 抓不到 ValueError，
    用户看到的是 traceback 而不是一句人话。InvalidState 本来就是为这件事定义的。
    """
    if kind not in GATE:
        raise InvalidState("kind 只能是 变式/迁移/保持，收到 %r" % (kind,))
    today = today or date.today()
    current = normalize(state.get("状态"))
    if current not in GATE[kind]:
        raise InvalidState("%s 关要求当前状态是 %s，但这张卡是「%s」—— 得先过前面的关"
                           % (kind, "/".join(GATE[kind]), current))

    result = {
        "状态": current,
        "变式连胜": _int(state.get("变式连胜"), 0),
        "迁移通过": str(state.get("迁移通过", "false")).lower() == "true",
        "保持测试": state.get("保持测试") or "",       # 不能把 None 写进卡片
    }

    if kind == "变式" and passed and current == "已掌握":
        # 已掌握的卡偶尔再过一道变式，答对了不该降回「变式中」——
        # 「失败退一级」是惩罚，答对没道理受罚
        result["状态"] = "已掌握"
        return result
    if kind == "变式":
        result["保持测试"] = ""                  # 回到变式关，说明还没到保持阶段
        if passed:
            result["变式连胜"] += 1
            if result["变式连胜"] >= WINS_NEEDED:
                result["状态"] = "迁移中"
                result["变式连胜"] = 0
            else:
                result["状态"] = "变式中"
        else:
            result["变式连胜"] = 0
            result["状态"] = "变式中"
            result["迁移通过"] = False           # 变式又错了，以前“迁移通过”的标记要清掉
    elif kind == "迁移":
        if passed:
            result["迁移通过"] = True
            result["状态"] = "待保持"
            stable = min(max(_float(stability, 12.0), 0.1), MAX_KEEP_DAYS)
            days = max(1, _int(round(stable), 12))
            result["保持测试"] = str(today + timedelta(days=days))
        else:
            result["迁移通过"] = False
            result["状态"] = "变式中"
            result["变式连胜"] = 0
            result["保持测试"] = ""
    else:                                          # 保持
        if passed:
            result["状态"] = "已掌握"
            result["保持测试"] = ""
        else:
            result["状态"] = "迁移中"
            result["迁移通过"] = False
            result["保持测试"] = ""
    result["迁移通过"] = "true" if result["迁移通过"] else "false"
    return result
