#!/usr/bin/env python3
"""对每篇 paper.md 生成中文总结 summary.md。

用 kimi -p 逐篇跑：每篇独立上下文，不污染主会话，可并行、可续跑。

属性区（title/author/year/journal/cite/doi）不在提示词里，由 `sync_properties.py`
从 Zotero 元数据写入——MinerU 常解析不出 DOI/刊名，模型只能猜；Zotero 建条目时就有。
提示词里只给一段 Zotero 元数据供正文引用，模型只写正文。

提示词正文放在 `prompts/summarize.md`（改措辞不必动代码，`--template` 可换别的）；
模板读不到就退回下面的内置常量——缺一个文件不该让流水线停。
`--dry-run` 只把拼好的提示词与图片清单打出来，不跑模型也不写任何文件。
"""
import json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_properties as sp

ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")
MANIFEST = os.path.join(ROOT, "state", "manifest.json")
TEMPLATE = os.path.join(ROOT, "prompts", "summarize.md")
KIMI = os.environ.get("LITHUB_KIMI") or os.path.expanduser("~/.kimi-code/bin/kimi")

FIGURES = "--figures" in sys.argv
FORCE = "--force" in sys.argv
DRY = "--dry-run" in sys.argv

# 清单本身也占上下文：几百张图全列出来跟让模型自己读目录一样贵，列到 60 条够挑了
MANIFEST_LIMIT = 60
# 图注只在引用下方这几行非空文本内找；再远就是正文句子，容易张冠李戴
CAPTION_SCAN = 10
# 正文段落（非图注）长这样：拿它当图注说明已经找错了地方
CAPTION_MAXLEN = 200
# 带值的开关，支持 `--opt v` 与 `--opt=v`
VALUE_FLAGS = ("--template", "--max-figures", "--max-steps", "--agent-file")

PROMPT = """{task}

论文元数据（脚本从 Zotero 取出，权威；正文需要写刊名/年份/卷期页/DOI 时以此为准，
不要写「原文未给出」）：
{meta}

库内相关文献（同一个库里、与本文标签重合度最高的几篇，供「与研究主题的关联」一节呼应。
**只能引用这里给出的信息**，不要杜撰它们的结论或数据；这里没列到的库内文献就当不存在）：
{library}

硬性要求：
1. 全文中文，学术风格，不写空话套话，不写"本文提出了一种..."式的填充句。
2. 所有数值必须具体（电压 V、容量 mAh/g、能量密度 Wh/kg、温度、尺度 nm/μm、循环圈数等），
   原文给了数字就写数字，没给就写"原文未给出"。
3. 不确定或原文没说的内容，明确标注"原文未明确"，不要推测。
4. **总长控制在 1000–1500 字**（含表格）。宁可短：话说清了就停，不要靠背景铺垫、形容词、复述论文
   结构来凑长度。各节的字数上限在下面逐节写明，超了就删，不要「多写一点更保险」。
5. 原文里可能有乱码（`�`、控制字符、`【?】` 占位符、断字或错拼）。**不要把乱码原样带进总结**：
   能据上下文判断就写出正确内容，判断不了就略去该片段、或写成「（原文此处乱码）」。
   引用公式、单位、希腊字母时尤其注意，宁可少引一句，也不要抄一串替换字符。
6. {draft_rule}

summary.md **只写正文**：第一行就是「## 一句话结论」，不要写 YAML 属性区、不要写
`---` 分隔线、不要在开头重复论文标题。文件的属性区（title/author/year/journal/cite/doi）
由 sync_properties.py 从 Zotero 写入，你写了也会被覆盖，不要浪费时间猜 DOI 或刊名。

正文结构固定如下（标题一字不改、顺序不变）：

## 一句话结论
<1–2 句话（不超过 90 字）。把条件与代价一起说清；本来就一句话能说清的就保持一句，
 不要为凑句数注水。>

## 研究问题
<要解决什么问题、前人卡在哪。**分 2 段、共 130–230 字**：第一段交代核心矛盾（只写与本文直接相关的
背景，不要从电池发展史或正极材料综述讲起），第二段点出既有工作的具体缺口、落到本文要做什么。>

## 方法与技术路线
<用了什么手段（实验/DFT/AIMD/表征等），关键参数设置。**100–170 字**，只写影响结论可信度的设置，
不要罗列仪器型号。>

## 关键发现
<**必须写成 Markdown 表格**，只有两列，表头固定为 `| 发现 | 证据 |`：>

| 发现 | 证据 |
|---|---|
| <**35–70 字、2–3 句**：这条发现是什么、对哪个对象、在什么条件下成立，把限定条件一并写进这一列，不要只写一句短语> | <支撑它的具体数据：数值 + 单位 + 条件（温度/倍率/电压区间/循环圈数等）。原文有对应图就写上图号，如「图 3」「图 3a」；没有图就只写数据> |

<表格 **4–6 行**（挑最重要的，不要把所有发现都塞进去）。两列字数比控制在 **1:1.5 ~ 1:3**——
   发现列别写成短语（渲染出来那一列会窄得没法看），证据列也别塞成一大段。图号用原文 Figure 的编号，
   不要自己重排、也不要写 Table 号。数值照抄原文，不要四舍五入成「约 200 mAh/g」这种。>

## 机理解释
<作者给出的物理/化学机制。**分点写，共 130–230 字**——比旧版略扩一点即可，不要展开成机理综述：
   因果链按环节逐条列出，写清先后顺序与依赖关系。>

## 局限与未解决的问题
<**分点列出**（无序列表），**共 80–140 字**。作者自己承认的在前，你看出来的在后；每条一句话说清，
   不要把一条能讲完的事拆成两条，也不要重复「关键发现」里已写过的内容。>

## 与研究主题的关联
<两层意思，缺一不可，**共 130–230 字**：
   ① 这篇工作对本研究领域意味着什么（结论能不能推广、改变了哪条既有认识、留下什么待验证的）；
   ② 与上面「库内相关文献」的呼应——是印证、补充还是冲突，用 `[KEY]` 指代，如 `[ABCD1234]`。
   上面清单为空、或确实无可对照的，就明写「库内暂无直接对照」，不要硬扯。>

{source}
---
{deliver}"""

