# LitHub —— 文献管理流水线

这个目录是锂离子电池正极材料文献的本地工作区，与 Zotero 库联动。

## 任何方向都必看

无论你研究什么方向，下面这五条是最容易踩、也最贵的。**第一次上手照 `帮助手册.md` §1 走。**

1. **领域内容全部在 `config/topic.json`**（分类法 / 子类与五个标签轴词表 / 检索式 / 关键词 /
   闸门正则 / 期刊白名单）。改完**必须重跑 `python3 scripts/build_profile.py`**——
   检索式经 `state/profile.json` 才进日报，不重跑画像等于没改。
2. **运行参数全部在 `config/runtime.json`**（频率 / 篇数 / 检索池 / 保底 / 打分权重 / 发信）。
   改完**立即生效**，不必重跑画像。优先级是 **CLI > `runtime.json` > 内置默认**，
   所以 systemd unit 的 `ExecStart` 里写死的 `--days`/`--limit` 会盖过配置文件。
   换领域后**第一件事是改 `cat_floor`**（留着作者的分类名会启动即报错退出）。
3. **代码里仍有领域硬编码，换方向不改就会一直按原方向筛**——最硬的几处是
   `daily_digest.py` 的 `LAYERED_PAT`（标题/摘要不命中直接丢弃）、`find_new.py` 的
   `relevant()` 排除规则、`translate()` 的翻译系统提示词。
   完整清单（按符号名定位）见 **`帮助手册.md` §4.2**，先改前三处管线就能跑通。
4. **「入库」的判据是跑到 `mineru_batch.py` + `summarize_batch.py`**，只建条目挂 PDF 不算。
   自查：`papers/` 目录数 = 库内顶层条目数，且每篇 `paper.md` 与 `summary.md` 都在
   （只建条目的，`link_markdown.py` 会报「缺文件」）。
5. **先跑通窄链路再加自动化**：Zotero 授权 → 1–2 篇 PDF 入库 → 手写 `classification.json` →
   `apply_to_zotero.py` → `mineru_batch.py` → `summarize_batch.py` → `link_markdown.py`。
   库里有 30–50 篇之后再重写 `topic.json`、跑画像；**最后**才配 systemd 与调打分权重。
   每一步先用 `--dry-run` 看效果；`tidy.py --apply` 是唯一会删东西的命令。

## 目录结构

```
~/LitHub/
├── papers/<KEY>_<标题前60字>/
│   ├── paper.md        # 全文 Markdown（MinerU 转换，或 Zotero 全文缓存兜底）
│   └── summary.md      # 中文总结（属性区由 sync_properties.py 写 + 固定七节 +
│                       #   可选的「库内同主题工作」跨篇对照一节，重跑时由
│                       #   preserve_manual() 按章节保护，见「风格约定」）
├── state/
│   ├── manifest.json           # 每篇：key / 标题 / 来源路径 / 状态
│   ├── classification.json     # 逐篇分类结果（primary / secondary / sub /
│   │                           #   method / materials / form / coupling）
│   ├── digest.md               # 紧凑摘要清单，供分类和写综述用
│   ├── candidates.json         # 主动检索到的库外新文献候选
│   ├── profile.json            # 文献画像的机器可读版（分类权重 / 检索式）
│   ├── library_index.json      # 库内 top-level journalArticle 的 DOI 索引（library_index.py）
│   ├── journal_abbr.json       # 刊名全称 → ISO4 简写（sync_properties.py 的 cite 字段用，手工可改）
│   ├── properties_backup_<日期>.json  # sync_properties.py --apply 前的属性区备份
│   ├── meta_fills.json         # Zotero 条目元数据补写记档（fill_meta.py：旧值→新值 + 改前全字段）
│   ├── citation_graph.json     # 库内文献引文图：by_citer / co_citation / ref_meta（citation_graph.py）
│   ├── journal_if.json         # 白名单期刊的 OpenAlex 2yr_mean_citedness 代理 IF（journal_if.py；J' 用，年报）
│   ├── recommendations.json    # 日报推过的 DOI：first/last_pushed、times_pushed、channel、score、status
│   ├── last_push.json          # 上次成功出报的日期，频率闸门（schedule.every_n_days）只看它
│   ├── email_sent.json         # 日报发信记录 {日期: {count, sent_at, to, subject, …}}；
│   │                           #   当日计数 ≥ 1 就不再自动发（见「发信」一节）
│   ├── smtp_password           # SMTP 专用密码/授权码（chmod 600，绝不进仓库）
│   ├── library_scores.json     # 库内每篇的「推荐分」与四个分量（score_library.py；
│   │                           #   分数同时写进 Zotero 条目的 Extra，见「库内文献打分」一节）
│   ├── digest_seen.json        # 旧版日报去重表，只读；首次运行会迁移进 recommendations.json（不删）
│   ├── work/                   # 检索/翻译/IF 的响应缓存；lib_emb.npz（S 项的库内向量，可选）
│   ├── trash/<日期>/           # tidy.py 移出的孤儿 papers/ 目录（可逆，不是删除）
│   ├── label_migration_backup_<日期>.json  # migrate_labels.py --apply 前的全字段备份
│   └── zotero_local_key        # Zotero 本地 API 密钥（chmod 600）
├── prompts/            # 提示词模板（summarize.md；改它不用动脚本，缺失时回退内置常量）
├── config/topic.json   # 领域配置：分类法 / 子类与五个标签轴的词表 / 检索式 /
│                       #   关键词 / 闸门正则 / 期刊白名单（见「领域配置」一节）
├── config/runtime.json # 运行参数：跑多勤 / 一期几篇 / 检索池 / 保底 / 打分权重
│                       #   （改完即生效，不必重跑 build_profile.py，见「运行配置」一节）
├── scripts/            # 全部可重跑、幂等
├── logs/
├── 待整理/              # 收件箱：手工下载的 PDF 丢这里，收编完本目录应清空
├── 文献日报/            # 每日 YYYY-MM-DD.md + YYYY-MM-DD.urls.txt
├── 分类与标签总表.md
├── 综述_氧活性.md
├── 文献画像.md          # 库内主题/标签/热点统计，每月自动刷新
├── 新文献候选.md        # find_new.py 的人力筛选版（含中文摘要）
└── 帮助手册.md          # 人看的上手手册：环境/换方向清单/全部参数/踩坑（Zotero API §7、MinerU §8）
```

命名规范：目录名 = `<8位Zotero key>_<标题去掉非单词字符取前60字、空格换下划线>`。
**标题变了目录名就会变**，脚本里统一用同一套 slug 逻辑（见 `zapi.py` 旁的辅助函数）。

### 领域配置：`config/topic.json`

**「这个库研究什么」全部集中在这一个文件里**，脚本里不再有领域常量。换研究方向只改它：

| 字段 | 内容 | 谁在用 |
|---|---|---|
| `category_order` | 一级分类及版面顺序 | `daily_digest.py` 的 `CAT_ORDER` |
| `categories.<类>.sub` | 子类标签（建目录用） | `apply_to_zotero.py` 的 `TAXONOMY` |
| `categories.<类>.queries` | 该类检索式 | `build_profile.py` → `profile.json` → `daily_digest.py` |
| `categories.<类>.keywords` | 该类判分关键词 | `daily_digest.py` 的 `CAT_KEYS` |
| `categories.<类>.pref` | 手调偏好系数（1.0 = 中性） | `build_profile.py` 的 `CAT_PREF` |
| `labels.{sub,method,materials,form,system}` | 五个标签轴的**词表：键即标签名**，值是匹配关键词（空 = 只允许手标） | `daily_digest.py` 打候选标签；`apply_to_zotero.py` 校验取值 |
| `coupling` | `耦合:<方向>` 轴取值 | 同上 |
| `journals` | 期刊白名单 | `find_new.py` / `journal_if.py` |
| `gates.{topic,must,strong,exclude,offtopic,topic_theory}` | 相关性闸门正则 | `find_new.py` → `daily_digest.py` |
| `extra_categories` / `fallback_category` / `theory_category` | 综述等其他目录、兜底分类。`其他` 已于 2026-09-20 删掉：它从没装过东西，而 `fallback_category` 是「结构退化」，自动分类不会落到它上面；`apply_to_zotero.py` 每轮建、`tidy.py` 每轮删，纯属互相抵消 | `apply_to_zotero.py` / `daily_digest.py` |
| `archive_collections` | 归档目录名（如 `["归档"]`），其下整棵子树是历史项目：`tidy.py` 不碰、也不扫描 | `tidy.py` 的 `ARCHIVE_NAMES` |

装载器是 `scripts/topic.py`（只读 + 编译正则，不含领域内容）；路径可用环境变量
`LITHUB_TOPIC` 覆盖，便于多主题并存。改完**先跑 `build_profile.py`**——检索式经
`profile.json` 才进日报（daily_digest 只在 profile.json 缺失时才退回 `find_new.QUERIES`）。

**路线图（未实现）：`scripts/bootstrap_topic.py`** —— 让别人不必手写这个 json。
输入 20–50 篇种子（DOI 列表，或一个 Zotero collection），自动产出 `topic.json` 初稿：

1. 取种子标题+摘要，LLM 归纳出 4–8 个一级类，每类 5–8 个子类标签
2. 每类生成 3–5 条检索式（复用 `CAT_KEYS` 风格的术语）
3. 从库内高频 n-gram 抽「必须词」，从邻近领域抽「离题词」，拼出 `gates`
4. 期刊白名单 = 库内已收期刊 ∪ OpenAlex 同领域刊（`journal_if.py` 已能查）
5. 冷启动没有库时，退化到种子扩充：用 Semantic Scholar 的 references/citations
   做两层引文图扩张，把结果当临时库喂给上面同一套逻辑

