#!/usr/bin/env python3
"""用 Crossref 参考文献建库内文献的引文图。

读 state/library_index.json 里有 DOI 的条目，按 100 个一批向 Crossref 批量取
reference，产出共被引计数（co_citation）与每条被引作品的元信息（ref_meta），
供下游打分与**零网络初筛**用。

产出：
  state/citation_graph.json
    by_citer      {<引用者 DOI>: [<被引 DOI>...]}
    co_citation   {<被引 DOI>: {n, citers}}        共被引计数
    ref_meta      {<被引 DOI>: {t, j, y}}          t=article-title 或
                   unstructured 前 200 字符，j=journal-title，y=年份；
                   取不到分别给 "" / "" / null；同 DOI 多条取字段最全的一条
  缓存：state/work/cache_citations/<批次内容 sha1>.json（命中即不重复下载）

ref_meta 完全来自 reference 条目自带字段，不需要额外请求；daily_digest.py 的
引文通道靠它先把几千个候选筛到几百个，再批量取元数据。

用法：
  python3 scripts/citation_graph.py             # 增量：只处理尚未建图的 DOI
  python3 scripts/citation_graph.py --rebuild   # 丢弃旧图，全量重建（缓存仍复用）

Crossref 调用形式是踩坑后的实测结论，不要改：
  /works?filter=doi:A,doi:B&select=DOI,reference&rows=200&mailto=...
  - 必须重复 doi: 键做 OR；写成 doi:A,B 会 400（pair-list-form-invalid）
  - 单篇路由 /works/{doi} 不支持 select，也会 400
  - 每批 <=100 个 DOI（URL 约 37 字符/DOI），rows >= 批内 DOI 数
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
CACHE = STATE / "work" / "cache_citations"
INDEX = STATE / "library_index.json"
OUT = STATE / "citation_graph.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import find_new as fc  # noqa: E402  复用其 User-Agent / 重试 / 限速 / 磁盘缓存与 IPv4 强制

MAILTO = os.environ.get("LITHUB_MAILTO", "you@example.com")
API = "https://api.crossref.org/works"
SELECT = "DOI,reference"
BATCH = 100
ROWS = 200
# DOI 里可能出现、但会破坏 query string 的字符（实测库内 DOI 均不含，防御性编码）
HOSTILE = str.maketrans({c: urllib.parse.quote(c, safe="")
                         for c in "&#?+% \t"})

# reference 条目的 year 可能是 "2010" / "2010a" / "in press" 等，只认四位年
YEAR = re.compile(r"(1[89]\d{2}|20\d{2})")

NET = {"fetch": 0, "cache": 0}


def ref_meta_of(r):
    """从一条 Crossref reference 条目里抽被引作品信息（零网络）。

    t 优先 article-title，退回 unstructured 前 200 字符；取不到给 ""。
    """
    t = fc.clean(r.get("article-title") or "")
    if not t:
        t = fc.clean(r.get("unstructured") or "")
    m = YEAR.search(str(r.get("year") or ""))
    return {
        "t": t[:200],
        "j": fc.clean(r.get("journal-title") or ""),
        "y": int(m.group(1)) if m else None,
    }


def meta_score(m):
    """字段齐全度：t / j / y 各算一分，用于同 DOI 多条目时择优。"""
    return bool(m.get("t")) + bool(m.get("j")) + (m.get("y") is not None)


def merge_meta(dst, src):
    """把 src 合并进 dst，同一 DOI 取字段更全的一条（已有的不丢）。"""
    for d, m in src.items():
        cur = dst.get(d)
        if cur is None or meta_score(m) > meta_score(cur):
            dst[d] = m
    return dst


def batch_url(dois):
    filt = ",".join("doi:" + d.translate(HOSTILE) for d in dois)
    return f"{API}?filter={filt}&select={SELECT}&rows={ROWS}&mailto={MAILTO}"


def fetch_batch(dois):
    """按批次内容取缓存；未命中才打网络。"""
    key = hashlib.sha1(",".join(dois).encode()).hexdigest()[:24]
    p = CACHE / f"{key}_{len(dois)}.json"
    if p.exists():
        NET["cache"] += 1
        return json.loads(p.read_text(encoding="utf-8"))
    data = fc.get(batch_url(dois))
    NET["fetch"] += 1
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return data


def parse(data, want):
    """-> (by_citer, ref_meta, n_ref_entries, n_no_doi, missing)"""
    by_citer, seen_citers, ref_meta = {}, set(), {}
    n_ref = n_no_doi = 0
    for it in (data.get("message") or {}).get("items") or []:
        citer = (it.get("DOI") or "").strip().lower()
        if citer not in want:
            continue
        refs, uniq = [], set()
        for r in it.get("reference") or []:
            n_ref += 1
            rd = (r.get("DOI") or "").strip().lower()
            if not rd:
                n_no_doi += 1
                continue
            merge_meta(ref_meta, {rd: ref_meta_of(r)})
            if rd in uniq:      # 同一引用者重复引用同一作品只算一次
                continue
            uniq.add(rd)
            refs.append(rd)
        by_citer[citer] = sorted(refs)
        seen_citers.add(citer)
    return by_citer, ref_meta, n_ref, n_no_doi, want - seen_citers


def rebuild_co(by_citer):
    co = {}
    for citer in sorted(by_citer):
        for cited in by_citer[citer]:
            co.setdefault(cited, set()).add(citer)
    return {d: {"n": len(cs), "citers": sorted(cs)} for d, cs in sorted(co.items())}


def main():
    # allow_abbrev=False：禁前缀缩写，打错的开关（如 --a / --f）必须报错退出 2，不能当真开关执行
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--rebuild", action="store_true", help="全量重建，丢弃旧图")
    args = ap.parse_args()

    # os.path.relpath 而不是 Path.relative_to：后者在路径不在 ROOT 下时会抛，
    # 报错信息自己再炸一次就没有调试价值了
    rel = os.path.relpath(INDEX, ROOT)
    if not INDEX.exists():
        raise SystemExit(f"缺 {rel}——先跑 python3 scripts/library_index.py 生成")
    try:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"读 {rel} 失败（{type(e).__name__} {e}）"
                         "——重跑 python3 scripts/library_index.py 生成")
    lib_dois = sorted({x["doi"] for x in index["items"] if x.get("doi")})
    n_lib_no_doi = sum(1 for x in index["items"] if not x.get("doi"))

    old = None
    if not args.rebuild and OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            old = None
    by_citer = dict((old or {}).get("by_citer") or {})
    ref_meta = dict((old or {}).get("ref_meta") or {})
    n_ref_entries = int((old or {}).get("n_ref_entries") or 0)
    n_no_doi = int((old or {}).get("n_no_doi") or 0)

    todo = [d for d in lib_dois if d not in by_citer]
    print(f"库内有 DOI {len(lib_dois)} 篇"
          + ("（全量重建）" if args.rebuild else f"，已建图 {len(by_citer)} 篇")
          + f"，本次处理 {len(todo)} 篇")
    if not todo:
        print("无新增，跳过网络请求")

    missing = []
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            data = fetch_batch(chunk)
        except Exception as e:
            print(f"  ! 批次 {i // BATCH + 1}（{len(chunk)} DOI）失败，跳过："
                  f"{type(e).__name__} {e}")
            continue
        got, got_meta, nr, nnd, miss = parse(data, set(chunk))
        by_citer.update(got)
        merge_meta(ref_meta, got_meta)
        n_ref_entries += nr
        n_no_doi += nnd
        if miss:                      # Crossref 没有的 DOI 记为已处理，避免每次重试
            for d in miss:
                by_citer[d] = []
        missing += sorted(miss)
        print(f"  批次 {i // BATCH + 1}: {len(chunk)} DOI -> "
              f"{len(got)} 篇有数据，引文 {nr} 条")

    co_citation = rebuild_co(by_citer)
    in_lib_edges = sum(1 for d in co_citation if d in set(lib_dois))

    out = {
        "generated": time.strftime("%F %T"),
        "n_citers": len(by_citer),
        "n_ref_entries": n_ref_entries,
        "n_no_doi": n_no_doi,
        "n_cited_unique": len(co_citation),
        "by_citer": {k: by_citer[k] for k in sorted(by_citer)},
        "co_citation": co_citation,
        "ref_meta": {k: ref_meta[k] for k in sorted(ref_meta)},
    }
    STATE.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, OUT)

    print(f"本次处理 DOI {len(todo)} 个（网络 {NET['fetch']} 次 / 缓存命中 {NET['cache']} 次）")
    n_meta_t = sum(1 for m in ref_meta.values() if m.get("t"))
    print(f"引文条目 {n_ref_entries}，唯一被引 {len(co_citation)}，"
          f"无 DOI 条目 {n_no_doi}，库内互引 {in_lib_edges} 个被引 DOI")
    print(f"ref_meta {len(ref_meta)} 条，其中 title 非空 {n_meta_t} 条"
          f"（journal 非空 {sum(1 for m in ref_meta.values() if m.get('j'))} 条，"
          f"year 非空 {sum(1 for m in ref_meta.values() if m.get('y') is not None)} 条）")
    print(f"引用者 {len(by_citer)} 篇（库内无 DOI 跳过 {n_lib_no_doi} 篇）")
    if missing:
        print(f"Crossref 无记录的 DOI {len(missing)} 个：{' '.join(missing[:5])}"
              + (" …" if len(missing) > 5 else ""))
    print(f"-> {OUT.relative_to(ROOT)}（generated {out['generated']}）")


if __name__ == "__main__":
    main()