FIGURE_ADDENDUM = """图片要求（本次特别要求，优先级高）：
- 图片清单见下方 {figure_manifest}。paper.md 里形如 ![](images/xxx.jpg) 的引用就是这些图，
  紧跟在引用下方的那行 "Figure N." / "Fig. N" 文字就是该图的图注。
- 先只看清单里的**路径与图注**做筛选，再用你的图片读取能力实际查看你挑中的那几张，
  从图像内容确认它们确实承载核心证据（机理示意图、关键表征、决定性曲线）。
- **不要打开、不要罗列、不要遍历 images/ 目录里的其它文件**：只读你挑中的这几张，
  且不超过清单开头给出的张数上限（没有一份完整清单是特意为之，目录里的每多读一张都是浪费）。
- 在「关键发现」表格的证据列里写出图号（如 `图 3`、`图 3a`）；如需同时给出图片路径，
  写成 `图 3（images/xxx.jpg）`。在「机理解释」里也照样指图，不要另外重排图号。
- 若你**无法读取图片**，不要在正文里编造图中内容；改为在文件最末尾追加一行：
  `> 注：本次总结未能读取图片（images/ 下的图片文件无法查看）。`"""

# 标准七节：七节之外的 ## 章节视为人工内容（见 preserve_manual）
SECTIONS = ("一句话结论", "研究问题", "方法与技术路线", "关键发现", "机理解释",
            "局限与未解决的问题", "与研究主题的关联")
# 老格式的章节名：仍要认得出来，好让重跑时把这一节丢掉/换掉，而不是当成人工章节又插回来
LEGACY_SECTIONS = ("关键术语中英对照",
                   "与锂离子电池正极材料研究的关联")   # 2026-09-20 改名为「与研究主题的关联」
MANUAL_ANCHOR = "与研究主题的关联"

FM_MARK_RE = re.compile(r"<!--\s*FIGURES-BEGIN\s*-->(.*?)<!--\s*FIGURES-END\s*-->", re.S)
COMMENT_RE = re.compile(r"<!--.*?-->\s*", re.S)
HEAD_RE = re.compile(r"^##\s+(.*?)\s*$", re.M)
IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*(images/[^)\s]+)")
IMG_ONLY_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)\s*$")
CAP_RE = re.compile(r"^(?:Figure|Fig\.?|Table|Tab\.?)\s*\d+\b|^[图表]\s*\d+", re.I)


def split_argv(argv):
    """拆出位置参数（并发数、key 列表）与带值开关。

    照原来「过滤掉 -- 开头的 token」写法，`--max-figures 5` 里的 5 会掉进位置参数、
    被当成并发数，所以带值开关要连着它的值一起摘掉。
    """
    pos, opts, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a in VALUE_FLAGS:
            opts[a[2:]] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
            continue
        if a.startswith("--") and "=" in a and "--" + a[2:].split("=", 1)[0] in VALUE_FLAGS:
            k, v = a[2:].split("=", 1)
            opts[k] = v
            i += 1
            continue
        if not a.startswith("--"):
            pos.append(a)
        i += 1
    return pos, opts


ARGS, OPTS = split_argv(sys.argv[1:])


def int_opt(name, default):
    """整数开关；值非法（比如漏写）时退回默认值，不因为一个参数把整批任务打掉。"""
    try:
        return int(OPTS.get(name) or default)
    except ValueError:
        log(f"⚠ {name} 的值 {OPTS.get(name)!r} 不是整数，按 {default} 处理")
        return default


def paper_dir(rec):
    safe = re.sub(r"[^\w\- ]", "", rec["title"])[:60].strip().replace(" ", "_")
    return os.path.join(PAPERS, f"{rec['key']}_{safe}")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


MAX_FIGURES = int_opt("max-figures", 5)
# 单篇允许的最大 agent 步数（0 = 不限）。传给 kimi 的 KIMI_LOOP_MAX_STEPS_PER_TURN，
# 撞上限时模型可能还没落笔，所以 summarize() 会核对 summary.md 是否真被改过。
# 默认 5：实测这一步数够「一次读完 + 一次写完」，而它限制的正是「反复读改」那部分开销——
# 每多一步都要把整篇 paper.md 重发一遍（实测同篇 38 步 4.08M token / 5 步 0.21M）。
MAX_STEPS = int_opt("max-steps", 5)
# 受限 agent（只给 Read/Write）：默认会话会把内置工具 + MCP 的全部工具 schema 塞进每次调用，
# 实测首调未缓存输入 35,441 token，换成 prompts/summarizer.agent.md 后降到 2,429 token。
# 传 "--agent-file=" 关闭；用法见 AGENTS.md「写一篇总结要花多少 token」。
# 判断「给没给这个开关」不能用 or：显式给空串就是「关闭」，or 会把它回落到默认 agent，开关静默失效。
AGENT_FILE = OPTS["agent-file"] if "agent-file" in OPTS else os.path.join(
    ROOT, "prompts", "summarizer.agent.md")