⚠️ 自动生成的质量在冷启动时不稳定——画像那套「热点词扩写」就是前车之鉴：
`phrases()` 不滤数字与单位，抽出来的全是 `mah g` / `3 v` 这类碎片，那条路**从未生效过**，
两节输出已在 2026-09-20 从画像里撤掉（详见下文「文献画像的 8 节」）。
定位是**自动草稿 + 人工过一遍**，不是全自动。另外换领域后
`--rows / --pages / --min-score` 这些工作点要重新标定，不能照搬。

### 运行配置：`config/runtime.json`

**「日报怎么跑」集中在这一个文件里**，与领域内容分家：`topic.json` 管研究什么（改完
**必须**重跑 `build_profile.py`），`runtime.json` 管怎么跑（改完**立即生效**，不碰画像）。
装载器是 `scripts/runtime.py`，路径可用环境变量 `LITHUB_RUNTIME` 覆盖。

| 段 | 键 | 作用 |
|---|---|---|
| `schedule` | `enabled` / `every_n_days` | 跑不跑、隔几天跑一期（见「每日文献日报」的频率闸门） |
| `digest` | `limit` / `days` / `min_year` / `cooldown_days` / `min_score` / `cat_floor` / `oa_top` / `model` | 版面篇数与各种阈值 |
| `pool` | `refresh_days` / `rows` / `pages` / `query_pages` | 检索池的刷新周期与取数深度 |
| `scoring` | `w_r` … `c_age_tau_y` | 打分权重与各分量常数 |
| `email` | `enabled` / `to` / `smtp_host` / `smtp_port` / `smtp_user` / `use_ssl` / `subject` | 日报发信（见「发信」一节）。**密码不在这里**——读 `state/smtp_password` |

- 优先级 **CLI 参数 > `runtime.json` > 内置默认**。内置默认与原硬编码**逐字一致**，
  所以配置文件缺失时行为不变（`runtime.py` 会把 `LOADED=False` 报出来）。
- 查生效值：`python3 scripts/daily_digest.py --show-config`（或 `python3 scripts/runtime.py`）。
- **字段说明写在文件里**：每段的 `_help` 是「字段名 → 说明」的字典，**装载器只把它当注释读掉**
  （不校验、不进配置、不出现在 `--show-config` 里）。JSON 没有注释语法，所以沿用了
  `topic.json` 的 `_comment` 那套下划线约定；**新增字段时请顺手补 `_help`**。
- **校验从严**：未知键、类型不对（`true` 不会被当 1 收下）、越界值一律**启动即 `ValueError`**，
  不静默忽略——与 `parse_cat_floors` 同一条纪律。只有两条权重不变量
  （`w_r+w_c+w_x+w_j+w_a ≈ 1.0`、`w_s ≤ w_r`）是警告而非报错，因为改了它们会让
  `--min-score` 的刻度跟着变，属于「有救但必须让人看见」。
- **改 `scoring` 就必须重标定 `--min-score`**：那 6 个权重是一起标定的，动一个门槛就失配。
- `cat_floor` 在**代码里的默认值是空串**（分类名属领域内容，代码不含任何分类名）；
  要开保底得在这里写明，且分类名必须存在于 `topic.json` 的 `category_order`，
  否则启动报错退出。
- ⚠️ **systemd unit 里写死的 `--days 14 --limit 16` 会盖过本文件**（CLI 优先）。
  要让这两项由配置决定，得先把参数从 unit 的 `ExecStart` 里删掉。

## Zotero 联动方式

- **分类目录**：一级分类 6 个（结构退化 / 氧活性 / 界面反应 / 离子输运 / 力学耦合 / 基础理论）+ 综述，另有子目录。
  **一级分类是单值的**，但条目可以同时挂在多个一级目录下：`classification.json` 的
  `secondary` 字段会一并写进对应的一级目录，实现多归属
- **归档**：一级目录 `归档`（2026-09-17 建）之下是**过时项目的归档**，当前是 `归档/浙工大项目`。
  归档子树里的东西**只入库、不参与日常整理**：`tidy.py` 跳过整棵子树（否则「空目录」判定会把它删掉），
  日常检索、分类、画像也都不必扫描和浏览它。名字由 `config/topic.json` 的 `archive_collections` 给出
- **标签轴**：主题子类（`相变`、`O2 释放`…）、方法（`方法:DFT`…）、材料（`材料:NCM/NCA`…）、
  形态（`形态:单晶` / `形态:多晶二次颗粒`——**是形貌不是材料体系**，2026-09-20 从材料轴分出）、
  体系（`体系:固态电解质` / `体系:固液对比`——**只标非默认情形**，液态不标；同日从材料轴分出，
  原先笼统记作 `材料:电解质/界面`，既不是材料家族也分不清固液），
  另有两套专为**交叉主题**设的附加轴（一级分类装不下跨轴论文，靠它们补）：
  - `耦合:<方向>`——该文主张的耦合机制，取值见 `config/topic.json` 的 `coupling`：
    `化学→力学`（吸附/腐蚀/氧流失/电解液侵蚀驱动力学失效）、`力学→化学`（开裂驱动界面化学）、
    `双向`、`本征力化学`（耦合源自脱锂过程本身，不依赖外部化学物种）。
    **只标注、不参与日报打分**——`daily_digest.py` 的 `classify()` 只读 `primary`/`sub`。
    判定准则：**只要作者自己主张了该方向的因果关系即计入，不要求其给出直接证据**
  - `兼属:<一级分类>`——该文的第二主题，与 `secondary` 字段一一对应
  - 五个轴的**可选值都在 `config/topic.json`**（`labels.*` 与 `coupling`）：`apply_to_zotero.py`
    会拿它校验 `classification.json`，词表外的取值会打印 `! 词表外的取值` 告警。
    `sub` 的可选值以 `categories.<类>.sub` 为准（`labels.sub` 只是关键词表）。
  - **改名/删标签**：`apply_to_zotero.py` 是纯新增、从不删除，改了标签名旧标签会一直残留在
    条目上。这类操作走 `python3 scripts/migrate_labels.py [--apply]`：按表换名、删游离标签、
    并用 `PATCH /collections/<key>` **原地改目录名**（不新建、不搬移、不删除），写前全字段备份到
    `state/label_migration_backup_<日期>.json`。三处要一起改：`config/topic.json`、
    `state/classification.json`、Zotero 本身
- **链接附件**：每篇挂 2 个 `linked_file` —— 「全文 Markdown」→ `paper.md`，「中文总结」→ `summary.md`
- 用绝对路径，**移动 `~/LitHub` 会导致链接失效**

读写 Zotero 前**先读** `帮助手册.md` §7（Zotero 本地 API）或调用 skill `zotero-local-api`，
里面记录了若干静默失败的坑。

## 常用操作

```bash
cd ~/LitHub

# 新增 PDF 批量转 Markdown（幂等，引擎不同则自动重转）
python3 scripts/mineru_batch.py all 3

# 逐篇生成中文总结（幂等；属性区由 Zotero 元数据写入）
python3 scripts/summarize_batch.py 3

# 只打印最终提示词，不调模型、不写文件（改了 prompts/summarize.md 后先用它自检）
python3 scripts/summarize_batch.py --dry-run 1 ABCD1234 --figures

# 换模板 / 限制每篇最多读几张图（默认 5）
python3 scripts/summarize_batch.py --template prompts/summarize.md --max-figures 3 2

# 步数上限（默认 5，0 = 不限）：这是**省钱的主要开关**，见下
python3 scripts/summarize_batch.py 4 --max-steps 5
```

#### 省 token 的四个开关（按性价比排序，2026-09-20 实测）

**① 一次调用（oneshot，默认已开，省最多）**：不让模型读文件、写文件——脚本把**裁剪后的论文全文**
直接塞进提示词，模型一次回复就是总结正文，脚本再落盘（`--output-format stream-json` 解析 assistant
文本）。实测 **0.14 → 0.037 元/篇（省 74%）**，只要 1 次调用。

三种载体由脚本自动选（`build_prompt()` 的 `mode`）：

| 载体 | 何时用 | 形态 | 实测 |
|---|---|---|---|
| `inline` | 裁剪后全文 ≤ 110 KB | 全文注入提示词，`prompts/oneshot.agent.md`（`tools: []`），1 次调用 | **0.037 元** |
| `file` | 塞不下（**Linux 单参数上限 128 KB**，实测 16/184 篇） | 全文写成本目录 `paper.summarize.md`，`prompts/oneshot-read.agent.md`（`tools: [Read]`），读完即答，**用完在 `finally` 里删掉** | 约 0.04 元 |
| `tool` | 前两者失败时兜底；`--no-oneshot` 可强制 | 模型自己读 `paper.md`、自己写 `summary.md`（老路径） | 0.053 元 |

**② 裁剪论文（默认已开，省约 7%）**：`prune_paper()` 去掉三块对总结没用、却占 token 的东西——
front-matter（元数据由 Zotero 给）、图片路径行（总结只写图号）、**参考文献段**。参考文献最容易切错，
两道护栏：**切完至少留原文 35%**、且切点必须落在文件 35% 之后。识别参考文献有两种办法：认
`## References` 标题（实测只覆盖 59%——MinerU 常把标题丢了），认不到就从末尾往回找「其后非空行里
≥60% 是编号引文行、且至少 15 行」的最靠前位置（不能一遇断链就停，参考文献区里常有几行不带编号的
续行，那样只能切到尾巴一小段）。合计覆盖 **165/184 篇，裁掉中位 26%（最多 67%）**。

**③ 受限 agent（`tool` 载体时生效，省约一半）**：默认会话会把**内置工具 + MCP（zotero 那 40 个）的
全部 schema** 塞进每次调用。实测只给 `Read`/`Write`（`prompts/summarizer.agent.md`），
**首调未缓存输入 35,441 → 2,429 token（省 93%）**，整篇 0.109 → 0.053 元。`embed_figures.py`
同样接了 `prompts/figures.agent.md`。

