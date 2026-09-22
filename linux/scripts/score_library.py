#!/usr/bin/env python3
"""给库内文献算「推荐分」，并写进 Zotero 条目的 Extra 字段（Extra 是一个原生列）。

与日报**同一套公式**（2026-09-21 起）
------------------------------------
日报的推荐分是
    B = w_r·R + w_s·S + w_c·C + w_x·X + w_j·J' + w_a·A   （六项已归一化到和为 1）
    final = (B + w_o·O)·(1 + f_gain·F) × 种子加成 × 综述 0.9
其中两项对库内文献只能取**边界值**——不是「没有值」，是它们的定义在库内就落在这里：

  R  检索排名   —— 库内文献不在任何检索结果集里，取 **0**（没有排名信息）
  S  库内相似度 —— 「与库内文献的最大相似度」对库内文献本身**恒等于 1**（它和自己最像）

而 **O（出度）只有库内有**：候选自己的参考文献表我们没抓（`by_citer` 的键全是库内条目），
所以它是库内这一侧独有的加分项，见下面「七个分量」。

所以这里**直接调用** `daily_digest.final_score()`，权重也**直接用** `scoring_weights(True)`，
只把 `w_o·O` 当参数传进去，不再把 C/X/J'/A 重新归一化到 1。一句话读得出来：

    推荐分 = 「假设这篇是候选、且语义完全贴合我的库，日报会给它多少分」+ 出度加分

好处是**两边共用一张权重表**：改 `config/runtime.json` 的 `scoring` 段，日报与推荐分一起动。
在此之前（≤2026-09-20）这里走的是「置 0 + 归一化」，量纲与日报接近但**不是同一个数**
（实测区间 −0.069 – 0.672，对不上日报的 0.047 – 0.520），两边没法直接比大小。

⚠️ 代价：`S=1` 那一项会跟着新鲜度倍数一起放大，**新文献被抬得更多**——这符合本库一贯
不给年龄扣分的口径。换成同一套公式后排序会变（实测 54 篇位移 >10 位）。

七个分量（都复用 daily_digest 的实现，保证与日报同源、不会两套公式漂移）：

  R   固定 0（见上）
  S   固定 1（见上）
  C   被多少篇库内文献引用（本地数据，零网络）
  X   绝对被引量（Crossref `is-referenced-by-count`，按周期缓存）
  J'  期刊层级（state/journal_if.json 的 OpenAlex 代理 IF）
  A   是否开放获取（Unpaywall，有缓存）
  O   出度：这篇的参考文献里**落在库内**的比例（本地数据，零网络）
  F   新鲜度 exp(-age/180)，只做加成

⚠️ 出度必须用**比例**而不是条数：k 是落在库内的篇数、R 是参考文献表条数，直接用 k 会被
「谁的参考文献表长」支配（实测出度与 R 的相关系数 +0.406；`NRUU8JZJ` 的 23/48 与
`2JN6LTZQ` 的 22/277 条数几乎一样，但后者只是表长）。取
`O = min(1, (k / max(R, o_ref_floor)) / o_sat)`，两个参数与 `w_o` 都在 runtime.json。

它**不并进上面那套归一化**，是加法：`w_o·O` 加在括号里（`final_score(idx, sc, bonus=)`）。
并进归一化集合会把现有六项的权重按比例压小，等于给 `O=0` 的那 29 篇（PAW / PBE 这类
方法学骨架）净扣分——它们不该因为「不引用你的库」而掉分。

**零 token 花费**——全程不调 LLM，只读本地数据 + 发免费接口请求。

写进哪里
--------
条目的 `extra` 字段里的一行 `推荐分: 0.312`。**Extra 是 Zotero 的原生列**：
在条目列表表头右键 → 列 → 勾上「Extra」就能看到，点表头还能排序。写成**定长三位小数**
（0.312 / 1.400 都是 5 字符）是为了让同一量级内的字符串排序等于数值排序——现在的上界是
`(1+w_o)×(1+f_gain)×种子倍数 ≈ 1.9`（分数可以过 1.0 了），仍是 5 字符。

2026-09-21 起换成「与日报同一套公式」+ 归一化 + 出度 O 后**分数恒为正**
（实测区间 0.217 – 0.625、中位 0.404），所以 Extra 列的字面排序与数值序一致。
**在那之前**这里允许负分（实测 −0.069 – 0.669）：
`-0.100` 是 6 字符且 `-` 的 ASCII 序在数字之后，负数之间的字符串序与数值序**相反**——
如果你在旧数据里看到负数，那几条的排序要认数字、别认列的字面顺序。

标签（tag）做不到这件事：标签不是列，纯文本标签根本不会出现在条目列表里。
真正的「自定义列」要装插件（Zotero 只给插件开了 ItemTreeManager.registerColumn）。

用法:
  python3 scripts/score_library.py              # 只报告（默认，不写任何东西）
  python3 scripts/score_library.py --apply      # 写 state/library_scores.json + Zotero 的 Extra
  python3 scripts/score_library.py --top 30     # 换报告里显示前几名
  python3 scripts/score_library.py --key XXXXXXXX   # 只看一条

幂等：Extra 里那行**已经等于**本次算出的值就不写；一次 184 条约 4 个请求（本地 API 上限
50/请求）。所以可以每天跑、也可以每次收编后顺手跑。
"""
import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_digest as dd        # noqa: E402  打分公式与网络工具的唯一来源，避免两套漂移
import zapi                      # noqa: E402  Zotero 本地 API（含写入）
from journal_if import if_lookup  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
LOGS = ROOT / "logs"
SCORES = STATE / "library_scores.json"
LABEL = "推荐分"                   # Extra 里的键名；改名会让旧行残留在 Extra 里，见 AGENTS.md
LINE_PAT = re.compile(r"^\s*" + re.escape(LABEL) + r"\s*[:：]")

