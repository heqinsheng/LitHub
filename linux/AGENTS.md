# LitHub —— 文献管理流水线

锂离子电池正极材料文献的本地工作区，与 Zotero 库联动，由一组**幂等、可续跑**的脚本驱动。

**公开仓库**：https://github.com/heqinsheng/LitHub —— 发布包由 `scripts/make_release.py` 生成到
`release/`，再把 `release/` 推到该仓库的 `main` 分支；每个版本的变更写在**仓库首页**
`README.md` 的「更新记录」一节（素材是 `docs/release/root-README.md`，改那里才会进发布包）。

**文档分工**：本文件是 **AI 代理的常驻约定**——每轮都要用的操作、纪律与红线。它每次开会话整份进
上下文，所以**只放「照着做」与「别踩」的**：参考数据（字段表、参数表、实测数字）不放这里。
`帮助手册.md`：人看的上手手册（换方向 §4、参数详解 §6、Zotero API §7、MinerU §8）。
`docs/设计决策与实测记录.md`：为什么这么写与实测数据（本文件写作 §N）。`README.md`：一页落地页。

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

## 目录结构

```
~/LitHub/
├── papers/<KEY>_<标题前60字>/   # paper.md（MinerU 全文）+ summary.md（中文总结）+ images/
│                              #   + 全文翻译（含批注）.md（可选，见「全文翻译」一节）
├── state/                       # 运行状态，按 key / DOI 存快照（见下）
├── prompts/                     # 提示词：summarize.md（研究论文）/ summarize_review.md（综述）
├── config/{topic,runtime}.json  # 领域配置 / 运行参数（各有专节）
├── scripts/                     # 全部可重跑、幂等
├── logs/
├── 待整理/                      # 收件箱：手工下载的 PDF 丢这里，收编完本目录应清空
│                                #   （~/Downloads 里「今日新增」的 PDF 也一并收编，见「收编」节）
├── 文献日报/                    # 每日 YYYY-MM-DD.md + YYYY-MM-DD.urls.txt
├── 分类与标签总表.md             # sync_classification.py 生成（§4 是人工分析，按锚点保护）
├── 文献画像.md                  # 库内主题/标签统计，每月自动刷新（build_profile.py）
├── 新文献候选.md                # find_new.py 的人力筛选版
├── 综述_氧活性.md
└── 帮助手册.md                  # 人看的上手手册
```

命名规范：目录名 = `<8位Zotero key>_<标题去掉非单词字符取前60字、空格换下划线>`；**标题变了目录名
就会变**，脚本统一用 `paper_dir()`（`mineru_batch.py` / `summarize_batch.py`）。

`state/` 下**手改会出错**的只有两个：`classification.json`（逐篇分类，权威、手工维护）与
`profile.json`（`build_profile.py` 生成，别手改）。其余是脚本产物与备份：`manifest.json`（收编清单）、
`library_index.json`（DOI 索引，**权威**）、`citation_graph.json`（引文图）、`library_scores.json`
（推荐分）、`digest.json`/`digest.md`、`candidates.json`、`journal_if.json`、`journal_abbr.json`、
`recommendations.json`、`last_push.json`、`email_sent.json`、`meta_fills.json`、`work/`（网络缓存）、
`trash/<日期>/`、`*_backup_<日期>.json`、`digest_seen.json`。密钥 `smtp_password` / `zotero_local_key`
**chmod 600，绝不进仓库**。

### 领域配置：`config/topic.json`

**「这个库研究什么」全部集中在这一个文件里**，脚本里不再有领域常量；换研究方向只改它（逐项说明与换
方向清单见 `帮助手册.md` §4）。四组字段：`category_order` / `extra_categories`（一级分类与版面顺序、
不参与按分类检索的目录）/ `fallback_category` / `theory_category`；`categories.<类>.{sub,queries,
keywords,pref}`（子类标签 / 检索式 / 判分关键词 / 手调偏好系数）；`labels.{sub,method,materials,form,
system}`（五个标签轴的**词表：键即标签名**，值是匹配关键词，`sub` 的可选值以 `categories.<类>.sub`
为准）；`coupling` / `gates.*` / `journals` / `archive_collections`（**其下整棵子树 `tidy.py` 不碰**）/
`backup_collections`（**备用库**：一级目录名以此为**前缀**的整棵子树，照常 MinerU + 极简总结，但不参与
推荐分 / 画像 / 日报。见「Zotero 联动方式」的备用库一节）。

装载器 `scripts/topic.py`；路径可用 `LITHUB_TOPIC` 覆盖。改完**先跑 `build_profile.py`**——检索式经
`profile.json` 才进日报（daily_digest 只在 profile.json 缺失时才退回 `find_new.QUERIES`）。

**路线图（未实现）：`scripts/bootstrap_topic.py`** —— 输入 20–50 篇种子自动产出 `topic.json` 草稿
（归纳分类 → 生成检索式 → 拼 `gates` → 期刊白名单 = 库内已收刊 ∪ OpenAlex 同领域刊；冷启动没有库时
用 Semantic Scholar 做两层引文图扩张）。定位是**自动草稿 + 人工过一遍**；换领域后
`--rows / --pages / --min-score` 要重新标定。

