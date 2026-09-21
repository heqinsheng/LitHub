# LitHub — 本地文献流水线

把 **Zotero 文献库**、**MinerU 文档转换** 和一个 **LLM CLI** 串成一条可续跑、幂等的流水线：
自动追踪某个研究方向的文献、读全文、写中文总结、按你的分类体系整理进 Zotero，
每天出一期带排序的文献日报。

> 面向「一个人 + 一个 Zotero 库 + 一条 cron」，不是多人服务。
> 所有脚本只用 Python 标准库，没有 `requirements.txt`。

## 你要哪个版本？

| 你的系统                            | 进这个目录                |
| ------------------------------- | -------------------- |
| **Linux**                       | [`linux/`](linux/)   |
| **Windows**（原生，或在 WSL2 里跑 Linux） | [`windows/`](windows/) |

两个目录里的**代码完全相同**。Windows 的适配（UTF-8 文件与子进程编码、命令行长度上限、
路径分隔符）是**合进主代码**的，不是分叉出来的第二个版本——分成两个目录只是为了让你
一眼找到该看哪份文档。`windows/` 里多一份 `WINDOWS_先读这个.txt`、Windows 安装说明
（`docs/windows.md`）和任务计划程序模板。

## ⚠️ 装之前先看这一条

**本项目必须放在 `~/LitHub`**（脚本里写死了这个根目录）。仓库分了两层，所以要把你要的
那个目录**整个搬出来**——不是在里面直接跑：

```bash
git clone <仓库地址> ~/lithub-src

mv ~/lithub-src/linux   ~/LitHub     # Linux 用户
# 或
mv ~/lithub-src/windows ~/LitHub     # Windows 用户（含 WSL2）
```

搬完 `~/LitHub/scripts/` 就该存在了。

## 如何开始：六步

一条从零到「日报每天自动推文献」的路。**第 ② 步和第 ⑥ 步各有一处与直觉相反，我标出来了。**

### ① 先备齐四样东西

