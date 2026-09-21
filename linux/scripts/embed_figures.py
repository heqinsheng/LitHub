#!/usr/bin/env python3
"""给 summary.md 补一节「关键图表」：按图注与正文挑图，图注中英双语，图片嵌入。

设计要点（都是为了省 token 和防幻觉）：
  - **不让模型读全文**：只给它各图的英文图注 + 正文里引用该图的句子，token 量约为全文的 1/10。
  - **不让模型写图片路径**：模型只输出「图号 / 中文标题 / 图注中文 / 为什么关键」，
    图片路径由本脚本从 paper.md 里解析后填进去 —— 模型编不出来。
  - 模型给的图号必须在 paper.md 里真实存在且有图注，否则该条丢弃（记录下来）。

用法:
  python3 scripts/embed_figures.py --dry-run
  python3 scripts/embed_figures.py <并发> [KEY,KEY...] [--force]
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")
MANIFEST = os.path.join(ROOT, "state", "manifest.json")
KIMI = os.environ.get("LITHUB_KIMI") or os.path.expanduser("~/.kimi-code/bin/kimi")
# 受限 agent（只给 Read/Write）：默认会话把内置工具 + MCP 的全部工具 schema 塞进每次调用，
# 实测首调未缓存输入 35,441 token，换成 prompts/figures.agent.md 后降到 2,429 token。
AGENT_FILE = os.path.join(ROOT, "prompts", "figures.agent.md")

DRY = "--dry-run" in sys.argv
FORCE = "--force" in sys.argv
MARK = "## 关键图表"
# 提示词让模型挑 3–5 张；模型偶尔给更多（实测给过 8 张），脚本按上限裁掉
MAX_FIGURES = 5
# 跑模型用的工作目录放 state/work/：论文目录是 Obsidian 库的一部分，中途 Ctrl-C 不该在
# papers/<key>/ 里留下 figures.context.md / figures.plan.md 这种残骸。每个 key 单独一个子目录，
# 并发跑时彼此不会看到对方的清单（输入文件名是固定的 figures.context.md，见 prompts/figures.agent.md）
WORK = os.path.join(ROOT, "state", "work")

PROMPT = """阅读当前目录下的 figures.context.md。它是某篇论文的**图表清单**：每张图给出图号、
英文图注原文，以及正文中引用该图的句子（可能还有摘要与结论）。

请挑出 **3–5 张**对论文论证最重要的图，把结果写入 figures.plan.md。**最多 5 张**——
多写的会被裁掉；实在挑不出 5 张就写多少算多少，硬性下限是 **2 张**：只挑得出 2 张就写 2 张，
一张都挑不出才算失败。宁少勿滥，但不要少于 2 张。
如果清单里的图不足这个数，就把它们全部选上，不要因此不输出。
格式严格如下，不要写任何多余文字、不要写图片文件名：

=== 图 1 ===
标题: <不超过 20 字的中文标题>
图注中文: <把该图英文图注准确翻译成中文，所有数值与单位照抄，不增删信息。若图注里有乱码
           （`�`、`【?】`、控制字符之类），**不要照抄**，用「（原文此处乱码）」代替该片段>
为什么关键: <一到两句话，说明这张图支撑论文的哪个结论，依据正文对它的引用>

=== 图 3 ===
标题: ...
图注中文: ...
为什么关键: ...

挑选准则：
- **必须至少包含一张机理／总结示意图**（机理图、TOC 图、结构演化示意、能垒图、相图之类）：
  这类图最能说明「作者认为发生了什么」。若全文确实没有，就改选最能概括结论的那张定量图，
  并在「为什么关键」里说明为什么选它。
