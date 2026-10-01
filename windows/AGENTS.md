# LitHub —— 文献管理流水线

锂离子电池正极材料文献的本地工作区，与 Zotero 库联动，由一组**幂等、可续跑**的脚本驱动。

**公开仓库**：https://github.com/heqinsheng/LitHub —— 发布包由 `scripts/make_release.py` 生成到
`release/`，再把 `release/` 推到该仓库的 `main` 分支；每个版本的变更写在**仓库首页**
`README.md` 的「更新记录」一节（素材是 `docs/release/root-README.md`，改那里才会进发布包）。

**文档分工**：本文件是 **AI 代理的常驻约定**——每轮都要用的操作、纪律与红线。它每次开会话整份进
上下文，所以**只放「照着做」与「别踩」的**：参考数据（字段表、参数表、实测数字）不放这里。
`帮助手册.md`：人看的上手手册（§1 上手、§4 换方向、§6 参数详解、§7 Zotero API 与库结构、
§8 MinerU、§9 定时任务、§12 全文翻译）。`docs/设计决策与实测记录.md`：为什么这么写与实测数据。
`README.md`：一页落地页。

## 任何方向都必看

第一次上手照 `帮助手册.md` §1 走。下面五条最容易踩、也最贵：

1. **领域内容全在 `config/topic.json`**（分类法 / 词表 / 检索式 / 关键词 / 闸门正则 / 期刊白名单）。
   改完**必须重跑 `python3 scripts/build_profile.py`**——检索式经 `state/profile.json` 才进日报。
2. **运行参数全在 `config/runtime.json`**，改完**立即生效**、不必重跑画像。优先级 **CLI >
   `runtime.json` > 内置默认**，所以 systemd unit 的 `ExecStart` 里写死的 `--days`/`--limit` 会盖过它。
   换领域后**第一件事是改 `cat_floor`**（留着作者的分类名会启动即报错退出）。
3. **代码里仍有领域硬编码**（`daily_digest.py` 的 `LAYERED_PAT`、`find_new.py` 的 `relevant()`、
   `translate()` 的系统提示词……）换方向不改就会一直按原方向筛；清单见 `帮助手册.md` §4.2。
4. **「入库」的判据是跑到 `mineru_batch.py` + `summarize_batch.py`**，只建条目挂 PDF 不算。
   自查：`papers/` 目录数 = 库内顶层条目数，且每篇 `paper.md` 与 `summary.md` 都在。
5. **先跑通窄链路再加自动化**：Zotero 授权 → 1–2 篇 PDF 入库 → 手写 `classification.json` →
   `apply_to_zotero.py` → `mineru_batch.py` → `summarize_batch.py` → `link_markdown.py`；库里
   30–50 篇之后再重写 `topic.json`、跑画像；**最后**才配 systemd 与调打分权重。每步先用 `--dry-run`；
   `tidy.py --apply` 是唯一会删东西的命令。

## 目录与配置速查

- **`state/` 下只有两个文件手改会出错**：`classification.json`（逐篇分类，**权威、手工维护**，
  格式见 `帮助手册.md` §5.4）与 `profile.json`（`build_profile.py` 生成，别手改）。其余都是脚本
  产物与备份（谁产出哪个文件见 §1.2 的步骤表）。
- **密钥**（`smtp_password` / `zotero_local_key` / `kimi_code_key`）**chmod 600，绝不进仓库**。
- 目录树与本篇目录的命名规范（`papers/<KEY>_<标题前60字>`；**标题一改目录名就变**，脚本统一走
  `paper_dir()`，别手工重命名）见 §2.1 目录速查。
- 逐字段的调参说明：`topic.json` 见 §4.1、`runtime.json` 见 §6.1–§6.2；打分工作点的标定见 §4.4。

## Zotero 库结构速查

- **一级分类是单值的**（6 个方向 + 综述，另有子目录），交叉主题靠两套附加轴：`classification.json`
  的 `secondary` 会一并写进对应的一级目录（标签 `兼属:<一级分类>`），`coupling` 只标注、**不参与打分**。
- **`归档` 之下不参与日常整理**；**`其它*` 之下是「备用库」侧库**——照常 MinerU + **极简总结**，
  但不参与推荐分 / 画像 / 日报，也不写 `classification.json`、不打标签、不挂 md 链接。
  ⚠️ 判据是**归属**（`zapi.is_backup_item()`）：只要条目还挂在任一 `其它*` 目录下就会被排除；
  想把侧库条目「**转入主库**」必须先移出该子树，五步流程见 §7.13 末条。
- 标签轴的完整取值、耦合方向的判定准则、种子文献的两张杠杆、改名/删标签的走法：**§7.13–§7.14**。
- 直接加进 Zotero 的条目（备用库这类）收编流程看不到，要跑
  `intake_pdfs.py --from-collection "<目录名>"` 才能进 MinerU（见 §7.13）。

