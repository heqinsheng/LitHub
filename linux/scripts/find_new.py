#!/usr/bin/env python3
"""按本地分类体系检索库里还没有的新文献。

用法：
    python3 scripts/find_new.py [起始日期] [每查询条数] [来源]
    python3 scripts/find_new.py 2025-01-01 40 crossref
    python3 scripts/find_new.py 2025-01-01 25 openalex

来源：crossref（默认，覆盖面广）| openalex（相关度好，但有 429 限流风险）
结果写 state/candidates.json，日志写 logs/find_new.log；原始响应带磁盘缓存，
同参数重跑不再打网络（幂等）。检索式**全部**失败时本轮不写盘（保留上一轮结果）
并以退出码 1 结束。
"""
import difflib
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
LOGS = ROOT / "logs"
CACHE = STATE / "work"
MAILTO = os.environ.get("LITHUB_MAILTO", "you@example.com")

# 本机 IPv6 可达性差，强制 IPv4（见 AGENTS.md）
_orig = socket.getaddrinfo
def _v4(host, port, family=0, *a, **k):
    return [r for r in _orig(host, port, family, *a, **k) if r[0] == socket.AF_INET]
socket.getaddrinfo = _v4

# 每个一级分类一组检索式，尽量覆盖该分类的子类标签
import topic  # 领域配置（检索式 / 闸门正则 / 期刊白名单）都在 config/topic.json

# 检索式按分类分组。daily_digest.py 优先读 state/profile.json（build_profile.py 生成、
# 由画像裁剪过），取不到才退回这里。换研究方向只改 config/topic.json。
QUERIES = topic.QUERIES


def dashes(s):
    """Unicode 连字符/短横统一成 ASCII，否则闸门正则会漏判。"""
    return re.sub(r"[\u2010-\u2015\u2212\u2043\uFE58\uFE63\uFF0D]", "-", s or "")


# 相关性闸门：必须是本主题的体系，且不是邻近的其他体系
# ⚠️ `lini` 一律写成 `\blini(?:o2|[^a-z])`：裸子串会命中 crystallinity / lining，
# 于是任何提到「结晶度」的材料论文都能过闸（实测 2026-09-15 放进来一篇二维硼烯
# 生物医学论文）。这个闸门 daily_digest.py 也会调，改这里两边一起生效。
MUST_LI = topic.MUST_LI
LITHIUM_STRONG = topic.LITHIUM_STRONG
MUST_TOPIC = topic.MUST_TOPIC
EXCLUDE = topic.EXCLUDE

# 摘要级离题词：标题看不出、摘要一看就不是本主题的
OFFTOPIC = topic.OFFTOPIC

# 「基础理论」类必须落回本主题体系
THEORY_TOPIC = topic.THEORY_TOPIC

# 期刊白名单：库内已收 + 领域主流刊。短名（<12 字符）只做全等匹配，
# 避免 "Chem" 之类把 Chemical Engineering Journal 也算进来。
JOURNALS = topic.JOURNALS


def journal_ok(name):
    jn = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    if not jn:
        return False
    for w in JOURNALS:
        w = re.sub(r"[^a-z0-9]+", "", w.lower())
        if jn == w or (len(w) >= 12 and jn.startswith(w)) \
                or (len(jn) >= 12 and w.startswith(jn)):
            return True
    return False


