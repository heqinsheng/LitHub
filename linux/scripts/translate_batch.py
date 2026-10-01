#!/usr/bin/env python3
"""对每篇 paper.md 生成「全文翻译（含批注）.md」——**分段翻译 + 脚本拼装**。

为什么要分段 + 脚本拼装（2026-09-29 用量实测驱动的改动）：把「读全文、切段、写属性区、
把各段拼成成品」一并交给模型时，那个调度会话必须同时装着原文、所有分片与组装稿，
实测累积 **10.06M 缓存读、58 次 LLM 请求**，而它产出的东西（组装稿）本来 `cat` 就能做。
所以现在这样分工：

    脚本    切段 / 写属性区 / 机械摘走参考文献 / 拼装 / 体检        ← 零 LLM
    模型    只写「自己那一块」：开头三节（1 次）或某一段译文（N 次）
    模型    永远不碰属性区与参考文献，也永远不做拼装

规范见 `prompts/translate_full.md`，工具裁剪见 `prompts/translator.agent.md`。
中间产物落在 `state/work/translate/<KEY>/`，**可续跑**：已有且不像残片的
`partNN.md` / `head.md` 会跳过，`--force` 才重做。

⚠️ 切段切的是 paper.md 的**行区间**（不是 token 数）：模型按行区间读、按行区间写，
   段与段既不重叠也不漏行。参考文献那一节在切段前就被脚本摘走，**不发给模型**。

用法:
  python3 scripts/translate_batch.py --dry-run ND2Z2J4Q        # 只看分段表与提示词
  python3 scripts/translate_batch.py ND2Z2J4Q                  # 跑（自动分段，4 段并行）
  python3 scripts/translate_batch.py --parts 8 --jobs 6 ND2Z2J4Q
  python3 scripts/translate_batch.py --report-usage ND2Z2J4Q    # 跑完打印每段 token 用量
  python3 scripts/translate_batch.py all 3                      # 还没有译文的，最多 3 篇
"""
import argparse, glob, json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# import 只为那两件事：强制 IPv4 补丁（本机 IPv6 100% 丢包时 Python 会死等超时）
# 与 stdout 编码兜底。zapi 在 import 阶段不发请求、不读密钥。
import zapi  # noqa: F401
import sync_properties as sp
import llm_router as llm

ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")
WORKROOT = os.path.join(ROOT, "state", "work", "translate")
TEMPLATE = os.path.join(ROOT, "prompts", "translate_full.md")
AGENT_FILE = os.path.join(ROOT, "prompts", "translator.agent.md")

OUT = "全文翻译（含批注）.md"
MIN_SRC = 800          # 比这还小的 paper.md 不是一篇文章
MIN_OUT = 2000         # 比这还小的译文不算译完
MIN_PART = 400         # 比这还小的分片算残片，重跑
TARGET_SEG_CHARS = 26000   # 自动分段时每段的目标字符数（实测 7 段≈180 KB 原文比较顺）
MAX_SEGS = 9

# 体检用的字面量。它们全是「读者从上下文就知道」的元信息，写进成品只是噪声——
# 用户 2026-09-29 明确点名要删的那几段就是它们（见 帮助手册.md §12.4）。
# 每条都写得比「自然语言里可能出现的词」更具体（带上引用块标记、固定搭配），否则正文里
# 一句正常的「怎么读」「逐字取自」都会误报——误报比漏报更伤，它会让体检结果没人信。
BANNED = ["> **怎么读**", "> **关于批注**", "（原文，未翻译）", "本文件是 Zotero 条目",
          "条目逐字取自", "阅读指南："]
# 开头三节必须在文件**前部**（正文译本之前）；参考文献节合法地在末尾，只要求存在。
HEAD_REQUIRED = ["## 库内定位", "## 作者与团队", "## 读前批注"]
ANY_REQUIRED = ["## 参考文献"]

# 参考文献节的标题：期刊惯例五花八门——RSC 的 "Notes and references"、Elsevier/IEEE 的
# "References"、Science 的 "REFERENCES AND NOTES"、还有 "Reference list"。所以判据不是
# 整串匹配，而是「标题里含 reference，且标题本身很短」：长标题里出现 reference，
# 多半是正文小节（如 "Effect of reference electrode…"）。

# 出版方网页在尾部留下的痕迹。Science / ECS / Elsevier 的 PDF 常在正文之后再接一段
# 网页内容（`Editor's summary`、`View the article online`、`Permissions`…），那是壳不是文章。
# 存**归一化**后的形式（去标记、去标点、只留字母数字），见 _norm_head()。
CHROME_HEADS = ("editorssummary", "viewthearticleonline", "permissions",
                "reprintsandpermissions", "youmayalsolike", "viewrelatedarticles",
                "relatedarticles", "downloadcitation", "citethisarticle")