### 运行配置：`config/runtime.json`

**「日报怎么跑」集中在这一个文件里**（`topic.json` 管研究什么、改完**必须**重跑 `build_profile.py`；
本文件改完**立即生效**、不碰画像）。装载器 `scripts/runtime.py`，路径可用 `LITHUB_RUNTIME` 覆盖。
六段：`schedule`（跑不跑、隔几天）/ `digest`（版面篇数与各种阈值）/ `pool`（检索池刷新与取数深度）/
`scoring`（打分权重与分量常数）/ `email`（发信；**密码不在这里**——读 `state/smtp_password`）/
`seed`（种子文献的打分杠杆）。逐字段说明见 `帮助手册.md` §6.1–§6.2。

- 查生效值：`python3 scripts/daily_digest.py --show-config`（或 `python3 scripts/runtime.py`）。
- **字段说明写在文件里**：每段的 `_help` 是「字段名 → 说明」字典，**装载器只把它当注释读掉**（沿用
  `topic.json` 的 `_comment` 约定；**新增字段时顺手补 `_help`**）。
- **校验从严**：未知键、类型不对（`true` 不会被当 1 收下）、越界值一律**启动即 `ValueError`**。权重
  不变量只剩一条警告：`w_s ≤ w_r`；`scoring_weights()` 按 `tot = w_r+w_c+w_x+w_j+w_a` **自己归一化**，
  基线分恒落在 `[0,1]`（§10.2）。**改 `scoring` 仍要重标定 `--min-score`**（6 个权重一起标定的）。
- `cat_floor` 的**代码默认是空串**（分类名属领域内容）；要开保底得在此写明，且分类名必须存在于
  `topic.json` 的 `category_order`，否则启动报错退出。
- ⚠️ **systemd unit 里写死的 `--days 14 --limit 16` 会盖过本文件**（CLI 优先）。

## Zotero 联动方式

- **分类目录**：一级分类 6 个（结构退化 / 氧活性 / 界面反应 / 离子输运 / 力学耦合 / 基础理论）+ 综述，
  另有子目录。**一级分类是单值的**，但条目可同时挂多个一级目录：`classification.json` 的 `secondary`
  字段会一并写进对应的一级目录。
- **归档**：`归档` 一级目录之下是**过时项目的归档**（当前 `归档/浙工大项目`），**只入库、不参与日常
  整理**：`tidy.py` 跳过整棵子树，检索/分类/画像也不扫描。名字由 `archive_collections` 给出。
- **备用库**：一级目录名以 `backup_collections`（默认 `["其它"]`）里任一词**开头**的目录，连同整棵子树，
  是一个**侧库**。语义与归档不同——归档是过时项目，备用库是「在用的、但不属于本研究主题」的材料。
  **当前结构是三级**：`其它` / `审稿` / `2026-9-29-NMC_Review`（叶子目录装 4 篇审稿被引文献）。
  - **不参与**：推荐分（`score_library.py`）、文献画像（`build_profile.py`）、日报及其下游
    （`library_index.json` → `citation_graph.py` → 日报的 refs 通道，也含 `manuscript-revision` skill 的
    库内检索）。排除落在 `library_index.py` 这一层（**根**）；`score_library.py` 的条目录自它，自动跟随。
  - **照常**：MinerU 转换与总结。总结走**极简模式**（见下方 `summarize_batch.py` 一节）。
  - **不写** `classification.json`、不打标签、不挂 md 链接附件——保持侧库干净。
  - 判据用**前缀**而不是精确名（`zapi.keys_matching()`）：既认「其它」这个父目录的整棵子树，
    也认「其它-xxx」这类把层级写成扁平名的目录。所以在 `其它` 下再加「教学」「项目」等兄弟目录
    不用改代码；哪怕层级名称拼错了（写成扁平名）也照样生效。
    ⚠️ **命名约定**：本库用 `-` 表示层级分隔（`其它-审稿-2026-9-29-NMC_Review` = 三级），
    但 Zotero 的目录名本身不解析它——**层级要真的建出来**（`parentCollection`），别只靠名字里的 `-`。
  - 这些条目直接加在 Zotero 里，**收编流程看不到**（`intake_pdfs.py` 只扫 `待整理/` 与 `~/Downloads/`），
    要进 MinerU 得跑一次 `intake_pdfs.py --from-collection "<目录名>"`（见收编一节）。
  - `tidy.py` 对整棵子树跳过**重复合并**与**空目录删除**——这同时保护了 `其它`、`审稿` 这类
    本身没挂文献的**中间层空容器**，否则它们会被当空目录硬删掉。
