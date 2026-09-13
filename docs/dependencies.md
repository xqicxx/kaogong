# 依赖清单

> 出问题时该怀疑谁：OCR 不对 → Vision；检索不准 → qmd/embedding；审查漏报 → ocr。

## 第一层：本项目自己的代码 —— 零第三方依赖

```text
Python   只用标准库：argparse json re os sys math datetime pathlib shutil
         subprocess tempfile urllib collections importlib
Swift    只用系统框架：Foundation Vision CoreImage CoreGraphics AppKit ImageIO
Shell    只用 BSD 自带：bash sips plutil curl date
```

没有 `requirements.txt`、没有 `package.json`、没有 `pyproject.toml`。
刻意的：这套东西要在这台机器上长期跑，少一个依赖就少一个半夜崩的可能。

## 第二层：流程里调用（或调用过）的开源工具

| 工具 | 版本 | 许可 | 来源 | 用在哪 |
| --- | --- | --- | --- | --- |
| qmd | 2.8.3 | MIT | github.com/tobi/qmd | 语义 / 全文检索（vault 的 `notes` collection） |
| llama.cpp（llama-server） | 0.4.0 (build 10809) | MIT | github.com/ggml-org/llama.cpp | 本地跑 embedding / reranker |
| open-code-review（ocr） | 1.12.0 | Apache-2.0 | github.com/alibaba/open-code-review | **只在开发期**审查本项目代码，运行时不用 |
| Apple Vision / Core Image | 系统 | 非开源 | 系统框架 | OCR 与透视校正 |
| swiftc | Xcode CLT | 非开源 | 系统 | 编译 `tools/*.swift` |

## 第三层：模型权重（不是代码）

| 模型 | 许可 | 用途 |
| --- | --- | --- |
| Qwen3-Embedding-0.6B | Apache-2.0 | 向量检索（qmd 的 embed 后端） |
| Qwen3-Reranker-0.6B | Apache-2.0 | 检索重排 |
| LFM2.5-1.2B | Liquid AI 开放许可 | 查询改写（HyDE） |

## 第四层：运行底座

```text
pi 扩展（全部 MIT）
  pi-memory 0.4.2 · pi-lens 4.1.6 · pi-subagents 0.67 · pi-web-access 0.29
  pi-markdown-preview 0.16 · pi-agent-extensions 0.5.4 · pi-mcp-adapter 2.33
  pi-fabric 0.92.4 · @llblab/pi-telegram 0.45.5 · @dietrichgebert/ponytail 4.9

Obsidian 插件（vault 侧）
  dataview · tasks · templater · quickadd · smart-connections · obsidian-git
  kanban · calendar · remotely-save · mermaid-tools · excalidraw
```

## 特别说明：哪些是「参考」而不是「依赖」

- **FSRS 排期算法**没有用别人的库（py-fsrs / fsrs-rs），是照着
  open-spaced-repetition 公开的公式自己实现的。公式完全确定，依赖一个库会让
  「为什么算出这个间隔」变得不可追。代码注释里写了公式、参数来源与升级路径。
- **红笔层提取**（`deink --red-only`）是自己写的像素判据（红墨水 R 高、G/B 低），
  没有依赖 OpenCV 之类的图像库。
- **去重**用字符 3-gram Jaccard，没有用 MinHash / simhash 库 —— 语料只有一库笔记，
  直接比较够快。

## 试过又删掉的

| 工具 | 为什么删 |
| --- | --- |
| MinerU 3.4.5（版面 + OCR + 公式） | 实测不如系统自带 Vision（同页相似度 1.000 vs 0.994，还慢 10 倍、内存 7 倍）。连同 4.6G 模型缓存一起删了 |
| docling / marker-pdf | 评估后没上：都要拖进 torch，内存与依赖代价高，而 Vision 已经够用 |

## 许可证

全部 MIT / Apache-2.0，**没有 GPL 类传染性许可**，本项目（MIT）与它们兼容。