⚠️ 两个坑：`--agent-file` 必须放在 `-p` **之前**、且用 `=` 形式——`-p` 自己吃下一个参数当提示词，
写成 `-p --agent-file X` 会让 X 变成子命令（实测报 `unknown command`）。另外 `--skills-dir`
指向空目录只省 1%，**技能不是开销大头，工具 schema 才是**。

**④ 步数上限 `--max-steps`（默认 5，只在 `tool` 载体下起作用）**：截断「反复读改」的来回。
oneshot 的 `inline`/`file` 载体没有工具调用，这个上限自然不参与。它仍然管着兜底路径，也是
「模型一步没走完」的护栏（`summarize()` 核对 `summary.md` 的 mtime，没变过就报「未落笔」）。

提示词模板 `prompts/summarize.md` 有 6 个占位符：`{task}`（任务定位句，按载体换措辞）、`{meta}`、
`{library}`、`{draft_rule}`、`{source}`（论文全文，只有 inline 注入）、`{deliver}`（交付要求），
外加 FIGURES 段里的 `{figure_manifest}`。改模板后要同步 `summarize_batch.py` 的内置兜底常量——
目前是用脚本把模板正文抽出来覆写 `PROMPT` / `FIGURE_ADDENDUM` 两个常量。

#### 写一篇总结要花多少 token（2026-09-20 实测）

`summarize_batch.py` 逐篇跑 `kimi -p`，是**代理式写作**：模型读 `paper.md`、写 `summary.md`、
再回头改，**每一步都把整篇论文重发一遍**。所以开销几乎完全由**来回次数**决定。两个开关配合起来
最有效：`--max-steps`（截断来回）+ 提示词里的「**一回读完、一次成稿**」（让回到次数本来就少）。
提示词第 6 条要求模型用**一次** `Read`（显式带 `max_chars: 500000`）读完 `paper.md`、再用**一次**
写文件调用写完——不这样的话，275 KB 的大论文会被按行分页读，光读就花光步数配额。

| 同篇（示例） | 模型调用 | 输入 token | 输出 | 耗时 | 成本 |
|---|---:|---:|---:|---:|---:|
| 不限步数、无「一次成稿」 | 38 | 4.08 M | 56.5 k | 247 s | 0.71 元 |
| `--max-steps 8` | 8 | 0.66 M | 34.7 k | 143 s | 0.22 元 |
| `--max-steps 6` | 6 | 0.61 M | 18.6 k | 92 s | 0.23 元 |
| `--max-steps 5` + 一次成稿 | 3 | 0.21 M | 10.6 k | 52 s | 0.13 元 |
| **再加受限 agent（现在的默认）** | 3 | **0.05 M** | 6.9 k | 35 s | **0.053 元** |

30 篇实测（2026-09-20，含受限 agent）：125 次调用（均 4.2）、输入 9.71 M（未缓存 2.40 M +
缓存 7.32 M）、输出 265.8 k，**合计 ≈ 4.19 元（均 0.140 元/篇）**。这批之后套用受限 agent
同样规模约 2.0 元。成本结构：未缓存输入 57% / 输出 25% / 缓存命中 18%——**受限 agent 把
「固定开销」那一块打掉之后，剩下的主要是论文正文本身（约 17 k）与输出（约 7 k）**。

最大的一篇（`paper.md` 275 KB）也验证过：`--max-steps 5` 用 3 次调用、31 s、0.13 元；
`--max-steps 4` 用 3 次调用、41 s、0.14 元，产出都是完整七节 + 5 行表格 + `关键图表` 保留。
所以 **4–5 步是实测下限**，默认取 5。

- 那 4.08 M 输入里 **3.99 M 是重发的上下文**，钱主要花在来回上，不是花在论文上。
- 成本按 `deepseek-flash` 的 1 元/M 输入、4 元/M 输出算；**缓存命中价仓库里没有记录**，
  上面的数字按 0.1× 估（DeepSeek 官方通常是这个量级），按原价算会高 3–6 倍。
- 上限设太紧的表现不是「质量差」，而是**整篇没写**（模型把步数花在读上、还没到写）——
  所以 `summarize()` 会核对 `summary.md` 的 mtime，没被改过就报「未落笔」而不是假装成功。
- `~/.kimi-code/config.toml` 里也有全局的 `[loop_control] max_steps_per_turn`，**别用**——
  那是全进程的，会把交互式长任务的单轮步数一起限死；`--max-steps` 只作用于子进程。
- 想彻底去掉来回（一次调用 ≈ 0.05 元/篇）：把 `paper.md` 原文塞进提示词、让模型把总结当纯文本
  输出、脚本再写文件（需给 `kimi -p` 传禁用工具的 agent）。**尚未实现**。

# 只刷新已有 summary.md 的属性区（不重跑模型）
python3 scripts/sync_properties.py --apply

# 更新分类数据与总表
python3 scripts/sync_classification.py

# 把新文献的 md 挂到 Zotero（幂等）
python3 scripts/link_markdown.py

