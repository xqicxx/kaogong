---
name: "kaogong-mistake"
description: "错题本：录入错题（含答题信心与错因）、按错因分流、关联考点、跑掌握三关验证。触发：用户发来错题、说“这题我做错了”、要求验证某考点是否掌握、要看错题统计"
---

# 错题本（kaogong-mistake）

实现：`~/code/kaogong/pipeline/mistake.py`。设计依据见 `PLAN.md` 第 3 节。

## 录入一道错题

```bash
python3 ~/code/kaogong/pipeline/mistake.py new \
  --module 判断推理 --stem "归因论证-把另有他因当成最弱削弱" \
  --mine B --correct D --confidence 高 --cause 概念性 \
  --points "另有他因,否定此因" --lecture "逻辑论证-归因论证" --source "2024 国考 第 118 题"
```

**两个必填字段，缺了这道错题就没价值：**

- `--confidence 高/中/低` —— 答题时的信心。**高信心 + 概念性错误是最高优先级**：hypercorrection 效应让这类错误最好纠正（Butterfield & Metcalfe 2001），但一周后会复发（Butler et al. 2011）→ 间隔要更短、多轮复测
- `--cause 概念性/程序性/粗心/超纲` —— 决定进哪个流程：

  ```text
  概念性 → 概念转变：反例打脸 → 重构规则 → 举正例 → 变式验证（不是重做一遍！）
  程序性 → 进题型交错队列（判别对比），别按题型块刷
  粗心   → 不进复习队列，进「限时正确率」指标单独盯
  超纲   → 归档，低优先
  ```

**这两条都要从用户那里问出来，不要自己编。** 信心可以问“当时你是很确定还是蒙的？”；错因可以问“是不会，还是会但没用对方法，还是看错了？”

## 补内容（你来做）

`new` 只生成骨架。**真正让这道错题产生学习效果的是这两段**（Metcalfe 2017 的三个必要条件）：

```text
① 先自己重做（不看答案）   → 让「察觉到自己错了」发生
② 我当时为什么会这么想     → 产生「意外感」，这是纠错的关键
```

把用户的口述整理进这两段，别直接抄标准解析。

## 掌握三关

```text
未掌握 → 变式中 → 迁移中 → 待保持 → 已掌握
任一步失败 → 退一级（不清零）

变式 同知识点、换设问/换数字，连续 2 次正确 → 进迁移
迁移 同知识点、换题型或换情境，正确 → 安排保持测试
保持 N 天后仍正确 → 才算「已掌握」
```

```bash
python3 ~/code/kaogong/pipeline/mistake.py verify <考点卡> --kind 变式 --result 对
python3 ~/code/kaogong/pipeline/mistake.py queue     # 现在该验证哪些
python3 ~/code/kaogong/pipeline/mistake.py stats
```

## 关联

```bash
python3 ~/code/kaogong/pipeline/mistake.py link "另有他因 削弱"
```

走 qmd（vault 已建索引，`notes` collection）。给用户找同知识点错题、变式题线索时用它。

## 硬规则

- ❌ 不要把所有错题一视同仁 —— “每个错题都整理一遍”是错题本最常见的死法
- ❌ 不要把「粗心」的题放进复习队列
- ❌ 不要用“做过一遍”当掌握标准，必须走三关
- ✅ 高信心错误要单独标出来
- ✅ 每次录完汇报：题名 / 信心 / 错因 / 分流去向 / 关联考点
