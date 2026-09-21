#!/usr/bin/env python3
"""扫描本地文献库，生成「文献画像」——纯 Python 统计，零 API 花销。

产出：
  文献画像.md         人读的中文画像
  state/profile.json  机器读的画像，供 daily_digest.py 分配检索配额与检索式

用法：python3 scripts/build_profile.py
"""
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"

import topic  # 分类法 / 检索式 / 手调偏好都在 config/topic.json（见 scripts/topic.py）

CATS = topic.CATS

# 「近三年」的起始年。以前这里写死 `>= 2024`，过了年就会静默失准（2027 年还算 2024 是
# 「近期」），与画像里「近三年活跃度」这个说法对不上，所以改成按当前年份算。
RECENT_FROM = date.today().year - 2

# 手调偏好（config/topic.json 的 categories.<类名>.pref）：在「库内占比」自动权重
# 之上再乘一个系数，用来把库里篇数少、但你想盯的类顶上去——自动权重按库内篇数算，
# 少的类天然吃亏，日报里这些候选会成片卡在门槛外。系数在自动权重夹到 [0.5, 2.0]
# 之后再乘，本身另有 3.0 的上限；填 1.0 即中性。
CAT_PREF = topic.CAT_PREF

# 每类的检索式（画像只做加权与裁剪，不改写检索语义）
BASE_QUERIES = topic.QUERIES

STOP = set("""a an the of in on for with and or to from by at as via into during
using based toward towards new novel high low via their its this that these those
study studies effect effects role origin mechanism mechanisms insight insights
understanding investigation reveal revealing toward enabling enhanced improved
performance properties materials material battery batteries cathode cathodes
li-ion lithium-ion li ion layered oxide oxides""".split())


def norm_tok(s):
    return re.sub(r"[^a-z0-9\-\s]", " ", (s or "").lower())


def phrases(text, nmin=2, nmax=4):
    """抽 2–4 词英文短语，用作「热点词」。"""
    toks = [t for t in norm_tok(text).split() if t]
    out = []
    for n in range(nmin, nmax + 1):
        for i in range(len(toks) - n + 1):
            g = toks[i:i + n]
            if g[0] in STOP or g[-1] in STOP:
                continue
            if all(t in STOP for t in g):
                continue
            if len(g) == 2 and (g[0] in STOP or g[1] in STOP):
                continue
            out.append(" ".join(g))
    return out