# 库内语义画像向量（日报的 S 项用；需先装依赖，见「每日文献日报」）
python3 scripts/build_embeddings.py
```

⚠️ `summarize_batch.py` 的 `--figures` 开关（让总结引用图片）**实测无效**：本环境没有
视觉能力，跑 `kimi -p` 的模型读不到 `images/` 里的图——一篇会诚实声明「未能读取图片」，
另一篇则把**图注转述**写成了像是观察结果。该开关默认关闭，**别开**。
2026-09-16 起它不再让模型遍历 `images/`：脚本先抽出「图号 + 图注」清单注入提示词，
并限死最多读 `--max-figures`（默认 5）张，所以误开不再白花时间（原先会读遍 31–246 张），
但**读不到图这件事没变**。要让总结带图，做法是**把图片直接嵌进 `summary.md`**
（写成 `![](images/xxx.jpg)` 相对路径，Obsidian 就地渲染），描述一律据图注与正文，
并注明未判读图像内容。

### 收编待整理/ 里的新 PDF

> **「入库」的判据**（用户口径）：**跑到 `mineru_batch.py`（转 Markdown）和
> `summarize_batch.py`（写中文总结）才算入库**，只建条目 + 挂 PDF 不算。
> 自查：`papers/` 目录数 = 库内顶层条目数，且每篇 `paper.md` 与 `summary.md` 都在；
> 只建了条目的，`link_markdown.py` 会报「缺文件」。
> （2026-09-16 起因「新建条目就算是入库」被纠正，勿再按该口径交付。）

```bash
# 按首页 DOI 匹配库里已有条目，缺则用 CrossRef 元数据新建；PDF 三段式上传并登记 manifest
python3 scripts/intake_pdfs.py --dry-run
python3 scripts/intake_pdfs.py --remove      # --remove：收编成功后删掉待整理里的原件
```

出版方的作者接受稿（OSTI/仓库版）常整篇不含 DOI，首页提取不到就会跳过；这类在
`待整理/_doi_hints.json` 里补一行 `"<文件名>": "<DOI>"`，脚本优先用它（提示优先于文本提取）。

收编只负责「条目 + PDF 附件 + manifest」。随后补齐三步才算整理完：

1. 给新条目在 `state/classification.json` 里加一条分类（primary/secondary/sub/method/materials/form）
2. `python3 scripts/apply_to_zotero.py` 写入分类目录与标签，再 `python3 scripts/sync_classification.py` 刷新总表
3. `python3 scripts/mineru_batch.py all 3` → `python3 scripts/summarize_batch.py 3` → `python3 scripts/link_markdown.py`

最后收尾跑一次 `python3 scripts/tidy.py --apply`（见下节），把空目录之类一并清掉。

### 补全 Zotero 条目的元数据：`scripts/fill_meta.py`

条目常缺 DOI / 刊名 / 年份 / 卷期页（全库顶层 155 条里 65 条有缺口，含 `issue`——
Elsevier 系刊条目本来就常缺期号），Zotero 自己补不了，而 `cite` 字段就靠它们。
脚本去 CrossRef 取回来写进 Zotero：

```bash
python3 scripts/fill_meta.py            # 只报告（扫全库顶层条目）
python3 scripts/fill_meta.py --apply
python3 scripts/fill_meta.py --key ABCD1234
```

- **只补空字段，绝不覆盖已有值**；有 DOI 按 DOI 取，没有才用标题检索
- 标题检索要求相似度 ≥0.95 **且作者重叠 ≥50%**，多条候选优先期刊论文（非预印本）；
  宁可不动，也不写可能错的 DOI
- 作者名单只在「Zotero 名单是 CrossRef 名单的严格前缀」时补齐末尾漏掉的作者，
  顺序或中间有出入一律不动——所以有时仍要人工核（见下）
- 写前把受影响条目的**完整旧数据**存进记档 `state/meta_fills.json`
  （追加式：日期 / key / 字段旧值→新值 / 来源 / 相似度），人工补的那次也记在这里
- CrossRef 也查不到的（多是该刊自己就没给卷期页）会列成「CrossRef 也没有」，不是失败
- 人工核过的例外：有一篇（Thornton 等）Zotero 名单漏了末位共同通讯作者
  Bethan J. V. Davies，而 CrossRef 那份**预印本**记录把通讯作者排在首位、顺序不同，
  前缀规则不认，只能照 PDF 首页人工补

补完记得 `sync_properties.py --apply` 刷新 `summary.md` 的属性区（卷期页、DOI 会变）。

### 常规清理：`scripts/tidy.py`

**每次整理完都要跑**。四类对象：

| 类别 | 判定 | 处理 |
|---|---|---|
| 空条目 | 顶层条目没有任何子项（无附件、无笔记） | 硬删除（**但已判定为重复条目待删的除外**，它要走合并那条路） |
| 重复条目 | DOI 相同，或标题归一化后相同 | 标签/目录并入留存条目后硬删除；**有子项的重跑默认只报告**，加 `--merge-dups` 才合并 |
| 孤儿目录 | `papers/<key>_*` 在 Zotero 已无对应条目 | **移动**到 `state/trash/<日期>/`，不删 |
| 空目录 | 库里没有任何条目归属、**且没有子目录**的分类目录 | 硬删除 |

留存条目由 `pick_survivor()` 挑，**优先留流水线认得的那条**（有 `papers/` 目录）——
`papers/<key>_*`、`manifest.json`、`classification.json` 都按 key 认人，留错 key 要连带改名一堆
东西（2026-09-20 修：此前只看子项/PDF，会把「只有 PDF 的重复条目」选中、把已入库那条删掉）。

`--merge-dups` 的合并动作：把重复条目的子项挂到留存条目（**同一份文件**——同类型同文件名——
不搬，随重复条目一起删），标签/目录并过去，再删重复条目；搬过去的 PDF 若是留存条目的
`manifest.json` 路径指向已消失文件的那一份，顺手把路径改成新位置。

⚠️ 空目录只删**叶子**（2026-09-17 修）：删父目录会**连带删除它的全部子目录**，
而「父目录自己没条目、子目录里却装着文献」很常见，旧写法会把整支子目录连文献归属一起删掉。
同时**跳过归档子树**（`config/topic.json` 的 `archive_collections`，见「Zotero 联动方式」）
与 `extra_categories`（`apply_to_zotero.py` 每轮都会把它们建出来，tidy 再删就是两个脚本互相抵消）。

```bash
python3 scripts/tidy.py                          # 只报告，默认
python3 scripts/tidy.py --apply                  # 备份后执行
python3 scripts/tidy.py --apply --keep-dups      # 不动重复条目
python3 scripts/tidy.py --apply --merge-dups     # 连有子项的重复条目一起合并
```

`--apply` 会先把要删的东西全量导出到 `state/tidy_backup_<日期>.json`，并把
`classification.json` / `manifest.json` 里指向已消失条目的记录一并清掉；
跑完再执行 `sync_classification.py && digest.py json` 刷新总表与摘要清单。

⚠️ **`DELETE /items/<key>` 是硬删除，不进回收站**（`帮助手册.md` §7.7 有核验方法），
所以这些操作都必须先备份、默认只报告。

### 两个转换引擎与图表

> 细节与踩坑见 `帮助手册.md` §8（token 报错不可信、一图多面板、控制字节、视觉能力等）。

`mineru_batch.py` 有两个引擎，**默认 `extract`**：

| | `extract`（默认，精准模式） | `flash`（`--flash`） |
|---|---|---|
| 令牌 | **需要** `mineru-open-api auth` 配置 | 免令牌 |
| 图表 | 导出 `images/`，MD 里是 `![](images/<hash>.jpg)` | 只有一个 `<!-- image-->` 占位符 |
| 公式/表格 | 识别 | 占位符 |
| 限额 | 200 MB / 600 页 | 10 MB / 20 页（需 gs 预压缩 + 分块） |

- 令牌失效时报 401 `A0211 user token expired`。**注意该错误码没有诊断力**：
  编造的 `sk-0000…` 同样返回它。真正常见的失败原因是**复制的 key 不完整**
  （控制台把中间打码，看不出长度）——正确长度是 **51 字符**。
- `paper.md` 的 front-matter 记了 `engine:`，跳过逻辑按引擎判断：flash 转的
  遇到 extract 会被重转（否则永远拿不到图）。
- 图片与 MD 同目录，Obsidian 直接渲染；一篇的 `images/` 常有 20–150 张。
  全库跑完 `papers/` 约 113 MB（纯 MD 时约 7 MB）。
- 一图常被拆成**多个连续图片文件**，图注在最后一张之后。要引用某图必须把
  图注前的连续图片全部取上，只取一张会漏掉大部分面板。

### summary.md 的属性区：`scripts/sync_properties.py`

**属性区（Properties）不由模型写**。`summary.md` 的正文是 `kimi -p` 读 MinerU 转出的
`paper.md` 写的，而 MinerU 常解析不出 DOI、刊名、卷期页——模型只能写 `unknown` 或按
正文猜（实测 147 篇里 39 篇的 `doi` 是 `unknown`）。这些字段 Zotero 建条目时就有了
（CrossRef 或出版商页面），所以改由脚本从 Zotero 写入，模型只管正文。

```bash
python3 scripts/sync_properties.py              # 只报告差异（默认）
python3 scripts/sync_properties.py --apply      # 写回，改前自动备份属性区
python3 scripts/sync_properties.py --apply --key ABCD1234
python3 scripts/sync_properties.py --build-abbr # 只补期刊简写表
```

| 字段 | 来源 |
|---|---|
| `title` | Zotero 标题（原文，不是模型转写的） |
| `author` | 全部作者，Zotero 顺序，逗号分隔 |
| `year` | `date` 里的 4 位年份 |
| `journal` | 期刊**全称**（`build_profile.py` 的刊名统计依赖它，别改成简写） |
| `cite` | 短引用：`<末位作者全名>, <年份>, <期刊简写>, <卷期页>, <DOI>` |
| `doi` | DOI |

- `cite` 的作者取**末位作者全名**（本领域的通讯作者惯例）：`Yang-Kook Sun`，不是 `Sun`。
  Zotero/CrossRef 都没有通讯作者标记，末位作者是最接近的近似。
- 期刊简写查 `state/journal_abbr.json`（全称 → ISO4 简写，**手工可改**）；
  表里没有的刊自动补（CrossRef `short-container-title` → Zotero `journalAbbreviation`
  → 全称），已有条目不会被覆盖，所以手工改过的值留得住。
- Zotero 某个字段为空时**沿用旧属性区的值**（模型可能从正文里正确抄到了），
  不会被覆盖成空；`cite` 的构件少于 3 个时留空，不写半截引用。
- `summarize_batch.py` 生成后自动调这套逻辑写属性区；老文件不重跑模型也能补，直接 `--apply`。

### 给 summary.md 配图：`scripts/embed_figures.py`

按**图注 + 正文引用句**挑 3–5 张关键图，嵌进 `summary.md` 的「关键图表」一节，
图注中英双语。模型只做判断，**图片路径由脚本解析填入**（防它编造文件名），
图号不存在则丢弃并记录。

```bash
python3 scripts/embed_figures.py --dry-run           # 看各篇上下文大小与图数
python3 scripts/embed_figures.py 4                   # 并发 4
python3 scripts/embed_figures.py 2 KEY1,KEY2 --force
```

- 只给模型标题/摘要/结论/各图图注与引用句（3–7 KB），**不喂全文**，token 约为 1/10
- 一篇约 20–28 秒；已带「关键图表」的会跳过（`--force` 重做）
- 依赖图注可解析：`Figure 1.` / `Fig. 1.` / `Fig. 1 |` 都支持。
  ⚠️ 正文里 `Fig. 8 shows …` 这类句子也以 `Fig. 8` 开头，脚本按「前面挂了图片最多的候选行」
  判定真图注（实测这个 bug 会让 21 篇丢 24 张图）。
- 少数 PDF 有图但**没有图注**（实测 4/103），无法把图片对应到图号，脚本会跳过并报告

### 修残留控制字节：`scripts/fix_control_bytes.py`

字体 ToUnicode 映射坏的 PDF 会产出 NUL 字节（连字、度符号、上标负号、希腊字母），
文件因此被当**二进制**处理，严格工具直接拒读。精准模式已修掉大部分，剩下的按
上下文规则补：

```bash
python3 scripts/fix_control_bytes.py            # 只报告
python3 scripts/fix_control_bytes.py --apply    # 写入（原件备份到 state/trash/）
```

判定不出的位置**不猜**，替换成 `【?】` 并写进 `state/control_bytes_fix.json`。
实测有极少数位置连 `pdftotext` 也解不出（字形在 PDF 里就丢了）。

⚠️ 占位符**不要用 `□`**：本库里 `□` 是晶格空位的合法记号（`Li4/7[□1/7Mn6/7]O2`），
会分不清哪个是原有内容。

### 主动检索新文献

```bash
# 按库内 6 个一级分类检索库外新文献（幂等，原始响应缓存在 state/work/）
python3 scripts/find_new.py 2025-01-01 80 crossref
```

- 结果写 `state/candidates.json`（全量）与 `state/candidates_meta.json`；日志追加到 `logs/find_new.log`
- 源可选 `crossref`（默认，覆盖广）或 `openalex`（相关度好，但有 429 限流风险）
- 过滤链：期刊白名单 → 去重（DOI + 标题模糊）→ 锂电层状正极相关性闸门 → 离题词
- 摘要来源依次为 Crossref → Semantic Scholar，Elsevier 系多不开放摘要，会标记「未取到」

### 每日文献日报（已挂 systemd 定时）

日报按**三个通道**选文献：各自过闸后统一打分，版面**分三轮发**：

1. **通道配额轮**：fresh 40% / refs 25% / 其余 query，某通道凑不满就把配额让给别的通道；
2. **分类保底轮**：`--cat-floor`（默认 `力学耦合=3,界面反应=2`）给指定分类留保底名额，
   按分类权重从高到低发（权重高的方向先占位）；
3. **全局补位轮**：剩下的名额给全局分数最高的候选（每类上限 `cap = limit 的 40%` 只在
   这一轮可以被放宽）。

第 1 轮的比例是**扣掉保底预留后的 budget** 算的，不是按 `limit`——否则三通道配额之和
仍是 `limit`，第一轮就成了先到先得，播放顺序里排最后的 refs 可能一席都拿不到、
只能靠补位轮找回，配额的通道多样性作用等于没了（预留越大偏差越明显）。
保底名额不得超过 `cap`，合计不得 ≥ `limit`（超了按比例缩到 `limit//2` 并告警）。

