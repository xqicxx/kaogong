#!/usr/bin/env python3
"""库内面板 —— 把「今天该复习什么」写成一个普通 markdown 笔记。

PLAN 2.3 的落点写的是「Obsidian Tasks / Dataview：⏳ 到期日 + 逾期红标」。
但查过这台机器的 `.obsidian/plugins` —— **Dataview 根本没装**，
写成 dataview 代码块就是一块死代码：打开只看到一段 query 文本。

所以改成生成静态 markdown：
    · 不依赖任何插件，Obsidian 打开就能看
    · 手机上也能看（不用等推送）
    · 内容每次推送后自动重写，不会过期

纯函数 build() 负责排版，refresh() 负责落盘 —— 排版逻辑能单独测。
"""
import datetime

from pipeline.errors import KaogongError  # type: ignore[import]
from pipeline.paths import VAULT_ROOT  # type: ignore[import]
from pipeline.vault import write  # type: ignore[import]

PANEL_NAME = "复习面板.md"


def _row(*cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def build(due, fresh, pending, broken, drop, today=None):
    """排一份面板 markdown。

    due / fresh / pending / drop 都是「已排好序的行」，每行是 (卡名, 模块, 备注)；
    broken 是坏卡名字列表。
    """
    # 正文不含 front-matter —— 那一段由 refresh() 交给 vault.write 统一写，
    # 否则会和 write() 自己包的那层 `---` 叠成两道（真出过这个 bug）
    out = [
        "# 复习面板",
        "",
        "> 每次推送后自动重写。不依赖任何插件，打开就能看。",
        "",
    ]

    def table(title, rows, empty_note):
        out.append("## %s（%d）" % (title, len(rows)))
        out.append("")
        if not rows:
            out.extend([empty_note, ""])
            return
        out.append(_row("卡名", "模块", "备注"))
        out.append(_row("---", "---", "---"))
        for name, module, note in rows:
            out.append(_row(name, module or "—", note or "—"))
        out.append("")

    table("今天到期", due, "今天没有到期的 —— 休息，或者去录新错题。")
    table("新学", fresh, "没有待引入的新卡。")
    table("待验证（掌握三关）", pending, "没有卡在验证流程里的。")
    table("无鉴别力（建议剔出验证集）", drop, "没有需要剔除的题。")

    if broken:
        out.extend(["## ⚠️ 排期字段坏了（%d）" % len(broken), "",
                    "这些卡的状态读不出来（可能手改过），已跳过排期，不影响其它卡：", ""])
        out.extend("- %s" % name for name in broken)
        out.append("")
    return "\n".join(out)


def refresh(text, today=None):
    """把面板正文写进 vault。返回写出的路径。

    走 vault.write：它有原子写（先临时文件再 replace）与当天快照，
    比自己 open() 写安全 —— 中途崩不会留下半页面板。
    """
    target = VAULT_ROOT / PANEL_NAME
    if not VAULT_ROOT.exists():
        raise KaogongError("vault 不存在，面板写不进去：%s" % VAULT_ROOT)
    today = today or datetime.date.today()
    write(target, {"type": "面板", "生成时间": "%s" % today.isoformat()}, text, "")
    return target



