#!/usr/bin/env python3
"""补全 Zotero 条目缺失的元数据字段，并把每次补写记档。

条目不少是浏览器抓取或早期手工建的，DOI / 刊名 / 年份 / 卷期页常有空缺
（实测 manifest 内 24 条，其中 2 条连 DOI 都没有）。Zotero 自己补不了，CrossRef 有；
而 summary.md 的 cite 字段（卷期页、DOI）就靠这些字段，所以单独成一步。

规则（宁可不动，也不写可能错的值）：
  - **只补空字段，绝不覆盖已有值**
  - 有 DOI 就按 DOI 取；没有 DOI 才用标题检索，要求标题相似度 ≥0.95
    且作者姓氏有重叠（命中率 ≥50%），否则跳过
  - 多条候选时优先期刊论文（有刊名、非预印本），其次标题相似度
  - 作者名单只在「Zotero 的名单是 CrossRef 名单的严格前缀」时才补齐末尾漏掉的作者，
    顺序或中间有差异一律不动
  - 默认只报告，`--apply` 才写；Zotero 回写成功后，把报告成功的条目（含改前全字段）
    追加进记档，失败的一条都不记

记档 `state/meta_fills.json`：每次补写的日期 / key / 字段的旧值→新值 / 来源 /
相似度 / 受影响条目的完整旧数据（够重建）。追加写，不覆盖历史。

用法:
  python3 scripts/fill_meta.py                 # 只报告（默认扫全库顶层条目）
  python3 scripts/fill_meta.py --apply
  python3 scripts/fill_meta.py --key ABCD1234  # 只处理一条
"""
import argparse
import difflib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zapi

ROOT = os.path.expanduser("~/LitHub")
FILLS = os.path.join(ROOT, "state", "meta_fills.json")
CROSSREF = "https://api.crossref.org/works"
UA = {"User-Agent": os.environ.get("LITHUB_UA",
                                  "LitHub/1.0 (mailto:you@example.com)")}

# 要补的字段（Zotero 字段名）
WANT = ("DOI", "publicationTitle", "date", "volume", "issue", "pages")
SKIP_TYPES = ("attachment", "note", "annotation")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def unesc(s):
    return html.unescape(str(s or "")).replace("\u00a0", " ").strip()


def field(d, *names):
    for n in names:
        v = unesc(d.get(n))
        if v:
            return v
    return ""


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s)


def norm_title(s):
    return re.sub(r"[^0-9a-z]+", "", strip_tags(unesc(s)).lower())


def ratio(a, b):
    return difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def name_of(c):
    return unesc(c.get("name")) or " ".join(
        p for p in (unesc(c.get("firstName")), unesc(c.get("lastName"))) if p)


def norm_name(s):
    return re.sub(r"[^0-9a-z]+", "", unesc(s).lower())


def creator_tokens(d):
    out = set()
    for c in d.get("creators", []):
        out |= {t.lower() for t in re.split(r"[^\w\-]+", name_of(c)) if len(t) > 1}
    return out


def crossref_creators(m):
    return [{"creatorType": "author", "firstName": unesc(a.get("given")),
             "lastName": unesc(a.get("family"))}
            for a in m.get("author", []) if a.get("family")]


def crossref_tokens(m):
    out = set()
    for c in crossref_creators(m):
        out |= {t.lower() for t in re.split(r"[^\w\-]+", name_of(c)) if len(t) > 1}
    return out


# ────────────────────────────────────────────── CrossRef

def crossref_doi(doi):
    try:
        req = urllib.request.Request(f"{CROSSREF}/{urllib.parse.quote(doi)}", headers=UA)
        return json.loads(urllib.request.urlopen(req, timeout=60).read())["message"], ""
    except Exception as e:
        return None, f"按 DOI 查 CrossRef 失败：{e}"


def crossref_title(title, hint):
    q = {"query.bibliographic": f"{strip_tags(title)} {hint}".strip(), "rows": 5}
    try:
        req = urllib.request.Request(f"{CROSSREF}?{urllib.parse.urlencode(q)}", headers=UA)
        items = json.loads(urllib.request.urlopen(req, timeout=60).read())["message"]["items"]
    except Exception as e:
        return None, 0.0, f"标题检索失败：{e}"
    best, best_key = None, (-1,)
    for it in items:
        r = ratio(title, (it.get("title") or [""])[0])
        if r < 0.95:
            continue
        scored = (0 if (it.get("container-title") or [""])[0]
                  and it.get("type") != "posted-content" else 1, r)
        if scored > best_key:
            best, best_key = it, scored
    return best, (best_key[1] if best else 0.0), ("" if best else "标题检索无 0.95 以上命中")


def from_crossref(m):
    parts = ((m.get("published-print") or m.get("published-online")
              or m.get("issued") or {}).get("date-parts") or [[None]])[0]
    # 补零按「滤掉空位后」的下标定：date-parts 里可能夹 null，沿用原下标会补错位
    date = "-".join(f"{p:02d}" if j else str(p)
                    for j, p in enumerate(p for p in parts if p))
    return {
        "DOI": unesc(m.get("DOI")),
        "publicationTitle": unesc((m.get("container-title") or [""])[0]),
        "date": date,
        "volume": unesc(m.get("volume")),
        "issue": unesc(m.get("issue")),
        "pages": unesc(m.get("page") or m.get("article-number")),
    }