- **标签轴**：主题子类（`相变`、`O2 释放`…）、方法（`方法:DFT`…）、材料（`材料:NCM/NCA`…）、
  形态（`形态:单晶` / `形态:多晶二次颗粒`——**是形貌不是材料体系**）、体系（`体系:固态电解质` /
  `体系:固液对比`——**只标非默认情形**，液态不标）。另有两套为**交叉主题**设的附加轴：
  - `耦合:<方向>`——该文主张的耦合机制（取值见 `config/topic.json` 的 `coupling`）：`化学→力学`
    （吸附/腐蚀/氧流失/电解液侵蚀驱动力学失效）、`力学→化学`（开裂驱动界面化学）、`双向`、
    `本征力化学`（源自脱锂过程本身）。**只标注、不参与日报打分**（`classify()` 只读 `primary`/`sub`）。
    判定准则：**只要作者主张了该方向的因果关系即计入，不要求直接证据**。
  - `兼属:<一级分类>`——该文的第二主题，与 `secondary` 字段一一对应。
  - `种子`——**跨主题的种子文献**：`classification.json` 里写 `"seed": true`，`apply_to_zotero.py`
    就打 `种子` 标签、归入 `种子文献` 目录（给 `bootstrap_topic.py` 攒种子集）。**它参与打分**，
    两张杠杆在 `config/runtime.json` 的 `seed` 段：`citer_boost`（默认 2.0）是「种子的一票算几票」
    （抬高被种子引用过的候选在 refs 通道与库内推荐分里的 `C`），`score_mult`（默认 1.3）是种子自身
    推荐分的倍数。⚠️ 取消种子身份时光删 `seed` 字段不够——`apply_to_zotero.py` 是纯新增，标签与目录
    归属要手工撤。
  - 五个轴的**可选值都在 `config/topic.json`**，`apply_to_zotero.py` 拿它校验 `classification.json`，
    词表外的取值打印 `! 词表外的取值` 告警。
  - **改名/删标签**：`apply_to_zotero.py` 纯新增、从不删除，改标签名旧标签会一直残留。这类操作走
    `python3 scripts/migrate_labels.py [--apply]`（按表换名、删游离标签、用 `PATCH /collections/<key>`
    **原地改目录名**，写前全字段备份到 `state/label_migration_backup_<日期>.json`）。三处要一起改：
    `config/topic.json`、`state/classification.json`、Zotero 本身。
- **链接附件**：每篇挂 2 个 `linked_file` —— 「全文 Markdown」→ `paper.md`，「中文总结」→ `summary.md`。
  用绝对路径，**移动 `~/LitHub` 会导致链接失效**。

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

### `summarize_batch.py`：省 token 的四个开关

默认已开前三个，不用管；**它们把每篇从 0.14 元压到 0.037–0.053 元**（实测见 §5）：

1. **一次调用（oneshot）**：全文注入提示词，模型一次回复即正文，脚本落盘。三种载体自动选（`inline`
   ≤110 KB 全文注入 / `file` 塞不下时写临时文件让模型读一次、用完即删 / `tool` 兜底，模型自己读写文件）；
   `--no-oneshot` 可强制 `tool`。
2. **裁剪论文**：去掉 front-matter、图片路径行、**参考文献段**（护栏：至少留原文 35%、切点须落在 35% 后）。
3. **受限 agent**：只给模型 `Read`/`Write`，不塞内置工具 + MCP 的全部 schema（省 93% 首调输入）。
4. **`--max-steps`（默认 5）**：只在 `tool` 载体下起作用；`summarize()` 核对 `summary.md` 的 mtime，
   没变过就报「未落笔」。⚠️ `~/.kimi-code/config.toml` 里的全局 `[loop_control] max_steps_per_turn`
   **别用**——那是全进程的。

⚠️ **`--figures` 默认关闭、别开**：本环境没有视觉能力，模型读不到 `images/` 里的图。要让总结带图，
做法是**把图片直接嵌进 `summary.md`**（`![](images/xxx.jpg)` 相对路径，Obsidian 就地渲染），描述
一律据图注与正文，并注明未判读图像内容。

提示词模板有**三套**（`prompts/summarize.md` 研究论文 / `summarize_review.md` 综述 /
`summarize_backup.md` 备用库极简），**按 `classification.json` 的 `is_review` 自动选**（`template_choice()`）；
该 key 不在表里就按研究论文处理并打一行日志；综述/备用库模板缺失时各自退回研究论文版并告警；
`--template PATH` 显式指定覆盖全部三套（连备用库的极简注入一起关掉）。末节「与研究主题的关联」在三套
模板里**同名是有意的**：`MANUAL_ANCHOR`、`embed_figures.py` 的插入锚点、`preserve_manual()` 的归位都
依赖那个字面量。改模板后要同步 `summarize_batch.py` 的内置兜底常量（只有研究论文版有兜底）。

**备用库极简模式**（判据是 manifest 记录的 `backup: true`，由 `intake_pdfs.py --from-collection` 打上，
与 `classification.json` 无关）：第 5 个开关是**正文只注入摘要+结论**，不注入全文。`backup_excerpt()`
从 `paper.md` 里按节抽这两段——⚠️ MinerU 出的 paper.md **摘要没有标题**，它在作者行与
`## 1. Introduction` 之间，所以抽取靠「首节之前的区域」定位，不是靠认 `Abstract` 这个词；结论则是
`## 6. Conclusion` 这种带编号的形式，正则认编号。实测压缩到全文的 **5–7%**，输出限定
「一句话结论 + 3–5 条要点 + 关键词 + 与研究主题的关联」共 250–400 字。
⚠️ 这里**刻意不走 `prune_paper()`**：它的参考文献定位会误判（实测 TM7UNDEF 在 36.8% 处就被当成
参考文献起点，结论段连同一半正文一起切掉——**这个 bug 对正常总结同样生效**，见 §10 待办）。