if AGENT_FILE and not os.path.exists(AGENT_FILE):
    log(f"⚠ --agent-file {AGENT_FILE} 不存在，本次按默认工具集跑（会多花约 35k token/篇）")
    AGENT_FILE = None
# oneshot：把裁剪后的全文直接塞进提示词、模型一次回复即正文（不给任何工具）。
# 比工具模式再省一截：省掉 Read 的 schema（1.7k）、第 1 次调用（4.5k）与收尾那次纯开销调用
# （26k 上下文重发）。这条现在是默认；`--no-oneshot` 退回「模型自己读 paper.md」的工具模式。
ONESHOT = "--no-oneshot" not in sys.argv
ONESHOT_AGENT = os.path.join(ROOT, "prompts", "oneshot.agent.md")
ONESHOT_READ_AGENT = os.path.join(ROOT, "prompts", "oneshot-read.agent.md")
if ONESHOT:
    for _p in (ONESHOT_AGENT, ONESHOT_READ_AGENT):
        if not os.path.exists(_p):
            log(f"⚠ {_p} 不存在，本次退回工具模式")
            ONESHOT = False
            break


# ────────────────────────────────────────────── 提示词模板

# 模板必须留住的占位符：缺 {task} 就不知道要写总结、缺 {meta} 没有元数据、
# 缺 {source} 则 oneshot 模式下**模型拿不到论文全文却照样产出**（正文全靠猜，还不报错）。
# 三种都是静默失败，所以缺了宁可退回内置模板；其余占位符只提醒，模板本身还能用。
REQUIRED_PH = ("task", "meta", "source")
OPTIONAL_PH = ("library", "draft_rule", "deliver")


def load_template(path):
    """读提示词模板，返回 (正文, 图片段)。

    图片段由 <!-- FIGURES-BEGIN --> / <!-- FIGURES-END --> 标出：不带 --figures 时整段不注入。
    HTML 注释一律剥掉，否则注释里写占位符原样字面量会把元数据块注入第二遍。
    """
    try:
        raw = open(path, encoding="utf-8").read()
    except OSError as e:
        log(f"⚠ 提示词模板 {path} 读不到（{e.strerror}），改用内置提示词")
        return PROMPT, FIGURE_ADDENDUM
    m = FM_MARK_RE.search(raw)
    figs = COMMENT_RE.sub("", m.group(1)).strip() if m else ""
    body = COMMENT_RE.sub("", raw[:m.start()] + raw[m.end():] if m else raw).strip()
    miss = [p for p in REQUIRED_PH if "{" + p + "}" not in body]
    if miss:
        log(f"⚠ 提示词模板 {path} 缺占位符 {'、'.join('{%s}' % p for p in miss)}，改用内置提示词")
        return PROMPT, FIGURE_ADDENDUM
    soft = [p for p in OPTIONAL_PH if "{" + p + "}" not in body]
    if soft:
        log(f"⚠ 提示词模板 {path} 缺占位符 {'、'.join('{%s}' % p for p in soft)}"
            f"（不致命，照用；缺 {{library}} 会少一段库内对照）")
    if not figs and "{figure_manifest}" in body:
        log(f"⚠ 提示词模板 {path} 的 {{figure_manifest}} 不在 FIGURES-BEGIN/END 之内，"
            f"不带 --figures 时该段也会一起交给模型")
    return body, figs


# ────────────────────────────────────────────── 图片清单

def caption_after(lines, i):
    """取第 i 行图片引用下方的图注，前 120 字；找不到返回空串。

    一组子图（a)(b)(c)…）是连着几条引用、图注只挂在这组最后一张下，所以不能只看紧邻
    那一行：跳过空行与同组引用（还有 "f" 这类子图字母），往前找图注行。
    先判图注再判长度——真图注常常好几百字，先按长度砍就会把图注误判成正文段落。
    """
    seen = 0
    for s in (ln.strip() for ln in lines[i + 1:]):
        if not s or len(s) <= 3 or IMG_ONLY_RE.match(s):
            continue
        if CAP_RE.match(s):
            return " ".join(s.split())[:120]
        seen += 1
        if seen > CAPTION_SCAN or s.startswith("#") or len(s) > CAPTION_MAXLEN:
            return ""
    return ""


