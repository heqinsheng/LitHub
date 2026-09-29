#!/usr/bin/env python3
"""给「全文翻译（含批注）」模块取数：库内引用关系 + 被引量 + 作者 h 指数。

**为什么要有这个脚本**：翻译一篇全文时，开头那几节（库内定位 / 作者与团队）要的
全是「查得到的事实」。让模型自己联网查一遍既慢又费 token，而且同一篇查两次结论
还可能不一样。这里把三件事一次算好并缓存：

  1. **库内引文关系**（零网络）——谁引了它、它引了库内哪些、共被引多少；数据取自
     `state/citation_graph.json` 与 `state/library_index.json`，与日报的 `C` 项同源；
  2. **被引量**——Crossref 的 `is-referenced-by-count`（免费，日报的 `X` 项也用它）；
  3. **作者 h 指数 / 发文量**——Semantic Scholar 作者接口（免费）。

输出是 markdown，可直接粘进 `全文翻译（含批注）.md` 开头；模型只负责把事实写成句子。

⚠️ **库内引文数据是快照**：`citation_graph.json` 不入库新文献就不会更新，先跑
`python3 scripts/library_index.py && python3 scripts/citation_graph.py` 再跑本脚本。

用法:
  python3 scripts/paper_context.py ND2Z2J4Q [...]
  python3 scripts/paper_context.py --json ND2Z2J4Q
  python3 scripts/paper_context.py --refresh ND2Z2J4Q      # 忽略缓存重新联网
"""
import argparse, datetime, json, os, sys, time, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 只为那两件事 import：强制 IPv4 补丁（本机 IPv6 100% 丢包时 Python 会死等超时）
# 与 stdout 编码兜底（标题里的 Å / ö 会撞 Windows cp936）。zapi 在 import 阶段不发请求。
import zapi

ROOT = os.path.expanduser("~/LitHub")
STATE = os.path.join(ROOT, "state")
CACHE = os.path.join(STATE, "work", "paper_context_cache.json")
TTL_DAYS = 7

CROSSREF = "https://api.crossref.org/works/"
S2 = "https://api.semanticscholar.org/graph/v1/paper/DOI:"
S2_FIELDS = ("title,year,citationCount,influentialCitationCount,"
             "authors.name,authors.hIndex,authors.paperCount,authors.affiliations")


def _get_json(url, timeout=30, tries=5):
    """带退避的 GET。

    Semantic Scholar 的匿名池经常在第一个请求上就回 429（同一个 key 隔几秒再试就通），
    不重试的话库里会出现「S2 全为 —」的结果，而那是**取数失败**不是「没有 h 指数」——
    报告里读起来一模一样，是最坏的一种静默。5 次退避（3/6/9/12 秒）覆盖实测到的抖动。
    """
    mailto = os.environ.get("LITHUB_MAILTO", "you@example.com")
    req = urllib.request.Request(url, headers={
        "User-Agent": f"LitHub/1.0 (mailto:{mailto})",
        "Accept": "application/json"})
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
            wait = int(e.headers.get("Retry-After") or 0) or 3 * (i + 1)
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            wait = 3 * (i + 1)
        time.sleep(wait)
    raise last


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _cache_read():
    return _load(CACHE, {})


def _cache_write(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)


def _fresh(entry):
    try:
        t = datetime.datetime.fromisoformat(entry["fetched"])
    except (KeyError, ValueError, TypeError):
        return False
    return (datetime.datetime.now() - t).days < TTL_DAYS


def crossref_one(doi):
    """Crossref 元数据。取不到（404 / 网络）返回 None —— 调用方不要编造被引量。"""
    try:
        m = _get_json(CROSSREF + urllib.request.quote(doi, safe=""))["message"]
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, KeyError):
        return None
    return {
        "cited_by": m.get("is-referenced-by-count"),
        "type": m.get("type"),
        "journal": (m.get("container-title") or [None])[0],
        "year": (m.get("published", {}).get("date-parts") or [[None]])[0][0],
        "title": (m.get("title") or [None])[0],
        "authors": [f"{a.get('given','')} {a.get('family','')}".strip()
                    for a in (m.get("author") or [])],
    }