| 需要                    | 干什么                            | 备注                                                              |
| --------------------- | ------------------------------ | --------------------------------------------------------------- |
| **Zotero 桌面版**        | 文献库本体，脚本经本地 API 读写             | 必须打开「允许其他应用与本机 Zotero 通信」，再做一次写入授权                               |
| **Obsidian**          | 读 `summary.md` 与日报（也可以只用任意编辑器） | L/mac/**Windows 都有原生版**，不是问题                                    |
| **一个 LLM CLI**        | 写中文总结、挑配图                      | 默认调 `~/.kimi-code/bin/kimi`，用环境变量 `LITHUB_KIMI` 换路径              |
| **MinerU API token**  | PDF → Markdown（保公式 / 表格 / 图）   | `mineru-open-api auth`；免费额度够个人用                                  |

> summarize_batch.py` 用的是 kimi 风格的参数
> （`-p`、`--agent-file=`、`--output-format stream-json`，以及环境变量
> `KIMI_LOOP_MAX_STEPS_PER_TURN`）。换别的 CLI 得确认这几个对得上，否则会退化成
> 「模型自己读文件」的老路径（每篇贵一倍多，见 `帮助手册.md` §6.5）。

备齐后先跑一次自检，它会逐项告诉你缺什么、怎么装：

```bash
python scripts/doctor.py
```

### ② 判断你的库够不够 50 篇

**够（≥ 50 篇）→ 直接跳到 ③。**

**不够 → 要先解决「该收什么文献」。这里有个现状必须说清：**

1. 先手工丢十几篇**你确定属于这个方向**的 PDF 进 `待整理/`，走一遍第 ③ 步
2. 拿这十几篇当种子，**照着它们归纳**出 3–6 个一级分类和每类的检索式
3. 写进 `config/topic.json`，跑 `python3 scripts/build_profile.py`

**目录分级这一步一定要人工过一遍。** 一级分类是**单值**的（一篇只归一个主类），但它可以
同时挂进多个一级目录（靠 `secondary` 字段）；子类则是多值的。**分类法定错，后面的检索式、
画像、库内打分全跟着错**——这是整个项目里最需要人的一步。

### ③ 生成 paper.md 与 summary.md

```bash
python3 scripts/intake_pdfs.py --remove    # 把 待整理/ 里的 PDF 收编进 Zotero
python3 scripts/mineru_batch.py all 3      # PDF → paper.md + images/
python3 scripts/summarize_batch.py 3       # 写中文 summary.md（约 0.04 元/篇）
python3 scripts/link_markdown.py           # 把两个 md 挂回 Zotero 条目
```

**「入库」的判据是跑到 `mineru_batch.py` + `summarize_batch.py`**，只建条目挂 PDF 不算。
自查：`papers/` 目录数 = 库内顶层条目数。

### ④ 读完总结，把目录收敛成一级 + 二级

一批总结读下来，你对这个方向的「骨架」就有感觉了。回到 `config/topic.json` 把分类法改准：
一级 3–6 个，每个 3–8 个子类。然后：

```bash
python3 scripts/build_profile.py         # 改完 topic.json 必须重跑（检索式经画像才进日报）
python3 scripts/apply_to_zotero.py       # 写回 Zotero 的目录与标签
python3 scripts/sync_classification.py   # 刷新 分类与标签总表.md
```

### ⑤ 调参数，出第一期日报

**第一次一定慢**——检索缓存是冷的（实测 ～**20 分钟**；之后同一周期内重跑零网络）。

跑之前先改**一处**：`config/runtime.json` 的 `digest.cat_floor`。留着作者的分类名
（`力学耦合=…`）会**启动即报错退出**——改成你的分类，或先留空 `""` 关掉保底。

```bash
python3 scripts/daily_digest.py --show-config   # 先看生效参数
python3 scripts/daily_digest.py --dry-run        # 预演：不写文件、不调 LLM
python3 scripts/daily_digest.py                  # 正式出一期
```

#### 嫌慢或嫌候选少：文献池四参数

`config/runtime.json` 的 `pool` 段是**文献池的取数深度**——**值越大 → 候选文献越多 →
「整理池子」越慢**。但**四个参数花的频率完全不同**，这是最容易搞错的地方：

| 键 | 现值 | 作用 | **什么时候花** |
|---|---:|---|---|
| `rows` | `300` | 每页拿多少条 | **几乎不花时间**——实测 100 行 4.0 秒 / 300 行 4.1 秒，三倍候选耗时不变 |
| `pages` | `7` | fresh 通道翻几页 | ⚠️ **每天都要付**（缓存键含 `since`，每天都变）。40 条检索式 × 7 页 = **每天最多 280 个请求** |
| `query_pages` | `3` | query 通道翻几页 | ✅ **只在跨周期时付一次**（默认 30 天），之后同周期内重跑零网络 |
| `refresh_days` | `30` | 池子多久重取一次 | 改它会**让缓存整体失效**，下一次整轮冷 |

也就是说：**"参数越大池子越慢"只对 `query_pages` / `refresh_days` 成立**；
`pages` 是每天都付的账。**想多要候选又不想天天等，先把 `rows` 拉满、
`query_pages` 调大，`pages` 保持小。**

> 完整说明与实测量见 `帮助手册.md` §6.2.3。

### ⑥ 循环：日报 → 待整理 → 入库

日报里感兴趣的，把 PDF 丢进 `待整理/`，回到第 ③ 步。如此往复。

**想干预「它推什么」，别去调分类权重——实测不管用。** 文档里有一条实测：
把某类的分类权重乘到 2.5 倍，那一类 85 篇候选里只有 19 篇来自该类检索式，最高分仍低于入选线。
根因是打分公式里 `R`（检索排名）**已与分类权重解耦**，分类权重现在只剩一处生效
（`C` 项里引用者的权重）。

**真正管用的是这两个：**

| 杠杆         | 改哪里                                                                          | 见效速度   |
| ---------- | ---------------------------------------------------------------------------- | ------ |
| **分类保底席位** | `config/runtime.json` 的 `digest.cat_floor`，如 `"力学耦合=3,界面反应=2"`               | 下一期立即  |
| **加检索式**   | `config/topic.json` 的 `categories.<类>.queries`，改完跑 `build_profile.py`         | 下一期    |

想知道某一类为什么一篇都没进版面：

```bash
python3 scripts/daily_digest.py --dry-run --diag-cats
```

它会打印每类的候选数量、命中来源（哪条检索式捞到的）、以及最高分离入选线差多少。

> 每一步的细节、全部参数与踩坑在 **`帮助手册.md`**：§1 是最短路径的上手清单，
> §4 是换方向要改的完整清单（**含代码里剩余的领域硬编码**），§6 是所有参数。

## 平台支持

| 平台          | 状态                                                                                                        |
| ----------- | --------------------------------------------------------------------------------------------------------- |
| **Linux**   | ✅ 作者主力环境，**已实测**                                                                                           |
| **macOS**   | ⚠️ 应该可用（纯标准库 + 同样的外部 CLI），**未实测**                                                                          |
| **Windows** | ⚠️ **代码已适配，但作者没有 Windows 机器、未实测**。见 [`windows/docs/windows.md`](windows/docs/windows.md)；也可以直接用 WSL2 原样跑 Linux |

不管哪个平台，第一次用都先跑自检，它会逐项告诉你缺什么、怎么装：

```bash
python scripts/doctor.py
```

## 这个项目做什么

| 环节       | 脚本                                       | 说明                                                               |
| -------- | ---------------------------------------- | ---------------------------------------------------------------- |
| 收编 PDF   | `intake_pdfs.py`                         | 按首页 DOI 匹配库里条目，缺则用 CrossRef 建条目，三段式上传 PDF 到 Zotero               |
| 转 Markdown | `mineru_batch.py`                        | 调 MinerU 精准模式，产出 `paper.md` + `images/`（公式、表格、图都保留）              |
| 中文总结     | `summarize_batch.py`                     | 每篇一个独立 LLM 会话，按 `prompts/summarize.md` 模板写 `summary.md`          |
| 挂回 Zotero | `link_markdown.py`                       | 把 `paper.md` / `summary.md` 作为 linked_file 附件挂到条目上（Obsidian 也能读） |
| 分类与标签    | `apply_to_zotero.py`                     | 按 `classification.json` 写入分类目录与各标签轴                              |
| 每日文献日报   | `daily_digest.py`                        | 三个通道取候选，按打分公式选出 N 篇，产出 `文献日报/YYYY-MM-DD.md` 与 `.urls.txt`        |
| 日报发信     | `send_digest.py`                         | 把当天的日报作为纯文本邮件发出（**零 token**）                                     |
| 引文图 / 画像 | `citation_graph.py` / `build_profile.py` | 共被引图（日报的引文通道靠它）、主题分布与时间趋势统计                                      |
| 库内文献打分   | `score_library.py`                       | 给库内每篇算「推荐分」，写进 Zotero 的 Extra 字段（**零 token**）                    |
| 清理       | `tidy.py`                                | 空条目 / 重复条目 / 孤儿目录 / 空分类目录，默认只报告，`--apply` 才动手                    |

## 仓库里有什么、没有什么

- **有**：全部代码、上手手册、一篇 **CC BY 4.0** 论文经流水线处理后的完整样例产出
- **没有**：任何论文原文与 PDF、作者的个人文献库、运行状态与缓存、API 密钥

## 许可

**代码与文档 MIT**（见 `LICENSE`）：`scripts/`、`prompts/`、`*.md` 都适用。

`examples/` 下的样例是一篇开放获取（**CC BY 4.0**）的期刊论文，其正文与图片的著作权归
原作者与出版方，按 CC BY 4.0 条款再分发，**不受 MIT 许可覆盖**——使用时请保留出处与许可声明。

---

更详细的介绍、**它和同类工具比有什么不一样**、以及**它做不到什么**（7 条），
写在两个版本各自的 `README.md` 里；完整上手路径与全部参数在 `帮助手册.md`。
