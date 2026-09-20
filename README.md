# LitHub — 本地文献流水线

把 **Zotero 文献库**、**MinerU 文档转换** 和一个 **LLM CLI** 串成一条可续跑、幂等的流水线：
自动追踪某个研究方向的文献、读全文、写中文总结、按你的分类体系整理进 Zotero，
每天出一期带排序的文献日报。

> 面向「一个人 + 一个 Zotero 库 + 一条 cron」，不是多人服务。
> 所有脚本只用 Python 标准库，没有 `requirements.txt`。
> 仓库里带的是一份「锂离子电池层状氧化物正极」的配置，**换成你自己的方向见手册 §4**。

## 它做什么

| 环节 | 脚本 | 说明 |
|---|---|---|
| 收编 PDF | `intake_pdfs.py` | 按首页 DOI 匹配库里条目，缺则用 CrossRef 建条目，三段式上传 PDF 到 Zotero |
| 转 Markdown | `mineru_batch.py` | 调 MinerU 精准模式，产出 `paper.md` + `images/`（公式、表格、图都保留） |
| 中文总结 | `summarize_batch.py` | 每篇一个独立 LLM 会话，按 `prompts/summarize.md` 模板写 `summary.md` |
| 挂回 Zotero | `link_markdown.py` | 把 `paper.md` / `summary.md` 作为 linked_file 附件挂到条目上（Obsidian 也能读） |
| 分类与标签 | `apply_to_zotero.py` | 按 `classification.json` 写入分类目录与各标签轴 |
| 主动检索 | `find_new.py` | 按库内分类检索库外新文献，产出候选清单 |
| 每日文献日报 | `daily_digest.py` | 三个通道取候选，按打分公式选出 N 篇，产出 `文献日报/YYYY-MM-DD.md` 与 `.urls.txt` |
| 日报发信 | `send_digest.py` | 把当天的日报作为纯文本邮件发出（配置在 `runtime.json`，**零 token**） |
| 引文图 / 画像 | `citation_graph.py` / `build_profile.py` | 共被引图（日报的引文通道靠它）、主题分布与时间趋势统计 |
| 库内文献打分 | `score_library.py` | 给库内每篇算「推荐分」，写进 Zotero 的 Extra 字段（**零 token**） |
| 清理 | `tidy.py` | 空条目 / 重复条目 / 孤儿目录 / 空分类目录，默认只报告，`--apply` 才动手 |

## 从这里开始

**新来的先读 [`帮助手册.md`](帮助手册.md) 的 §1** —— 一条照着做就能跑通的路径，
约 30 分钟能看到第一个成果（一篇 PDF 变成 Zotero 里带中文总结的条目）。

| 想做什么 | 看哪里 |
|---|---|
| 第一次跑起来 | `帮助手册.md` §1 |
| 换成我自己的研究方向 | `帮助手册.md` §4（**含代码里剩余的领域硬编码清单**） |
| 查参数 / 调日报权重 | `帮助手册.md` §6 |
| Zotero 本地 API 的坑 | `帮助手册.md` §7 |
| MinerU 转换的坑 | `帮助手册.md` §8 |
| 出错了 | `帮助手册.md` §10 故障速查 |
| 项目内部约定与实现决策 | `AGENTS.md` |

**前置**（详见手册 §3）：Python 3.12+（无第三方包）、Zotero 桌面版保持运行并打开
「允许其他应用与本机 Zotero 通信」、MinerU CLI 配好 token、一个 LLM CLI。
**项目必须放在 `~/LitHub`。**

## 样例

`examples/NatureComm_2024_grain-level-chemo-mechanics/` 是一篇 **CC BY 4.0** 开放获取论文
经本流水线处理后的完整产出（`paper.md` + `summary.md` + 图），用来说明输出长什么样。
其余论文一律不进仓库（`state/`、`papers/`、`文献日报/` 同理）。

## 许可

代码与文档 MIT（见 `LICENSE`）。`examples/` 下的论文内容按 **CC BY 4.0** 授权，
著作权归原作者与出版方。