def resolve(d):
    """返回 (CrossRef 记录, 来源说明, 标题相似度)；查不到返回 (None, 原因, 0)。"""
    doi = field(d, "DOI")
    if doi:
        m, err = crossref_doi(doi)
        return (m, f"crossref:doi {doi}", 1.0) if m else (None, err, 0.0)
    title = field(d, "title")
    if not title:
        return None, "标题都没有，无从检索", 0.0
    hint = " ".join(name_of(c) for c in d.get("creators", [])[-2:])
    m, r, err = crossref_title(title, hint)
    if not m:
        return None, err, 0.0
    z, cand = creator_tokens(d), crossref_tokens(m)
    if z and len(z & cand) / len(z) < 0.5:
        return None, f"标题命中但作者对不上（{len(z & cand)}/{len(z)}）", r
    note = "（只有预印本记录）" if m.get("type") == "posted-content" else ""
    return m, f"crossref:title r={r:.3f}{note}", r


def creator_fill(d, m):
    """Zotero 的名单纯粹是 CrossRef 名单的前缀（末尾漏作者）时才补齐。"""
    old = [name_of(c) for c in d.get("creators", [])]
    new = crossref_creators(m)
    if not old or len(new) <= len(old):
        return None
    for a, b in zip(old, new):
        if norm_name(a) != norm_name(name_of(b)):
            return None
    return new


# ────────────────────────────────────────────── 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回 Zotero（默认只报告）")
    ap.add_argument("--key", default="", help="只处理这一条")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    items = [i for i in zapi.get_all("items/top")
             if i["data"].get("itemType") not in SKIP_TYPES]
    if a.key:
        items = [i for i in items if i["key"] == a.key]
    log(f"顶层条目 {len(items)} 条 | 模式: {'写入' if a.apply else '只报告'}")

    todo = [(i, [f for f in WANT if not field(i["data"], f)]) for i in items]
    todo = [(i, miss) for i, miss in todo if miss]
    if a.limit:
        todo = todo[:a.limit]
    log(f"有缺口的 {len(todo)} 条")

    fills, blank, failed = [], 0, 0
    for i, miss in todo:
        d = i["data"]
        m, src, r = resolve(d)
        if not m:
            failed += 1
            log(f"  ✗ {i['key']} {src} | {field(d, 'title')[:50]}")
            continue
        rec = from_crossref(m)
        got = {k: v for k, v in rec.items() if k in miss and v}
        new_creators = creator_fill(d, m)
        if not got and not new_creators:
            blank += 1
            log(f"  · {i['key']} CrossRef 记录里也没有缺的字段 | {field(d, 'title')[:44]}")
            continue
        shows = [f"{k}={v}" for k, v in got.items()]
        if new_creators:
            shows.append(f"作者 {len(d.get('creators', []))}→{len(new_creators)} 人")
        log(f"  + {i['key']} {src} → " + ", ".join(shows))
        fields = {k: {"old": field(d, k), "new": v} for k, v in got.items()}
        if new_creators:
            fields["creators"] = {"old": d.get("creators", []), "new": new_creators}
        fills.append({"date": time.strftime("%Y-%m-%d %H:%M:%S"), "key": i["key"],
                      "title": field(d, "title"), "source": src, "match": round(r, 4),
                      "fields": fields, "before": d, "version": i["version"]})
        time.sleep(0.3)

    log(f"可补 {len(fills)} 条，CrossRef 也没有 {blank} 条，查不到 {failed} 条")
    if not a.apply:
        if fills:
            log("预览无误后跑：python3 scripts/fill_meta.py --apply")
        return
    if not fills:
        return

    objs = []
    for f in fills:
        obj = dict(f["before"])
        obj.update({k: v["new"] for k, v in f["fields"].items()})
        obj["key"] = f["key"]
        obj["version"] = f["version"]
        objs.append(obj)
    ok, st, r = zapi.update_items(objs)
    good = (r.get("successful") or {}) if isinstance(r, dict) else {}
    bad = (r.get("failed") or {}) if isinstance(r, dict) else {}
    log(f"Zotero 回写 HTTP {st}：success {len(ok)} / successful {len(good)}，失败 {len(bad)}")
    for idx, err in bad.items():
        log(f"      ✗ {objs[int(idx)]['key']} {str(err)[:200]}")
    done = [fills[int(i)] for i in ok] if ok else []
    if not done:
        log("没有条目报告成功，记档不动——先查上面的响应")
        return
    os.makedirs(os.path.dirname(FILLS), exist_ok=True)  # 新环境里 state/ 可能还没被建出来
    hist = json.load(open(FILLS, encoding="utf-8")) if os.path.exists(FILLS) else []
    json.dump(hist + done, open(FILLS, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log(f"记档 {len(done)} 条 → {FILLS}（含改前全字段）")


if __name__ == "__main__":
    main()