def figure_manifest(d, max_read):
    """从 paper.md 预筛图片清单（路径 + 图注前 120 字），按出现顺序。

    为什么要预筛：images/ 少则二十几张多则两百多张，让模型自己遍历目录去挑是这条流水线
    最贵的一项开销——脚本先把「路径 + 图注」摆出来，模型只动嘴挑、只读挑中的那几张。
    """
    path = os.path.join(d, "paper.md")
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
    except OSError:
        return "图片清单：（读不到 paper.md，本次不要读图）"
    out, seen = [], set()
    for i, ln in enumerate(lines):
        m = IMG_RE.search(ln)
        if m and m.group(1) not in seen:  # 同一张图被引用两次只列一条
            seen.add(m.group(1))
            out.append((m.group(1), caption_after(lines, i)))
    if not out:
        return "图片清单：paper.md 里没有 ![](images/...) 引用（共 0 张），本次不要读图。"
    head = (f"图片清单：共 {len(out)} 张"
            + (f"，仅列前 {MANIFEST_LIMIT}" if len(out) > MANIFEST_LIMIT else "")
            + f"（按 paper.md 中的出现顺序），本次最多只读其中 {max_read} 张。")
    rows = [f"- {p} ｜ {c}" if c else f"- {p} ｜ （未解析到图注）"
            for p, c in out[:MANIFEST_LIMIT]]
    return "\n".join([head] + rows)


_LIB = None


def library_index():
    """库内每篇的 {key: {title, primary, sub, materials, form, blurb}}，只读本地文件、零 API。

    blurb 优先取 classification.json 的 note（分类时写的一句话备注），没有就退回 digest.json
    的「一句话结论」——两者都是现成的中文提要，不必再调模型。
    """
    global _LIB
    if _LIB is not None:
        return _LIB
    cls = json.load(open(os.path.join(ROOT, "state", "classification.json"), encoding="utf-8"))
    try:
        digest = {d["key"]: d for d in json.load(
            open(os.path.join(ROOT, "state", "digest.json"), encoding="utf-8"))}
    except (OSError, ValueError):
        digest = {}
    out = {}
    for c in cls:
        blurb = (c.get("note") or "").strip() or \
            (digest.get(c["key"], {}).get("concl") or "").strip()
        out[c["key"]] = {"title": c.get("title") or "", "primary": c.get("primary") or "",
                         "sub": c.get("sub") or [], "materials": c.get("materials") or [],
                         "form": c.get("form") or [], "blurb": blurb,
                         "is_review": bool(c.get("is_review"))}
    _LIB = out
    return out


# 综述标签多、几乎必然挤满前几名，但对「印证/补充/冲突」的价值不如同主题的研究论文，
# 所以给综述留上限，保证清单里至少有几篇可对照的一手工作
REVIEW_MAX = 2


def library_context(key, limit=5):
    """挑库内与本文标签重合度最高的几篇，拼成提示词里的一段清单。

    打分 = 3×共用子类 + 2×共用材料 + 1×共用形态 + 1（同一级分类），≥3 才入选。
    阈值取 3：等于「至少共用 1 个子类」或「同一级分类 + 至少 1 个共用材料」——
    只靠「同一个一级分类」凑数的不算，否则同分类的几十篇会全被列进来。
    """
    lib = library_index()
    me = lib.get(key)
    if not me:
        return "（本文还没进 classification.json，本次无库内对照）"
    subs, mats, forms = set(me["sub"]), set(me["materials"]), set(me["form"])
    scored = []
    for k, v in lib.items():
        if k == key:
            continue
        s = (3 * len(subs & set(v["sub"])) + 2 * len(mats & set(v["materials"]))
             + len(forms & set(v["form"])) + (1 if v["primary"] == me["primary"] else 0))
        if s >= 3:
            scored.append((s, k, v))
    if not scored:
        return "（库内暂无标签重合的文献，本节只写对领域的意义）"
    scored.sort(key=lambda x: (-x[0], x[1]))
    review = [x for x in scored if x[2]["is_review"]][:REVIEW_MAX]
    picked = review + [x for x in scored if not x[2]["is_review"]][:limit - len(review)]
    picked.sort(key=lambda x: (-x[0], x[1]))
    lines = []
    for _s, k, v in picked:
        blurb = v["blurb"]
        if len(blurb) > 150:
            blurb = blurb[:150] + "…"
        lines.append(f"- [{k}] {v['title'][:110]}（{v['primary']}"
                     + ("·综述" if v["is_review"] else "") + "）"
                     + (f"：{blurb}" if blurb else ""))
    return "\n".join(lines)


# ────────────────────────────────────────────── 论文裁剪（oneshot 模式用）

REF_HEAD_RE = re.compile(
    r"^#{1,4}[ \t]*(references|bibliography|reference list|参考文献)[ \t]*$", re.M | re.I)
# 编号开头的引文行：(383) … ／ 383. … ／ [383] …／ 12 …（纯数字编号后面接作者名）
REF_ITEM_RE = re.compile(r"^(?:\(\d{1,4}\)|\d{1,4}[.)]|\[\d{1,4}\]|\d{1,4})\s+\S")
REF_MIN_RUN = 15          # 连续这么多行才认定是参考文献区，防正文里的小编号列表误判
IMG_LINE_RE = re.compile(r"^!\[\]\(images/[^)]*\)[ \t]*$", re.M)
FM_HEAD_RE = re.compile(r"^---\n.*?\n---\n", re.S)