def norm(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def clean(s):
    """剥 JATS/HTML 标签与实体。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&nbsp;", " "), ("&#x2010;", "-"), ("&apos;", "'")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def get(url, tries=5, sleep=0.6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": f"LitHub/1.0 (mailto:{MAILTO})"})
            with urllib.request.urlopen(req, timeout=45) as r:
                time.sleep(sleep)
                return json.load(r)
        except urllib.error.HTTPError as e:
            if i == tries - 1:
                raise
            time.sleep(20 * (i + 1) if e.code == 429 else 3 * (i + 1))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def cached(url, key, sub):
    """带磁盘缓存的取数，幂等可续跑。"""
    d = CACHE / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / (re.sub(r"\W+", "_", key)[:140] + ".json")
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    data = get(url)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def search_crossref(query, since, rows):
    url = ("https://api.crossref.org/works?query.bibliographic="
           + urllib.parse.quote(query)
           + f"&filter=from-pub-date:{since},type:journal-article"
           + "&select=DOI,title,container-title,published,published-online,"
             "abstract,is-referenced-by-count,author,URL"
           + f"&rows={rows}&sort=relevance&order=desc&mailto={MAILTO}")
    data = cached(url, f"{since}_{rows}_{query}", "cache_crossref")
    out = []
    for rank, it in enumerate(data.get("message", {}).get("items", [])):
        dp = ((it.get("published") or it.get("published-online") or {})
              .get("date-parts") or [[""]])[0]
        out.append({
            "title": clean((it.get("title") or [""])[0]),
            "journal": clean((it.get("container-title") or [""])[0]),
            "date": "-".join(f"{x:02d}" if isinstance(x, int) else str(x)
                             for x in dp),
            "year": dp[0] if isinstance(dp[0], int) else None,
            "doi": (it.get("DOI") or "").lower(),
            "cited": it.get("is-referenced-by-count", 0),
            "oa": it.get("URL") or "",
            "authors": [a.get("family", "") for a in (it.get("author") or [])[:6]],
            "abstract": clean(it.get("abstract", "")),
            "rel": max(0.0, 100.0 - rank * (100.0 / max(rows, 1))),
        })
    return out


def rebuild_abstract(inv):
    if not inv:
        return ""
    pos = [(i, w) for w, idx in inv.items() for i in idx]
    pos.sort()
    return " ".join(w for _, w in pos)


def search_openalex(query, since, per_page):
    filt = ",".join([
        "title_and_abstract.search:" + urllib.parse.quote(query),
        f"from_publication_date:{since}",
        "type:article", "language:en",
    ])
    url = ("https://api.openalex.org/works?filter=" + filt
           + f"&per_page={per_page}&sort=relevance_score:desc&mailto={MAILTO}")
    data = cached(url, f"{since}_{per_page}_{query}", "cache_openalex")
    out = []
    for w in data.get("results", []):
        src = (w.get("primary_location") or {}).get("source") or {}
        out.append({
            "title": clean(w.get("title") or ""),
            "journal": clean(src.get("display_name") or ""),
            "date": w.get("publication_date") or "",
            "year": w.get("publication_year"),
            "doi": (w.get("doi") or "").replace("https://doi.org/", "").lower(),
            "cited": w.get("cited_by_count", 0),
            "oa": (w.get("open_access") or {}).get("oa_url") or "",
            "authors": [a["author"]["display_name"]
                        for a in (w.get("authorships") or [])[:6]
                        if a.get("author")],
            "abstract": rebuild_abstract(w.get("abstract_inverted_index")),
            "rel": round(w.get("relevance_score") or 0, 1),
        })
    return out


def crossref_abstract(doi):
    """OpenAlex 缺摘要时回 Crossref 按 DOI 取。"""
    if not doi:
        return ""
    try:
        data = cached("https://api.crossref.org/works/"
                      + urllib.parse.quote(doi),
                      "doi_" + doi, "cache_crdoi")
    except Exception:
        return ""
    return clean((data.get("message") or {}).get("abstract", ""))


def s2_abstract(doi):
    """Semantic Scholar 按 DOI 取摘要，补 Crossref 覆盖不到的 Nature/ACS 等。"""
    if not doi:
        return ""
    key = "s2_" + doi
    d = CACHE / "cache_s2"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (re.sub(r"\W+", "_", key) + ".json")
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    else:
        try:
            data = get("https://api.semanticscholar.org/graph/v1/paper/DOI:"
                       + urllib.parse.quote(doi) + "?fields=title,abstract,venue,year",
                       tries=4, sleep=2.5)
        except Exception:
            data = {}
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return clean(data.get("abstract", "") or "")


def relevant(title, abstract, cat):
    t, a = dashes(title), dashes((abstract or "")[:4000])
    if EXCLUDE.search(t):
        return False
    if OFFTOPIC.search(t) or OFFTOPIC.search(a[:1200]):
        return False
    if re.match(r"\s*(correction|erratum|retraction|editorial|reply to|comment on)",
                t, re.I):
        return False
    # 负极主导的条目，以及不属于层状氧化物正极的材料体系
    if re.search(r"\banode\b", t, re.I) and not re.search(
            r"cathode|positive electrode", t, re.I):
        return False
    if re.search(r"halide cathode|redox-active halide|organic cathode|conversion cathode|"
                 r"prussian|sulfur cathode|li-s battery|polysulfide", t, re.I):
        return False
    if cat == "基础理论":
        # 这一类只收计算方法/理论工作，且必须落回锂电正极体系
        if not THEORY_TOPIC.search(t):
            return False
        if not (MUST_LI.search(t) or MUST_LI.search(a)):
            return False
        if not MUST_LI.search(t) and len(re.findall(r"sodium|na-ion|na ion", a, re.I)) > 3:
            return False
        return bool(re.search(
            r"first-principles|first principles|dft|density functional|ab initio|"
            r"machine learning|neural network|molecular dynamics|monte carlo|"
            r"phase[- ]field|high-throughput|high throughput|computational|"
            r"simulation|modeling|modelling|theoretical|thermodynamic|descriptor|"
            r"screening|potential energy surface|cluster expansion", t, re.I))
    if not (MUST_TOPIC.search(t) or MUST_TOPIC.search(a)):
        return False
    if not (MUST_LI.search(t) or MUST_LI.search(a)):
        return False
    if re.search(r"sodium|na-ion|zinc-ion|potassium-ion", t, re.I):
        return False
    # 标题没有自证锂电、摘要又以钠电为主的，剔除（例如 Na/Li 混标体系）
    if not LITHIUM_STRONG.search(t) and len(re.findall(r"sodium|na-ion|na ion", a, re.I)) > 3:
        return False
    return True


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    per_query = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    source = sys.argv[3] if len(sys.argv) > 3 else "crossref"
    search = (search_crossref if source == "crossref" else search_openalex)

    cls = json.loads((STATE / "classification.json").read_text(encoding="utf-8"))
    lib = [(c["key"], c["title"], norm(c["title"])) for c in cls]
    lib_norm = {n for _, _, n in lib}
    # DOI 精确去重（digest.json 里有 69 篇真 DOI）
    lib_doi = set()
    try:
        for x in json.loads((STATE / "digest.json").read_text(encoding="utf-8")):
            if x.get("doi", "").startswith("10."):
                lib_doi.add(x["doi"].lower())
    except Exception:
        pass

    def in_library(title):
        n = norm(title)
        if not n:
            return None
        if n in lib_norm:
            return "exact"
        if any(n.startswith(l[:60]) or l.startswith(n[:60])
               for _, _, l in lib if len(l) > 30):
            return "prefix"
        for key, _, l in lib:
            if difflib.SequenceMatcher(None, n, l).ratio() >= 0.90:
                return key
        return None

    seen, seen_title, report, dropped = {}, {}, {}, []
    failed = 0
    for cat, queries in QUERIES.items():
        kept = 0
        for q in queries:
            try:
                res = search(q, since, per_query)
            except Exception as e:
                print(f"  ! {cat} / {q}: {e}")
                failed += 1
                continue
            for r in res:
                title = r["title"]
                if not title:
                    continue
                if not journal_ok(r["journal"]):
                    continue
                if r["doi"] and r["doi"] in lib_doi:
                    dropped.append(title)
                    continue
                if in_library(title):
                    dropped.append(title)
                    continue
                if not relevant(title, r["abstract"], cat):
                    continue
                uid = r["doi"] or norm(title)
                tkey = norm(title)
                # 同一篇文章的 Early View / 正式版两条记录（DOI 不同、标题相同）合并
                if tkey in seen_title or uid in seen:
                    rec = seen_title.get(tkey) or seen.get(uid)
                    if rec is not None and cat not in rec["cats"]:
                        rec["cats"].append(cat)
                    continue
                r["cats"] = [cat]
                r["queries"] = [q]
                r["source"] = source
                seen[uid] = r
                seen_title[tkey] = r
                kept += 1
        report[cat] = kept

    tried = sum(len(qs) for qs in QUERIES.values())
    if tried and failed == tried:
        # 检索式全挂（网络断、限流、接口改版）时会得到一个空的 items；照旧写盘
        # 等于把上一轮的全量候选静默清空，调用方只看到「这一类 0 篇」。
        print(f"✗ {failed}/{tried} 条检索式全部失败，本轮不写 state/candidates.json"
              "（上一轮结果保留）")
        sys.exit(1)

    items = sorted(seen.values(), key=lambda r: (r["cats"][0], -r["cited"]))
    # 缺摘要的回 Crossref / Semantic Scholar 按 DOI 兜底
    for r in items:
        if len(r["abstract"]) >= 200 or not r["doi"]:
            continue
        extra, src = crossref_abstract(r["doi"]), "crossref"
        if len(extra) < 200:
            extra = s2_abstract(r["doi"])
            src = "s2"
        if len(extra) > len(r["abstract"]):
            r["abstract"] = extra
            r["abstract_src"] = src
    # 「新」= 当年或更晚。写死年份会让这个 0.15 分在跨年后静默全部落空
    # （2027 年起一篇候选都拿不到），打分刻度跟着漂。
    cur_year = time.localtime().tm_year
    for r in items:
        recency = 1.0 if (r["year"] or 0) >= cur_year else 0.0
        r["score"] = round(0.60 * min(r["rel"], 100) / 100
                           + 0.25 * math.log1p(r["cited"]) / 5
                           + 0.15 * recency, 4)

    (STATE / "candidates.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (STATE / "candidates_meta.json").write_text(json.dumps(
        {"since": since, "per_query": per_query, "source": source,
         "report": report, "total": len(items),
         "with_abstract": sum(1 for r in items if len(r["abstract"]) > 200),
         "run_at": time.strftime("%F %T")}, ensure_ascii=False, indent=1), encoding="utf-8")

    LOGS.mkdir(exist_ok=True)
    with (LOGS / "find_new.log").open("a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n=== {time.strftime('%F %T')} source={source} "
                f"since={since} ===\n")
        for cat, n in report.items():
            f.write(f"{cat}: {n}\n")
        f.write(f"合计 {len(items)}，有摘要 "
                f"{sum(1 for r in items if len(r['abstract']) > 200)}\n")

    for cat, n in report.items():
        print(f"{cat:6s} {n:3d}")
    print(f"合计 {len(items)} -> state/candidates.json "
          f"(source={source})")


if __name__ == "__main__":
    main()
