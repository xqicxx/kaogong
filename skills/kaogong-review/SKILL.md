---
name: "kaogong-review"
description: "考公考点卡片的间隔重复排期：每日到期推送、评分回写、进度查询。触发：用户问“今天复习什么”、回复卡片评分、要看复习进度、或早上 8 点的定时推送"
---

# 复习排期（kaogong-review）

纸片实现：`~/code/kaogong/pipeline/review.py`（FSRS-4.5 核心公式，无第三方依赖）。
**状态写在卡片自己的 front-matter 里**（`到期` / `稳定度` / `难度` / `复习次数` / `上次复习`），不另建数据库 —— Obsidian 里看得见、Dataview 查得到、git 记得住。

## 命令

```bash
python3 ~/code/kaogong/pipeline/review.py push          # 发今天的复习清单到 Telegram
python3 ~/code/kaogong/pipeline/review.py push --dry    # 只看不发
python3 ~/code/kaogong/pipeline/review.py due           # 控制台列到期卡片
python3 ~/code/kaogong/pipeline/review.py grade <卡片名> <1-4>
python3 ~/code/kaogong/pipeline/review.py stats
python3 ~/code/kaogong/pipeline/review.py init          # 新卡片初始化排期
python3 ~/code/kaogong/pipeline/review.py selftest      # 排期逻辑自检
```

定时：`~/Library/LaunchAgents/com.kaogong.review.plist`（每天 08:00 自动 `push`）。

## 评分语义（关键，直接决定排期对不对）

```text
1 忘了    完全想不起来，或想起来的是错的      → 间隔砍半，很快再见面
2 勉强    想起来了，但有犹豫 / 需要提示      → 算“没达到正确回忆”
3 对了    干净地回忆出来，没有犹豫         → 正常推进
4 太简单  秒答，几乎不费力                 → 大幅拉长
```

**successive relearning（Rawson & Dunlosky）：掌握的门槛是“干净回忆”，不是“看过觉得会”。**
评分时必须按这个卡：用户说“有点印象”“大概记得” → 就是 2，不是 3。

## 用户回分怎么处理

推送里每张卡带编号。用户回 `3 4` 这类 → 表示第 3 张给评分 4。
把编号映射回卡片名（就是本次 push 的顺序），再逐张执行 `grade`。
映射靠不住时（用户回分跨天、顺序不明）→ 先把清单重新列出来确认，别猜着写。

## 推送时遵守的三条证据

1. **逾期优先**：按 FSRS 的可提取度 R 升序排（最可能忘的先做），不是随机抽
2. **语义相近的错开**（LECTOR 2025）：同一批里别同时出现“另有他因 / 伪他因 / 排除他因”这类近义考点，会记混而不是忘记
3. **题型交错**（Foster 2019 的判别对比机制）：题型训练要混着出，别按题型块刷

## 常见的坑

- ❌ 别把“今天到期 0 张”当成没事 —— 检查是不是新卡片没 `init`
- ❌ 别手动改 `到期` 字段来“提前复习” —— 那会污染稳定度估计；想加练就新建一张变式卡
- ✅ 每次改完卡片，报一行：`卡片名 评分 下次日期（N 天后）`
- ✅ 用户问进度时给数字：总数 / 已排期 / 今天到期 / 平均稳定度（`stats` 直接有）

## 相关

- 方案与证据：`~/code/kaogong/PLAN.md` 第 2 节
- 错题（任务三）：`考公/错题/`，模板在 `~/code/kaogong/vault-templates/错题.md`