def find_refs_start(text):
    """找参考文献区的起始字符偏移；找不到返回 None。

    先认标题（`## References` 之类，实测只覆盖 59% 的篇目——MinerU 常把标题丢了）；
    认不到就从**末尾往回扫连续 ≥15 行的编号引文行**，取这段的起点，且必须落在文件后半段。
    """
    m = REF_HEAD_RE.search(text)
    if m:
        return m.start()
    lines = text.split("\n")
    n = len(lines)
    is_ref = [bool(REF_ITEM_RE.match(l.strip())) for l in lines]
    is_blank = [not l.strip() for l in lines]
    offs, pos = [], 0
    for l in lines:
        offs.append(pos)
        pos += len(l) + 1
    # 从末尾往前累计：取**最靠前**的、其后「非空行里 ≥60% 是编号引文行、且至少 15 行」的位置。
    # 不能一遇到断链就返回——参考文献区里常有几行不带编号的续行，那样只能切到尾巴一小段
    # （实测有篇因此只切掉末尾 2%）。
    cnt = rc = 0
    best = None
    for i in range(n - 1, -1, -1):
        if is_blank[i]:
            continue
        cnt += 1
        if is_ref[i]:
            rc += 1
        if cnt >= REF_MIN_RUN and rc / cnt >= 0.6 and offs[i] >= 0.35 * len(text):
            best = i
    return offs[best] if best is not None else None


def prune_paper(text):
    """把 paper.md 裁成「只为总结用」的版本，返回 (文本, 说明)。

    裁掉对总结没用、却占 token 的三块：front-matter（元数据由 Zotero 给）、图片路径行
    （总结只写图号）、参考文献段。参考文献最容易切错，所以加护栏：**切完必须至少留原文
    35%**，不满足就原样不动（实测有篇参考文献占 88%，那种切了正好）。
    """
    n0 = len(text)
    notes = []
    m = FM_HEAD_RE.match(text)
    if m:
        text = text[m.end():]
        notes.append("front-matter")
    text, k = IMG_LINE_RE.subn("", text)
    if k:
        notes.append(f"图片路径×{k}")
    pos = find_refs_start(text)
    if pos is not None and len(text) - pos >= 0.05 * len(text) and pos >= 0.35 * len(text):
        cut = len(text) - pos
        text = text[:pos]
        notes.append(f"参考文献×{cut / n0 * 100:.0f}%")
    return text, "、".join(notes) if notes else "无"


TASK_TOOL = ("阅读当前目录下的 paper.md（一篇学术论文的全文，Markdown 格式），"
             "写一份中文总结并存为 summary.md。")
TASK_ONESHOT = ("下面给出的是一篇学术论文的全文（Markdown；为省 token 已去掉参考文献段与图片路径，"
                "图注仍在）。请直接写出这篇论文的中文总结。")
TASK_FILE = ("工作目录下的 `paper.summarize.md` 是一篇学术论文的全文（为省 token 已去掉参考文献段与"
             "图片路径，图注仍在）。用一次 `Read`（带上 max_chars: 500000）读完它，然后直接写出这篇"
             "论文的中文总结。")
DELIVER_TOOL = "只写 summary.md 这一个文件，不要输出其他解释性文字，不要修改 paper.md。"
DELIVER_ONESHOT = ("只输出总结正文本身：从 `## 一句话结论` 开始，到最后一节结束。"
                   "不要前言、后记、解释，也不要代码围栏。")
DRAFT_TOOL = ("**一回读完、一次成稿**（这条直接决定成本）：\n"
              "   - 读 paper.md 用**一次** Read 调用，显式带上 `max_chars: 500000`，**不要分次读**"
              "（按行号分页读会把同一篇论文反复塞进上下文）；\n"
              "   - 然后用**一次**写文件的调用把整篇 summary.md 写完——不要先写草稿再回头补、"
              "不要分段追加、不要为了润色再读改一遍；\n"
              "   - 动笔前在心里把七节都想好，然后一口气写完。")
DRAFT_ONESHOT = "**一次成稿**：全文一次写完，不要先给提纲、不要分几次输出、不要回头补充。"

# 裁剪后的全文塞进命令行参数的上限，按平台取：
# Windows 的 CreateProcess 把**整个命令行**限制在 32767 字符，远低于 Linux 单个参数的
# 128 KB（MAX_ARG_STRLEN）。oneshot 的 inline 载体把裁剪后的全文塞进 -p 参数，超了直接
# WinError 206——所以 Windows 上留出其余参数（--agent-file 等）的余量取 30 KB。
# 超限时不做别的，直接降级到 file 载体（写 paper.summarize.md 让模型读一次）。
ARGV_LIMIT = 30_000 if os.name == "nt" else 110_000
TMP_NAME = "paper.summarize.md"