def _norm_head(s):
    """标题归一化：去 markdown 标记、去标点、只留字母数字。

    必须去标点——Science 网页上是 `## Editor’s summary`（弯引号 U+2019），
    照 ASCII 的 `'` 去比永远匹配不上，实测因此漏掉了整个落地页。
    """
    return re.sub(r"[^a-z0-9]", "", re.sub(r"^#{1,6}[ \t]*", "", s).strip().lower())

# 文献条目的编号（`1 J. Lu` / `1. Xu, B.` / `[1] Author` 三种写法都要认）
NUM_REF = re.compile(r"^\s*\[?(\d{1,3})\]?[.)]?\s+\S")


def refs_end(lines, r, n):
    """参考文献块的终点：**跟着编号走**，而不是「下一个标题」。

    实测 T6ES2W2M（Joule）的文献表被 MinerU 插进了页眉——`## Review`、`## Joule`、
    `## CellPress` 把 139 条文献切成几段。按「下一个标题就收工」只拿到 19 条，
    剩下 120 条会被当正文发给模型「翻译」。所以这里跟编号：编号继续增大就还在文献区，
    编号回到 1（或不再出现编号）就收工。数不到 3 条时退回「下一个标题」，
    免得把某种无编号排版的正文整段吞掉。
    """
    last, hi = 0, r + 1
    for i in range(r + 1, n):
        m = NUM_REF.match(lines[i])
        if not m:
            continue
        v = int(m.group(1))
        if last < v <= last + 20:        # 允许跳号，但不许回退（回退=另一节从 1 开始）
            last, hi = v, i + 1
    if last < 3:
        return next((i for i in range(r + 1, n) if lines[i].startswith("#")), n)
    while hi < n and not lines[hi].strip():
        hi += 1
    return hi


def is_ref_head(line):
    s = re.sub(r"^#{1,6}[ \t]*", "", line).strip().lower()
    s = re.sub(r"[^a-z ]", "", s)
    return bool(s) and "reference" in s and len(s) <= 40


def log(m):
    print(m, flush=True)


def paper_dir(key):
    hits = sorted(glob.glob(os.path.join(PAPERS, f"{key}_*")))
    return hits[0] if hits else None


def load_template():
    try:
        with open(TEMPLATE, encoding="utf-8") as f:
            return re.sub(r"<!--.*?-->\s*", "", f.read(), flags=re.S)
    except OSError:
        raise SystemExit(f"缺模板 {TEMPLATE}")


# ────────────────────────────────────────────── 切段与拆件（零 LLM）

def _h1_key(s):
    """一级标题的归一化指纹：只留字母数字、截前 30 位。

    不能按原文逐字比——同一篇的网页标题与正文标题常常只差 OCR：IEAVRVCE 的第一处是
    `$\\mathsf { L N i } _ { 0 . 3 3 }…`，第二处是 `$\\mathbf { L i N i _ { 0 . 3 3 } }…`，
    逐字比就认不出它们是同一个标题。
    """
    return re.sub(r"[^a-z0-9]", "", s.lower())[:30]