# 出度 O 的三个参数（runtime.json 的 scoring 段）。与日报那六个权重不同，w_o **不参与
# 归一化**，是加在括号里的加分，所以分数上限从 1.0 抬到 1.0 + w_o。
_SC = dd._rt.SCORING
W_O, O_REF_FLOOR, O_SAT = _SC["w_o"], _SC["o_ref_floor"], _SC["o_sat"]


def upsert_line(extra, val):
    """把 `推荐分: <val>` 插进 Extra：同名的旧行**原地替换**，没有就放最前面。

    Zotero 自己合并 Extra 也是「同键原地替换」（combineExtraFields），所以这里照做，
    结果是幂等的、不会堆重复行。放最前面是为了让 Extra 列里一眼就能看到分数。
    重复行（历史遗留、手工粘的）顺手合并成一行。
    """
    out, done = [], False
    for ln in (extra or "").splitlines():
        if LINE_PAT.match(ln):
            if not done:
                out.append(f"{LABEL}: {val}")
                done = True
            continue
        out.append(ln)
    if not done:
        out.insert(0, f"{LABEL}: {val}")
    return "\n".join(out).strip("\n")


def current_line(extra):
    """Extra 里现有的推荐分值（没写过返回 None）。"""
    for ln in (extra or "").splitlines():
        if LINE_PAT.match(ln):
            return ln.split(":", 1)[1].strip() if ":" in ln else ln.split("：", 1)[1].strip()
    return None


