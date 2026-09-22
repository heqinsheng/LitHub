#!/usr/bin/env python3
"""导出库内全部 top-level journalArticle 的权威 DOI 索引。

DOI 只存在于 Zotero 本地 API，state/ 下其他 json 都没有 doi 字段，所以下游
（citation_graph.py 等）统一以本索引为准。没有 DOI 的条目也保留，标题匹配去重
还要靠它们。

产出：
  state/library_index.json   {generated, count, with_doi, items[]}

用法：
  python3 scripts/library_index.py             # 拉取并写文件
  python3 scripts/library_index.py --dry-run   # 只打印统计，不写文件

幂等：items 按 key 排序，重复运行除 generated 外完全一致。写文件走临时文件 + rename，
避免半截文件被并发读。
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
OUT = STATE / "library_index.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import find_new as fc  # noqa: E402  复用 fc.norm / fc.clean，避免两套文本规则漂移
import zapi  # noqa: E402  Zotero 本地 API 封装（含强制 IPv4 与分页）

YEAR = re.compile(r"(1[89]\d{2}|20\d{2})")


def fetch():
    raw = zapi.get_all("items/top", itemType="journalArticle")
    items = []
    for it in raw:
        d = it.get("data") or {}
        title = fc.clean(d.get("title") or "")
        doi = (d.get("DOI") or "").strip().lower() or None
        m = YEAR.search(d.get("date") or "")
        items.append({
            "key": d.get("key") or it.get("key"),
            "doi": doi,
            "title": title,
            "norm_title": fc.norm(title),
            "year": int(m.group(1)) if m else None,
            "journal": fc.clean(d.get("publicationTitle") or ""),
        })
    items.sort(key=lambda x: x["key"])
    return items


def build():
    items = fetch()
    return {
        "generated": time.strftime("%F %T"),
        "count": len(items),
        "with_doi": sum(1 for x in items if x["doi"]),
        "items": items,
    }


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    # allow_abbrev=False：禁前缀缩写，打错的开关（如 --a / --f）必须报错退出 2，不能当真开关执行
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = ap.parse_args()

    old = load(OUT)
    data = build()

    if args.dry_run:
        print("[dry-run] 不写文件")
    else:
        STATE.mkdir(exist_ok=True)
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, OUT)

    n = data["count"]
    w = data["with_doi"]
    print(f"库内 top-level journalArticle {n} 篇，有 DOI {w} 篇，无 DOI {n - w} 篇")
    if old is None:
        print("无上次索引可比（首次生成）")
    else:
        ak = {x["key"] for x in old.get("items", [])}
        bk = {x["key"] for x in data["items"]}
        new, gone = sorted(bk - ak), sorted(ak - bk)
        print(f"较上次：新增 {len(new)} 篇，消失 {len(gone)} 篇"
              f"（上次 {old.get('count', '?')} 篇 / 有 DOI {old.get('with_doi', '?')}）")
        if new:
            print("  新增 " + " ".join(new[:12]) + (" …" if len(new) > 12 else ""))
        if gone:
            print("  消失 " + " ".join(gone[:12]) + (" …" if len(gone) > 12 else ""))
    if not args.dry_run:
        print(f"-> {OUT.relative_to(ROOT)}（generated {data['generated']}）")


if __name__ == "__main__":
    main()