### 收编 `待整理/` 与 `~/Downloads/`（只取今日新增）里的新 PDF

> **判据**：**跑到 `mineru_batch.py` 与 `summarize_batch.py` 才算**，只建条目 + 挂 PDF 不算。

`intake_pdfs.py` 有**两个来源**：`待整理/` 里的 PDF 全收；`~/Downloads/` 只收**今日新增**（按
`ctime` 判断）的那些。**下载完把 PDF 留在 `Downloads/` 即可，不必再手工搬到 `待整理/`**——每次
收编都会顺带把当天的 Downloads PDF 一起入库，`--remove` 把两个来源的原件都删掉。要改额外来源用
`--extra-inbox DIR`（可重复）或 `--since YYYY-MM-DD`；设 `LITHUB_EXTRA_INBOX=""` 可关闭 Downloads
这一来源（默认就是 `~/Downloads`）。

```bash

# 按首页 DOI 匹配库里已有条目，缺则用 CrossRef 元数据新建；PDF 三段式上传并登记 manifest
python3 scripts/intake_pdfs.py --dry-run
python3 scripts/intake_pdfs.py --remove      # --remove：收编成功后删掉两个来源里的原件
```

出版方的作者接受稿（OSTI/仓库版）常整篇不含 DOI，首页提取不到就会跳过；这类在
`待整理/_doi_hints.json` 里补一行 `"<文件名>": "<DOI>"`，脚本优先用它（Downloads 里的文件按
**文件名**同样适用）。

**另一条入口：`--from-collection`**（补 manifest 用，不是收编）：

```bash
python3 scripts/intake_pdfs.py --from-collection "其它-审稿" --dry-run   # 目录名可只写前缀
python3 scripts/intake_pdfs.py --from-collection "其它-审稿-2026-9-29-NMC_Review"
```

它把 Zotero 某个目录（**含子树**）下条目的 PDF 附件登记进 manifest，**不新建条目、不重传附件、
不联网**，幂等（已在 manifest 里的跳过）。存在意义：收编主流程只扫 `待整理/` 与 `~/Downloads/`，
所以**直接加进 Zotero 的条目永远进不了 MinerU 流程**——备用库（`其它*`）正好是这种来源。
记录的 `backup: true` 标记会驱动极简总结模式；目录名匹配是前缀式的，多个候选会报错要你写全名。
⚠️ 这条路径收 `preprint` 类型（主流程只认 `journalArticle`），别拿它当收编用。

收编只负责「条目 + PDF 附件 + manifest」。随后补齐四步才算整理完：

1. 给新条目在 `state/classification.json` 里加一条分类（primary/secondary/sub/method/materials/form）
2. `python3 scripts/apply_to_zotero.py` → `python3 scripts/sync_classification.py`
3. `python3 scripts/mineru_batch.py all 3` → `python3 scripts/summarize_batch.py 3` →
   `python3 scripts/embed_figures.py 4` → `python3 scripts/link_markdown.py`
   （**总结里的图是 `embed_figures.py` 事后插的「关键图表」一节**——模型不写图、也读不到图。
   漏跑这一步，那批 `summary.md` 一张图都没有：2026-09-27 就这么漏了一整批。）
4. **刷新库内派生数据**——这几个文件按 key/DOI 存快照，**不会自己察觉新条目**，必须按顺序手动跑：
   `library_index.py`（**不跑它，新条目不会被推荐分与日报的库内关联看到**）→ `citation_graph.py`
   → `score_library.py --apply` → `digest.py json` → `build_profile.py`

最后收尾 `python3 scripts/tidy.py --apply`。（漏跑第 4 步踩过的坑见 §10.4）

### 补全 Zotero 条目的元数据：`scripts/fill_meta.py`

条目常缺 DOI / 刊名 / 年份 / 卷期页（Elsevier 系刊本就常缺期号），而 `cite` 字段就靠它们。脚本去
CrossRef 取回来写进 Zotero：

```bash
python3 scripts/fill_meta.py            # 只报告（扫全库顶层条目）
python3 scripts/fill_meta.py --apply    # 写回；--key ABCD1234 只处理一条
```

- **只补空字段，绝不覆盖已有值**；有 DOI 按 DOI 取，没有才用标题检索（要求相似度 ≥0.95 **且作者
  重叠 ≥50%**，多条候选优先期刊论文）。宁可不动，也不写可能错的 DOI。
- 作者名单只在「Zotero 名单是 CrossRef 名单的严格前缀」时补齐末尾漏掉的作者，顺序有出入一律不动——
  所以有时仍要人工核（例外见 §10.5）。写前把**完整旧数据**存进 `state/meta_fills.json`（追加式）。
- CrossRef 也查不到的会列成「CrossRef 也没有」，不是失败。补完记得 `sync_properties.py --apply`。

### 常规清理：`scripts/tidy.py`