读写 Zotero 前**先读** `帮助手册.md` §7 或调用 skill `zotero-local-api`，里面记录了若干静默失败的坑。

## 常用操作

```bash
cd ~/LitHub

python3 scripts/mineru_batch.py all 3           # PDF → Markdown（幂等，引擎不同则自动重转）
python3 scripts/summarize_batch.py 3            # 逐篇中文总结（幂等；--dry-run 1 KEY 只打印提示词）
python3 scripts/sync_properties.py --apply      # 只刷新已有 summary.md 的属性区（不重跑模型）
python3 scripts/sync_classification.py          # 更新分类数据与总表
python3 scripts/link_markdown.py                # 把新文献的 md 挂到 Zotero（幂等）
python3 scripts/build_embeddings.py             # 库内语义向量（日报的 S 项用，需先装依赖）
```

各脚本的完整 CLI 参数见 `帮助手册.md` §6.4–§6.6；下面只记**不写进参数表的约定与红线**。

| 脚本 | 约定与红线 |
|---|---|
| `intake_pdfs.py` | 收编 `待整理/` 全量 + `~/Downloads/` **今日新增**；`--remove` 删两个来源的原件。首页提不到 DOI 的在 `待整理/_doi_hints.json` 补一行。`--from-collection` 只补 manifest（收 preprint、不联网、不新建条目） |
| `summarize_batch.py` | 范围用 `<并发> [KEY,KEY...] [--force]`；`--force` 才重做已有 `summary.md`。三个省 token 开关默认已开；`--figures` **别开**（本环境读不到图）。模板三套按 `is_review` 自动选；**总结里的图是 `embed_figures.py` 事后插的**，漏跑那批就一张图都没有 |
| `embed_figures.py` | 只喂图注与结论挑 3–5 张，**图片路径由脚本解析填入**；已带「关键图表」节的跳过 |
| `sync_properties.py` | 属性区**不由模型写**，由脚本从 Zotero 写入；只报告模式**只算不落盘**，落盘必须显式 `--apply` |
| `link_markdown.py` | 每篇挂 2 个 `linked_file`（`paper.md` / `summary.md`），**绝对路径**，移动 `~/LitHub` 会失效；幂等 |
| `translate_batch.py` | 产物 `全文翻译（含批注）.md`**文件名固定、一字不差**；「**分段 + 脚本拼装**」是设计核心——**别让模型做拼装**；五条红线由 `verify()` 拦。详见 §12 |
| `score_library.py` | 前置 = `library_index.json` + `citation_graph.json` + `classification.json`；与日报**共用同一张权重表**。⚠️ 别忘 `LITHUB_MAILTO`，漏了 `A` 项永远为 0 且每篇白等 ~12 秒 |
| `tidy.py` | **唯一会删东西的命令**；`--apply` 前把要删的全量导出到 `state/tidy_backup_<日期>.json`。`DELETE` 是**硬删除、没有回收站** |
| `fill_meta.py` | **只补空字段、绝不覆盖已有值**；宁可不动，也不写可能错的 DOI |
| `fix_control_bytes.py` | 占位符**不要用 `□`**——本库里 `□` 是晶格空位的合法记号 |
| `daily_digest.py` | `--min-score` 是**硬门槛**；`--show-config` 看生效值。三轮发牌与保底机制见 §6.3 |
| `send_digest.py` | 零 token；收件人在 `config/runtime.json` 的 `email` 段，**密码读 `state/smtp_password`** |
| 其它 | `find_new.py`（库外检索）、`journal_if.py`（期刊层级表，年报刷新）、`build_profile.py`（画像，零 API 花销）、`doctor.py`、`migrate_labels.py`（标签改名/删游离标签，见 §7.14） |

## 收编的完整流程

> **判据**：**跑到 `mineru_batch.py` 与 `summarize_batch.py` 才算**，只建条目 + 挂 PDF 不算。

收编（`intake_pdfs.py`）只负责「条目 + PDF 附件 + manifest」。随后四步才算整理完：

1. 在 `state/classification.json` 补一条分类 → `python3 scripts/apply_to_zotero.py`
   → `python3 scripts/sync_classification.py`
2. `python3 scripts/mineru_batch.py all 3` → `summarize_batch.py 3` → `embed_figures.py 4`
   → `python3 scripts/link_markdown.py`
3. **刷新库内派生数据**（这几个文件按 key/DOI 存快照，**不会自己察觉新条目**，必须按顺序手动跑）：
   `library_index.py` → `citation_graph.py` → `score_library.py --apply` → `digest.py json`
   → `build_profile.py`
4. 收尾 `python3 scripts/tidy.py --apply`

完整命令、`_doi_hints.json` 的用法与踩过的坑见 §5.3；换用 `--flash` / 引擎选择的细节见 §8。

## 环境注意事项

