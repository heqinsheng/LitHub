#!/usr/bin/env python3
"""从 86 篇 summary.md 里抽取紧凑摘要，供分类使用（避免一次性读入全部全文）。"""
import os, re, sys, json

ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")


TBL_ROW = re.compile(r"^\|(.+)\|\s*$")
TBL_SEP = re.compile(r"^\|[\s:\-|]+\|\s*$")


def flatten_tables(body):
    """把 Markdown 表格压成「发现：证据」逐行——摘录清单里表格反而不好读。

    「关键发现」自 2026-09-20 起是两列表格，这里顺手压平；表头行（表格第一行）丢掉。
    """
    out, in_tbl = [], False
    for ln in body.split("\n"):
        s = ln.strip()
        if TBL_SEP.match(s):
            continue
        m = TBL_ROW.match(s)
        if m:
            cells = [c.strip() for c in m.group(1).split("|") if c.strip()]
            if not in_tbl:
                in_tbl = True
                continue
            out.append("：".join(cells))
        else:
            if s:
                in_tbl = False
            out.append(ln)
    return "\n".join(out)


def section(text, name, maxlen=400):
    m = re.search(rf"^##\s*{re.escape(name)}\s*$(.*?)(?=^##\s|\Z)",
                  text, re.S | re.M)
    if not m:
        return ""
    body = flatten_tables(m.group(1).strip())
    body = re.sub(r"\n{2,}", " ", body)
    return body[:maxlen]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "digest"
    rows = []
    for d in sorted(os.listdir(PAPERS)):
        p = os.path.join(PAPERS, d)
        s = os.path.join(p, "summary.md")
        if not os.path.exists(s):
            rows.append({"dir": d, "key": d.split("_")[0], "missing": True})
            continue
        t = open(s, encoding="utf-8", errors="replace").read()
        rows.append({
            "dir": d,
            "key": d.split("_")[0],
            "title": (re.search(r'^title:\s*(.+)$', t, re.M) or [None, d])[1].strip().strip('"'),
            "year": (re.search(r'^year:\s*(.+)$', t, re.M) or [None, "?"])[1].strip(),
            "journal": (re.search(r'^journal:\s*(.+)$', t, re.M) or [None, "?"])[1].strip(),
            "concl": section(t, "一句话结论", 220),
            "method": section(t, "方法与技术路线", 260),
            "finding": section(t, "关键发现", 340),
        })
    if mode == "json":
        json.dump(rows, open(os.path.join(ROOT, "state", "digest.json"), "w"),
                  ensure_ascii=False, indent=1)
        print(f"写入 state/digest.json，{len(rows)} 条")
        return
    done = [r for r in rows if not r.get("missing")]
    miss = [r for r in rows if r.get("missing")]
    print(f"# 已完成 {len(done)} 篇，缺 {len(miss)} 篇\n")
    for r in done:
        print(f"### [{r['key']}] {r['title'][:88]}")
        print(f"{r['year']} | {r['journal']}")
        print(f"- 结论: {r['concl']}")
        print(f"- 方法: {r['method']}")
        print(f"- 发现: {r['finding']}")
        print()
    if miss:
        print("## 缺 summary 的:")
        for r in miss:
            print("  -", r["key"])


if __name__ == "__main__":
    main()