**每次整理完都要跑**。四类对象：

| 类别 | 判定 | 处理 |
|---|---|---|
| 空条目 | 顶层条目没有任何子项（无附件、无笔记） | 硬删除（**但「重复条目」那一组里的除外**：待删的走合并，要留的那条可能正是「Zotero 侧子项已丢、`papers/` 还在」的记录） |
| 重复条目 | DOI 相同，或标题归一化后相同 | 标签/目录并入留存条目后硬删除；**有子项的重跑默认只报告**，加 `--merge-dups` 才合并 |
| 孤儿目录 | `papers/<key>_*` 在 Zotero 已无对应条目 | **移动**到 `state/trash/<日期>/`，不删 |
| 空目录 | 库里没有任何条目归属、**且没有子目录**的分类目录 | 硬删除（跳过归档子树、**备用库子树**与 taxonomy 目录） |

留存条目由 `pick_survivor()` 挑，**优先留流水线认得的那条**（有 `papers/` 目录）——`papers/<key>_*`、
`manifest.json`、`classification.json` 都按 key 认人，留错 key 要连带改名一堆东西。「同一份文件」
（同类型同文件名）不搬，随重复条目一起删；**只要有子项没搬走就整条跳过**并把失败计入退出码。

**备用库（`其它*`）整棵子树跳过重复合并与空目录删除**：`pick_survivor()` 只看子项与字段，不会替我们
守住「别把侧库条目并进主库」这条边界；空目录那边同理，`其它*` 不在 `MANAGED_NAMES` 里，不专门排除
就会被当空目录硬删掉。报告末尾有 `[备用库] N 个目录跳过` 一行可核对。

```bash
python3 scripts/tidy.py [--apply] [--keep-dups] [--merge-dups]   # 默认只报告；--apply 备份后执行
```

`--apply` 先把要删的东西全量导出到 `state/tidy_backup_<日期>.json`，并把 `classification.json` /
`manifest.json` 里指向已消失条目的记录一并清掉；跑完再 `sync_classification.py && digest.py json`。
三条「会丢东西」的历史坑见 §10.6。⚠️ **`DELETE /items/<key>` 是硬删除，不进回收站**（`帮助手册.md`
§7.7 有核验方法），所以这些操作都必须先备份、默认只报告。

### 两个转换引擎与图表

默认 `extract`（精准模式：**需 `mineru-open-api auth` 令牌**、200 MB / 600 页、导出 `images/` 与公式
表格）；`--flash` 免令牌兜底（10 MB / 20 页、图表与公式只剩占位符）。⚠️ 令牌报 401 `A0211`
**没有诊断力**（编造的 key 同样返回它），最常见的失败是**复制的 key 不完整**（正确 **51 字符**）。
`.md` 里的图可能一图拆成多个连续文件，引用要取全。对照表与细节见 §11 与 `帮助手册.md` §8。

### summary.md 的属性区：`scripts/sync_properties.py`

**属性区不由模型写**，改由脚本从 Zotero 写入（MinerU 常解析不出 DOI / 刊名 / 卷期页，模型只能写
`unknown` 或按正文猜），模型只管正文。写 6 个字段：`title` / `author`（Zotero 顺序）/ `year` /
`journal`（**全称**，`build_profile.py` 的刊名统计依赖它）/ `doi` /
`cite`（`<末位作者全名>, <年份>, <期刊简写>, <卷期页>, <DOI>`）。

```bash
python3 scripts/sync_properties.py              # 只报告差异（默认）
python3 scripts/sync_properties.py --apply      # 写回，改前自动备份属性区（--key 只处理一条）
python3 scripts/sync_properties.py --build-abbr # 只补期刊简写表
```

- ⚠️ 只报告模式**只算不落盘**，落盘必须显式 `--apply`；Zotero 字段为空时沿用旧属性区的值，`cite`
  构件少于 3 个时留空。四条细节见 §12。

### 给 summary.md 配图：`scripts/embed_figures.py`

按**图注 + 正文引用句**挑 3–5 张关键图嵌进「关键图表」一节（模型只做判断，**图片路径由脚本解析填入**），
只喂标题/摘要/结论/图注（3–7 KB，约全文的 1/10），一篇 20–28 秒，已带该节的会跳过。
`python3 scripts/embed_figures.py 4`（`--force` 重做；`--dry-run` 先看规模）。细节见 §13。

### 全文翻译（含批注）：`scripts/translate_batch.py`

产出 `papers/<KEY>_*/全文翻译（含批注）.md`——逐段中文翻译 + 批判性批注（`summary.md` 是提炼，
本模块是全文对照本）。**文件名固定，一字不差**（`link_markdown.py` 按它挂 Zotero 链接附件）。

```bash
python3 scripts/paper_context.py ND2Z2J4Q                 # 零 LLM 取数：库内引文关系/被引量/h 指数
python3 scripts/translate_batch.py --dry-run ND2Z2J4Q     # 只看分段表与图片对账
python3 scripts/translate_batch.py all 3                  # 挑还没译文的，最多 3 篇（--force 重做）
python3 scripts/translate_batch.py --report-usage ND2Z2J4Q # 跑完打印各段 token 用量
```