**为什么需要保底**（2026-09-16）：`R` 去掉内容分类权重后，分类权重不再影响 fresh/query
通道的择优，实测入选里力学耦合从 3 跌到 1（它入池只有 91 篇，结构退化 717 篇）。
保底把 `w_cat` 原来的「补偿供给不足」职能接了过来，同时不打分、不引入三重加权。
**保底复用 `take()`，所以 `--min-score` 与每类 `cap` 照旧管着它**——保底给的是「版面名额」
而不是「免检入场券」，跨不过门槛就空着，并在日志里写明原因（候选不足 / 被门槛挡下 /
撞到 cap / limit 已发完）。日志里「保底席位：力学耦合 3/3、界面反应 2/2」一行
是复核用的，计数取保底轮结束时点（之后的补位轮可能再给同一分类加人，那不算保底的功劳）。

实测（2026-09-16 同一批缓存，limit 16）：去 `w_cat` 后为「离子输运 4、结构退化 4、氧活性 3、
界面反应 2、基础理论 2、力学耦合 1」；加保底后为「离子输运 3、氧活性 3、结构退化 3、
力学耦合 3、界面反应 2、基础理论 2」，保底两项均达标。

```
B = 0.25*R + 0.15*S + 0.22*C + 0.20*X + 0.13*J' + 0.05*A    # S 可用时
B = 0.40*R + 0.22*C + 0.20*X + 0.13*J' + 0.05*A             # S 不可用：那 0.15 回补给 R
final = B * (1 + 0.35*F)                     # 综述再 ×0.9
```

| 项 | 含义 |
|---|---|
| `R` | **纯检索排名** `1/(1+rank/5)`。2026-09-16 起**不再乘内容分类权重**，理由与实测代价见下 |
| `S` | 候选与**库内 147 篇**的最大余弦相似度（候选 `title+abstract` 对库内 `title+concl+method`）。需要 `state/work/lib_emb.npz`（`scripts/build_embeddings.py` 生成）与 `sentence-transformers`，缺任一项就整项跳过、把那 0.15 回补给 `R`，并在日志写明原因 |
| `C` | 库内共被引：`min(1, ln(1+n)/ln(31))·(0.65+0.35·q̄)·(0.75+0.25·r̄)`；`n`=被几篇库内文献引用，`q̄`=引用者平均分类权重比（**分类权重现在只在这里生效**），`r̄`=引用者平均 `exp(-Δy/3)`（Δy 单位**年**） |
| `X` | 经典度：`min(1, ln(1+被引数)/ln(5001))`——只看绝对被引量，**完全不看年份**（Crossref `is-referenced-by-count`，三个通道都取） |
| `J'` | 期刊层级：`clamp(ln(IF/4)/ln(8), 0, 1) − 0.5`，IF 查 `state/journal_if.json`（OpenAlex 代理指标，**不是 JCR**）；查不到按 0（中性） |
| `A` | 开放获取（1/0） |
| `F` | 新鲜度 `exp(-Δ/180天)`，只加成：老文 ×1.0、新文最多 ×1.35（用 published，不用 Crossref created） |

**年龄不再做乘法惩罚**，这是这套公式的核心：`X` 不看年份，`C` 里引用者年代只做 ≤25% 的
有界调节（地板 `--citer-floor`，默认 0.75）。旧公式 `C = Σ exp(-age/730天)/4` 让 10 年前的
引用只剩 0.7% 权重，等于把「被多少篇库内文献引用」退化成年龄惩罚——1980 年的 LiCoO₂ 开山作
因此在经典组里垫底。**J' 减 0.5 也不能省**：直接留 `[0,1]` 会让所有白名单期刊白拿 0.1–0.15，
总分整体抬高、门槛失去意义；减完是「好刊加分、普通刊小扣」，净效应接近零。

`A` 只在**有资格入选的候选**里取值一致：先按不含 A 的分排短名单（各通道配额+2）与全局前
`--oa-top`（默认 48）名，只有这批去查 Unpaywall 拿 A，其余 A=0。旧代码只给短名单查 OA，
却拿含 A 的分做全局排序与门槛判定——进短名单的白拿 0.15、没进的 1300 篇候选被静默扣分
（它们从没被查过），那是 bug 不是设计。

**R 为什么与分类解耦**（2026-09-16）：旧 `R = (1/(1+rank/5))·(w(cls.primary)/w_max)`。
`rank` 本身已经来自画像生成的检索式，再乘一次内容分类权重是**三重加权**（检索式条数 +
rank + w_cat），且 `cls.primary` 是关键词数出来的、与命中通道 `hit_cat` 一致率只有 30%
（1362 篇候选里 413 篇），等于用一个有噪声的量去修正另一个。改完之后 `R` 只回答
「搜索引擎认为它匹配到什么程度」，画像的作用交给检索式（供给）与 `S`（打分）。

**代价是实测出来的**（同一批缓存数据，2026-09-16）：去掉 `w_cat` 后入选
`力学耦合 3→1`、`基础理论 0→2`、`离子输运 2→4`、`结构退化 6→4`，通道上
`refs 8→4`、`query 6→9`，低于门槛的候选 236→177。也就是说 `w_cat` 真正在干的是
**补偿供给不足**（力学耦合入池仅 91 篇，结构退化 717 篇）：它的「打分」职能删对了，
「保底」职能还空着。

**分类权重（`CAT_PREF`）现在只剩一处生效**：`C` 的 `q̄`（「库内权重高的方向引用了它」，
是画像信号，不算冗余），**不再影响 `R`**，也影响不到 fresh/query 通道的择优。
调法不变：改 `build_profile.py` 顶部的 `CAT_PREF`（自动权重 × 偏好，上限 3.0），
`python3 scripts/build_profile.py` 重跑；**不要手改 `state/profile.json`**
（`lithub-profile.timer` 每月会覆盖它）。`文献画像.md` 第 1 节打印的 `w/w_max` 现在只喂 `q̄`。

想让力学耦合回到每天 3 篇左右，有两条路（**都还没做**）：

1. **加分类保底席位**：在入选循环里给每个分类留保底名额，把分类权重从「分值」改成「配额」——
   既保住去三重加权，又保住手动杠杆（推荐）。
2. **补检索式**：加权重换不来力学文献，必须同时加检索式——见下方 2026-09-15 的实测。

另外两点要注意：

- **refs 通道没有排名，`R=0`**，分类权重只能经 `C` 的 `q̄` 起作用。力学候选里有
  30/96 走这个通道。
- ⚠️ **启用 `S` 前必须重标定 `--min-score`**：有 `S` 时几乎每篇候选都拿 `0.15×S`，
  实测（stub 向量）门槛挡下数从 177/1426 掉到 13/1426；`0.04` 是**无 `S` 时**标定的。

调完用 `python3 scripts/daily_digest.py --dry-run --diag-cats --date <日期>` 看效果：
每类的候选数量、命中来源（哪条检索式捞到的）、以及最高分的几篇离入选线差多少。
实测（2026-09-15，库内 118 篇）：只把力学耦合乘到 2.5 倍时，力学候选 85 篇里只有
19 篇来自力学检索式，最高分 0.326 仍低于入选线（当日入选 力学耦合 1 篇）——
**加权重换不来力学文献，必须同时加检索式**。补了 4 条检索式（力学耦合 6→8、
界面反应 7→8）后，力学候选 96 篇、其中 35 篇来自力学检索式，最高分升到 0.481 和
0.445（后者是某条检索式的第 1 名），入选变成 力学耦合 3、界面反应 6（顶到上限）。
选检索式的办法：直接量产率——`search_crossref` 拉 100 条，过 `journal_ok` +
`LAYERED_PAT` + `relevant()` 后再 `classify()`，数「过闸篇数 / 其中力学耦合篇数 /
最好排名」。实测 `chemo-mechanical degradation Ni-rich cathode` 一次给 10 篇力学耦合，
而 `stress corrosion cracking cathode electrolyte cathode` 给 0 篇。

| 通道 | 来源 | 取数参数 |
|---|---|---|
| fresh | Crossref 近 `--days` 天新入库的 journal-article，**窗口固定，不再自动放宽**；缓存键含 `since`，每期必冷 40 个请求（检索式数 × 页数） | `--rows` 100 × `--pages` 1 |
| query | 同一检索式但不加日期过滤，按相关度分页多取；**默认不限年份**（`--min-year 0`。旧默认 2010 会把 1980 年的 LiCoO₂、1996 年的 PBE 挡在闸门外，与「经典不因年龄掉权重」冲突）；缓存键带**刷新周期桶**（`period_tag`，默认 30 天），同一周期内重跑零网络、跨周期自动刷新 | `--rows` 100 × `--query-pages` 3 |
| refs | 库内引文推荐：`state/citation_graph.json` 的 `co_citation`。先用 `ref_meta` 的期刊/标题做零网络初筛（参考文献刊名是 ISO 缩写，先展开成白名单全名再判），剩下的才按 DOI 批量取元数据（批次键同样带周期桶）。**被引数取 Crossref 的 `is-referenced-by-count`**，`X` 靠它 | — |

工作点 `--rows 100 --pages 1 --query-pages 3 --min-score 0.04` 是实测定的（现在都在
`config/runtime.json` 的 `pool` / `digest` 段）：单请求 100 条约 7.9 s、50 条约 3.0 s
（耗时随 rows 涨），但 fresh 每期必冷的请求数是固定的 40 个，100 条换来的候选是 50 条的
两倍（16 篇 vs 7 篇），所以宁可要 100 条——每日稳定态实测约 10 分钟（2026-09-20，
40 条检索式、query/refs 命中周期缓存），仍在预算内。`--min-score`（默认 0.04）是硬门槛：final 低于它的候选任何
通道都不入选，配额空出的名额让给全局最高分的其他候选。门槛随公式一起重标定过（R 权重
0.55→0.40、A 0.15→0.05 让相关度主导的分数整体下移，X 与 J' 又把经典与好刊抬回去）：
0.04 挡掉实测 `final ≲ 0.035` 的噪声（R≈0.02、0 被引、普通刊），但不误杀 fresh 版面那一档
（默认 limit 下最低入选实测 0.076）。`--citer-years`（默认 3 年）与 `--citer-floor`（默认 0.75）
是 `C` 里引用者年龄的衰减常数与地板——地板调到 0.85–0.90 就更彻底地忽略引用者年代；
旧的 `--citer-tau`（天）已随公式重写移除。`--oa-top`（默认 48）是定 A 时除短名单外额外
查 Unpaywall 的全局名额，0 = 只查短名单（旧行为）。