def s2_one(doi):
    """Semantic Scholar：h 指数只在作者接口给。

    ⚠️ 它的作者名是**归并过的**（`Jingyu Lu` → `Jing Lu`），同名归并错误因此有可能：
    报告里同时打印原始名与 S2 名，姓名对不上时人工核一下。

    返回 None 表示**这个 DOI 不在 S2 里**（老论文、非英文刊常见），与「取数失败」是两回事：
    前者可以照写「S2 未收录」，后者不能——把两者都印成「—」会让下游把「没有」写成「没查到」。
    """
    try:
        d = _get_json(S2 + urllib.request.quote(doi, safe="") + "?fields=" + S2_FIELDS)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"missing": True}
        return None
    except (urllib.error.URLError, ValueError):
        return None
    return {
        "cited_by": d.get("citationCount"),
        "influential": d.get("influentialCitationCount"),
        "authors": [{"name": a.get("name"), "h": a.get("hIndex"),
                     "papers": a.get("paperCount"),
                     "affil": " / ".join(a.get("affiliations") or [])}
                    for a in (d.get("authors") or [])],
    }


def net(doi, cache, refresh):
    if not refresh and doi in cache and _fresh(cache[doi]):
        return cache[doi]
    entry = {"fetched": datetime.datetime.now().isoformat(timespec="seconds"),
             "crossref": crossref_one(doi), "s2": s2_one(doi)}
    # 只有两边都取到才落缓存：把「取数失败」按 TTL 缓存 7 天，等于让一次网络抖动变成
    # 一周内所有报告里的「S2 —」。失败不落盘，下次跑自然会再试一遍（很便宜）。
    if entry["crossref"] is not None and entry["s2"] is not None:
        cache[doi] = entry
    else:
        print(f"! {doi} 取数不完整（Crossref {'ok' if entry['crossref'] else 'fail'} / "
              f"S2 {'ok' if entry['s2'] else 'fail'}），不写缓存", file=sys.stderr)
    return entry


def lib_maps():
    """一次读盘，三张表：DOI→条目、key→条目、key→分类。"""
    items = _load(os.path.join(STATE, "library_index.json"), {}).get("items", [])
    by_doi = {(i.get("doi") or "").lower(): i for i in items if i.get("doi")}
    by_key = {i["key"]: i for i in items if i.get("key")}
    cls = {c["key"]: c for c in _load(os.path.join(STATE, "classification.json"), [])
           if isinstance(c, dict) and c.get("key")}
    return by_doi, by_key, cls


def lib_edges(doi, by_doi):
    """库内引文关系，零网络。返回 (谁引了它, 它引了库内哪些)。"""
    cg = _load(os.path.join(STATE, "citation_graph.json"), {})
    by_citer = cg.get("by_citer", {})
    d = doi.lower()
    citers = [i for i, v in by_citer.items() if d in [x.lower() for x in v]]
    cites = [i for i in by_citer.get(doi, []) if i.lower() in by_doi]
    return citers, cites


def _v(x):
    """把 None 显示成 —，好让「没有这个值」和「取数失败」不至于都读成 0。"""
    return "—" if x is None else x


def _label(doi, by_doi, cls):
    it = by_doi.get(doi.lower())
    if not it:
        return f"[{doi}]"
    c = cls.get(it["key"], {})
    # `sub` / `secondary` 都可能是列表（一篇文章挂多个子类），`primary` 是单值字符串。
    def _j(v):
        return "/".join(map(str, v)) if isinstance(v, (list, tuple)) else (v or "")
    tag = " · ".join(x for x in (_j(c.get("primary")), _j(c.get("sub"))) if x)
    bits = " · ".join(str(x) for x in (it.get("year"), it.get("journal"), tag) if x)
    return f"[{it['key']}] {(it.get('title') or '')[:78]}（{bits}）"