四块拼起来才是模块：`prompts/translate_full.md`（总规范）、`prompts/translator.agent.md`
（工具只留 Read/Write/WebSearch）、`scripts/paper_context.py`（取数）、以及**按类型注入**的
`prompts/translate_mode_{review,research}.md`（`classification.json` 的 `is_review` 决定用哪份：
综述问「选择偏差 / 概念通胀 / 拼出来的时间线」，研究论文问「n 与误差 / 对照 / 这张图撑不撑得住
这句话」）。改措辞改模板即可，别动代码。

⚠️ **「分段 + 脚本拼装」是这套设计的核心**，别改回去：一个文件由多个进程各写一块——
脚本切**行区间**、写属性区、整节抄走参考文献、最后拼装；模型只写开头三节（1 次调用）
或某一段译文（N 次并行）。**不要让模型做拼装**：那样调度会话得同时装原文 + 全部
分片 + 组装稿，实测一篇综述累积 10.06M 缓存读、58 次 LLM 请求，而组装稿 `cat` 就能做。
中间产物在 `state/work/translate/<KEY>/`，可续跑（已有分片默认跳过）。用量账与「只能认
`usage.record`、别被 grep 放大 3 倍」的坑见 `帮助手册.md` §12.7。

⚠️ **五条红线**（`translate_batch.py` 的 `verify()` 会拦，不通过就退出码非 0）：

1. **开头三节顺序固定**：`## 库内定位`（谁引了它、它引了库内哪些、被引量）→ `## 作者与团队`
   （原文小传 + 联网查团队方向；查不到写 h 指数）→ `## 读前批注：我的整体判断`。
2. **禁写的五类废话**：文件来源自述（「本文件是 Zotero 条目 X 的…」）、阅读指南（「怎么读」）、
   批注元说明（「关于批注」「共 N 条」）、参考文献节的说明段（「逐字取自 paper.md」）、
   「（原文，未翻译）」这类括注。读者从上下文就知道，写了只是噪声。
3. **批注必须带批判性**：每条至少一句指出证据强度 / 过度声称 / 缺失判据 / 替代解释 / 证伪路径 /
   与库内文献的冲突；**禁止复述该段内容**。
4. **参考文献节**标题只有 `## 参考文献`，下面直接是条目（英文原貌、不翻译）。
5. **漏图**：每段都发了「本段应出现的图片清单」，拼装后与原文对账，少一张就点名报错。

作者 h 指数与引文关系都从 `paper_context.py` 取，**不要自己联网复核被引量**。细节见 `帮助手册.md` §12。

### 修残留控制字节：`scripts/fix_control_bytes.py`

字体 ToUnicode 映射坏的 PDF 会产出 NUL 字节，文件被当**二进制**处理、严格工具拒读。
`python3 scripts/fix_control_bytes.py [--apply]`（原件备份到 `state/trash/`），判定不出的位置**不猜**、
替换成 `【?】`。⚠️ 占位符**不要用 `□`**——本库里 `□` 是晶格空位的合法记号（`Li4/7[□1/7Mn6/7]O2`）。
详见 §14。

### 主动检索新文献：`scripts/find_new.py`

`python3 scripts/find_new.py 2025-01-01 80 crossref`（幂等；源可换 `openalex`，相关度好但有 429 风险）。
过滤链：期刊白名单 → 去重（DOI + 标题模糊）→ 相关性闸门 → 离题词；结果写 `state/candidates.json`。
详见 §15。

### 每日文献日报（已挂 systemd 定时）

三个通道取候选：`fresh`（Crossref 近 `--days` 天的 journal-article，窗口固定）/ `query`（同一批检索式
但不加日期过滤，默认不限年份）/ `refs`（库内引文推荐，取自 `citation_graph.json` 的 `co_citation`）。
各自过闸后统一打分，版面**分三轮发**：

1. **通道配额轮**：fresh 40% / refs 25% / 其余 query，凑不满就把配额让给别的通道；比例按**扣掉保底
   预留后的 budget** 算，不是按 `limit`。
2. **分类保底轮**：`--cat-floor`（默认 `力学耦合=3,界面反应=2`）按分类权重从高到低发。**保底复用
   `take()`**，所以 `--min-score` 与每类 `cap` 照旧管着它——给的是「版面名额」不是「免检入场券」，
   跨不过门槛就空着并写明原因；保底名额不得超过 `cap`、合计不得 ≥ `limit`（超了缩到 `limit//2`）。
   为什么需要保底见 §2。
3. **全局补位轮**：剩下的名额给全局分数最高的候选（每类上限 `cap = limit 的 40%` 只在这一轮可放宽）。

```

# 系数 = config 的 w_* 除以 tot = w_r+w_c+w_x+w_j+w_a（2026-09-22 起 scoring_weights() 自己归一化）
B = 0.25*R + 0.15*S + 0.22*C + 0.20*X + 0.13*J' + 0.05*A    # S 可用时
B = 0.40*R + 0.22*C + 0.20*X + 0.13*J' + 0.05*A             # S 不可用：那 0.15 回补给 R
final = B * (1 + 0.35*F)                     # 综述再 ×0.9
```