```bash
# 先看生效的运行配置（config/runtime.json 再被 CLI 覆盖后的值）
python3 scripts/daily_digest.py --show-config

# 手动生成某天的日报（幂等，重跑命中缓存则零 LLM 花销）
python3 scripts/daily_digest.py                                   # 裸跑 = 用 runtime.json 的工作点
python3 scripts/daily_digest.py --date 2026-09-12 --no-llm       # 完全不调用 LLM
python3 scripts/daily_digest.py --dry-run                         # 预演，不写文件也不调 LLM
python3 scripts/daily_digest.py --dry-run --diag-pagination       # 诊断 rows/页数够不够
python3 scripts/daily_digest.py --force                           # 忽略 schedule 频率限制，强制出一期
python3 scripts/daily_digest.py --cooldown-days 30 --min-year 2015   # 显式恢复年份下限
python3 scripts/daily_digest.py --min-score 0 --citer-years 1.5      # 关门槛 / 收紧引用者年龄衰减

# 引文通道的前置数据（都幂等；库内新增文献后各跑一次）
python3 scripts/library_index.py      # -> state/library_index.json（权威 DOI 索引）
python3 scripts/citation_graph.py     # -> state/citation_graph.json（增量；--rebuild 全量重建）

# 期刊层级表（J' 用；年报刷新，全量 60 余个刊 ≈ 60 个请求）
python3 scripts/journal_if.py             # 增量：已有 IF 的不重查
python3 scripts/journal_if.py --refresh   # 全部重查

# 刷新文献画像（纯统计，零 API 花销）
python3 scripts/build_profile.py
```

**文献画像的 8 节**：1 分类分布与检索配额 / 2 子类标签热度 / 3 方法・材料・形态・体系 /
4 年份与期刊 / **5 时间趋势** / **6 值得注意的发现** / **7 被引与推荐分** / 8 当前每日检索式。

三个新节（2026-09-20 加）全部**由规则算出、零 token**（`build_profile.py` 不调 LLM，
可以放心挂每月定时）：

- **§5 时间趋势**：出版年 × 一级分类交叉表（近 12 年）、近三年 vs 更早的份额变化与
  升温/降温判定、中位出版年、近期年份断层。「近三年」的界由 `RECENT_FROM =
  当前年 − 2` 算，**不再写死 2024**（写死过一年就失准）。
- **§6 值得注意的发现**：分类集中度、综述分布、**方法学缺项**（常用方法在哪些类里
  完全缺席的交叉表）、语料自查（重复 DOI / 重复标题 / 标签覆盖率 / 覆盖最薄的方向）。
  ⚠️ 它只给**结构性事实**——「结论互相矛盾」「方法学机会」这类要读全文的判断仍得人工写
  （`分类与标签总表.md` 的 §4 就是人工的，`sync_classification.py` 按锚点保护它）。
- **§7 被引与推荐分**：被库内引用最多的 10 篇、**孤立文献**（既没被库内引用、也没引用
  库内文献，实测 11 篇）、推荐分分档与各分类中位分。数据来自 `citation_graph.json` 与
  `library_scores.json`，**两者缺失时整段降级成一句说明**，不会让画像跑不出来。

⚠️ **「热点词」「新兴方向」两节 2026-09-20 已从画像里去掉**：`phrases()` 不滤数字与单位，
抽出来的是 `mah g` / `3 v` / `4 3` / `0 5` 这类碎片，登出来是噪声（原来 AGENTS.md 说的
「热点词扩写基本不生效」，根子是**源头就是垃圾**）。
`hot` / `emerging` 的计算**暂时保留在代码里**——`hot` 还接着「检索式扩写」那条路
（`for p in hot: if any(w in p for w in ("cathode", "layered", …))`）。实测那条路
**从未生效过**（`profile.json` 的 `queries` 与 `topic.json` 的基础式逐字相同），
`profile.json` 的 `hot_phrases` / `emerging_phrases` 也**没有任何消费者**。
要彻底删掉这套机制（含扩写）说一声——删了不影响 `queries`。

⚠️ **那套短语的排序必须是确定性的**（2026-09-20 修）：`freq_*` 这些 Counter 是拿
`set(phrases(b))` 喂出来的，而 set 的迭代顺序受哈希随机化影响——只按 `-count` 排序时，
并列项的顺序**每次运行都不同**，实测连跑两次 `hot_phrases` 就不一样。画像的 `queries`
由 hot 生成，漂移会顺带传到日报的检索式。现在排序带短语做次级键（`(-count, phrase)`）。

⚠️ **OpenAlex 2026 起按请求计费**（$0.001/条，匿名池每天 $0.1 ≈ 100 条），额度耗尽返回
429 + `retry-after`，重置在**次日 00:00 UTC**（= 08:00 CST）。`journal_if.py` 遇到 429 会在
第一个失败的刊处收工、把已查到的写盘并提示，不会逐条空转（旧写法每个刊要睡 200 秒）。
所以 `state/journal_if.json` 的 `missing` 里可能是「今天没查到」而不是「真的没有」——
额度重置后重跑 `journal_if.py` 即可补齐。已修的取错源问题：`Angewandte Chemie` 原先取到
OpenAlex 的**德文版**（2.2），而白名单靠前缀匹配把 `Angewandte Chemie International Edition`
（15.3）也归在这个名字下；现已写进 `journal_if.py` 的 `NAME_ALIAS` 并从缓存改正表值。

定时任务（系统级，以当前用户身份运行）：

```bash
systemctl list-timers lithub-daily.timer lithub-profile.timer   # 看下次触发时间
systemctl status lithub-daily.service                           # 看最近一次结果
journalctl -u lithub-daily.service -n 50                        # 看 systemd 日志
sudo systemctl disable --now lithub-daily.timer                 # 停掉

# 手动触发一次
sudo systemctl start lithub-daily.service
```

- `lithub-daily.timer`：每天 05:00 **无条件**触发 `daily_digest.py`，产出
  `文献日报/YYYY-MM-DD.md` 与 `.urls.txt`；`Persistent=true`，关机错过后开机补跑
- `lithub-profile.timer`：每月 1 日 04:40 跑 `build_profile.py`，先于当日日报刷新画像
- `lithub-mailcheck.timer`：每天 06:00 跑 `send_digest.py` 兜底——当日日报已生成、
  且 `state/email_sent.json` 里当日计数 < 1 时补发一次；已发过或当天没日报就直接跳过。
  它兜的是「05:00 出报成功、但发出去那一步失败」（SMTP 抖动）这种情况
- **频率闸门**（2026-09-20 起）：timer 照旧每天触发，**跑不跑由脚本读
  `config/runtime.json` 的 `schedule` 段自己决定**——`enabled: false` 直接跳过；
  `every_n_days: N`（N>1）时距上次成功出报不足 N 天也跳过。选中这条路而不是
  动态 enable/disable timer，是为了不让「timer 说跑、配置说不跑」各说各话。
  跳过时**只在日志留一行写明原因**，不写日报、不动 `recommendations.json`；
  `--dry-run` / 显式 `--date` / `--force` 三者**一律放行**（预演要看真实结果、
  回填历史与手动强制都得跑得动）。
- 上次成功出报的日期记在 `state/last_push.json`，闸门**只看它**——不猜「日报文件在不在」，
  因为没候选、翻译全失败、手工删过报都会让文件与「跑过了」对不上。
  没候选那次不写它（=「没推送就不算跑过」，`every_n_days>1` 时第二天会再试一次）
- 推送状态在 `state/recommendations.json`（`first_pushed` / `last_pushed` /
  `times_pushed` / `channel` / `score` / `status`）：`--cooldown-days`（默认 60）内同一
  DOI 不再推，超期后高分老文可再推一次；旧的 `state/digest_seen.json` 首次运行会
  迁移进来，旧文件保留不删
- `--dry-run` 不写任何 state 文件（网络缓存 `state/work/` 除外），也不更新
  `last_push.json`
- 成本：检索/摘要/OA 链接全走免费接口；只有翻译调 LLM（deepseek-v4-flash，关思考链），
  结果按 DOI 缓存，重跑零花销。按左一档模型（空闲时段 输入 1 元/M、输出 4 元/M）
  实测：旧版一次请求翻 16 篇约 1.8 分/天；分块 + 中文概述后 输入 3.75K / 输出 4.17K
  ≈ 2.0 分/天
- 翻译**分块 + 重试**：`TRANS_CHUNK`（默认 4）篇一块、每块最多 `TRANS_RETRY`（默认 3）
  次重试、每块成功即落盘。旧版一次请求翻 16 篇，2026-09-15 实测「HTTP 200 但响应体
  没有 choices」→ 整期日报静默退回英文标题；分块后爆炸半径降到 4 篇。翻译不完整时
  日报标题下会加一行 ⚠️，明细在 `logs/daily_digest.log` 的「翻译第 i/n 块」行
- 日报**版面**：标题下直接进文献（每条给中文标题、**推荐性分数**＝final 总分，
  不给 R/C/X/J'/A/F 细则；其后是来源、期刊、作者、**单位**、分类、链接、**概述**
  （90–150 字中文提要，覆盖方法核心 / 主要贡献 / 关键结果，与翻译同一个请求产出）、
  中文摘要、**库内关联**），然后是下载清单；检索口径、收录命中与概览表一律挪到最后
  「统计口径」一节