- 其余名额给承载核心证据的图：决定性实验数据、关键定量结果。
- 跳过方法示意、设备照片、纯补充表征、以及只是被顺带提及的图。
- 「为什么关键」必须能对上正文的论述，不要泛泛而谈「展示了…性能」。
- 图号必须来自 figures.context.md，不要发明不存在的图号。"""


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def parse_figures(d):
    """从 paper.md 解析出 {图号: {'panels':[...], 'caption':英文原文}}。

    注意：正文里「Fig. 8 shows …」这类句子也以 "Fig. 8" 开头，会被同一套正则命中。
    所以对每个图号收集**所有**候选行，取「前面挂了图片最多」的那一行当图注——
    真正的图注前面必有图片，正文句子前面通常是正文。
    """
    path = os.path.join(d, "paper.md")
    if not os.path.exists(path):
        return {}
    lines = open(path, encoding="utf8", errors="replace").read().split("\n")
    cands = {}
    for i, ln in enumerate(lines):
        m = re.match(r"\s*(?:Figure|Fig\.?)\s*(\d+)\s*[.:|｜]?\s+(\S.*)", ln, re.I)
        if not m:
            continue
        n = int(m.group(1))
        panels, j = [], i - 1
        while j >= 0:
            s = lines[j].strip()
            if s.startswith("![]("):
                panels.insert(0, s[4:s.index(")")])
                j -= 1
            elif s == "" or len(s) <= 3:
                j -= 1
            else:
                break
        cands.setdefault(n, []).append({"panels": panels, "caption": " ".join(ln.split())[:900]})

    out = {}
    for n, lst in cands.items():
        best = max(lst, key=lambda c: len(c["panels"]))
        out[n] = {"panels": best["panels"], "caption": best["caption"],
                  "candidate_lines": len(lst),
                  "panels_best": len(best["panels"]),
                  "panels_first": len(lst[0]["panels"])}
    return out


def build_context(d, figs):
    """拼给模型看的上下文：标题 / 摘要 / 结论 / 各图图注 + 正文引用句。"""
    md = open(os.path.join(d, "paper.md"), encoding="utf8", errors="replace").read()
    body = re.sub(r"!\[\]\(images/[^)]+\)", "", md)
    parts = []
    t = re.search(r"^title:\s*\"?(.+?)\"?\s*$", md, re.M)
    if t:
        parts.append(f"# 标题\n{t.group(1)}")
    for name in ("Abstract", "摘要"):
        m = re.search(rf"^#+\s*{name}\s*$([\s\S]{{0,2000}})", md, re.M)
        if m:
            parts.append(f"# 摘要\n{' '.join(m.group(1).split())[:1800]}")
            break
    m = re.search(r"^#+\s*(?:Conclusions?|结论)\s*$([\s\S]{0,2500})", md, re.M)
    if m:
        parts.append(f"# 结论\n{' '.join(m.group(1).split())[:2000]}")
    parts.append("# 图表清单")
    flat = " ".join(body.split())
    usable = [n for n in sorted(figs) if figs[n]["panels"]]
    parts.append(f"（只有下面这 {len(usable)} 张图真正带图片，**只能从它们里选**；"
                 f"正文里可能提到别的图号，那些没有对应图片，选了无效。）")
    for n in usable:
        cap = figs[n]["caption"]
        mentions = re.findall(rf"[^.]{{0,260}}(?:Fig(?:ure)?\.?\s*{n}\b|图\s*{n})[^.]{{0,200}}\.",
                             flat)
        parts.append(f"## Figure {n}\n图注原文：{cap}")
        if mentions:
            parts.append("正文引用：" + " / ".join(dict.fromkeys(mentions))[:900])
    return "\n\n".join(parts)


def parse_plan(text):
    """解析模型输出：[(图号, 标题, 图注中文, 为什么关键)]"""
    out = []
    for block in re.split(r"^===\s*图\s*", text, flags=re.M)[1:]:
        m = re.match(r"(\d+)\s*===", block)
        if not m:
            continue
        n = int(m.group(1))
        rest = block[m.end():]

        def field(name):
            mm = re.search(rf"^{name}\s*[:：]\s*(.+?)(?=^(?:标题|图注中文|为什么关键)\s*[:：]|\Z)",
                           rest, re.M | re.S)
            return " ".join(mm.group(1).split()) if mm else ""
        title, cap, why = field("标题"), field("图注中文"), field("为什么关键")
        if title and why:
            out.append((n, title, cap, why))
    return out


def build_section(items, figs):
    """按脚本自己解析出的面板路径拼 Markdown。"""
    sec = [MARK, "",
           "> 图片取自本文同目录的 `images/`，在 Obsidian 中就地渲染。图的选取依据**图注与正文**",
           "> （本环境没有图像识别能力，未判读图像内容本身）；图注为英文原文，其下附中文翻译。", ""]
    for n, title, cap_cn, why in items:
        sec.append(f"### 图 {n} ｜ {title}")
        sec.append("")
        for p in figs[n]["panels"]:
            sec.append(f"![]({p})")
        sec.append("")
        sec.append(f"> {figs[n]['caption']}")
        sec.append(">")
        sec.append(f"> {cap_cn or '（未给出中文图注）'}")
        sec.append("")
        sec.append(why)
        sec.append("")
    return "\n".join(sec)


def process(rec):
    key = rec["key"]
    dirs = glob.glob(os.path.join(PAPERS, f"{key}_*"))
    if not dirs:
        return key, "无目录", 0, []
    d = dirs[0]
    summ = os.path.join(d, "summary.md")
    if not os.path.exists(summ):
        return key, "无 summary", 0, []
    if not os.path.isdir(os.path.join(d, "images")):
        return key, "无图片", 0, []
    if not FORCE and MARK in open(summ, encoding="utf8", errors="replace").read():
        return key, "skip", 0, []

    figs = parse_figures(d)
    if not figs:
        return key, "解析不到图注", 0, []
    ctx = build_context(d, figs)
    run_dir = os.path.join(WORK, f"figures_{key}")
    plan_path = os.path.join(run_dir, "figures.plan.md")
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir, ignore_errors=True)   # 清掉上次中断留下的残骸
    os.makedirs(run_dir, exist_ok=True)
    open(os.path.join(run_dir, "figures.context.md"), "w", encoding="utf8").write(ctx)

    t0 = time.time()
    if DRY:
        shutil.rmtree(run_dir, ignore_errors=True)
        return key, f"dry({len(ctx)}字上下文,{len(figs)}图)", 0, []

    items = []
    try:
        try:
            subprocess.run([KIMI] + ([f"--agent-file={AGENT_FILE}"]
                                     if os.path.exists(AGENT_FILE) else []) + ["-p", PROMPT],
                           cwd=run_dir, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=2400)
        except subprocess.TimeoutExpired:
            return key, "TIMEOUT", time.time() - t0, []
        if not os.path.exists(plan_path):
            return key, "模型未产出 plan", time.time() - t0, []
        items = parse_plan(open(plan_path, encoding="utf8", errors="replace").read())
    finally:
        # 清单与模型写的 plan 一起清掉；return 走哪条路都会经过这里
        shutil.rmtree(run_dir, ignore_errors=True)

    bad = [n for n, _, _, _ in items if n not in figs or not figs[n]["panels"]]
    items = [(n, t, c, w) for n, t, c, w in items if n in figs and figs[n]["panels"]]
    if not items:
        return key, "无有效图号", time.time() - t0, bad
    # 提示词说的是 3–5 张，但模型照给多少就多少（实测给过 8 张）：超上限的按序裁掉，
    # 保留下来的仍是模型排在前面的那几张
    cut = len(items) - MAX_FIGURES
    if cut > 0:
        items = items[:MAX_FIGURES]

    sec = build_section(items, figs)
    s = open(summ, encoding="utf8", errors="replace").read()
    # --force 重做时必须先摘掉旧的那一节：下面是在「## 机理解释」前插入，旧节也在那个位置，
    # 不删就会两节叠在一起（实测有篇攒了两份「关键图表」）。re.sub 不带 count，
    # 顺带把历史遗留的多份一次性清掉。
    s = re.sub(rf"^{re.escape(MARK)}.*?(?=^## |\Z)", "", s, flags=re.M | re.S)
    if "## 机理解释" in s:
        s = s.replace("## 机理解释", sec + "\n## 机理解释", 1)
    else:
        s = s.rstrip() + "\n\n" + sec
    open(summ, "w", encoding="utf8").write(s)
    return key, f"ok({len(items)}图{',裁掉超限%d张' % cut if cut > 0 else ''})", time.time() - t0, bad


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    workers = int(args[0]) if args else 3
    only = args[1].split(",") if len(args) > 1 else None
    man = json.load(open(MANIFEST, encoding="utf-8"))
    todo = man if not only else [r for r in man if r["key"] in only]
    log(f"待处理 {len(todo)} 篇，并发 {workers}"
        f"{'，DRY-RUN' if DRY else ''}{'，强制重做' if FORCE else ''}")

    stat, badrefs = {}, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process, r): r for r in todo}
        for f in as_completed(futs):
            r = futs[f]
            try:
                key, status, dt, bad = f.result()
            except Exception as e:
                key, status, dt, bad = r["key"], f"EXC({e})", 0, []
            stat[status.split("(")[0]] = stat.get(status.split("(")[0], 0) + 1
            if bad:
                badrefs.append((key, bad))
            log(f"  {key} {status} {dt:.0f}s | {r['title'][:40]}")
    log(f"完成：{stat}")
    if badrefs:
        log(f"模型给了不存在的图号（已丢弃）：{badrefs}")


if __name__ == "__main__":
    main()
