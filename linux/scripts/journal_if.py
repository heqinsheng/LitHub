#!/usr/bin/env python3
"""给 find_new.JOURNALS 白名单期刊查 OpenAlex 的 2 年篇均被引，产出期刊层级表。

指标说明（**重要**）：取的是 OpenAlex source 的
`summary_stats.2yr_mean_citedness`，它是 OpenAlex 自算的代理指标，**不是 JCR 影响
因子**，而且会压扁顶端（实测 Nature ≈ 18.8、Science ≈ 20.9，真实 IF 分别是 50/45
量级）。它只用来做「期刊层级」的相对排序，不要当 IF 引用。

刊名解析复用引文通道那套 ISO 缩写展开（J_ABBR / j_token_match / cited_journal_ok）
——这套逻辑原先在 daily_digest.py 里，现在集中到本模块，daily_digest 从这里 import，
保证 refs 通道的 "J. Power Sources" 与 fresh/query 通道的 "Journal of Power Sources"
命中同一行。

产出：
  state/journal_if.json
    {generated, metric, count, with_if, missing[], kept_old[], not_queried[],
     by_name{白名单全名: IF}, by_norm{归一化全名: IF}, alias{缩写归一化写法: 白名单全名}}
  missing     查不到 IF 的刊（本次查过没有、或没查过且表里也没旧值）
  kept_old    表里原有旧值、本次没拿到新值的刊（沿用旧值，不被清空）
  not_queried 因 429 提前收工而根本没查的刊（missing / kept_old 的子集）
  缓存：state/work/cache_if/<刊名 slug>.json（命中即不重复下载）

用法：
  python3 scripts/journal_if.py             # 增量：只查还没有 IF 的刊
  python3 scripts/journal_if.py --refresh   # 全部重查（会保留旧值，429 提前收工也不清空表）
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
CACHE = STATE / "work" / "cache_if"
OUT = STATE / "journal_if.json"
MAILTO = os.environ.get("LITHUB_MAILTO", "you@example.com")
API = "https://api.openalex.org/sources"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import find_new as fc  # noqa: E402  复用其 User-Agent / 重试 / IPv4 强制


# ------------------------------------------------ 刊名归一化（引文通道与 IF 表共用）
# 引文候选的期刊名是 ISO 缩写（J. Power Sources / Adv. Energy Mater.），而
# find_new.JOURNALS 只收全名，journal_ok 的字母数字比对对不上（实测 759 个已过
# LAYERED_PAT 的候选被这一步误杀）。这里加一层缩写展开，展开结果仍然交给
# fc.journal_ok() 判定——白名单始终是最终权威，不会放宽到白名单之外。
J_ABBR = {
    "j": ["journal"], "adv": ["advanced"], "mater": ["materials", "material"],
    "chem": ["chemistry", "chemical", "chem"], "soc": ["society"],
    "electrochem": ["electrochemical"], "electrochim": ["electrochimica"],
    "int": ["international"], "sci": ["science"], "phys": ["physical"],
    "commun": ["communications", "communication"], "lett": ["letters", "letter"],
    "rev": ["reviews", "review"], "energ": ["energy"],
    "environ": ["environmental", "environment"], "am": ["american"],
    "angew": ["angewandte"], "comput": ["computational"], "sustain": ["sustainable"],
    "eng": ["engineering"], "appl": ["applied"], "nanotechnol": ["nanotechnology"],
    "ed": ["edition"], "edit": ["edition"],
}
# 缩写会丢掉 of / the / & 这类连接词，反查白名单时允许跳过
J_STOP = {"of", "the", "and", "&", "for", "on", "in", "a", "-", "part"}


def j_tokens(name):
    """期刊名 -> 纯字母数字 token 列表。"""
    raw = fc.dashes(name or "").lower()
    return [t for t in (re.sub(r"[^a-z0-9]+", "", x) for x in raw.split()) if t]


def j_forms(tok):
    return J_ABBR.get(tok) or [tok]


def j_variants(toks):
    """把每个 token 展开成全名（Chemistry/Chemical 这类多形态逐个变体）。"""
    out = [""]
    for t in toks:
        out = [f"{p} {f}".strip() for p in out for f in j_forms(t)]
        if len(out) > 64:                      # 防御：变体数量封顶
            out = out[:64]
    return out


def j_token_match(cand, want, max_skip=3):
    """cand 的 token 序列能否对齐白名单条目 want 的 token 序列。

    返回 (跳过几个候选 token, want 的 token 数)，对不上返回 None。
    规则：
      - 同位置的 token 允许互为前缀（已知缩写如 J. / Am. 可以更短）
      - want 里的 of/the/& 等连接词可跳过
      - 候选 token 只有在 want 已对齐完、且自己是已知缩写时才可丢弃（只允许尾部
        丢弃，最多 max_skip 个）——用来兼容 Angew. Chem. Int. Ed. /
        J Mater Chem A Mater 这类「白名单只收短名」的写法。开头和中间一律不许丢，
        否则 Chem. Sci. 会靠丢掉 chem 命中 Science
      - want 用剩的必须都是连接词
      - 至少要真正对上 2 个 token，且对上数 >= 丢弃数：否则 Chem. Sci. 会靠丢掉
        sci、只对上 "Chem" 而混进来
    """
    i, skips, m = 0, 0, 0
    for ct in cand:
        while i < len(want) and want[i] in J_STOP and not j_tok_like(ct, want[i]):
            i += 1
        if i < len(want) and j_tok_like(ct, want[i]):
            i += 1
            m += 1
            continue
        if i >= len(want) and ct in J_ABBR and skips < max_skip:
            skips += 1
            continue
        return None
    if m < 2 or m < skips or not all(t in J_STOP for t in want[i:]):
        return None
    return (skips, len(want))


def j_tok_like(ct, wt):
    """缩写 token 与全名 token 是否算同一个词。"""
    if ct == wt:
        return True
    if not (wt.startswith(ct) or ct.startswith(wt)):
        return False
    return min(len(ct), len(wt)) >= 3 or ct in J_ABBR


def cited_journal_ok(name):
    """引文候选专用期刊闸门：缩写展开后再交给 fc.journal_ok()。

    依次尝试：原样 journal_ok → 展开变体 journal_ok → 按白名单反查补全 of/the
    （多条命中时取「丢弃的 token 最少、白名单名最长」的那条）。
    返回 (是否通过, 匹配到的刊名)，用于日志核对。
    """
    if fc.journal_ok(name):
        return True, fc.clean(name)
    toks = j_tokens(name)
    if not toks:
        return False, None
    for v in j_variants(toks):
        if fc.journal_ok(v):
            return True, fc.clean(v)
    best = None
    for w in fc.JOURNALS:
        r = j_token_match(toks, j_tokens(w))
        if r is None:
            continue
        # 并列时取白名单名更长的：短名更容易被长刊名误配（key 里把长度取负）
        key = (r[0], -r[1])
        if best is None or key < best[0]:
            best = (key, w)
    if best and fc.journal_ok(best[1]):
        return True, best[1]
    return False, None


# ---------------------------------------------------------------------- IF 查表
# 白名单里几个非标准写法：OpenAlex 按字面搜不到、或者会挑到错的那个源，这里显式指过去。
#   Chem Reviews      → Chemical Reviews（白名单用的是简称）
#   Angewandte Chemie → Angewandte Chemie International Edition：OpenAlex 里叫
#     「Angewandte Chemie」的那个源是德文版（2yr_mean_citedness 只有 2.2），而白名单
#     靠前缀匹配把 Int. Ed.（15.3）也归在这个名字下——按字面查会把本领域顶级刊
#     当成低分刊，J' 直接给到 -0.5 的地板。
NAME_ALIAS = {
    "Chem Reviews": "Chemical Reviews",
    "Angewandte Chemie": "Angewandte Chemie International Edition",
}

# OpenAlex 2026 起按请求计费（$0.001/条），匿名池每天 $0.1、额度耗尽返回 429 并给出
# retry-after（实测 reset 到次日 00:00 UTC）。额度没了就别再逐条重试：一次全量刷新
# 60 个刊，每个都睡 200 秒能拖上几小时，必须立刻停下来。
_RATE_LIMITED = {"hit": False, "detail": ""}


def _note_rate_limit(e):
    """记下 429（额度耗尽），供 build() 提前收工。返回是否命中 429。"""
    if isinstance(e, urllib.error.HTTPError) and e.code == 429:
        hdrs = getattr(e, "headers", None)
        _RATE_LIMITED["hit"] = True
        _RATE_LIMITED["detail"] = (hdrs.get("retry-after") if hdrs else "") or ""
        return True
    return False


_MEMO = {"table": None, "path": None}


def load_table(path=None):
    """读 state/journal_if.json（内存里缓存一份，别每个候选都读盘）。"""
    p = Path(path) if path else OUT
    if _MEMO["table"] is not None and _MEMO["path"] == str(p):
        return _MEMO["table"]
    try:
        tbl = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        tbl = {}
    _MEMO["table"], _MEMO["path"] = tbl, str(p)
    return tbl


def if_lookup(journal, table=None):
    """统一入口：任意通道的刊名字符串 -> 2 年篇均被引（查不到返回 None）。

    三条路依次试：
      1. 归一化全名直接命中（fresh/query 通道给的就是全名）
      2. 缩写展开后拿到的白名单全名（refs 通道的 J. Power Sources 之类）
      3. 按白名单反查：白名单条目常只是刊名的前缀——"Angewandte Chemie" ⊂
         "Angewandte Chemie International Edition"，journal_ok 靠「候选以白名单名
         开头」判过，但 by_name 里只有短名，直接查会落空（实测这一步漏掉 Int. Ed.
         全名）。这里用与 journal_ok 相同的规则反查，取最长的命中条目。
    """
    tbl = table if table is not None else load_table()
    if not tbl or not journal:
        return None
    by_norm = tbl.get("by_norm") or {}
    by_name = tbl.get("by_name") or {}
    n = fc.norm(journal)
    if n in by_norm:
        return by_norm[n]
    ok, canon = cited_journal_ok(journal)
    if ok and canon:
        if canon in by_name:
            return by_name[canon]
        cn = fc.norm(canon)
        if cn in by_norm:
            return by_norm[cn]
    hit = None
    for w in fc.JOURNALS:
        wn = fc.norm(w)
        if wn and wn in by_norm and (n == wn or (len(wn) >= 12 and n.startswith(wn))):
            if hit is None or len(wn) > len(hit):
                hit = wn
    return by_norm[hit] if hit else None


def _cached(url, slug, refresh=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / (slug + ".json")
    if p.exists() and not refresh:
        return json.loads(p.read_text(encoding="utf-8")), True
    # tries 压到 2：OpenAlex 额度耗尽时每次要睡 20s×(i+1)，5 次能拖 200 秒
    data = fc.get(url, tries=2, sleep=0.5)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data, False


def fetch_one(name, refresh=False, log=print):
    """查一个刊名，返回 (IF 或 None, 命中的 OpenAlex display_name 或 None, 是否网络)。

    取前 5 条结果再挑最像的那条（OpenAlex 的相关度排序经常把
    Materials Today Proceedings 排在 Materials Today 前面）。
    名字必须能对齐才采用：完全同名，或者「白名单名 ⊂ 返回名」的尾部缩写写法
    （Angewandte Chemie ⊂ Angewandte Chemie International Edition）。
    """
    per = 5
    url = (API + "?filter=display_name.search:" + urllib.parse.quote(name)
           + f"&per_page={per}&select=id,display_name,summary_stats&mailto=" + MAILTO)
    slug = "if%d_" % per + (re.sub(r"\W+", "_", name.lower())[:70] or "x")
    try:
        data, hit = _cached(url, slug, refresh=refresh)
    except Exception as e:
        if _note_rate_limit(e):
            log(f"  ! OpenAlex 额度耗尽（HTTP 429，retry-after "
                f"{_RATE_LIMITED['detail'] or '未给出'} 秒）")
        else:
            log(f"  ! {name}: {type(e).__name__} {e}")
        return None, None, True
    best = None
    for got in (data.get("results") or []):
        got_name = got.get("display_name") or ""
        if fc.norm(got_name) == fc.norm(name):
            rank = (0, 0, len(got_name))
        else:
            r = j_token_match(j_tokens(got_name), j_tokens(name))
            if r is None:
                continue
            rank = (1, r[0], len(got_name))
        if best is None or rank < best[0]:
            best = (rank, got)
    if best is None:
        got_name = ((data.get("results") or [{}])[0]).get("display_name") or ""
        if got_name:
            log(f"  ! {name}: OpenAlex 只返回 {got_name!r}，名字对不上，按查不到处理")
        return None, got_name or None, not hit
    got = best[1]
    val = (got.get("summary_stats") or {}).get("2yr_mean_citedness")
    return (float(val) if val else None), got.get("display_name"), not hit


def build(refresh=False, log=print):
    names, seen = [], set()
    for w in fc.JOURNALS:
        n = fc.norm(w)
        if n and n not in seen:
            seen.add(n)
            names.append(w)
    old = {}
    if OUT.exists():
        # --refresh 也要读旧表：OpenAlex 额度耗尽会提前收工，那时旧值是唯一的底，
        # 不读就会用「前几个刊」的残缺表覆盖整张表
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("by_name") or {}
        except Exception:
            old = {}
    by_name, missing, kept_old, not_queried, n_net = {}, [], [], [], 0
    _RATE_LIMITED["hit"], _RATE_LIMITED["detail"] = False, ""
    for i, w in enumerate(names, 1):
        if w in old and not refresh:
            by_name[w] = old[w]
            continue
        # 白名单里几个非标准写法（Chem Reviews / Angewandte Chemie），OpenAlex 按字面
        # 搜不到、或者会挑到错的那个源，按 NAME_ALIAS 指到的正式刊名去查，仍记在白名单名下
        val, got, net = fetch_one(NAME_ALIAS.get(w, w), refresh=refresh, log=log)
        n_net += int(net)
        if val is None:
            if w in old:      # 本次没查到不代表旧值作废，表里沿用旧的，只标成「沿用」
                by_name[w] = old[w]
                kept_old.append(w)
            else:
                missing.append(w)
        else:
            by_name[w] = round(val, 2)
        if _RATE_LIMITED["hit"]:
            # 收工时剩下的刊一种都还没查：有旧值的沿用，没旧值的记为「查不到」，
            # 否则报告里会让人误以为它们是真的没有
            not_queried = list(names[i:])
            for w2 in not_queried:
                if w2 in old:
                    by_name[w2] = old[w2]
                    kept_old.append(w2)
                else:
                    missing.append(w2)
            log(f"  ! OpenAlex 额度耗尽，在第 {i}/{len(names)} 个刊处收工；"
                f"已查到 {len(by_name)} 种（未查的 {len(not_queried)} 种里 "
                f"{sum(1 for w2 in not_queried if w2 in old)} 种沿用旧值）。"
                f"额度次日 00:00 UTC 重置，重跑本脚本即可补齐")
            break
        if i % 10 == 0 or i == len(names):
            log(f"  {i}/{len(names)} …")
    # 归一化索引 + 缩写别名表（别名的 key 用归一化写法，查表时只做字符串比较）
    by_norm = {fc.norm(k): v for k, v in by_name.items()}
    alias = {}
    for w in by_name:
        for t in j_tokens(w):
            if t in J_ABBR:
                alias.setdefault(t, w)
    return {
        "generated": time.strftime("%F %T"),
        "metric": "OpenAlex summary_stats.2yr_mean_citedness（代理指标，非 JCR 影响因子；"
                  "顶端被压扁：实测 Nature≈18.8 / Science≈20.9）",
        "source": API + "?filter=display_name.search:<刊名>&per_page=5",
        "count": len(names),
        "with_if": len(by_name),
        "missing": missing,
        "kept_old": kept_old,
        "not_queried": not_queried,
        "rate_limited": (_RATE_LIMITED["detail"] if _RATE_LIMITED["hit"] else ""),
        "name_alias": dict(NAME_ALIAS),
        "by_name": dict(sorted(by_name.items())),
        "by_norm": dict(sorted(by_norm.items())),
        "alias": dict(sorted(alias.items())),
    }, n_net


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，全部重查")
    args = ap.parse_args()

    out, n_net = build(refresh=args.refresh)
    STATE.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, OUT)

    print(f"白名单期刊 {out['count']} 种，查到 2 年篇均被引 {out['with_if']} 种"
          f"（命中率 {out['with_if'] / max(out['count'], 1) * 100:.1f}%），"
          f"本次网络请求 {n_net} 次")
    if out["missing"]:
        print(f"查不到的 {len(out['missing'])} 种：{'、'.join(out['missing'])}")
    if out.get("kept_old"):
        print(f"沿用旧值的 {len(out['kept_old'])} 种（本次没拿到新值，表里保留旧值）："
              f"{'、'.join(out['kept_old'])}")
    if out.get("rate_limited"):
        n_un = len(out.get("not_queried") or [])
        print(f"注意：OpenAlex 每日额度已耗尽（retry-after {out['rate_limited']} 秒，"
              f"次日 00:00 UTC 重置），本次是提前收工——有 {n_un} 种刊根本没查过，"
              f"别把它们当成「真的没有」；额度重置后重跑本脚本即可补齐。")
    print(f"-> {OUT.relative_to(ROOT)}（generated {out['generated']}）")
    print("提醒：这是 OpenAlex 代理指标，不是 JCR 影响因子，只用于期刊层级的相对排序。")


if __name__ == "__main__":
    main()