- **摘要来源是三跳**（`fill_abstract()`），前两跳落空才走第三跳：
  1. Crossref `/works/<doi>` 的 `abstract`
  2. Semantic Scholar 的 `abstract`
  3. **出版社 landing page 的 meta 标签**（`landing_absolute()`，2026-09-20 加）——
     只对 `LANDING_ABS_PREFIX` 白名单前缀尝试，目前是 `10.1038/`（Nature 系）。
     **别扩到 Elsevier**：`sciencedirect.com` 直连 403，只为已知会被挡的出版方白花请求。
     结果（含「试过但没抠到」）缓存在 `state/work/cache_landing/`，重跑零网络。
     实测 2026-09-20：近期 6 期日报 160 条里 **49 条取不到摘要（约 30%）**，且**与新旧
     无关、按期刊一刀切**——Elsevier 付费刊与 Nature 系付费刊基本取不到，Wiley / RSC /
     Nature Communications（OA）全部 0 缺失。免费聚合源帮不上忙（OpenAlex 1/12、
     Europe PMC 0/12）；白名单那一跳把 Nature 系 **10/10** 全补上了。
     剩下的 Elsevier 付费刊只能等收编后从 PDF 拿（`paper.md` 里有完整摘要）
- **单位**取自 Crossref 的 `author[].affiliation[].name`（`author` 本来就在
  `CROSSREF_SELECT` 里，**零新增网络请求**），经 `clean_affil()` 规则链清洗：去 URL / 邮箱 /
  ORCID / 纯数字片段 → 跨行连字符合并 → HTML 实体解码 → CamelCase 切开（**含数字的片段
  一律不切**，这是保住 `LiFePO4`/`NCM811` 的唯一办法）→ 18+ 字母粘连串能切就切、切不开丢片段
  → 忽略大小写与空白去重 → `; ` 连接。**没有可用单位就整行不输出**（不写 hermes 那句
  「未找到单位信息」兜底——日报每篇都跟一句是噪声）。实测 2026-09-16 那期 16 篇里 5 篇有，
  其余是出版方本身没 deposit 单位，不是提取失败
- **库内关联**（`lib_links()`）零 API 花销：有引用边就写「被库内 N 篇引用（分类分布）」
  ＋同类的引用者篇名，没有引用边就退回「与库内 N 篇标签重合」＋最相关的 3 篇。
  只转述本地事实（`citation_graph.json` 的 `co_citation` + `classification.json`），
  不推断内容关系。注意：**只有 refs 通道的 `C` 参与打分**，query 通道那篇即使被
  库内 13 篇引用也拿不到 `C`——版面会显示这个关联，但分数里没有它

### 发信：`scripts/send_digest.py`

把 `文献日报/<日期>.md` 作为**纯文本**邮件发出去（不转 HTML：邮件客户端本来也不渲染
Markdown，保持原文最忠实，也避免为一个渲染功能引入第三方库）。

**零 token 花费**——日报在 `daily_digest.py` 里已经生成好了，这里只读文件 + 说 SMTP，
全程不调 LLM。主题行用 `email.subject` 模板填 `{date}` / `{n}`。

**配置与密钥分离**：收件人 / 服务器 / 端口在 `config/runtime.json` 的 `email` 段；
**密码读 `state/smtp_password`（chmod 600）**，因为那个 json 是要进复现包给别人的，
和 `state/zotero_local_key` 同一套路。

**当日计数**：一天成功发出几次记在 `state/email_sent.json` 的 `count` 字段。两个钩子：

1. `lithub-daily.service` 的 `ExecStartPost` —— 05:00 出完报立刻发，正常路径（计数 → 1）。
   带 `-` 前缀，**发信失败不让这个 unit 变 failed**（出报成功就算成功）；
2. `lithub-mailcheck.timer` —— 06:00 兜底，当日有日报但计数 < 1 才补发。

只要计数 ≥ 1 就不再自动发，所以脚本可以随便多跑；`--force` 才会计数 +1 再发一次。

```bash
python3 scripts/send_digest.py --dry-run        # 只打印将发送的内容概要，不连服务器
python3 scripts/send_digest.py --check          # SMTP 连通性 + 登录自检，不发送
python3 scripts/send_digest.py                  # 发当天（已发过则跳过）
python3 scripts/send_digest.py --date 2026-09-19  # 补发某天
python3 scripts/send_digest.py --force          # 强制重发（计数 +1）
```

⚠️ **两处踩过的坑**：

- **XJTU 的 SMTP 主机名不能照官方文档填**。官方给的是 `smtp.stu.xjtu.edu.cn`，但它的
  证书是通配符 `*.xjtu.edu.cn`，**通配符不跨两层标签**，握手直接 `Hostname mismatch`。
  改用 **`stu.xjtu.edu.cn`**（同一台机器 202.117.1.52，证书合法、SMTP 正常）。
  别把它当笔误改回去。
- **登录密码 ≠ 客户端密码**。XJTU 邮箱的 SMTP/IMAP 只认网页端生成的**专用密码**
  （设置 → 客户端设置 → 专用密码生成），用登录密码会一律 535 认证失败——
  而且 Coremail 对「密码错」和「服务未开」返回同一个错，排查时容易走偏。
  专用密码**只在生成时可见**，丢了只能重新生成。

### 库内文献打分：`scripts/score_library.py`

给**库内**每篇算一个「推荐分」，写进 Zotero 条目的 `extra` 字段，同时也落在
`state/library_scores.json`（含四个分量，便于复核）。

**为什么不能照搬日报的公式**——日报的分是
`B = w_r·R + w_s·S + w_c·C + w_x·X + w_j·J' + w_a·A`，其中两项**对库内文献没有意义**：

| 项 | 为什么不能用 |
|---|---|
| `R` 检索排名 | 库内文献不在任何检索结果集里，没有 rank |
| `S` 库内相似度 | 「与库内文献的最大相似度」对库内文献本身恒 ≈ 1，等于白送 |

所以这里把这两项**置 0**，把剩下的 `C / X / J' / A` **按原比例重新归一化到 1**，再乘
新鲜度加成。量纲与日报接近但**不是同一个数**，别拿两边直接比大小。
四个分量都**复用 `daily_digest.py` 的函数**（`citation_term` / `classic_term` /
`journal_term` / `freshness` / `final_score`），保证与日报同源、不会两套公式漂移。

`C` 是真有信号的：实测 184 篇里 **127 篇被库内其他文献引用过**，被引最多的正是
VASP / PBE / Sun 的 Ni-rich 综述这类骨架文献。实测分数区间 **-0.069 – 0.669、中位 0.30**；
**允许负分**（J' 的零点在 IF=6，低 IF 刊 + 0 被引 + 无库内引用的新文会落到 0 以下，
实测 5/187 篇）——没有截断，因为截断会让整个底部并列在 0、丢掉区分度。

**写进哪里**：条目的 `extra` 里一行 `推荐分: 0.312`。**`Extra` 是 Zotero 的原生列**
（条目列表表头右键 → 列 → 勾上 Extra），点表头可排序。定长三位小数是为了让
**字符串排序 = 数值排序**。同键的行**原地替换**（`upsert_line` 自己实现，
与 Zotero 的 `combineExtraFields` 同语义），所以幂等、不会堆重复行。

> ⚠️ **标签做不到这件事**：标签不是列，纯文本标签根本不会出现在条目列表里
> （只有被染色或含 emoji 的标签会以小块出现在标题格）。真正的「自定义列」需要装插件
> ——Zotero 只给插件开了 `ItemTreeManager.registerColumn`，本地 HTTP API 没有这个能力。

**两个更新钩子**（幂等，重复跑无副作用）：

1. `lithub-daily.service` 的**第二个 `ExecStartPost`** —— 每天出完报顺手刷新一次
   （带 `-`，失败不影响出报）；
2. **收编新文献后手动跑一次** —— `python3 scripts/score_library.py --apply`。

零 token 花费（全程不调 LLM）。冷缓存时约 5 分钟（184 次 Unpaywall + 2 次 Crossref
批量）；缓存热了整轮 **7 秒**。

```bash
python3 scripts/score_library.py             # 只报告（默认，不写任何东西）
python3 scripts/score_library.py --apply     # 写 state/library_scores.json + Zotero 的 Extra
python3 scripts/score_library.py --key XXXXXXXX   # 看某一条的分量
```

⚠️ **别忘 `LITHUB_MAILTO`**：Unpaywall 会**硬拒**占位邮箱（实测 `you@example.com`
→ HTTP 422「Please use your own email address」），而且 `cached_json` 要先重试退避
**~12 秒**才失败，等于每篇白等一轮、`A` 项永远拿不到值。手动跑时先
`export LITHUB_MAILTO=...`；systemd 不读 `.bashrc`，所以两个 unit 里都写死了这个变量。

## 环境注意事项

### Unpaywall 会硬拒占位邮箱（重要）

`LITHUB_MAILTO` 不设时默认是 `you@example.com`，**Unpaywall 直接 422 拒绝**：

```
HTTP 422  {"message": "Please use your own email address in API calls."}
```

后果比报错更隐蔽：`cached_json` 会先按退避策略**重试 3 次（3s + 6s）才放弃**，
所以 `oa_info()` 每篇要多等 **~12 秒**，而 `A`（是否开放获取）**永远拿到 0**——
实测换成真邮箱后同一请求 1 秒返回、且 `is_oa` 正确。

- 手动跑：`export LITHUB_MAILTO=你的邮箱`
- systemd **不读 `.bashrc`**，两个 unit 的 `Environment=` 里都写死了这个变量；
  漏写就会静默退化（不报错，只是慢且 A 恒为 0）
- Crossref / OpenAlex 对占位邮箱不硬拒，所以只有走 Unpaywall 的功能会中招

### IPv6 陷阱（重要）

本机曾出现「有全局 IPv6 地址与默认路由，但实际 100% 丢包」的情况。
**Python 没有 curl 那样的 happy-eyeballs 回退**，会死等 IPv6 超时（实测同一请求
181 秒 vs 强制 IPv4 的 0.72 秒）。