def render(key, entry, by_doi, by_key, cls):
    it = by_key.get(key)
    if not it:
        return f"## {key}\n\n! `state/library_index.json` 里没有这个 key —— 先跑 `library_index.py`。\n"
    doi = it.get("doi") or ""
    cr, s2 = entry.get("crossref") or {}, entry.get("s2") or {}
    c = cls.get(key, {})

    L = [f"## {key} 库内定位", ""]
    L.append(f"- **元数据**：{(cr.get('title') or it.get('title') or '')[:100]} · "
             f"{cr.get('journal') or it.get('journal') or '—'} · "
             f"{cr.get('year') or it.get('year') or '—'} · doi:{doi or '—'}")
    L.append(f"- **类型**：{'综述' if c.get('is_review') else '研究论文'}"
             f"（Crossref: {cr.get('type') or '—'}）")

    if not doi:
        L.append("- **被引量/引文关系**：（该条目没有 DOI，Crossref 与引文图都查不到）")
        return "\n".join(L) + "\n"

    cited_cr = cr.get("cited_by")
    s2_missing = bool(s2.get("missing"))
    cited_s2 = "未收录" if s2_missing else _v(s2.get("cited_by"))
    ok = entry.get("crossref") is not None and entry.get("s2") is not None
    L.append(f"- **被引量**：Crossref {_v(cited_cr)}（权威）· Semantic Scholar {cited_s2}"
             f"（滞后） · 取数日 {entry.get('fetched', '')[:10] or '—'}"
             + ("" if ok else " ⚠️ 本次有源取数失败，见 stderr"))

    citers, cites = lib_edges(doi, by_doi)
    L.append("")
    L.append(f"**库内引用本篇（{len(citers)} 篇）**——被库内谁引了：")
    L += [f"- {_label(x, by_doi, cls)}" for x in citers] or ["- 无"]
    L.append("")
    L.append(f"**本篇引用的库内文献（{len(cites)} 篇）**——它引了库内谁：")
    L += [f"- {_label(x, by_doi, cls)}" for x in cites] or ["- 无"]

    authors = cr.get("authors") or it.get("authors") or []
    s2a = s2.get("authors") or []
    L.append("")
    L.append(f"**作者与 h 指数**（Crossref 顺序，共 {len(authors)} 位）"
             + ("——⚠️ S2 未收录本篇，h 指数取不到" if s2_missing
                else "（S2 失败时全为 —）"))
    for i, a in enumerate(authors):
        hit = s2a[i] if i < len(s2a) else {}
        name_s2 = hit.get("name") or ""
        warn = "" if (not name_s2 or name_s2.split()[-1].lower() == a.split()[-1].lower()) else \
            f"  ← S2 匹配为「{name_s2}」，需人工核"
        L.append(f"- {i+1}. {a} — h={_v(hit.get('h'))}（发文 {_v(hit.get('papers'))}）"
                 f"{(' · ' + hit['affil']) if hit.get('affil') else ''}{warn}")
    L.append("")
    L.append("<!-- h 指数来自 Semantic Scholar 的作者归并，可能同名误配；"
             "通讯作者以原文星标为准。 -->")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(
        description="取一篇库内文献的库内引用关系、被引量与作者 h 指数（零 LLM）",
        allow_abbrev=False)
    ap.add_argument("keys", nargs="+", help="Zotero 条目 key（8 位）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON 而不是 markdown")
    ap.add_argument("--refresh", action="store_true", help=f"忽略 {TTL_DAYS} 天缓存，重新联网")
    a = ap.parse_args()

    if os.environ.get("LITHUB_MAILTO", "you@example.com") == "you@example.com":
        print("! 未设 LITHUB_MAILTO，Crossref 走公共池（本脚本不走 Unpaywall，"
              "不会 422，但礼貌池更快）", file=sys.stderr)

    by_doi, by_key, cls = lib_maps()
    if not by_key:
        print("! state/library_index.json 是空的 —— 先跑 python3 scripts/library_index.py",
              file=sys.stderr)

    cache = _cache_read()
    out = {}
    for k in a.keys:
        doi = (by_key.get(k) or {}).get("doi") or ""
        entry = net(doi, cache, a.refresh) if doi else {"fetched": ""}
        out[k] = {"doi": doi, **entry}
    _cache_write(cache)

    if a.json:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        for k in a.keys:
            print(render(k, out[k], by_doi, by_key, cls))


if __name__ == "__main__":
    main()
