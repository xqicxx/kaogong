#!/usr/bin/env python3
"""领域异常。

规矩：**库函数只管抛错，只有 main() 把它翻译成退出码**。
以前一张卡找不到就直接 SystemExit，函数没法被复用、也没法在测试里单独调用。
"""


class KaogongError(Exception):
    """本项目所有可预期错误的基类。"""


class CardNotFound(KaogongError):
    """按名字/编号找不到卡片。"""


class AmbiguousCard(KaogongError):
    """模糊匹配命中多于一张 —— 绝不能自己挑一张改。"""


class InvalidState(KaogongError):
    """状态机拒绝这次转换（例如没过变式关就考迁移关）。"""


class ConfigError(KaogongError):
    """配置缺失或坏了（telegram.json、推送记录等）。"""


class DeliveryError(KaogongError):
    """推送没送达（网络、token、被 Telegram 拒收）。"""
