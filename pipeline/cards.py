#!/usr/bin/env python3
"""考点卡片的读取与查询（review 与 mistake 共用）。

只负责“把卡片读出来 / 找到某一张 / 把状态写回去”，不掺 CLI 与排期算法。
"""
# 卡片 I/O 层：只负责“读出来 / 找到某一张 / 把状态写回”，
# 不掺 CLI、不掺排期算法。review 与 mistake 都走这里。
import json
from datetime import date, datetime

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.errors import AmbiguousCard, CardNotFound, ConfigError  # type: ignore[import]  # noqa: E402
from pipeline.fsrs import KEYS  # type: ignore[import]  # noqa: E402
from pipeline.paths import CARD_DIR, PUSH_STATE  # type: ignore[import]  # noqa: E402
from pipeline.vault import read, write  # type: ignore[import]  # noqa: E402


def read_state(front_matter):
    """从 front-matter 解出排期状态；缺字段或格式坏了就当作“没排期”。"""
    keys = KEYS
    if not front_matter.get(keys["due"]):
        return None
    try:
        last = (datetime.strptime(front_matter[keys["last"]], "%Y-%m-%d").date()
                if front_matter.get(keys["last"]) else None)
        return {
            "s": float(front_matter[keys["s"]]),
            "d": float(front_matter[keys["d"]]),
            "reps": int(front_matter.get(keys["reps"], 0)),
            "last": last,
            "due": datetime.strptime(front_matter[keys["due"]], "%Y-%m-%d").date(),
        }
    except (ValueError, KeyError, TypeError):
        return None


def load(path):
    """读一张卡片，带上解析好的排期状态。"""
    front_matter, body, raw = read(path)
    return {"path": path, "fm": front_matter, "body": body, "raw": raw,
            "state": read_state(front_matter)}


def all_cards():
    """vault 里的全部考点卡片（按文件名排序）。"""
    if not CARD_DIR.exists():
        return []
    return [load(p) for p in sorted(CARD_DIR.glob("*.md"))]


def find(name, allow_partial=True):
    """按名字找卡：精确优先；模糊命中多张就报错，绝不自己挑一张。"""

    def _load(paths_found):
        return [load(p) for p in paths_found]

    exact = sorted(CARD_DIR.glob("*.md")) if CARD_DIR.exists() else []
    hits = _load([p for p in exact if p.stem == name])
    if len(hits) == 1:
        return hits[0]
    if not allow_partial:
        raise CardNotFound("没找到卡片：%s" % name)
    fuzzy = _load([p for p in exact if name in p.stem])
    if not fuzzy:
        raise CardNotFound("没找到卡片：%s" % name)
    if len(fuzzy) > 1:
        raise AmbiguousCard("「%s」匹配到 %d 张卡：%s"
                                   % (name, len(fuzzy),
                                      "、".join(c["path"].stem for c in fuzzy[:6])))
    return fuzzy[0]


def by_index(index, today=None):
    """按上次推送的编号取卡（推送里说“回分：编号 评分”，靠这份记录对回来）。"""
    today = today or date.today()
    try:
        saved = json.loads(PUSH_STATE.read_text(encoding="utf-8"))
    except OSError:
        raise ConfigError("读不到上次推送的编号表（%s），请用卡片名" % PUSH_STATE)
    except ValueError as exc:
        raise ConfigError("编号表坏了（%s）：%s" % (PUSH_STATE, exc))
    if not isinstance(saved, dict):
        raise ConfigError("编号表格式不对（%s）—— 删了它再跑一次 push" % PUSH_STATE)
    if "date" not in saved or "cards" not in saved:
        raise ConfigError("编号表缺少 date/cards 字段（%s）—— 删了它再跑一次 push"
                                   % PUSH_STATE)
    pushed_on = str(saved["date"])
    if pushed_on != str(today):
        raise ConfigError("编号表是 %s 推的，不是今天 —— 用卡片名，或先跑一次 push" % pushed_on)
    names = saved["cards"] or []
    try:
        position = int(index) - 1
    except (TypeError, ValueError):
        raise CardNotFound("编号得是数字：%r" % (index,))
    if not 0 <= position < len(names):
        raise CardNotFound("编号 %s 超出上次推送范围（共 %d 张）" % (index, len(names)))
    return find(names[position], allow_partial=False)


def state_fields(state):
    """排期状态 → front-matter 字段名与字符串值（写回卡片用）。"""
    keys = KEYS
    return {
        keys["s"]: state["s"],
        keys["d"]: state["d"],
        keys["reps"]: state["reps"],
        keys["last"]: str(state["last"]) if state.get("last") else "",
        keys["due"]: str(state["due"]),
    }


def save_state(card, state):
    """把排期状态写回卡片（其余字段原样保留）。"""
    write(card["path"], state_fields(state), card["body"], card["raw"])