def analyze(raw):
    """把 paper.md 解析成**有序块序列**：[(kind, start, end)]，kind ∈ translate|verbatim|drop。

    三类东西不进模型上下文——

    1. **paper.md 自己的 YAML 头**（MinerU 的产物）；
    2. **出版方网页的壳**：开头（`REVIEW ARTICLE`、`Published on …`、`You may also like`
       与整页产品广告——IEAVRVCE 的第 10–49 行就是 ECS 产品页，还夹着 4 张广告图）与
       结尾（Science 的 `Editor's summary` / `View the article online` / `Permissions`）；
    3. **整节参考文献**：逐字照抄、几十 KB，翻译它既没意义又最贵。

    ⚠️ **参考文献不一定在文件末尾**：Science 的排版是「正文 → REFERENCES AND NOTES →
       致谢 → 补充材料」，后面还有正文。所以这里返回块序列而不是一个正文区间，
       拼装按原顺序还原，切段也不会跨越参考文献块。

    返回的起止是 **paper.md 的绝对行号**（0-based 半开区间），这样发给模型的
    `offset`/`limit` 能直接对上原文——切段的行号必须与模型读到的一致。
    """
    lines = raw.split("\n")
    n = len(lines)
    blocks = []

    # ① 开头：YAML 头 + 出版方的壳，一路推到真正的正文标题
    lo = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, n):
            if lines[i].strip() == "---":
                lo = i + 1
                break
    win = min(lo + 60, n)
    first = next((i for i in range(lo, win) if lines[i].startswith("# ")), None)
    if first is not None:
        k = _h1_key(lines[first])
        dup = next((j for j in range(first + 1, win)
                    if lines[j].startswith("# ") and _h1_key(lines[j]) == k), None)
        lo = dup if dup is not None else first
    if lo:
        blocks.append(("drop", 0, lo))

    # ② 参考文献块：从标题到编号连续性的终点（Science 后面还跟着致谢与补充材料）
    r = next((i for i in range(lo, n) if is_ref_head(lines[i])), None)
    r_end = refs_end(lines, r, n) if r is not None else n

    # ③ 尾部网页壳：一级标题重复出现，或命中已知的网页小节名 → 从那里起全部丢掉
    h1k = _h1_key(lines[first]) if first is not None else ""
    stop = n
    for i in range(max(r_end, (first or lo) + 1), n):
        s = lines[i]
        if not s.startswith("#"):
            continue
        # 标题重复：**不限层级**——网页把同一标题排成 `## `、正文里是 `# `，实测就是这么漏的
        if h1k and _h1_key(s) == h1k:
            stop = i
            break
        t = _norm_head(s)
        if any(t.startswith(c) for c in CHROME_HEADS):
            stop = i
            break

    # ④ 按原顺序拼出块
    if r is None:
        if stop > lo:
            blocks.append(("translate", lo, stop))
    else:
        if r > lo:
            blocks.append(("translate", lo, r))
        blocks.append(("verbatim", r, min(r_end, stop)))
        if stop > r_end:
            blocks.append(("translate", r_end, stop))
    if stop < n:
        blocks.append(("drop", stop, n))
    return lines, blocks


