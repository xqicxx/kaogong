#!/usr/bin/env python3
"""把消息送到 Telegram。

从 review.py 里拆出来：排期算法和“怎么把消息发出去”是两件事，
以后换通道（邮件、本地通知）不该动排期代码。
"""
import json
import urllib.request
from urllib.error import HTTPError

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.errors import ConfigError, DeliveryError  # type: ignore[import]  # noqa: E402
from pipeline.paths import TELEGRAM_CONFIG  # type: ignore[import]  # noqa: E402

TELEGRAM_API = "https://api.telegram.org/bot%s/sendMessage"
MAX_LENGTH = 4096          # Telegram 单条消息上限
SAFE_LENGTH = 3500         # 我们自己留的余量，给尾部说明用


def load_credentials():
    """读 botToken 与 allowedUserId。"""
    try:
        config = json.loads(TELEGRAM_CONFIG.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError("读不到 %s：%s" % (TELEGRAM_CONFIG, exc))
    except ValueError as exc:
        raise ConfigError("%s 不是合法 JSON：%s" % (TELEGRAM_CONFIG, exc))
    profile = (config.get("profiles") or {}).get(config.get("defaultProfile", "default")) or {}
    token, chat_id = profile.get("botToken"), profile.get("allowedUserId")
    if not token or not chat_id:
        raise ConfigError("%s 里缺 botToken / allowedUserId" % TELEGRAM_CONFIG)
    return token, chat_id


def send(text):
    """发送一条 Markdown 消息；返回 HTTP 状态码。被拒收会抛 DeliveryError。"""
    if len(text) > MAX_LENGTH:
        raise DeliveryError("消息 %d 字符，超过 Telegram 的 %d 上限" % (len(text), MAX_LENGTH))
    token, chat_id = load_credentials()
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
    request = urllib.request.Request(TELEGRAM_API % token, data=payload,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except HTTPError as exc:
        # HTTPError 自己带着 Telegram 的说明（「can't parse entities: ...」），
        # 不读出来就只剩「HTTP Error 400: Bad Request」—— 那条信息等于没说，
        # 而这正是最常发生的一类失败（卡片名里的 _ * [ ] 会把 Markdown 解析搞崩）
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace")).get("description") or ""
        except (OSError, ValueError):
            detail = ""
        raise DeliveryError("Telegram 拒收（HTTP %s）：%s" % (exc.code, detail or exc.reason))
    except OSError as exc:
        raise DeliveryError("发送失败：%s" % exc)
    # HTTP 200 也可能是 ok:false（Markdown 解析失败、被限流…），必须查这个字段，
    # 否则会把“没发出去”当成成功，推送记录却已经落盘
    try:
        body = json.loads(raw)
    except ValueError:
        body = {}
    if not body.get("ok"):
        raise DeliveryError("Telegram 拒收（HTTP %s）：%s"
                                   % (status, body.get("description") or raw[:200]))
    return status