六项含义与各分量常数见 `帮助手册.md` §6.3.3。最容易被误改的三处：`R` **不乘**内容分类权重（分类
权重只在 `C` 的 `q̄` 里生效，理由见 §1.2）、`X` **完全不看年份**、`J'` 已减 0.5。**年龄一律不做乘法
惩罚**。⚠️ **启用 `S` 前必须重标定 `--min-score`**（`0.04` 是**无 `S` 时**标定的）；`S` 缺
`lib_emb.npz` 或 `sentence-transformers` 就整项跳过、把 0.15 回补给 `R`；**refs 通道没有排名，`R=0`**。

工作点 `--rows 100 --pages 1 --query-pages 3 --min-score 0.04`、`--citer-years 3`、`--citer-floor 0.75`、
`--oa-top 48` 都在 `config/runtime.json` 的 `pool`/`digest` 段（标定见 §3）。`--min-score` 是硬门槛：
低于它的候选任何通道都不入选、名额让给全局最高分的其他候选。想看某天各类候选与离入选线差多少：
`python3 scripts/daily_digest.py --dry-run --diag-cats --date <日期>`。

```bash
python3 scripts/daily_digest.py --show-config   # 看生效的运行配置（runtime.json 再被 CLI 覆盖后的值）
python3 scripts/daily_digest.py                 # 裸跑 = 用 runtime.json 的工作点（幂等，命中缓存零花销）
python3 scripts/daily_digest.py --date 2026-09-12 --no-llm   # 回填某天、完全不调 LLM
python3 scripts/daily_digest.py --dry-run       # 预演，不写文件也不调 LLM
python3 scripts/daily_digest.py --force         # 忽略 schedule 频率限制，强制出一期

python3 scripts/library_index.py                # 引文通道前置数据（都幂等，库内新增文献后各跑一次）
python3 scripts/citation_graph.py               # --rebuild 全量重建
python3 scripts/journal_if.py                   # 期刊层级表（J' 用，年报刷新）
python3 scripts/build_profile.py                # 刷新文献画像（纯统计，零 API 花销）
```

**文献画像的 8 节**：1 分类分布与检索配额 / 2 子类标签热度 / 3 方法・材料・形态・体系 / 4 年份与期刊 /
5 时间趋势 / 6 值得注意的发现 / 7 被引与推荐分 / 8 当前每日检索式。`build_profile.py` 不调 LLM、
纯统计、零 token，可放心挂每月定时；§5–§7 的算法口径见 §4。

⚠️ **OpenAlex 2026 起按请求计费**（$0.001/条，匿名池每天 $0.1 ≈ 100 条），额度耗尽返回 429 +
`retry-after`，重置在**次日 00:00 UTC**（= 08:00 CST）；`journal_if.py` 遇 429 会在第一个失败的刊处
收工。所以 `state/journal_if.json` 的 `missing` / `kept_old` / `not_queried` 三栏含义不同（§7）。

定时任务（系统级，以当前用户身份运行）：

```bash
systemctl list-timers lithub-daily.timer lithub-profile.timer   # 看下次触发时间
systemctl status lithub-daily.service                           # 看最近一次结果
journalctl -u lithub-daily.service -n 50                        # 看 systemd 日志
sudo systemctl start lithub-daily.service                       # 手动触发一次（disable --now 停掉）
```

- `lithub-daily.timer`（每天 05:00）出报，`ExecStartPost` 依次发信与刷新推荐分（带 `-` 前缀，失败不让
  unit 变 failed）；`lithub-profile.timer`（每月 1 日）刷新画像；`lithub-mailcheck.timer`（每天 06:00）
  兜底补发。三者都 `Persistent=true`。
- **频率闸门**：timer 照旧每天触发，**跑不跑由脚本读 `config/runtime.json` 的 `schedule` 段自己决定**
  （`enabled: false` 跳过；`every_n_days: N>1` 时距上次成功出报不足 N 天也跳过，只在日志留一行）。
  `--dry-run` / 显式 `--date` / `--force` 三者**一律放行**。闸门只看 `state/last_push.json`
  （**不猜「日报文件在不在」**，没候选那次不写它）；`--cooldown-days`（默认 60）内同一 DOI 不再推。
- `--dry-run` 不写任何 state 文件（网络缓存除外）。⚠️ **只有 refs 通道的 `C` 参与打分**——query 通道
  那篇即使被库内多篇引用也拿不到 `C`。其余（成本、版面字段、推荐分迁移）见 §9 与 §16。

### 发信：`scripts/send_digest.py`

把 `文献日报/<日期>.md` 作为**纯文本**邮件发出去（不转 HTML：邮件客户端本来也不渲染 Markdown）。
**零 token 花费**。主题行用 `email.subject` 模板填 `{date}` / `{n}`。**配置与密钥分离**：收件人 /
服务器 / 端口在 `config/runtime.json` 的 `email` 段，**密码读 `state/smtp_password`（chmod 600）**。
**当日计数**记在 `state/email_sent.json` 的 `count`：出报后发一次、06:00 兜底补发；计数 ≥ 1 就不再
自动发，`--force` 才计数 +1 再发一次。

