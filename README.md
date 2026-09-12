# 考公知识系统

把纸质讲义/书拍成照片 → 转成 Obsidian 里的 markdown 与考点卡片 → 按间隔重复排期复习 → 错题回流验证掌握。

**完整方案（含实测数据与文献证据）：[PLAN.md](PLAN.md)**

## 为什么是这套

- OCR 用 **macOS Vision**（系统自带）：0.64s/张、峰值 152MB、零模型下载零安装
- 拍照必须**透视矫正**；红笔标注要**去红**；头号错误源是**纸张背面透印**（背后垫黑纸可解）
- 复习排期用 **FSRS-6**，初始间隔按考试日反推（最优间隔 ≈ 保持期的 10–20%，Cepeda 2008）
- 知识点与题型**分两套机制**：知识点靠间隔检索，题型靠交错练习（判别对比）
- 错题按**错因分流**，并记录**答题信心**（hypercorrection 效应：高信心错误最值钱也最容易复发）

## 目录

```text
tools/            OCR 工具链（Swift + Python，零第三方依赖）
pipeline/         考点切分：页 md → 骨架笔记 + 考点卡片
vault-templates/  Obsidian 模板（考点卡 / 错题）
docs/             文献与实测记录
```

## 安装

```bash
./tools/install.sh          # 编译 Swift 工具、装脚本到 ~/.pi/bin
```

需要 macOS（Vision / Core Image）+ python3（只用标准库）。**没有第三方依赖。**

## 用法

```bash
# 1) 照片 → markdown（机器产出落到 考公/_raw/<模块>/）
tools/kaogong-ocr.sh --source "逻辑论证-归因论证" --module 行测/判断推理 图1.jpg 图2.jpg

# 2) 校对：把 _raw/ 的机器产出对着原图核一遍，写到 考公/<模块>/
#    （校准记录写在文件尾部的注释里；拿不准就裁切放大看）
tools/crop 原图.jpg 局部.png 0 1400 1760 800 2.5

# 3) 切考点：骨架 + 卡片
pipeline/split.py --module 行测/判断推理 --lecture 逻辑论证-归因论证 \
  "$HOME/Documents/Obsidian Vault/考公/行测/判断推理/逻辑论证-归因论证-p001.md" ...
```


## ⚠️ 改了 tools/ 之后一定要重跑安装

    ~/code/kaogong/tools/install.sh

vision2md.py 与 kaogong-ocr.sh 现在是软链（改了立即生效），
但 **Swift 二进制（rectify / deink / vision-ocr / crop）是编译产物，必须重装才生效**。
踩过这个坑：改完参数校验去测试，行为没变 —— 因为跑的还是 ~/.pi/bin 里的旧拷贝。

## 去重（同一页拍多次也好收拾）

```bash
tools/kaogong-ocr.sh --source "逻辑论证-归因论证" --module 行测/判断推理 图1.jpg 图2.jpg
  跳过 19743 —— 已处理过：与 逻辑论证-归因论证-p002.md 是同一页（相似度 0.54）
  全部是重复页，没有新内容需要写
```

判据：文字相似度 ≥ 0.45（同页重拍 0.61 / 相邻页 0.04），或页脚页码相同 + ≥ 0.25。
批内与跨次运行都去；`--no-dedup` 可关。

**卡片不去重**：同一组的兄弟条目相似度天然就高，合并等于丢知识点。
处理方式是**错开排期**（同组卡不同天出现）。

## 两类笔记

```text
考公/行测/<模块>/<讲义>.md     骨架层：按页拼接的长文，查阅用
考公/考点/<考点名>.md          卡片层：一条一个考点，记忆用（绑回骨架双链）
考公/错题/<题目>.md            错题：含信心与错因，按错因分流
考公/_raw/<模块>/              机器原始产出（未校对），永不删
```

### 模块划分

```text
pipeline/
  paths.py    所有路径的唯一来源（KAOGONG_VAULT / KAOGONG_STATE 环境变量可覆盖，
              所以测试能跑在临时库上，不碰真笔记）
  errors.py   领域异常：CardNotFound / AmbiguousCard / InvalidState / ConfigError / DeliveryError
  fsrs.py     排期算法 —— 纯函数，无 I/O，可以单独验
  mastery.py  掌握三关状态机 —— 同样纯函数
  cards.py    卡片读取 / 查找 / 写回状态（review 与 mistake 共用）
  notify.py   Telegram 传输
  vault.py    front-matter 读写、原子写、写前快照
  review.py   命令行：排期 / 推送 / 回分
  mistake.py  命令行：错题录入 / 分流 / 关联 / 验证
  split.py    页面 markdown → 骨架 + 卡片
```

两条约定：

- **库函数只管抛错，只有 main() 把异常翻成退出码**（不再从深处 SystemExit）
- **纯逻辑（fsrs / mastery）不 import 任何做 I/O 的模块**，所以能被单独断言

## 数据安全与回滚

流水线会写你的笔记，所以：

- **写前留快照**：每次改写前把原文件存到 `~/.pi/kaogong/backup/<日期>/`（保留 14 天）
  回滚：`cp ~/.pi/kaogong/backup/2026-09-13/另有他因.md "$HOME/Documents/Obsidian Vault/考公/考点/"`
- **原子写**：先写临时文件再 `os.replace`，中途崩不会留下半个笔记
- **权限保留**：不会把 644 的笔记改成 600
- **原始件永不删**：`_raw/` 留着机器产出，分得清是识别错还是校错

推荐把 vault 也纳入版本控制（obsidian-git 插件已装，配一下 remote 即可）。

## 卸载

    launchctl unload ~/Library/LaunchAgents/com.kaogong.review.plist
    rm ~/Library/LaunchAgents/com.kaogong.review.plist
    rm -rf ~/.pi/bin/{rectify,deink,vision-ocr,crop,vision2md.py,kaogong-ocr.sh} ~/.pi/kaogong

笔记本身在 vault 里，不会被卸掉。

## 状态

- [x] 拍照 → markdown 工具链（实测 0.7s/张）
- [x] 目录结构 + 考点卡片切分（骨架 + 卡片两层）
- [x] FSRS 排期 + 每天早上 08:00 Telegram 推送
- [ ] 错题本模板 + 语义关联
- [ ] 掌握验证闭环（变式 / 迁移 / 保持三关）

目标：2026 年 12 月上旬 · 四川省考（2027 年度）· 首轮复习间隔 12 天

## Skill

两个流程已装成 pi skill：

- `kaogong-ingest`　拍照 → 矫正/去红 → OCR → **对着原图校准** → 落库 → 切卡片
- `kaogong-review`　每日到期推送 → 评分回写 → 进度查询

## 排期

状态写在卡片自己的 front-matter 里（不另建数据库）：

```yaml
到期: 2026-09-25
稳定度: 12.0
难度: 5.16
复习次数: 0
上次复习:
```

```bash
pipeline/review.py push            # 发今天清单
pipeline/review.py push --dry      # 只看不发
pipeline/review.py grade <卡> <1-4>
pipeline/review.py selftest        # 排期逻辑自检
```

评分：1 忘了 / 2 勉强 / 3 对了 / 4 太简单。**2 不算“回忆正确”**（successive relearning）。