- ⚠️ **必须设 `LITHUB_MAILTO`**（两个 systemd unit 的 `Environment=` 里已写死）：不设时默认
  `you@example.com`，**Unpaywall 直接 422 拒绝**，`A` 项永远为 0 且每篇白等 ~12 秒。
- ⚠️ **写网络代码要显式强制 IPv4**：本机曾有「有全局 IPv6 地址与默认路由、但 100% 丢包」，而
  Python 没有 happy-eyeballs 回退，会死等超时（实测同一请求 181 秒 vs 强制 IPv4 的 0.72 秒）。
  沿用 `daily_digest.py` / `find_new.py` 里那段 `socket.getaddrinfo` 包装。
- **国内网络**：pip 已配阿里云镜像；HuggingFace 不可达，需 `export HF_ENDPOINT=https://hf-mirror.com`。
  MinerU 的 `--flash` 有 **10 MB / 20 页**双限制（**文件大小先于页数触发**），超 8 MB 先用
  ghostscript 预压缩。
- Obsidian 已注册本目录：`.md` 默认用它打开，`obsidian-open` 必须用 `xdg-open` 发 URI。

细节见 §3.8–§3.10（环境）、§8.2（引擎与限额）、`docs/` §20–§21。

## 省 token 的工作方式（做长表、批量读文献、批量抽数据时）

**2026-09-30 实测**：同一批 42 篇文献，因为分类方案改了三次，数据被 LLM **重述了 6 遍**
（必要的那遍笔记 376 KB，外加六种表式中间产物约 400 KB），最终只有 1/7 活到成品。
下面五条专挡这类浪费，做「多篇文献 → 一张长表」的活之前先读一遍。

1. **抽一次，之后重组只用脚本。** 先定义**原子行**（一行一个「论文 × 量 × 条件」，字段固定，
   存 TSV），让子代理只产出这一份；**分组、翻译、压缩、排序、取舍全部写脚本**，不再让模型重述。
   重组是纯数据操作——付模型的钱去做 `cut`/`sort` 能干的事是最贵的错误。
2. **先定式样再批量。** 先用 2–3 行样例跟用户确认**列与粒度**，确认后才跑全量。用户改的通常
   是式样而不是内容；式样没定就跑，改一次就要重述整批。
3. **子代理只回一行结论，正文写文件**；主代理**不要把长表或长文件打印进自己的上下文**，
   只 `cut` 出要看的列/行。要读全文的任务一律交子代理分片读（2.6 MB 全文进主上下文是灾难）。
4. **先查本地权威源再联网。** 手稿的 Zotero 域里就有全部引文的精确 DOI（`extract_csl.py`）；
   用标题反查 CrossRef 既慢、又会认错条目（2026-09-30 白跑过一轮）。
5. **读大文本必须带 `line_offset`**（漏参数会把整篇读回），**脚本先想清解析方式再写**
   （按行/按段/按列定错，返工一轮的成本常超过先想清楚的时间）。

## 风格约定

- 总结和综述用中文，数值必须具体，原文没给的写「未报道」，不推测。
- 脚本一律**幂等 + 可续跑**（已完成的跳过），输出到 `logs/`。
- **CLI 纪律（2026-09-22 起）**：所有脚本的 `-h` / `--help` **只打印用法、退出 0**；**认不出的
  `--xxx` 一律报错退出 2**，绝不静默忽略；用 `argparse` 的脚本全部关掉前缀缩写（`--f` ≠ `--force`）。
  这条是拿事故换来的：此前 `--help` 会**真跑**（2026-09-21 因此误写了两篇 `summary.md`、误发了一封
  日报邮件），而 `send_digest.py --f` 会**真发信**。
- 对 Zotero 的批量写入以**纯新增**为默认；删除只有两条路：条目/目录/重复项走 `scripts/tidy.py`，
  **标签改名与游离标签清理**走 `scripts/migrate_labels.py`。两者都先导出全量备份到 `state/` 再动手；
  `DELETE` 是硬删除、没有回收站兜底。
- 每次整理完都要清理**空条目与重复条目**（外加孤儿 `papers/` 目录、空分类目录），即
  `python3 scripts/tidy.py --apply`。
- `summary.md` 的固定七节与每节的格式要求，**以 `prompts/summarize.md`（综述用
  `summarize_review.md`）为准**——模板就是规范，改模板即改规范。三条容易忘的：属性区**由
  `sync_properties.py` 从 Zotero 写**、模型不碰；`library_context()` 已按标签重合度选好库内对照清单
  （综述最多 2 篇），模型只能引用清单里给出的信息；七节之外的 `## ` 章节（含手工的
  `## 库内同主题工作` 与脚本写的 `## 关键图表`）算人工内容，`preserve_manual()` 会原样插回，
  `--force` 也不冲掉。详见 `docs/设计决策与实测记录.md` §22。