def _split_span(lines, pref, a, b, n):
    """在 [a, b) 里按**空行**（段边界）切 n 段，返回 1-based 闭区间列表。

    只在段边界切：切在句子中间会让两个模型各自补半句话，拼起来正好是病句。
    空行不够 n-1 个时退回等行数切——那总比少切几段好。
    """
    cand = [i for i in range(a, b) if not lines[i].strip()]
    cuts = []
    if len(cand) >= n - 1:
        total = pref[b] - pref[a]
        for k in range(1, n):
            target = pref[a] + total * k / n
            i = min(cand, key=lambda j: abs(pref[j + 1] - target))
            if i not in cuts:
                cuts.append(i)
    else:
        step = max(1, (b - a) // n)
        cuts = [a + step * k - 1 for k in range(1, n)]
    bounds, prev = [], a - 1
    for c in sorted(cuts):
        if pref[c + 1] - pref[prev + 1] < 200:
            continue
        bounds.append((prev + 1, c))
        prev = c
    bounds.append((prev + 1, b - 1))
    out = []
    for x, y in bounds:
        while y >= x and not lines[y].strip():      # 段尾空行不带走，免得拼出多余空行
            y -= 1
        while x <= y and not lines[x].strip():
            x += 1
        if y >= x:
            out.append((x + 1, y + 1))
    return out


def plan_segments(lines, blocks, n):
    """按块序列切段，返回 [(序号, 起始行, 结束行)]（1-based 闭区间，按文序）。

    每个可翻译块至少分到一段：块的边界**必须**是段的边界（参考文献块两侧尤其如此），
    否则一段会横跨「正文—参考文献—正文」，模型既读不懂也写不对。
    """
    pref = [0]
    for l in lines:
        pref.append(pref[-1] + len(l) + 1)
    spans = [(a, b) for kind, a, b in blocks if kind == "translate"]
    total = sum(pref[b] - pref[a] for a, b in spans)
    scale = total / max(1, n)
    segs = []
    for a, b in spans:
        k = max(1, round((pref[b] - pref[a]) / scale)) if scale else 1
        segs += _split_span(lines, pref, a, b, k)
    return [(i + 1, x, y) for i, (x, y) in enumerate(segs)]


def images_in(text):
    """按出现顺序取出 images/… 引用（去重保序）——一图拆成多个连续文件时要全留。"""
    out = []
    for m in re.finditer(r"!\[\]\((images/[^)]+)\)", text):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def body_images(lines, blocks):
    """可翻译块里的图片引用（去重保序）。对账与计数都走它——

    对账必须只比正文：网页广告图在被丢掉的那一段里，拿整篇比对会永远报「漏图 4 张」。
    """
    return images_in("\n".join("\n".join(lines[a:b])
                               for kind, a, b in blocks if kind == "translate"))


def counts_of(paper_md):
    raw = open(paper_md, encoding="utf-8", errors="replace").read()
    lines, blocks = analyze(raw)
    body_chars = sum(len(l) + 1 for kind, a, b in blocks if kind == "translate"
                     for l in lines[a:b])
    refs_chars = sum(len(l) + 1 for kind, a, b in blocks if kind == "verbatim"
                     for l in lines[a:b])
    return (f"原文 {len(raw)} 字符（正文 {body_chars}、参考文献 {refs_chars}，"
            f"后两者由脚本处理）、图 {len(body_images(lines, blocks))} 处引用")


def facts_block(key):
    """跑 paper_context.py 取事实（库内引文关系 / 被引量 / h 指数）。

    返回 (事实文本, 是否取数不完整)。**不完整就别开跑**：实测一次 Semantic Scholar 抖动
    会让事实块印出「取数失败」，而开头撰写者会老老实实把「本次取数失败」写进成品——
    那句错话会跟着文件一直留着。宁可让它失败、让人重跑一次（很便宜）。
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_context.py")
    rc = subprocess.run([sys.executable, script, key], capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    txt = rc.stdout or ""
    if rc.returncode != 0 or not txt.strip():
        return "（paper_context.py 取数失败：开头「库内定位」只写元数据，其余留空并注明。）", True
    return txt, ("有源取数失败" in txt)


def frontmatter(key):
    """属性区由脚本写。让模型写它只会白烧 token，还会猜错 DOI 与刊名。"""
    d = sp.load_items().get(key)
    if not d:
        return ""
    title = sp.field(d, "title")
    rows = [
        ("title", f"{title} —— 全文中文翻译（含批注）" if title else ""),
        ("original_title", title),
        ("authors", ", ".join(sp.creators(d))),
        ("journal", sp.journal_of(d)),
        ("year", sp.year_of(d)),
        ("volume_pages", sp.volpages(d)),
        ("doi", sp.field(d, "DOI").lower()),
        ("zotero_key", key),
        ("source_file", "paper.md"),
        ("document_type", "全文翻译（含逐节批注）"),
        ("translated_on", time.strftime("%Y-%m-%d")),
    ]
    return "---\n" + "\n".join(f"{k}: {sp.scalar(v)}" for k, v in rows) + "\n---\n"


# ────────────────────────────────────────────── 提示词

HEAD_ROLE = """你的角色：**开头撰写者**。只写一个文件（绝对路径，一字不差）：

    {path}

内容就是规范里的三节（`## 库内定位`、`## 作者与团队`、`## 读前批注：我的整体判断`）。

- 源文件是 `{src}`（**绝对路径，就用它**）。用它 `Read` 一次读完整篇：
  带 `line_offset=1`、`max_chars=500000`；
- ⚠️ **不要探针式地短读**（`max_chars=300`、`n_lines=3` 这种一个一个试）——
  实测那样会白烧五六个来回，最后还是要整篇读一次。要读就一次读完；
- 不要读工作目录里的任何其它文件：不要读自己将要写的 `head.md`（它还不存在），
  也不要读别人的 `partNN.md`；
- 不要翻译正文、不要写属性区、不要写参考文献；
- 不要动最终产物 `{out_name}`（那是脚本拼的）。
"""

SEG_ROLE = """你的角色：**分段翻译者**，只做第 {i}/{n} 段，别的一概不做。

- 源文件是 `{src}`（**绝对路径，就用它**）的**第 {a}–{b} 行**（1-based，含两端）。
  用它 `Read` 一次读完：带 `line_offset={a}`、`n_lines={lim}`，**不要读区间外的内容**；
- ⚠️ 不要探针式地短读，也不要读工作目录里的其它文件（别的 `partNN.md` 与你将要写的那个都不读）；
- 产出：只写这一个文件（绝对路径，一字不差）：

    {path}

  里面只放这一段的正文译文 + 图注 + 批注；
- 不要写开头三节（`## 库内定位` / `## 作者与团队` / `## 读前批注`）、属性区、参考文献——
  那些由别人写，你写了就是重复。**区间里若出现原文的一级标题，照译**（那是译文的大标题）；
- 本段应出现的图片共 {k} 张，**一张都不能少**（按 `![](images/…)` 原样照抄）：
{images}
"""


def seg_table_of(segs):
    return "\n".join(f"- 第 {i}/{len(segs)} 段：`paper.md` 第 {a}–{b} 行 → `part{i:02d}.md`"
                     for i, a, b in segs)


MODE_FILES = {True: "translate_mode_review.md", False: "translate_mode_research.md"}


def mode_spec(key):
    """按 classification.json 的 is_review 选类型专项要求。

    与研究论文的差别是实打实的：综述的证据是别人的、要问「选得全不全、有没有拼时间线」；
    研究论文的证据是作者自己的、要问「n 多少、误差哪来的、这张图撑不撑得住这句话」。
    沿用 summarize_batch.py 的 `template_choice()` 口径（同一份 classification.json）。
    """
    cls = {c["key"]: c for c in json.load(open(os.path.join(ROOT, "state",
                                                            "classification.json"),
                                               encoding="utf-8"))
           if isinstance(c, dict) and c.get("key")}
    rev = bool((cls.get(key) or {}).get("is_review"))
    path = os.path.join(ROOT, "prompts", MODE_FILES[rev])
    try:
        with open(path, encoding="utf-8") as f:
            return re.sub(r"<!--.*?-->\s*", "", f.read(), flags=re.S).strip(), \
                ("综述" if rev else "研究论文"), path
    except OSError:
        # 缺文件不该让流水线停：退回一句最保守的话，并让日志看得见
        return ("（类型专项要求文件缺失，按通用要求执行：批注要有批判性，"
                "盯证据是否撑得住结论。）"), ("综述" if rev else "研究论文"), path


def build_base(tpl, key, seg_table):
    """一篇论文**共用**的那一段提示词（规范 + 元数据 + 事实 + 类型专项 + 分段表）。

    只算一次：分段是并行的，每个任务各跑一遍 `paper_context.py`、各读一遍期刊简写表，
    N 段就是 N 倍无谓开销；何况这部分每个任务一字不差，同一份前缀还能吃到 prompt cache。
    返回 (提示词, 取数是否完整, 类型专项文件路径)。
    """
    items = sp.load_items()
    ab = sp.build_abbr(items, {}, save=False)
    facts, incomplete = facts_block(key)
    mtext, doc, mpath = mode_spec(key)
    p = (tpl.replace("{meta}", sp.meta_block(items.get(key) or {}, ab))
            .replace("{context}", facts)
            .replace("{counts}", counts_of(os.path.join(paper_dir(key), "paper.md")))
            .replace("{segment_table}", seg_table)
            .replace("{doctype}", doc)
            .replace("{modespec}", mtext)) + "\n---\n\n"
    return p, incomplete, mpath


# ────────────────────────────────────────────── 跑 kimi 与用量

def run_kimi(prompt, cwd, timeout):
    """跑一次 kimi -p，返回 (是否拿到会话 id, stdout, stderr, session_id, 实际用的模型)。

    带 `--output-format stream-json`：它不是给解析正文用的（正文由模型写文件），
    而是为了从 `session.resume_hint` 那行拿到 session_id —— 有了它才能去
    `~/.kimi-code/sessions/*/<sid>/agents/*/wire.jsonl` 里读真实用量。

    模型由 llm_router 决定：默认走 config/runtime.json 的 llm.model（Kimi 月付额度），
    失败（没额度 / 限流 / 认证错）自动改用 llm.fallback_model。
    """
    # --agent-file 必须放在 -p 之前：-p 自己吃下一个参数当提示词，
    # 写成「-p --agent-file X」会让 X 被当成子命令（实测报 unknown command）。
    args = [f"--agent-file={AGENT_FILE}", "-p", prompt, "--output-format", "stream-json"]
    r, used, note = llm.run_kimi_cli(args, cwd=cwd, timeout=timeout, log=log)
    if r is None:
        return False, "", f"TIMEOUT（{timeout // 60} 分钟）", "", used
    sid = ""
    for ln in r.stdout.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if isinstance(o, dict) and o.get("type") == "session.resume_hint":
            sid = o.get("session_id") or ""
    return True, r.stdout, (r.stderr or "")[-400:], sid, used


def session_usage(sids):
    """汇总若干 session 的 token 用量（只认 `usage.record`）。

    ⚠️ 同一个 usage 对象会**同时嵌在 `usage.record` 与别的记录里**，按
    `grep '"usage"'` 数一遍会把用量放大近 3 倍——2026-09-29 第一次统计就栽在这里，
    报了 490,805 输出（实际 283,089）。
    """
    tot = {"inputOther": 0, "output": 0, "inputCacheRead": 0, "inputCacheCreation": 0}
    seen = 0
    for sid in filter(None, sids):
        pat = os.path.expanduser(f"~/.kimi-code/sessions/*/{sid}/agents/*/wire.jsonl")
        for f in glob.glob(pat):
            for line in open(f, encoding="utf-8", errors="replace"):
                if '"usage"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") != "usage.record":
                    continue
                u = r.get("usage") or {}
                seen += 1
                for k in tot:
                    tot[k] += u.get(k, 0) or 0
    return (tot if seen else None), seen


# ────────────────────────────────────────────── 体检

def assemble(key, lines, order, work, out):
    """拼装：脚本写的属性区 + head.md + 各分片 + 机械抄来的参考文献，**按原文顺序**。

    这就是以前交给模型做、也最没必要交给它的那一步（实测那一步让调度会话累积了
    10.06M 缓存读）。`order` 是 [(kind, …)]：`seg` 取对应分片，`verbatim` 直接把
    paper.md 那几行抄过来（跳过原标题行，改写成 `## 参考文献`）——所以参考文献
    落在文中哪一段，成品里就落在哪一段。缺任何一块都返回 None，不写半成品。
    """
    chunks = []
    p0 = os.path.join(work, "head.md")
    if not os.path.exists(p0):
        return None
    chunks.append(open(p0, encoding="utf-8").read().strip())
    for item in order:
        if item[0] == "seg":
            p = os.path.join(work, f"part{item[1]:02d}.md")
            if not os.path.exists(p):
                return None
            chunks.append(open(p, encoding="utf-8").read().strip())
            continue
        _, a, b = item
        # 文献区里的孤行标题是 MinerU 抽进来的页眉（`## Review`、`## Joule`、`## CellPress`），
        # 文献表里不会真有标题，所以这一段整块丢掉标题行。
        body = "\n".join(l for l in lines[a + 1:b] if not l.startswith("#")).strip()
        if body:
            chunks.append("## 参考文献\n\n" + body)
    doc = frontmatter(key) + "\n" + "\n\n".join(c for c in chunks if c) + "\n"
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(doc)
    return out


def tail_msg(so, se):
    """错误诊断用的末行。流式输出里最后几行永远是 meta JSON（`session.resume_hint`），
    直接取末行只会打印出一串没人能读的 JSON——实测踩过。"""
    lines = [l.strip() for l in (so or "").splitlines()
             if l.strip() and not l.strip().startswith("{")]
    lines += [l.strip() for l in (se or "").splitlines() if l.strip()]
    return lines[-1][:70] if lines else "无输出"


def collect_output(expected, cwd, name):
    """把产出收拢到中间目录，返回给日志用的一句话。

    模型偶尔把「写到 <绝对路径>」当成建议、按相对路径写进了 cwd——实测 `head.md`
    就是这么消失的（文件内容完全正确，只是落错了地方）。捡回来比重跑便宜几个数量级，
    所以先搬、不先判失败。
    """
    if os.path.exists(expected):
        return ""
    stray = os.path.join(cwd, name)
    if os.path.exists(stray):
        os.replace(stray, expected)
        return f"（模型写到了 {cwd.split('/')[-1]}/{name}，已搬回中间目录）"
    return ""


def verify(path):
    """返回问题列表（空 = 通过）。"""
    problems = []
    if not os.path.exists(path):
        return ["没有产出文件"]
    txt = open(path, encoding="utf-8", errors="replace").read()
    if len(txt) < MIN_OUT:
        problems.append(f"只有 {len(txt)} 字")
    for b in BANNED:
        if b in txt:
            problems.append(f"出现禁写的废话「{b}」")
    head = txt[:12000]
    for r in HEAD_REQUIRED:
        if r not in txt:
            problems.append(f"缺 {r}")
        elif r not in head:
            problems.append(f"{r} 不在开头")
    for r in ANY_REQUIRED:
        if r not in txt:
            problems.append(f"缺 {r}")
    if not re.search(r"^doi:", txt, re.M):
        problems.append("front-matter 缺 doi")
    if "images/" not in txt:
        problems.append("没有图片引用")
    return problems


def coverage(expected, out_path):
    """图数对账：成品里的 images/ 引用应覆盖正文的全部（一图拆多张时只多不少）。"""
    got = set(images_in(open(out_path, encoding="utf-8", errors="replace").read()))
    return sorted(set(expected) - got)


# ────────────────────────────────────────────── 单篇

def run_one(key, tpl, a, dry):
    """返回 (key, 结论, 秒数, [(块名, session_id)…], 是否通过)。

    最后一个布尔值是「结论字符串猜不出来」的那件事——早先靠
    `verdict.startswith("FAIL")` 判断，于是「1/3 块失败」被判成通过，
    打印出「1/3 块失败」和「1/1 篇通过」两句自相矛盾的日志。
    """
    d = paper_dir(key)
    if not d:
        return key, "没有 papers/ 目录", 0, [], False
    src = os.path.join(d, "paper.md")
    if not os.path.exists(src) or os.path.getsize(src) < MIN_SRC:
        return key, "缺 paper.md（先跑 mineru_batch.py）", 0, [], False
    out = os.path.join(d, OUT)
    exists = os.path.exists(out) and os.path.getsize(out) > MIN_OUT
    if exists and not a.force and not dry:
        return key, "已有译文，跳过（--force 重做）", 0, [], True

    text = open(src, encoding="utf-8", errors="replace").read()
    lines, blocks = analyze(text)
    spans = [(x, y) for kind, x, y in blocks if kind == "translate"]
    if not spans:
        return key, "paper.md 里没找到可翻译的正文（解析失败）", 0, [], False
    body_chars = sum(len(l) + 1 for x, y in spans for l in lines[x:y])
    expect_imgs = body_images(lines, blocks)
    n = a.parts or min(MAX_SEGS, max(3, round(body_chars / TARGET_SEG_CHARS)))
    segs = plan_segments(lines, blocks, n)
    work = os.path.join(WORKROOT, key)
    os.makedirs(work, exist_ok=True)
    # 拼装顺序：块序列 + 每段分片，按文序排好（参考文献落在文中就落在文中）
    order, si = [], 0
    for kind, x, y in blocks:
        if kind == "verbatim":
            order.append(("verbatim", x, y))
        elif kind == "translate":
            while si < len(segs) and segs[si][1] - 1 >= x and segs[si][2] <= y:
                order.append(("seg", segs[si][0]))
                si += 1
    if si != len(segs):
        return key, "分段与块序列对不上（内部错误）", 0, [], False

    if dry:
        print(f"\n===== {key} | {os.path.basename(d)[:56]}")
        print(f"正文 {body_chars} 字符 / 丢弃网页壳 {sum(y-x for kind,x,y in blocks if kind=='drop')} 行"
              f" / 参考文献 {sum(y-x for kind,x,y in blocks if kind=='verbatim')} 行（不发给模型）")
        for kind, x, y in blocks:
            print(f"  [{kind:9s}] 行 {x+1}–{y}")
        print(f"→ 切 {len(segs)} 段\n{seg_table_of(segs)}")
        print(f"中间目录 {work}\n产物 {out}")
        for i, x, y in segs:
            imgs = images_in("\n".join(lines[x - 1:y]))
            print(f"  第 {i} 段 行 {x}–{y}（{y-x+1} 行）：图 {len(imgs)} 张")
        print("===== 分段结束 =====\n")
        return key, f"dry-run（{len(segs)} 段）", 0, [], True

    # 头（三节）与各分段互不依赖 → 一起并行
    base, incomplete, mpath = build_base(tpl, key, seg_table_of(segs))
    if incomplete:
        return (key, "paper_context.py 取数不完整（多半是 Semantic Scholar 抖动），"
                     "先重跑一次再译——别把「取数失败」写进成品", time.time(), [], False)
    log(f"  {key} 类型专项：{os.path.basename(mpath)}")
    tasks = []
    head_path = os.path.join(work, "head.md")
    if a.force or not (os.path.exists(head_path) and os.path.getsize(head_path) > 400):
        tasks.append(("head", head_path,
                      base + HEAD_ROLE.format(path=head_path, src=src, out_name=OUT)))
    else:
        log(f"  {key} head.md 已存在，跳过")

    for i, x, y in segs:
        p = os.path.join(work, f"part{i:02d}.md")
        if not a.force and os.path.exists(p) and os.path.getsize(p) > MIN_PART:
            log(f"  {key} part{i:02d}.md 已存在，跳过")
            continue
        imgs = images_in("\n".join(lines[x - 1:y]))
        tasks.append((f"part{i:02d}", p,
                      base + SEG_ROLE.format(i=i, n=len(segs), a=x, b=y, lim=y - x + 1,
                                             src=src, path=p, k=len(imgs),
                                             images="\n".join(f"    {u}" for u in imgs))))

    def job(t):
        name, path, prompt = t
        t0 = time.time()
        ok, so, se, sid, used = run_kimi(prompt, d, a.timeout)
        dt = time.time() - t0
        at = f"@{used}" if used else ""
        if not ok:
            return name, f"TIMEOUT（{se}）", 0, sid, False
        moved = collect_output(path, d, name)
        if not os.path.exists(path) or os.path.getsize(path) < MIN_PART:
            return name, f"FAIL（没拿到 {name}.md；末行：{tail_msg(so, se)}）", dt, sid, False
        return name, f"ok{at}（{os.path.getsize(path)} 字节，{dt/60:.1f} 分）{moved}", \
            dt, sid, True

    usage, bad, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for fut in as_completed([ex.submit(job, t) for t in tasks]):
            name, verdict, dt, sid, good = fut.result()
            if sid:
                usage.append((key, name, sid))
            if not good:
                bad += 1
            log(f"  {key} {name}: {verdict}")
    if bad:
        return key, f"{bad}/{len(tasks)} 块失败（可重跑续做）", time.time() - t0, usage, False

    # ── 拼装（零 LLM；这一步以前是模型做的，最贵也最没必要）────────────
    if not assemble(key, lines, order, work, out):
        return key, "分片不全，未拼装（可重跑续做）", time.time() - t0, usage, False

    dt = time.time() - t0
    problems = verify(out)
    missing = coverage(expect_imgs, out)
    if missing:
        problems.append(f"漏图 {len(missing)} 张（如 {missing[0]}）")
    if problems:
        return key, "已拼装但体检不过：" + "；".join(problems), dt, usage, False
    return key, f"ok（{os.path.getsize(out)} 字节，总 {dt/60:.1f} 分）", dt, usage, True


def main():
    ap = argparse.ArgumentParser(
        description="生成「全文翻译（含批注）.md」（分段翻译 + 脚本拼装）", allow_abbrev=False)
    ap.add_argument("keys", nargs="+", help="Zotero 条目 key，或 all（挑还没译文的）")
    ap.add_argument("--limit", type=int, default=0, help="all 模式最多处理几篇（0 = 不限）")
    ap.add_argument("--parts", type=int, default=0, help=f"切成几段（0 = 按 {TARGET_SEG_CHARS} 字符自动）")
    ap.add_argument("--jobs", type=int, default=4, help="并行段数（默认 4）")
    ap.add_argument("--dry-run", action="store_true", help="只打印分段表，不跑模型不写文件")
    ap.add_argument("--force", action="store_true", help="已有译文/分片也重做")
    ap.add_argument("--timeout", type=int, default=5400, help="单段超时秒数（默认 90 分钟）")
    ap.add_argument("--report-usage", action="store_true", help="打印每段的 token 用量表")
    a = ap.parse_args()

    tpl = load_template()
    if "all" in a.keys:
        keys = []
        for k in sorted(sp.load_items()):
            d0 = paper_dir(k)
            if d0 and os.path.exists(os.path.join(d0, "paper.md")) \
                    and not os.path.exists(os.path.join(d0, OUT)):
                keys.append(k)
        if a.limit:
            keys = keys[:a.limit]
        log(f"all：{len(keys)} 篇还没有译文")
    else:
        keys = a.keys

    log(f"模式: {'DRY-RUN' if a.dry_run else '实际生成'} | 并行 {a.jobs} | "
        f"模板 {os.path.basename(TEMPLATE)} | agent {os.path.basename(AGENT_FILE)}")
    bad, usage = 0, []
    for k in keys:
        key, verdict, dt, u, ok = run_one(k, tpl, a, a.dry_run)
        usage += u
        if not ok:
            bad += 1
        log(f"  {key}  {verdict}")

    if not a.dry_run:
        # 按篇归集：一次跑多篇时，混在一起的总数没法回答「哪篇最贵」
        per = {}
        for key, name, sid in usage:
            s, n = session_usage([sid])
            if not s:
                continue
            acc = per.setdefault(key, {"req": 0, "inputOther": 0, "output": 0,
                                       "inputCacheRead": 0})
            acc["req"] += n
            for kk in ("inputOther", "output", "inputCacheRead"):
                acc[kk] += s[kk]
        tot = {"req": 0, "inputOther": 0, "output": 0, "inputCacheRead": 0}
        for acc in per.values():
            for kk in tot:
                tot[kk] += acc[kk]
        for key, acc in per.items():
            log(f"  {key} 用量（{acc['req']} 次请求）：输出 {acc['output']:,} · "
                f"新输入 {acc['inputOther']:,} · 缓存读 {acc['inputCacheRead']:,}")
        if per:
            log(f"合计（{tot['req']} 次请求）：输出 {tot['output']:,} · "
                f"新输入 {tot['inputOther']:,} · 缓存读 {tot['inputCacheRead']:,}")
        if a.report_usage:
            for key, acc in per.items():
                log(f"    {key:10s} 输出 {acc['output']:>9,} · 新输入 {acc['inputOther']:>9,} · "
                    f"缓存读 {acc['inputCacheRead']:>11,}")
        log(f"完成：{len(keys) - bad}/{len(keys)} 篇通过。挂 Zotero 链接附件跑 "
            f"python3 scripts/link_markdown.py")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