写网络代码时**显式强制 IPv4**：

```python
import socket
_orig = socket.getaddrinfo
def _v4(host, port, family=0, *a, **k):
    return [r for r in _orig(host, port, family, *a, **k) if r[0] == socket.AF_INET]
socket.getaddrinfo = _v4
```

根治办法（需 sudo）：`echo 'precedence ::ffff:0:0/96  100' | sudo tee -a /etc/gai.conf`

### 国内网络

- pip 已配阿里云镜像（`~/.config/pip/pip.conf`）
- HuggingFace 不可达，需 `export HF_ENDPOINT=https://hf-mirror.com`
- MinerU 的 `flash-extract` 有 **10 MB 且 20 页**双限制，且**文件大小先于页数触发**，
  `--pages` 绕不过 10 MB。超过 8 MB 的 PDF 先用 ghostscript 预压缩：
  `gs -dPDFSETTINGS=/ebook`
  → 这些限制**只针对 `--flash` 兜底模式**；默认的 `extract` 是 200 MB / 600 页，
  不用压缩也不用分块（见上「两个转换引擎与图表」）。
- MinerU 限流（官方）：提交任务 50 文件/分钟、每日 5000 文件（html ≤100）；
  取结果 1000 次/分钟。目前无收费计划。

## 与 Obsidian 的关系

`~/LitHub` 已注册为 Obsidian 库，`.md` 的默认打开程序是 Obsidian
（`~/.local/bin/obsidian-open` 包装脚本把文件路径转成 `obsidian://` URI）。

⚠️ **该脚本必须用 `xdg-open` 发 URI**：直接执行 `/snap/bin/obsidian "obsidian://..."`
时 snap 包装器不转发 URI，Obsidian 会静默忽略。

## 同类开源项目与可借鉴点

2026-09-16 调研，star 为当日 GitHub 实查值（WebSearch 配额耗尽，走 GitHub API 与
raw 源码核对，仓库名均已验在）。**结论：没有覆盖全链路的对等项目。**
「分类三轴写回 Zotero」「共被引图 + OpenAlex 代理 IF」「冷却期去重」「幂等 + 断点续跑」
这几项在开源侧基本空白，是本项目的差异化部分，改造时别误删。

### 最接近的参考项目

| 仓库 | star | 对上的环节 | 值得看什么 | 明显缺什么 |
|---|---:|---|---|---|
| TideDra/zotero-arxiv-daily | 5,951 | 以 Zotero 库做每日推荐 | 库内摘要 embedding 加权打分 | 无转换/分类/图谱；PDF 只用 pymupdf4llm |
| windingwind/zotero-better-notes | 8,214 | 模板化笔记 | 模板外置 + 生成段原位替换 + 双 md5 判定 | 无 LLM、无批处理、无 headless |
| yilewang/llm-for-zotero | 3,015 | MinerU 解析缓存 / Obsidian 落盘 / 打标签 | 把 MinerU 结果缓存进插件层 | Agent 驱动，非可续跑批处理 |
| Marverlises/Paper-Agent-Zotero | 148 | 库作画像 + 中文摘要落本地 MD | 时间衰减加权公式（见下） | 无去重、无冷却、无推送 |
| quas-modo/zotero-arxiv-daily-notion | 5 | 每日推荐 | 85/15 语义+关键词混合打分、三键去重 | 单人维护，无状态文件 |
| genggng/hermes-arxiv-agent | 120 | 中文摘要 + IM 推送 | 90–150 字摘要 prompt、机构单位提取规则链 | 零打分零个性化，永久去重无冷却 |

### 明确不借鉴的路线（原因已核实）

- **Better Notes 的模板引擎**：`src/modules/template/api.ts` 把模板当 JS 模板字符串
  `new AsyncFunction(args, "return \`" + text + "\`")`，在 Zotero 特权环境里 eval；
  模板存 prefs（`extensions.zotero.Knowledge4Zotero.template.*`）而非文件，导入导出靠
  剪贴板和手动 YAML 备份。做模板外置用**纯文本 + `{meta}` 占位**即可，不引入 eval、不存配置。
- **Better Notes 的「总结」**：全仓无 LLM 依赖，内容 = 元数据 + 已有批注拼装（不读 PDF 全文）。
  它不生成总结，社区抱怨集中在模板上手难度（`topItem is not defined`），不是总结质量。
- **Zotero 插件整体路线**：GUI 绑定、无 CLI / headless、不能挂 systemd timer，
  与本项目「幂等 + 可续跑 + 定时任务」的架构冲突。
- **旧路径勿引用**：`mgmeyers/obsidian-zotero-integration` 已 301 迁到
  `community-archive/obsidian-zotero-integration`；`MuiseDestiny/obsidian-zotero-integration`
  是 404。paperswithcode 站点已跳转 HuggingFace，`karpathy/arxiv-sanity-lite` 停更 4 年。

### 三个可借鉴点（2026-09-16 起带实施状态）

1. **模板外置** —— ✅ 已实施（2026-09-16）：提示词抽到 `prompts/summarize.md`，占位符为
   `{meta}` 与 `{figure_manifest}`；`summarize_batch.py` 运行时读它，读不到则回退内置常量，
   `--template PATH` 可换模板。同时新增 `--dry-run`（只打印最终提示词，零成本、不写文件）与
   `--max-figures`（默认 5）。
2. **R 项的语义化** —— ⚠️ 部分实施：`R` 已按本文件「R 为什么与分类解耦」去掉分类权重；
   `S` 项（库内语义相似度，权重 0.15）已接入 `daily_digest.py` 且带降级路径，但**尚未启用**——
   要先装 CPU 版 torch + `sentence-transformers`，再跑 `python3 scripts/build_embeddings.py`
   生成 `state/work/lib_emb.npz`。启用前必须重标定 `--min-score`（见该节末尾的警告）。
   本条原定的融合公式（`0.7·max + 0.3·加权平均`、时间衰减 `w_j = exp(u_j − max(u))`）**没有采用**：
   当前只取库内最大余弦（max），更简单也更好解释；加权平均与时间衰减留待 max 被证明不够用时再加。
3. **日报摘要 prompt** —— ✅ 实施了一半（2026-09-16）：`zh_summary` 已改成 90–150 字提要
   （覆盖方法核心 / 主要贡献 / 关键结果，禁分点，带自检重写），缓存换名
   `state/work/cache_translate_v2.json`（旧缓存不复用，一次性重译约 0.15 元）。
   **机构单位提取规则链还没做**（hermes 那套：PDF 前两页 → 上标脚注映射 → 机构名匹配 →
   CamelCase 还原 → 跨行连字符合并 → 去 URL/公式/参考文献噪声 → `;` 分隔 →
   兜底「未找到单位信息」）。

## 风格约定

- 总结和综述用中文，数值必须具体，原文没给的写「未报道」，不推测
- 脚本一律**幂等 + 可续跑**（已完成的跳过），输出到 `logs/`
- 对 Zotero 的批量写入以**纯新增**为默认；删除只有两条路：条目/目录/重复项走
  `scripts/tidy.py`，**标签改名与游离标签清理**走 `scripts/migrate_labels.py`。
  两者都先导出全量备份到 `state/` 再动手；`DELETE` 是硬删除、没有回收站兜底
- 每次整理完都要清理**空条目与重复条目**（外加孤儿 `papers/` 目录、空分类目录），
  即 `python3 scripts/tidy.py --apply`
- `summary.md` 的固定七节是：一句话结论 / 研究问题 / 方法与技术路线 / 关键发现 / 机理解释 /
  局限与未解决的问题 / 与研究主题的关联。
  七节之上还有一段 YAML 属性区，**由 `sync_properties.py` 从 Zotero 写**，模型不碰
  （见「summary.md 的属性区」一节）。正文的格式要求（2026-09-20 改版）：
  - 一句话结论 1–3 句，按需伸缩，不为凑句数注水
  - 研究问题 分成 2–3 段、篇幅约为旧版的 1–1.75 倍
  - **关键发现 写成 Markdown 表格**，表头固定 `| 发现 | 证据 |`，证据列给具体数值 + 条件，
    能指到原文的图就写图号（图 3 / 图 3a），不要重排图号
  - 机理解释 扩到 1–1.5 倍并分点/分段；局限与未解决的问题 分点列
  - 「与研究主题的关联」分两层：对领域的意义 + **对库内文献的呼应**。库内清单由
    `summarize_batch.py` 的 `library_context()` 按标签重合度选（3×子类 + 2×材料 + 1×形态
    + 1×同分类，≥3 入选，综述最多 2 篇），数据取自 `classification.json` 的 `note` 或
    `digest.json` 的「一句话结论」，零 API 花销；模型只能引用清单里给出的信息
  - 旧版的「关键术语中英对照」已删除；`LEGACY_SECTIONS` 让重跑时把这一节丢掉，
    而不是当成人工章节插回来
  同一主题成组入库时，在「与研究主题的关联」前手工插一节
  `## 库内同主题工作：<主题>对照`，逐篇给出库内 key 与工艺差异、并点明各篇的分工与遗留张力。
  已有实例：PAN 系包覆三篇 `ABCD1234` / `EFGH5678` / `IJKL9012`。该节由人工撰写。
  **重跑保护**（2026-09-16 起）：`summarize_batch.py` 的 `preserve_manual()` 按**章节**认——
  七节之外的 `## ` 章节（含「库内同主题工作」与 `embed_figures.py` 写的「## 关键图表」）
  都算人工内容，重跑时原样插回「与研究主题的关联」之前，`--force` 也不会再冲掉它，
  不再依赖「文件 > 600 字节就跳过」那条脆弱保护。它按 `## ` 级标题切分，
  所以人工补充内容要用 `## `；`### ` 子标题会留在所属章节内部（「关键图表」下的 `### 图 N` 即如此）。
