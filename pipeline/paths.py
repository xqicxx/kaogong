#!/usr/bin/env python3
"""所有路径的唯一来源。

以前 vault 位置分别硬编码在 review / mistake / split / selftest 四个文件里，
换目录要改四处，测试也没法跑在临时目录上。现在统一从这里取，
并且可以用环境变量覆盖 —— 测试就能指向临时 vault，不碰真笔记。
"""
import os
from pathlib import Path


def _path(env_name, default):
    """环境变量优先；相对路径要绝对化，不然会随 cwd 漂移。"""
    raw = os.environ.get(env_name)
    if not raw:
        return default
    # 两个分支原来写成同一个表达式（`candidate.resolve()`），条件毫无作用 ——
    # 相对路径也要绝对化，否则会随 cwd 漂移；直接 resolve 就够了。
    return Path(raw).expanduser().resolve()


# 笔记库（考公子目录）；KAOGONG_VAULT 可覆盖
VAULT_ROOT = _path("KAOGONG_VAULT",
                   Path.home() / "Documents" / "Obsidian Vault" / "考公")
CARD_DIR = VAULT_ROOT / "考点"
MISTAKE_DIR = VAULT_ROOT / "错题"
RAW_DIR = VAULT_ROOT / "_raw"

# 运行状态；KAOGONG_STATE 可覆盖
STATE_DIR = _path("KAOGONG_STATE", Path.home() / ".pi" / "kaogong")
PUSH_STATE = STATE_DIR / "last-push.json"

# pi 自己的配置（Telegram 凭据在这里）
TELEGRAM_CONFIG = _path("KAOGONG_TELEGRAM_CONFIG",
                        Path.home() / ".pi" / "agent" / "telegram.json")