def load_library(log):
    """读库内索引与分类/引文图，拼出 citation_term 与出度 O 需要的联表。

    四个输入缺一不可（C 要联索引、引文图与分类三张表，O 要索引 + 引文图），而它们由不同
    脚本产出——裸 FileNotFoundError 说不清该先跑哪个，所以这里逐个查并报出产出者。
    """
    need = (("library_index.json", "scripts/library_index.py"),
            ("citation_graph.json", "scripts/citation_graph.py"),
            ("classification.json", "帮助手册.md §5.4 手工撰写，冷启动可先 "
                                    "`echo '[]' > state/classification.json` 占位"))
    missing = [(n, how) for n, how in need if not (STATE / n).exists()]
    if missing:
        raise SystemExit("缺输入文件，先补上：" +
                         "；".join(f"state/{n} ← {how}" for n, how in missing))
    idx = json.loads((STATE / "library_index.json").read_text(encoding="utf-8"))
    cg = json.loads((STATE / "citation_graph.json").read_text(encoding="utf-8"))
    cls = json.loads((STATE / "classification.json").read_text(encoding="utf-8"))

    items = []
    doi2key, doi_year = {}, {}
    for x in idx.get("items", []):
        if not x.get("key"):
            continue
        items.append(x)
        if x.get("doi"):
            d = x["doi"].lower()
            doi2key[d] = x["key"]
            doi_year[d] = x.get("year")
    key2primary, is_review = {}, {}
    for c in cls:
        if c.get("key"):
            if c.get("primary"):
                key2primary[c["key"]] = c["primary"]
            is_review[c["key"]] = bool(c.get("is_review"))

    # 出度 O 的原料：by_citer 的键是**引用者 DOI**，值是它的参考文献 DOI 列表
    # （只含带 DOI 的那些、且同篇重复引用已去重）。k = 落在库内的篇数，R = 列表长度。
    # 库里 193 篇全在 by_citer 里；R=0 的几篇是 Crossref 没给 reference 数组的那几篇
    # （`citation_graph.py` 对取不到的 DOI 也写一个空表，避免每次重试）。
    by_citer = cg.get("by_citer") or {}
    lib_doi = set(doi2key)
    key2out, n_no_refs = {}, 0
    for x in items:
        refs = by_citer.get((x.get("doi") or "").lower()) or []
        if not refs:
            n_no_refs += 1
        key2out[x["key"]] = (sum(1 for r in refs if r in lib_doi), len(refs))

    weights = {}
    pf = STATE / "profile.json"
    if pf.exists():
        weights = json.loads(pf.read_text(encoding="utf-8")).get("weights") or {}
    log(f"  库内 {len(items)} 篇；分类联表 {len(key2primary)} 条；"
        f"引文图 {len(cg.get('co_citation') or {})} 个被引 DOI；分类权重 {len(weights)} 类")
    log(f"  出度：引文图覆盖 {len(items) - n_no_refs}/{len(items)} 篇"
        + (f"；{n_no_refs} 篇的参考文献表为空（R=0 → O=0，引文图抓不到该篇参考文献）"
           if n_no_refs else ""))
    return items, cg.get("co_citation") or {}, doi2key, doi_year, key2primary, is_review, \
        weights, (max(weights.values()) if weights else 1.0), key2out


def library_weights():
    """**日报那一套权重原样返回**（含 S；`scoring_weights` 已把 R 的那份扣给 S）。

    库内文献的 R 固定 0、S 固定 1，所以直接套日报的 `final_score()` 就得到同一把尺子；
    不再把 C/X/J'/A 归一化到 1。两边共用一张权重表，改 `scoring` 段时同步生效。
    """
    return dd.scoring_weights(True)