```bash
python3 scripts/send_digest.py [--dry-run|--check]   # 预演 / 只做 SMTP 自检
python3 scripts/send_digest.py [--date 2026-09-19] [--force]   # 发当天 / 补发某天 / 强制重发
```

⚠️ 两处**初次配置**才会踩的坑（SMTP 主机名不能照官方文档填、登录密码 ≠ 客户端专用密码）见 §10.11。

### 库内文献打分：`scripts/score_library.py`

给**库内**每篇算一个「推荐分」，写进 Zotero 条目的 `extra` 字段，同时也落在
`state/library_scores.json`（含各分量）。**前置**：`state/library_index.json`、
`state/citation_graph.json`、`state/classification.json`——缺任一个脚本直接退出并点名该先跑谁。

**与日报是同一套公式**：这里**直接调用** `daily_digest.final_score()`，权重由 `scoring_weights()`
原样返回。两项对库内文献只能取**边界值**——不是「没有值」，是它们的定义在库内就落在这里：
`R`（检索排名）**= 0**（库内文献不在任何检索结果集里），`S`（库内相似度）**= 1**（它和自己最像）。
再加一项**库内专有**的加法项（日报没有）：`final = (B + w_o·O) · (1 + f_gain·F) ×（种子 ? score_mult : 1)`，
`O = min(1, (k / max(n_ref, o_ref_floor)) / o_sat)`（`k` = 参考文献里落在库内的篇数，`n_ref` = 参考文献
总条数，分母下限防小分母放大）。一句话：`推荐分 = 「假设这篇是候选、且语义完全贴合我的库，日报会给它
多少分」+ 出度加分`。好处是**两边共用一张权重表**（改 `runtime.json` 的 `scoring` 段，两边一起动），
七个分量都复用 `daily_digest.py` 的函数。参数与实测见 §6。

**写进哪里**：条目的 `extra` 里一行 `推荐分: 0.312`。**`Extra` 是 Zotero 的原生列**（条目列表表头
右键 → 列 → 勾上 Extra），点表头可排序；同键的行**原地替换**（`upsert_line`），幂等。⚠️ 定长三位小数
只让**正分**的字符串排序 = 数值排序，**负分相反**；⚠️ **标签做不到这件事**（本地 HTTP API 没有注册
列的能力）。详见 §19。

**两个更新钩子**（幂等）：① `lithub-daily.service` 的第二个 `ExecStartPost`；② **收编新文献后手动跑
一次** `python3 scripts/score_library.py --apply`。零 token 花费；冷缓存约 5 分钟，缓存热了整轮 **7 秒**。

```bash
python3 scripts/score_library.py [--apply] [--key XXXXXXXX]   # 默认只报告；--apply 写盘 + Zotero Extra
```

⚠️ **别忘 `LITHUB_MAILTO`**（见「环境注意事项」）：漏设会让 `A` 项永远拿不到值、每篇白等 ~12 秒。

## 环境注意事项

### Unpaywall 会硬拒占位邮箱（重要）

`LITHUB_MAILTO` 不设时默认 `you@example.com`，**Unpaywall 直接 422 拒绝**；后果是 `cached_json` 先按
退避策略重试 3 次（3s + 6s）才放弃，`oa_info()` 每篇白等 **~12 秒**、`A` 项**永远拿到 0**。
手动跑先 `export LITHUB_MAILTO=你的邮箱`；systemd 不读 `.bashrc`，两个 unit 的 `Environment=` 里都
写死了这个变量。Crossref / OpenAlex 不硬拒，只有走 Unpaywall 的功能会中招（详见 §20）。

### IPv6 陷阱（重要）

本机曾「有全局 IPv6 地址与默认路由、但 100% 丢包」，而 **Python 没有 curl 那样的 happy-eyeballs
回退**，会死等超时（实测同一请求 181 秒 vs 强制 IPv4 的 0.72 秒）。写网络代码时**显式强制 IPv4**
（沿用 `daily_digest.py` / `find_new.py` 里那段 `socket.getaddrinfo` 包装）。根治见 §20。

### 国内网络

- pip 已配阿里云镜像（`~/.config/pip/pip.conf`）；HuggingFace 不可达，需
  `export HF_ENDPOINT=https://hf-mirror.com`。
- MinerU 的 `flash-extract` 有 **10 MB 且 20 页**双限制，**文件大小先于页数触发**；超过 8 MB 的 PDF
  先用 ghostscript 预压缩（`gs -dPDFSETTINGS=/ebook`）。限制**只针对 `--flash`**；`extract` 是 200 MB /
  600 页。限流（官方）：提交 50 文件/分钟、每日 5000 文件；取结果 1000 次/分钟。

## 与 Obsidian 的关系

`~/LitHub` 已注册为 Obsidian 库，`.md` 默认用 Obsidian 打开（`~/.local/bin/obsidian-open` 把路径转成
`obsidian://` URI）。⚠️ **该脚本必须用 `xdg-open` 发 URI**，直接执行 `/snap/bin/obsidian` 会被 snap
包装器静默忽略。详见 §21。

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
  `--force` 也不冲掉。详见 §22。