def build_prompt(key, d, item, ab, tpl, mode=None):
    """拼一篇的提示词，返回 (提示词, 裁剪说明, 载体, 临时文件路径)。

    载体（mode）：
      inline  裁剪后的全文直接注入提示词，一次调用拿到正文——能把 argv 塞下的走这条；
      file    塞不下（超过 ARGV_LIMIT，Linux 110 KB / Windows 30 KB）就写成本目录的
             `paper.summarize.md`，让模型读一次再回答；
      tool    兜底：模型自己读 paper.md、自己写 summary.md（`--no-oneshot` 强制这条）。
    """
    body, figs = tpl
    if mode is None:
        mode = "inline" if ONESHOT else "tool"
    # --figures 注入的是**图注文本清单**，不是图像：本环境没有视觉能力，跑 kimi -p 的模型
    # 读不到 images/ 里的图，这份清单的作用只在于把「图号 ↔ 图注 ↔ 有没有图片文件」摆给它，
    # 好让它引对图号。原先只在 tool 载体注入，默认（oneshot）路径下这个开关等于空操作。
    figs_note = ("\n\n" + figs.replace("{figure_manifest}", figure_manifest(d, MAX_FIGURES))
                 if FIGURES and figs.strip() else "")
    src, note, tmp = "", "—", None
    if mode in ("inline", "file"):
        try:
            raw = open(os.path.join(d, "paper.md"), encoding="utf-8", errors="replace").read()
            trimmed, note = prune_paper(raw)
        except OSError as e:
            log(f"  ! 读 paper.md 失败（{e}），本次退回工具模式")
            mode, note = "tool", "读取失败"
        else:
            # 图片清单同样进 -p 参数，判大小得带上它，否则卡在阈值上的那几篇会撑爆 argv
            if mode == "inline" and len(trimmed.encode()) + len(figs_note.encode()) > ARGV_LIMIT:
                mode = "file"
            if mode == "inline":
                src = f"\n以下是论文全文：\n\n{trimmed}\n"
            else:
                tmp = os.path.join(d, TMP_NAME)
                open(tmp, "w", encoding="utf-8").write(trimmed)
    task = {"inline": TASK_ONESHOT, "file": TASK_FILE, "tool": TASK_TOOL}[mode]
    prompt = (body.replace("{task}", task)
                  .replace("{draft_rule}", DRAFT_ONESHOT if mode != "tool" else DRAFT_TOOL)
                  .replace("{source}", src)
                  .replace("{deliver}", DELIVER_ONESHOT if mode != "tool" else DELIVER_TOOL))
    prompt = prompt.replace("{meta}", sp.meta_block(item, ab))
    prompt = prompt.replace("{library}", library_context(key))
    return prompt.replace("{figure_manifest}", "") + figs_note, note, mode, tmp


def parse_stream_json(out):
    """从 --output-format stream-json 的输出里取出 assistant 文本（多段则拼接）。

    每行一个 JSON 对象；正文行形如 {"role":"assistant","content":"…"}，meta 行忽略。
    """
    parts = []
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if isinstance(o, dict) and o.get("role") == "assistant" \
                and isinstance(o.get("content"), str):
            parts.append(o["content"])
    return "".join(parts).strip()


CHATTY_RE = re.compile(r"^(?:以下|下面|这是|好的|希望|总结如下|综上|注意|说明|注[：:]|```|\||#|\*)")


def clean_body(text):
    """去掉代码围栏与客套前言，保证第一节是标准标题。

    两个坑（都是实测踩出来的）：
      - 只从第一个 `^## ` 切，会在模型把第一节写成加粗/裸写时**连结论一起丢掉**
        （实测 11/184 篇因此没了「一句话结论」）；
      - 反过来，无脑保留前言又会把「好的，我来总结」这种客套话写进文件。
    所以：截取点同时认「标题」与「一句话结论」的各种写法；前言只有在**短且像客套话**时才丢，
    否则当成模型裸写的结论、补成第一节。
    """
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    m = re.search(r"^(?:#{1,6}[ \t]*)?\**[ \t]*一句话结论|^#{1,6}[ \t]+\S", t, re.M)
    if m:
        head, rest = t[:m.start()].strip(), t[m.start():]
        keep = (head and not CHATTY_RE.match(head)
                and len(head) <= 300 and not re.match(r"^\**[ \t]*一句话结论", rest))
        t = ("## 一句话结论\n\n" + head + "\n\n" + rest) if keep else rest
    # 加粗/裸写的第一节归一成标准标题，保证七节结构一致
    t = re.sub(r"^\**\s*一句话结论\**\s*[:：]?[ \t]*", "## 一句话结论\n\n", t, count=1)
    return t.strip() + "\n" if t.strip() else ""


def missing_sections(body):
    """正文里缺哪几节（按前缀认，容错模型把标题写长/写短）；全齐返回 []。"""
    have = {section_key(h) for h in HEAD_RE.findall(body)}
    return [s for s in SECTIONS if s not in have]


def write_summary(summ, old, body, item, ab):
    """落盘：保留人工章节 → 写正文 → 属性区由 Zotero 元数据写死。"""
    merged = preserve_manual(old, body)
    open(summ, "w", encoding="utf-8").write(merged)
    sp.rewrite_file(summ, item, ab, apply=True)


# ────────────────────────────────────────────── 人工章节保留