def main():
    ap = argparse.ArgumentParser(
        # allow_abbrev=False：禁前缀缩写，打错的开关（如 --a / --f）必须报错退出 2，
        # 不能当真开关执行
        allow_abbrev=False,
        description="给库内文献算推荐分并（--apply 时）写进 Zotero 的 Extra 字段。"
                    "权重取自 config/runtime.json 的 scoring 段。")
    ap.add_argument("--apply", action="store_true",
                    help="写 state/library_scores.json 并把分数写进 Zotero 的 Extra")
    ap.add_argument("--top", type=int, default=20, help="报告里列前几名（默认 20）")
    ap.add_argument("--key", default="", help="只看某一条（Zotero key）")
    a = ap.parse_args()

    LOGS.mkdir(exist_ok=True, parents=True)
    lines = []

    def log(msg):
        print(msg)
        lines.append(msg)

    def flush():
        with (LOGS / "score_library.log").open("a", encoding="utf-8",
                                               errors="replace") as f:
            f.write("\n".join(lines) + "\n")

    # 注意：下面传给 freshness() / citation_term() 的 today_d 必须是 date 对象
    # （daily_digest 里就是 date.fromisoformat 的结果），传字符串会 TypeError
    today_d = date.today()
    log(f"=== {time.strftime('%F %T')} 库内文献打分 ===")
    for w in dd._rt.WARNINGS:
        log(f"  ! 配置警告：{w}")

    (items, co, doi2key, doi_year, key2primary, is_review,
     weights, w_max, key2out) = load_library(log)

    # ── X：被引量。批量取（100 个 DOI 一批），按 pool.refresh_days 的周期桶缓存；
    # 这是唯一的新增网络开销。周期必须与日报读同一个配置，否则两边缓存键不同桶，
    # 同一批元数据会被取两遍
    tag = dd.period_tag(today_d, dd._rt.POOL["refresh_days"])
    dois = [x["doi"].lower() for x in items if x.get("doi")]
    meta = dd.fetch_doi_meta(dois, log, tag=tag)
    log(f"  Crossref 批量取被引量：{len(meta)}/{len(dois)} 篇命中（缓存键周期 {tag}）")

    sc = library_weights()
    log("  权重（与日报同一套，库内取 R=0、S=1）："
        + "、".join(f"{k}={v:.3f}" for k, v in sc.items() if v)
        + f"；外加出度 O×{W_O:g}（不参与归一化，加在括号里）")

    # 读一次 Zotero 条目：既拿 date 字段算新鲜度（比索引里只有年份准），
    # 也拿到 version 供 --apply 写回用；只读，报告模式也需要。
    live = {it["data"]["key"]: it["data"]
            for it in zapi.get_all("items", itemType="journalArticle")}
    log(f"  Zotero 里取到 {len(live)} 个 journalArticle 条目")

    scored = []
    seeds = dd.seed_keys()      # 种子集：自身推荐分加成，见 runtime.json 的 seed 段
    n_seed = 0
    n_no_doi = n_no_jif = n_oa = 0
    n_o_pos = n_o_zero = n_o_sat = 0
    for x in items:
        key = x["key"]
        doi = (x.get("doi") or "").lower()
        cited = int((meta.get(doi) or {}).get("cited") or 0)
        if not doi:
            n_no_doi += 1
        jif = if_lookup(x.get("journal"))
        if not jif:
            n_no_jif += 1
        # 日期优先用 Zotero 条目的 date（有具体月日），没有才退回索引里的年份
        dstr = str((live.get(key) or {}).get("date") or x.get("year") or "")
        F, _age = dd.freshness(dstr, today_d)
        C, _nw, _ny = dd.citation_term(
            (co.get(doi) or {}).get("citers") or [], weights, w_max,
            doi2key, key2primary, doi_year, today_d,
            tau_years=dd.C_AGE_TAU_Y, age_floor=dd.C_AGE_FLOOR)
        # A 走 Unpaywall（oa_info 自带缓存，首轮慢、之后零网络）
        is_oa = dd.oa_info(doi)[2] if doi else False
        n_oa += 1 if is_oa else 0
        # 出度 O：它的参考文献里落在库内的**比例**（不是条数，见文件头）。k 与 R 由
        # load_library 从 by_citer 算好；分母下限 o_ref_floor 兜住参考文献表很短的篇
        k_ref, n_ref = key2out.get(key, (0, 0))
        O = min(1.0, (k_ref / max(n_ref, O_REF_FLOOR)) / O_SAT)
        n_o_pos += 1 if O > 0 else 0
        n_o_zero += 1 if O == 0 else 0
        n_o_sat += 1 if O >= 1.0 else 0
        # R=0（库内文献没有检索排名）、S=1（它与自己最像）——见文件头「同一套公式」一节
        r = {"R": 0.0, "S": 1.0, "C": C, "X": dd.classic_term(cited),
             "J": dd.journal_term(jif), "A": 1.0 if is_oa else 0.0, "F": F,
             "cls": {"is_review": is_review.get(key, False)}}
        # w_o·O 是归一化之外的加分，加在括号里（新鲜度倍数之内），所以跟着 F 与综述折扣放大
        final = dd.final_score(r, sc, bonus=W_O * O)
        is_seed = key in seeds
        if is_seed:
            final *= dd._rt.SEED["score_mult"]
            n_seed += 1
        scored.append({"key": key, "doi": doi or None, "title": x.get("title") or key,
                       "year": x.get("year"), "journal": x.get("journal"),
                       "score": final, "cited": cited, "is_oa": is_oa,
                       "n_citers": len((co.get(doi) or {}).get("citers") or []),
                       "C": r["C"], "X": r["X"], "J": r["J"], "A": r["A"], "F": r["F"],
                       "O": O, "k_ref": k_ref, "n_ref": n_ref, "seed": is_seed})

    scored.sort(key=lambda z: -z["score"])
    log(f"  算完 {len(scored)} 篇；{n_no_doi} 篇没有 DOI（X/C 按 0）、"
        f"{n_no_jif} 篇查不到期刊 IF（J'=0，中性）、{n_oa} 篇开放获取（A=1）、"
        f"种子 {n_seed} 篇（×{dd._rt.SEED['score_mult']:g} 加成）")
    log(f"  出度：O>0 的 {n_o_pos} 篇（其中比例顶到饱和的 {n_o_sat} 篇）、O=0 的 {n_o_zero} 篇"
        f"（w_o={W_O:g}、饱和比例 {O_SAT:g}、分母下限 {O_REF_FLOOR}）")

    if a.key:
        for z in scored:
            if z["key"] == a.key:
                log(f"  {a.key}: 推荐分 {z['score']:.3f}  C={z['C']:.3f} X={z['X']:.3f} "
                    f"J'={z['J']:+.3f} F={z['F']:.2f} O={z['O']:.3f}（引库内 {z['k_ref']}/"
                    f"{z['n_ref']}）  被引={z['cited']} 被库内引={z['n_citers']}  "
                    f"{z['title'][:60]}")
                break
        else:
            log(f"  ! 库里没有 key={a.key}")
        flush()
        return 0

    log(f"\n  推荐分前 {min(a.top, len(scored))} 名：")
    for z in scored[:a.top]:
        log(f"    {z['score']:.3f}  C={z['C']:.2f} X={z['X']:.2f} J'={z['J']:+.2f} "
            f"O={z['O']:.2f} 被引={z['cited']:<5} 库内引={z['n_citers']:<3} "
            f"{z['title'][:58]}")
    if scored:
        log(f"\n  分数区间 {scored[-1]['score']:.3f} – {scored[0]['score']:.3f}，"
            f"中位 {scored[len(scored) // 2]['score']:.3f}")

    if not a.apply:
        log("\n  （只报告，没写任何东西。要写 state/library_scores.json 与 Zotero 的 Extra：加 --apply）")
        flush()
        return 0

    # ── 写本地记档。`O` 是出度那一段（与 `seed` 一样只为复核；k/R 只在 --key 的输出里
    # 打印，不落盘——落盘字段按 AGENTS.md 的白名单来，别顺手加）。
    SCORES.write_text(json.dumps(
        {"generated": time.strftime("%F %T"), "label": LABEL,
         "weights": {k: round(v, 4) for k, v in sc.items()},
         "n": len(scored),
         "items": {z["key"]: {kk: z[kk] for kk in
                              ("doi", "title", "year", "journal", "score", "cited",
                               "is_oa", "n_citers", "C", "X", "J", "A", "F", "O",
                               "seed")}
                         for z in scored}},
        ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    log(f"  已写 state/library_scores.json（{len(scored)} 篇）")

    # ── 写 Zotero 的 Extra（live 是上面读过的条目，含 version）
    todo, n_same, n_missing = [], 0, 0
    for z in scored:
        d = live.get(z["key"])
        if not d:
            n_missing += 1
            continue
        # 定长三位小数：正分下字符串序=数值序（负分不满足，见模块 docstring）
        val = f"{z['score']:.3f}"
        if current_line(d.get("extra")) == val:
            n_same += 1
            continue
        todo.append({"key": z["key"], "version": d["version"],
                     "extra": upsert_line(d.get("extra"), val)})
    log(f"  需更新 {len(todo)} 篇；已是最新 {n_same} 篇"
        + (f"；Zotero 里找不到 {n_missing} 篇" if n_missing else ""))

    # 判成功看响应里的 successful（HTTP 200 也可能是 unchanged 的静默失败），
    # 与 apply_to_zotero.py / migrate_labels.py 同一套写法
    ok = bad = 0
    for i in range(0, len(todo), 25):          # 本地 API 上限 50/请求，与其它脚本一样取 25
        chunk = todo[i:i + 25]
        _succ, st, r = zapi.update_items(chunk)
        succ = len(r.get("successful", {})) if isinstance(r, dict) else 0
        ok += succ
        bad += max(0, len(chunk) - succ)
        if st != 200 or succ < len(chunk):
            log(f"    ! 批次 {i // 25 + 1}: HTTP {st} 成功 {succ}/{len(chunk)}，"
                f"细节 {str(r)[:220]}")
        time.sleep(0.5)
    log(f"  {'✅' if not bad else '⚠️'} Extra 写入完成：成功 {ok} 篇"
        + (f"，失败 {bad} 篇" if bad else "")
        + f"（在 Zotero 里勾上「Extra」列即可看到 {LABEL}）")
    flush()
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