def main():
    cls = json.loads((STATE / "classification.json").read_text(encoding="utf-8"))
    digest = {d["key"]: d for d in json.loads((STATE / "digest.json").read_text(encoding="utf-8"))}
    n = len(cls)

    cat_cnt = Counter(c["primary"] for c in cls)
    sub_cnt, meth_cnt, mat_cnt, form_cnt, sys_cnt = (Counter() for _ in range(5))
    for c in cls:
        sub_cnt.update(c.get("sub") or [])
        meth_cnt.update(c.get("method") or [])
        mat_cnt.update(c.get("materials") or [])
        form_cnt.update(c.get("form") or [])
        sys_cnt.update(c.get("system") or [])

    years = Counter()
    journals = Counter()
    for c in cls:
        d = digest.get(c["key"], {})
        y = re.search(r"(19|20)\d{2}", str(c.get("year") or d.get("year") or ""))
        if y:
            years[int(y.group(0))] += 1
        j = re.split(r"[（(]", str(d.get("journal") or ""))[0].strip()
        if j and j not in ("原文未给出", "原文未明确", "未给出"):
            journals[j] += 1

    # 热点词：全库频次 + 近三年 vs 更早的突现度
    recent_txt, old_txt, all_txt = [], [], []
    for c in cls:
        d = digest.get(c["key"], {})
        blob = " ".join([c.get("title", ""), d.get("concl", ""), d.get("finding", "")])
        all_txt.append(blob)
        y = re.search(r"(19|20)\d{2}", str(c.get("year") or ""))
        (recent_txt if y and int(y.group(0)) >= RECENT_FROM else old_txt).append(blob)
    freq_all = Counter(p for b in all_txt for p in set(phrases(b)))
    freq_rec = Counter(p for b in recent_txt for p in set(phrases(b)))
    freq_old = Counter(p for b in old_txt for p in set(phrases(b)))
    # ⚠️ 排序必须带短语本身做**次级键**。上面三个 Counter 是把 `set(phrases(b))` 喂进去
    # 建的，而 set 的迭代顺序受字符串哈希随机化影响——同一份输入、两次运行，键的插入
    # 顺序就不同；只用 -count 排序时并列项的顺序随之漂移，实测 hot_phrases 两次跑出来
    # 不一样（画像的 queries 由 hot 生成，漂移会顺带传到日报的检索式）。加上短语做
    # 次级键之后才是确定性的。
    _by_freq = lambda cnt: sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
    hot = [p for p, k in _by_freq(freq_all)[:200] if k >= 3][:40]
    emerging = [p for p, k in _by_freq(freq_rec)
                if k >= 3 and freq_old.get(p, 0) <= 1][:15]

    # 分类权重：库内占比 70% + 近三年活跃度 30%，夹到 [0.5, 2.0]，再乘手调偏好
    # CAT_PREF——偏好不受那个夹子限制，另有 3.0 的顶，否则一抬就被截平。
    weight = {}
    for cat in CATS:
        share = cat_cnt.get(cat, 0) / max(n, 1)
        rec = sum(1 for c in cls if c["primary"] == cat
                  and re.search(r"(19|20)\d{2}", str(c.get("year") or ""))
                  and int(re.search(r"(19|20)\d{2}", str(c["year"])).group(0)) >= RECENT_FROM)
        rec_share = rec / max(sum(1 for c in cls
                                  if re.search(r"(19|20)\d{2}", str(c.get("year") or ""))
                                  and int(re.search(r"(19|20)\d{2}", str(c["year"])).group(0)) >= RECENT_FROM), 1)
        auto = min(2.0, max(0.5, 0.7 * share * len(CATS) + 0.3 * rec_share * len(CATS)))
        weight[cat] = round(min(3.0, auto * CAT_PREF.get(cat, 1.0)), 3)

    # 检索式：基础式 + 由热点词生成的扩写（零成本；当前热点词质量差，基本不生效）
    queries = {}
    for cat in CATS:
        qs = list(BASE_QUERIES[cat])
        cap = max(6, len(qs))          # 基础式不许被 [:6] 截掉
        for p in hot:
            if len(qs) >= cap:
                break
            if any(w in p for w in ("cathode", "layered", "oxygen", "interphase", "crack",
                                    "diffusion", "strain", "redox")):
                qs.append(p)
        queries[cat] = qs[:cap]

    # ══════════════════════════════════════════════════════════════════════
    # 画像第 5–7 节的数据：时间趋势 / 值得注意的发现 / 被引与推荐分
    # 三个外部数据源都是**可选**的——缺了就把对应那段降级成一句说明，
    # 不让画像整体失败（它是每月定时跑的，缺一个文件不该什么都不出）。
    # ══════════════════════════════════════════════════════════════════════
    errors = {}

    def load_json(name, default):
        """读一个可选的外部数据文件：**不存在**返回 default，**读不出来**返回 default 并记下原因。

        「文件缺了」与「文件坏了」必须分开——否则第 7 节会把两种情形都写成
        「跑 citation_graph.py 后重出」，而真正的问题是那个文件已经损坏，
        重跑生成脚本前得先修好或删掉它（errors 里的键就是坏掉的文件名）。
        """
        try:
            return json.loads((STATE / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as e:
            errors[name] = f"{type(e).__name__}: {e}"
            print(f"⚠ {name} 读不出来（{type(e).__name__}: {e}），本次按缺失处理")
            return default

    def year_of(c):
        m = re.search(r"(19|20)\d{2}", str(c.get("year") or digest.get(c["key"], {}).get("year") or ""))
        return int(m.group(0)) if m else None

    def clean_title(t):
        """清掉标题里的 <sub>/<sup> 标记与多余空白。

        classification.json 存的是**原始标题**，化学式的上下标会带 HTML 标记
        （实测 `Nanoscale In<sub>2</sub>O<sub>3</sub>`），直接进 Markdown 表格会很难看。
        """
        # 只删标记 + 折叠空白，得到「In 2 O 3」这种样子。试过「连同标记两侧空白一起删」
        # 以还原 In2O3，但源文里标记两侧的空白不可靠（实测 `O<sub>3</sub>␣␣␣Induced`），
        # 那种写法会把词粘成 `O3Induced`——宁可多空格，不可粘连。184 篇里只有 1 篇受影响。
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t or "")).strip()

    # 时间趋势：出版年 × 一级分类
    year_cat = defaultdict(Counter)
    cat_recent, cat_old = Counter(), Counter()
    for c in cls:
        y = year_of(c)
        if not y:
            continue
        year_cat[y][c["primary"]] += 1
        (cat_recent if y >= RECENT_FROM else cat_old)[c["primary"]] += 1
    y_years = sorted(year_cat)
    n_dated = sum(year_cat[y].total() for y in y_years)
    n_recent = sum(cat_recent.values())
    median_year = None
    if y_years:
        acc, half = 0, n_dated / 2
        for y in y_years:
            acc += year_cat[y].total()
            if acc >= half:
                median_year = y
                break
    # 综述分布
    rev_cnt = Counter(c["primary"] for c in cls if c.get("is_review"))

    # 方法 × 分类：找出「某类里完全缺席」的常用方法
    cat_meth = defaultdict(Counter)
    for c in cls:
        for m in (c.get("method") or []):
            cat_meth[c["primary"]][m] += 1
    top_meth = [m for m, _ in meth_cnt.most_common(6)]

    # 语料自查：重复条目（DOI 相同 / 标题归一化相同）
    def norm_t(t):
        return re.sub(r"[^a-z0-9]", "", (t or "").lower())
    li = load_json("library_index.json", {})
    doi_of, seen_doi, seen_t = {}, {}, {}
    dup_doi, dup_t = [], []
    for x in (li.get("items") or []):
        k, d, t = x.get("key"), (x.get("doi") or "").lower(), norm_t(x.get("title"))
        if k and d:
            doi_of[k] = d
        if d:
            if d in seen_doi:
                dup_doi.append((k, seen_doi[d]))
            else:
                seen_doi[d] = k
        if t:
            if t in seen_t:
                dup_t.append((k, seen_t[t]))
            else:
                seen_t[t] = k
    lib_dois = set(doi_of.values())

    # 引文网络：每篇被几篇库内文献引用 / 引用了几篇库内文献
    cg = load_json("citation_graph.json", {})
    co = cg.get("co_citation") or {}
    by_citer = cg.get("by_citer") or {}
    cited_in, cites_in = {}, {}
    for c in cls:
        d = doi_of.get(c["key"])
        cited_in[c["key"]] = (co.get(d) or {}).get("n", 0) if d else 0
        cites_in[c["key"]] = sum(1 for r in (by_citer.get(d) or []) if r in lib_dois) if d else 0
    isolated = [c for c in cls if not cited_in.get(c["key"]) and not cites_in.get(c["key"])]

    # 推荐分（scripts/score_library.py 的产出；缺了就跳过第 9 节）
    sc_items = (load_json("library_scores.json", {}) or {}).get("items") or {}
    sc_vals = [v["score"] for v in sc_items.values() if isinstance(v.get("score"), (int, float))]
    sc_by_cat = defaultdict(list)
    key2cat = {c["key"]: c["primary"] for c in cls}
    for k, v in sc_items.items():
        if isinstance(v.get("score"), (int, float)) and k in key2cat:
            sc_by_cat[key2cat[k]].append(v["score"])

    profile = {
        "generated": time.strftime("%F %T"),
        "library_size": n,
        "categories": {c: cat_cnt.get(c, 0) for c in CATS},
        "weights": weight,
        "queries": queries,
        "hot_phrases": hot,
        "emerging_phrases": emerging,
        "sub_tags": dict(sub_cnt.most_common()),
        "methods": dict(meth_cnt.most_common()),
        "materials": dict(mat_cnt.most_common()),
        "form_tags": dict(form_cnt.most_common()),
        "system_tags": dict(sys_cnt.most_common()),
        "journals": dict(journals.most_common(40)),
        "years": {str(k): v for k, v in sorted(years.items())},
        # 以下供第 5–7 节与其它脚本读取
        "recent_from": RECENT_FROM,
        "year_by_cat": {str(y): dict(year_cat[y]) for y in y_years},
        "cat_cited_in": {c: sum(cited_in.get(k, 0) for k, v in key2cat.items() if v == c)
                         for c in CATS},
        "scores": {k: round(v["score"], 3) for k, v in sc_items.items()
                   if isinstance(v.get("score"), (int, float))},
    }
    (STATE / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

    L = []
    L.append("# 文献画像\n")
    L.append(f"> 扫描时间 {profile['generated']}　|　库内 {n} 篇　|　本文件由 `scripts/build_profile.py` 生成，建议每月自动刷新\n")
    L.append("\n## 1. 分类分布与检索配额\n")
    L.append("| 一级分类 | 篇数 | 占比 | 检索权重 | 手调偏好 |\n|---|---:|---:|---:|---:|\n")
    w_max = max(weight.values())
    for c in CATS:
        L.append(f"| {c} | {cat_cnt.get(c,0)} | {cat_cnt.get(c,0)/max(n,1)*100:.0f}% | "
                 f"{weight[c]:.2f} | ×{CAT_PREF.get(c, 1.0):.1f} |\n")
    L.append("\n**日报 R 实际用的系数（w/w_max）**：" + "、".join(
        f"{c} {weight[c]/w_max:.2f}" for c in CATS) + "\n")
    L.append("\n> 检索权重 = （库内占比 70% + 近三年活跃度 30%，夹到 [0.5, 2.0]）× "
             "手调偏好 `CAT_PREF`（`build_profile.py` 顶部，上限 3.0）。R 用归一化后的 "
             "w/w_max，判断调权效果要看上面那行系数。\n")
    L.append("\n## 2. 子类标签热度\n")
    L.append("| 子类标签 | 篇数 |\n|---|---:|\n")
    for k, v in sub_cnt.most_common():
        L.append(f"| {k} | {v} |\n")
    L.append("\n## 3. 方法、材料、形态与体系\n")
    L.append("| 方法 | 篇数 |\n|---|---:|\n")
    for k, v in meth_cnt.most_common():
        L.append(f"| {k} | {v} |\n")
    L.append("\n| 材料体系 | 篇数 |\n|---|---:|\n")
    for k, v in mat_cnt.most_common():
        L.append(f"| {k} | {v} |\n")
    L.append("\n| 形态 | 篇数 |\n|---|---:|\n")
    for k, v in form_cnt.most_common():
        L.append(f"| {k} | {v} |\n")
    L.append("\n| 体系（只标非默认） | 篇数 |\n|---|---:|\n")
    for k, v in sys_cnt.most_common() or [("（无）", 0)]:
        L.append(f"| {k} | {v} |\n")
    L.append("\n## 4. 年份与期刊\n")
    L.append("年份分布：" + "、".join(f"{k} 年 {v} 篇" for k, v in sorted(years.items())) + "\n")
    L.append("\n常见期刊（前 20）：\n\n")
    for k, v in journals.most_common(20):
        L.append(f"- {k}（{v}）\n")
    # 「热点词」「新兴方向」两节 2026-09-20 已去掉：phrases() 不滤数字与单位，
    # 抽出来的是 `mah g` / `3 v` / `4 3` / `0 5` 这类碎片，登在画像里是噪声。
    # （hot / emerging 的计算暂时保留——hot 还接着检索式扩写那条路，
    #   详见 AGENTS.md 里的说明。）
    # ── 5. 时间趋势 ──────────────────────────────────────────────────────
    L.append("\n## 5. 时间趋势\n")
    if not y_years:
        L.append("（解析不出任何出版年）\n")
    else:
        recent_years = [y for y in y_years if y >= RECENT_FROM]
        L.append(f"中位出版年 **{median_year}**；最早 {y_years[0]}、最新 {y_years[-1]}；"
                 f"近三年（≥{RECENT_FROM}）**{n_recent}** 篇，占能解析年份的 "
                 f"{n_recent / max(n_dated, 1) * 100:.0f}%。\n")
        # 断层只看**近期窗口**：这个库是现代材料学文献，1952–2012 之间的空白是必然的，
        # 全列出来（实测 55 个年份）纯粹是噪声；真正值得看的是近几年有没有断档
        show = [y for y in y_years if y >= max(y_years[-1] - 11, y_years[0])]
        gaps_recent = [y for y in range(show[0], show[-1] + 1) if y not in year_cat]
        if gaps_recent:
            L.append(f"\n⚠️ **近 12 年里的年份断层**："
                     + "、".join(str(g) for g in gaps_recent) + "（这些年份一篇都没有）\n")
        L.append(f"\n近 {len(show)} 年的出版年 × 一级分类（只列有文献的年份）：\n\n")
        L.append("| 出版年 | 合计 | " + " | ".join(CATS) + " |\n")
        L.append("|---:|---:|" + "---:|" * len(CATS) + "\n")
        for y in show:
            L.append(f"| {y} | {year_cat[y].total()} | "
                     + " | ".join(str(year_cat[y].get(c, 0)) for c in CATS) + " |\n")
        L.append("\n**近三年 vs 更早**（看哪个方向在升温）：\n\n")
        L.append("| 一级分类 | 近三年 | 更早 | 近三年占比 | 更早占比 | 走向 |\n")
        L.append("|---|---:|---:|---:|---:|---|\n")
        tot_r, tot_o = max(n_recent, 1), max(n_dated - n_recent, 1)
        for c in CATS:
            r, o = cat_recent.get(c, 0), cat_old.get(c, 0)
            rs, os_ = r / tot_r * 100, o / tot_o * 100
            d = rs - os_
            arrow = "↗ 升温" if d >= 3 else ("↘ 降温" if d <= -3 else "→ 持平")
            L.append(f"| {c} | {r} | {o} | {rs:.0f}% | {os_:.0f}% | {arrow} |\n")
        L.append(f"\n> 上表的「占比」是**该类在近三年/更早两个池子里的份额**，不是该类内部的新旧比；"
                 f"差 ≥3 个百分点才标走向。\n")

    # ── 6. 值得注意的发现（全部由上面的统计推出，不调 LLM）─────────────────
    L.append("\n## 6. 值得注意的发现\n")
    L.append("> 本节是**规则推出来的**结构性事实（集中度、缺项、语料自查），"
             "不是语义判断——「结论互相矛盾」「方法学机会」这类需要读全文的结论得人工写。\n")
    top2 = cat_cnt.most_common(2)
    if not top2:
        # 空库是手册 §1.1 步骤 2 的起点（classification.json 先写成 []），要能出一份「还是空的」画像
        L.append("\n（`state/classification.json` 还是空的，暂无可统计的分类——"
                 "先按手册 §5.4 手工填几条再重出画像。）\n")
    else:
        L.append(f"\n**分类集中度**：{top2[0][0]}（{top2[0][1]}）"
                 + (f"+ {top2[1][0]}（{top2[1][1]}）" if len(top2) > 1 else "")
                 + f"合计占 {sum(v for _, v in top2) / max(n, 1) * 100:.0f}%，"
                 f"最少的 {CATS[-1]} 只有 {cat_cnt.get(CATS[-1], 0)} 篇。"
                 f"权重按占比自动算，少的类天生吃亏——要靠 `CAT_PREF` 手调。\n")
    if rev_cnt:
        L.append(f"\n**综述分布**：{sum(rev_cnt.values())} 篇综述"
                 f"（占 {sum(rev_cnt.values()) / max(n, 1) * 100:.0f}%），"
                 f"其中最集中的是 {rev_cnt.most_common(1)[0][0]}（{rev_cnt.most_common(1)[0][1]} 篇）；"
                 f"没有综述的类："
                 + ("、".join(c for c in CATS if c not in rev_cnt) or "（每类都有）") + "。\n")
    L.append("\n**方法学缺项**（常用方法在哪些类里完全缺席）：\n\n")
    L.append("| 方法 | 全库 | " + " | ".join(CATS) + " |\n")
    L.append("|---:|---:|" + "---:|" * len(CATS) + "\n")
    for m in top_meth:
        L.append(f"| {m} | {meth_cnt.get(m, 0)} | "
                 + " | ".join(("—" if not cat_meth[c].get(m) else str(cat_meth[c][m])) for c in CATS)
                 + " |\n")
    L.append("\n（`—` = 该类一篇都没有。整行没有 `—` 说明这个方法是全库通用的。）\n")
    L.append("\n**语料自查**：\n\n")
    if "library_index.json" in errors:
        L.append(f"- ⚠️ 读不出 `state/library_index.json`（{errors['library_index.json']}）："
                 f"下面的重复项检查不完整，先修好或删掉它、跑 `scripts/library_index.py` 重建\n")
    L.append(f"- 重复 DOI：{len(dup_doi)} 组" +
             ("；" + "、".join(f"{a}/{b}" for a, b in dup_doi[:5]) if dup_doi else "（无）") + "\n")
    L.append(f"- 归一化标题重复：{len(dup_t)} 组" +
             ("；" + "、".join(f"{a}/{b}" for a, b in dup_t[:5]) if dup_t else "（无）") + "\n")
    L.append(f"- 没打 `method` 标签 {sum(1 for c in cls if not c.get('method'))} 篇、"
             f"没打 `materials` {sum(1 for c in cls if not c.get('materials'))} 篇、"
             f"没打 `sub` {sum(1 for c in cls if not c.get('sub'))} 篇"
             f"（分类法覆盖率，不是错误，但会影响标签统计的可读性）\n")
    thin = sorted(CATS, key=lambda c: cat_cnt.get(c, 0))[:2]
    L.append(f"- 覆盖最薄的方向："
             + "、".join(f"{c}（{cat_cnt.get(c, 0)} 篇）" for c in thin)
             + "——若关心这些方向，库内证据明显不足\n")

    # ── 7. 被引与推荐分 ──────────────────────────────────────────────────
    L.append("\n## 7. 被引与推荐分\n")
    if not co:
        # 「文件缺了」与「文件坏了」分开说：后者重跑 citation_graph.py 也盖不掉坏内容
        if "citation_graph.json" in errors:
            L.append(f"（`state/citation_graph.json` 读不出来：{errors['citation_graph.json']}——"
                     f"先修好或删掉它，再跑 `scripts/citation_graph.py` 重建）\n")
        elif (STATE / "citation_graph.json").exists():
            L.append("（`state/citation_graph.json` 里没有共被引数据——库内文献的参考文献"
                     "还没解析出来，跑 `scripts/citation_graph.py` 后重出）\n")
        else:
            L.append("（缺 `state/citation_graph.json`，跑 `scripts/citation_graph.py` 后重出）\n")
    else:
        ranked = sorted(cls, key=lambda c: -cited_in.get(c["key"], 0))
        L.append(f"**被库内引用最多的 10 篇**（本库自引网络的核心；"
                 f"全库 {sum(1 for c in cls if cited_in.get(c['key']))} 篇被至少一篇库内文献引用）：\n\n")
        L.append("| 被库内引 | 分类 | 标题 |\n|---:|---|---|\n")
        for c in ranked[:10]:
            L.append(f"| {cited_in.get(c['key'], 0)} | {c['primary']} | {clean_title(c.get('title'))[:56]} |\n")
        L.append(f"\n**孤立文献**（既没被库内引用、也没引用任何库内文献）："
                 f"**{len(isolated)}** 篇，占 {len(isolated) / max(n, 1) * 100:.0f}%。"
                 f"它们不在库内对话里——可能是新入库还没被引用，也可能是方向偏。\n")
        if isolated:
            L.append("\n<details><summary>孤立文献清单</summary>\n\n")
            for c in isolated[:40]:
                L.append(f"- `{c['key']}`（{c['primary']}）{clean_title(c.get('title'))[:64]}\n")
            if len(isolated) > 40:
                L.append(f"- …另有 {len(isolated) - 40} 篇\n")
            L.append("\n</details>\n")
    if sc_vals:
        bins = [("≥0.6", lambda s: s >= 0.6), ("0.4–0.6", lambda s: 0.4 <= s < 0.6),
                ("0.2–0.4", lambda s: 0.2 <= s < 0.4), ("0–0.2", lambda s: 0 <= s < 0.2),
                ("<0", lambda s: s < 0)]
        L.append(f"\n**推荐分分布**（来自 `scripts/score_library.py`，同时写在 Zotero 的 Extra 里）："
                 f"区间 {min(sc_vals):.3f} – {max(sc_vals):.3f}、"
                 f"中位 {sorted(sc_vals)[len(sc_vals) // 2]:.3f}。\n\n")
        L.append("| 分数档 | 篇数 |\n|---|---:|\n")
        for label, f in bins:
            L.append(f"| {label} | {sum(1 for s in sc_vals if f(s))} |\n")
        L.append("\n各分类的中位推荐分：\n\n")
        L.append("| 一级分类 | 篇数 | 中位推荐分 |\n|---|---:|---:|\n")
        for c in CATS:
            v = sorted(sc_by_cat.get(c, []))
            med = f"{v[len(v) // 2]:.3f}" if v else "—"
            L.append(f"| {c} | {len(v)} | {med} |\n")
        L.append("\n> 库内的分**不含 `R` 与 `S`**（对库内文献无意义），`C/X/J'/A` 重新归一化过，"
                 "与日报的推荐分不是同一个数，别直接比大小。\n")
    else:
        # 手册 §6.7 承诺「两个数据源缺失时整段降级成一句说明」——library_scores.json 这一半
        # 原先直接消失，读的人只会以为画像漏印了一节
        if "library_scores.json" in errors:
            L.append(f"\n（`state/library_scores.json` 读不出来：{errors['library_scores.json']}——"
                     f"先修好或删掉它，再跑 `scripts/score_library.py --apply` 重建）\n")
        elif (STATE / "library_scores.json").exists():
            L.append("\n（`state/library_scores.json` 里还没有分数，"
                     "跑 `scripts/score_library.py --apply` 后重出）\n")
        else:
            L.append("\n（缺 `state/library_scores.json`，"
                     "跑 `scripts/score_library.py --apply` 后重出）\n")

    L.append("\n## 8. 当前每日检索式\n")
    for c in CATS:
        L.append(f"\n**{c}**\n\n")
        for q in queries[c]:
            L.append(f"- `{q}`\n")
    (ROOT / "文献画像.md").write_text("".join(L), encoding="utf-8")

    print(f"文献画像.md 已更新（库内 {n} 篇）")
    for c in CATS:
        print(f"  {c:6s} {cat_cnt.get(c,0):3d} 篇  权重 {weight[c]:.2f}  {len(queries[c])} 条检索式")


if __name__ == "__main__":
    main()