def norm_head(s):
    """标题归一：只留中英文与数字，去掉空格和标点。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s or "")


def section_key(head):
    """把 ## 标题归到标准七节（或老格式的 LEGACY_SECTIONS）之一，归不上返回空串（= 人工内容）。

    按前缀匹配是必要的：模型会把标题写成「## 与研究主题的关联（对领域的意义）」，甚至偶尔
    截短成「## 与研究主题」，这两种都得仍认得出来。
    老格式的「关键术语中英对照」也算「认得出来」——这样重跑时它会被丢掉，
    而不是当成人工章节又插回新文件里。
    """
    h = norm_head(head)
    for s in SECTIONS + LEGACY_SECTIONS:
        n = norm_head(s)
        if h.startswith(n) or (len(h) >= 3 and n.startswith(h)):
            return s
    return ""


def split_sections(body):
    """按 ## 级标题切正文，返回 (题前导语, [(标题, 该节原文), ...])。

    只切 `## `，`### ` 之类的子标题留在所属章节内（embed_figures.py 写的「## 关键图表」
    底下就是一串 `### 图 N`）。
    """
    heads = list(HEAD_RE.finditer(body))
    preamble = body[:heads[0].start()] if heads else body
    parts = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        parts.append((m.group(1), body[m.start():end]))
    return preamble, parts


def preserve_manual(old_text, new_text):
    """把旧 summary.md 里的人工章节搬回模型新正文；属性区不动，返回合并后的全文。

    人工约定的跨篇对照节（`## 库内同主题工作：…对照`）按 AGENTS.md 写在「关键术语中英
    对照」之前，而 `--force` 重跑会让模型整篇覆盖 summary.md——所以按**章节**认：八节
    之外的 ## 章节就是人工内容，原样插回锚点之前（也包括 embed_figures.py 写的「关键图表」）。
    不能用文件里的 HTML 注释当标记：这些 .md 要在 Obsidian 里读、还要挂进 Zotero。
    没有人工章节时原样返回 new_text——大多数篇目的行为与从前完全一致。
    """
    _, old_body = sp.split_fm(old_text)
    new_fm, new_body = sp.split_fm(new_text)

    manual = [(h, c) for h, c in split_sections(old_body)[1] if not section_key(h)]
    preamble, parts = split_sections(new_body)
    have = {norm_head(h) for h, _ in parts}
    # 已在正文里的同名章节不重复插：重跑一次已合并过的文件要得到同一份结果（幂等）
    add = [(h, c) for h, c in manual if norm_head(h) not in have]
    if not add:
        return new_text

    at = next((i for i, (h, _) in enumerate(parts) if section_key(h) == MANUAL_ANCHOR),
              len(parts))
    parts[at:at] = add
    merged = preamble + "".join(c for _, c in parts)
    if merged and not merged.endswith("\n"):
        merged += "\n"
    return new_fm + merged


# ────────────────────────────────────────────── 主流程

def summarize(rec, items, ab, tpl):
    key = rec["key"]
    d = paper_dir(rec)
    paper = os.path.join(d, "paper.md")
    summ = os.path.join(d, "summary.md")
    if not os.path.exists(paper) or os.path.getsize(paper) < 800:
        return key, "no-source", 0
    # dry-run 要看的就是提示词，已存在的 summary.md 不该把它挡掉
    if not DRY and not FORCE and os.path.exists(summ) and os.path.getsize(summ) > 600:
        return key, "skip", 0

    item = items.get(key)
    if not item:
        return key, "no-zotero-item", 0
    prompt, prune_note, mode, tmp = build_prompt(key, d, item, ab, tpl)
    if DRY:
        print(f"\n===== {key} | {rec['title'][:60]} | 载体 {mode} | prompt {len(prompt)} 字"
              f" | 裁剪：{prune_note} =====\n{prompt}\n===== prompt 结束 =====\n", flush=True)
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
        return key, "dry-run", 0
    # 模型会整篇覆盖 summary.md，先把旧正文留住，人工加的章节事后要插回来
    old = open(summ, encoding="utf-8", errors="replace").read() if os.path.exists(summ) else ""
    mtime0 = os.path.getmtime(summ) if os.path.exists(summ) else 0
    env = dict(os.environ)
    if MAX_STEPS:
        env["KIMI_LOOP_MAX_STEPS_PER_TURN"] = str(MAX_STEPS)
    t0 = time.time()

    # ── inline / file：模型只回正文（不写文件），脚本落盘 ─────────────
    if mode in ("inline", "file"):
        agent = ONESHOT_AGENT if mode == "inline" else ONESHOT_READ_AGENT
        cmd = [KIMI, f"--agent-file={agent}", "-p", prompt,
               "--output-format", "stream-json"]
        body, miss = "", []
        try:
            for attempt in (1, 2):       # 缺节就重试一次：同一份提示词再跑一遍即可
                try:
                    r = subprocess.run(cmd, cwd=d, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace",
                                       timeout=2400, env=env)
                except subprocess.TimeoutExpired:
                    return key, "TIMEOUT", time.time() - t0
                body = clean_body(parse_stream_json(r.stdout))
                miss = missing_sections(body) if len(body) > 400 else ["正文过短"]
                if not miss:
                    break
                if attempt == 1:
                    log(f"  ! {key} 第 1 次不合规（{'/'.join(miss)}），重试一次")
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)            # 临时全文用完即删，不留残骸
        if len(body) > 400:
            write_summary(summ, old, body, item, ab)
            tag = f"{mode},裁剪{prune_note}" + (f",缺{'/'.join(miss)}" if miss else "")
            return key, f"ok({tag})", time.time() - t0
        log(f"  ! {key} {mode} 没拿到正文（{len(body)} 字），退回工具模式重试")
        prompt, prune_note, mode, tmp = build_prompt(key, d, item, ab, tpl, mode="tool")
        t0 = time.time()

    # ── 工具模式（兜底，或 --no-oneshot 强制）────────────────────────
    try:
        # --agent-file 必须放在 -p 之前，且用 = 形式：-p 自己吃下一个参数当提示词，
        # 写成「-p --agent-file X」会让 X 变成子命令（实测报 unknown command）
        cmd = [KIMI] + ([f"--agent-file={AGENT_FILE}"] if AGENT_FILE else []) + \
              ["-p", prompt]
        r = subprocess.run(cmd, cwd=d, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=2400, env=env)
    except subprocess.TimeoutExpired:
        return key, "TIMEOUT", time.time() - t0
    if os.path.exists(summ) and os.path.getsize(summ) > 600:
        # 撞上 --max-steps 时模型可能一步都没走完，文件还是旧的那份——
        # 不能把旧内容当新结果报 ok，否则上限设太小会静默产出「什么都没变」
        if mtime0 and os.path.getmtime(summ) == mtime0:
            return key, "未落笔（summary.md 没被改过，多半撞了 --max-steps）", time.time() - t0
        new = open(summ, encoding="utf-8", errors="replace").read()
        body = sp.split_fm(new)[1]
        write_summary(summ, old, body, item, ab)
        miss = missing_sections(body)
        return key, "ok" + (f"(缺{'/'.join(miss)})" if miss else ""), time.time() - t0
    tail = (r.stdout + r.stderr).strip().split("\n")[-1:] or ["?"]
    return key, f"FAIL({tail[0][:60]})", time.time() - t0


def paper_md_problems(path):
    """paper.md 的体检结果（不改文件）：控制字节 / 字形丢失 / front-matter 缺字段。"""
    raw = open(path, "rb").read()
    txt = raw.decode("utf-8", "replace")
    probs = []
    if any(b < 9 or (13 < b < 32) for b in raw):
        probs.append("控制字节")
    if "\ufffd" in txt:
        probs.append(f"乱码字符×{txt.count(chr(0xfffd))}")
    if "【?】" in txt:
        probs.append(f"占位符【?】×{txt.count('【?】')}")
    m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
    if m:
        fm = m.group(1)
        miss = [f for f in ("title", "zotero_key", "source", "pages")
                if not re.search(rf"^{f}:", fm, re.M)]
        unk = [f for f in ("pages",) if re.search(rf"^{f}:\s*unknown", fm, re.M)]
        if miss:
            probs.append("front-matter 缺 " + "/".join(miss))
        if unk:
            probs.append("front-matter " + "/".join(f"{f}=unknown" for f in unk))
    return probs


def preflight(todo):
    """总结前顺手体检：能自动修的（NUL 控制字节）先修，其余只报告。

    控制字节交给 `fix_control_bytes.py` 修——它有规则表和原件备份，别在这里重造。
    字形丢失（`�`、`【?】`）是 PDF 层面就没了、编不出来，只报告，并要求模型别照抄
    （提示词硬性要求第 5 条）。
    """
    bad = []
    for rec in todo:
        p = os.path.join(paper_dir(rec), "paper.md")
        if os.path.exists(p):
            probs = paper_md_problems(p)
            if probs:
                bad.append((rec["key"], probs))
    if not bad:
        return
    if not DRY and any("控制字节" in pr for _k, pr in bad):
        here = os.path.dirname(os.path.abspath(__file__))
        r = subprocess.run([sys.executable, os.path.join(here, "fix_control_bytes.py"), "--apply"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        # 只看 stdout 末行是判断不出成败的：脚本报错退出时也有一行输出，照报「已修」
        # 等于骗自己——源文件还带着控制字节，后面模型照抄乱码还查不出原因
        # 失败时优先看 stderr（诊断在那边），成功时看 stdout
        stream = (r.stderr or r.stdout) if r.returncode else (r.stdout or r.stderr)
        tail = ((stream or "").strip().split("\n") or ["?"])[-1]
        if r.returncode == 0:
            log(f"  → 控制字节修复：{tail[:80]}")
            mark = "（本轮已修）"
        else:
            log(f"  ! 控制字节修复失败（exit {r.returncode}）：{tail[:80]}")
            mark = f"（修复失败 exit {r.returncode}）"
        bad = [(k, [f"控制字节{mark}" if x == "控制字节" else x for x in pr]) for k, pr in bad]
    log(f"paper.md 体检：{len(bad)} 篇有问题（其余干净）")
    for k, pr in bad:
        log(f"  {k}  {'、'.join(pr)}")


def main():
    workers = int(ARGS[0]) if ARGS else 3
    only = ARGS[1].split(",") if len(ARGS) > 1 else None
    man = json.load(open(MANIFEST, encoding="utf-8"))
    todo = man if not only else [r for r in man if r["key"] in only]
    items = sp.load_items()
    ab = sp.Abbr()
    tpl = load_template(OPTS.get("template") or TEMPLATE)
    log(f"待总结 {len(todo)} 篇，并发 {workers}"
        f"{'，引用图片' if FIGURES else ''}{'，强制重写' if FORCE else ''}"
        f"{f'，步数上限 {MAX_STEPS}' if MAX_STEPS else ''}"
        f"{'，DRY-RUN（不调 kimi、不写文件）' if DRY else ''}")
    preflight(todo)

    ok = fail = skip = nosrc = dry = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(summarize, r, items, ab, tpl): r for r in todo}
        for f in as_completed(futs):
            r = futs[f]
            try:
                key, status, dt = f.result()
            except Exception as e:
                key, status, dt = r["key"], f"EXC({e})", 0
            if status.startswith("ok"): ok += 1
            elif status == "skip": skip += 1
            elif status == "no-source": nosrc += 1
            elif status == "dry-run": dry += 1
            else: fail += 1
            log(f"  {key} {status} {dt:.0f}s | {r['title'][:42]}")
    log(f"完成：成功 {ok}，跳过 {skip}，无源 {nosrc}，失败 {fail}"
        f"{f'，DRY-RUN {dry}' if DRY else ''}")


if __name__ == "__main__":
    main()
