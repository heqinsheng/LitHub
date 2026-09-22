#!/usr/bin/env python3
"""从 papers/ 下各篇 summary.md 里抽取紧凑摘要，供分类使用（避免一次性读入全部全文）。"""
import os, re, sys, json

# ── 控制台编码兜底 ────────────────────────────────────────────────────
# Windows 中文控制台的 Python 默认编码是 cp936：print 文献数据里的 Å / ö / Π
# 会抛 UnicodeEncodeError，整篇输出断在半路（本脚本实测就是这么挂的）。只放宽
# 错误策略、不改 encoding——编不出来时退化成 "?"，UTF-8 环境下的输出字节一个都
# 不变。本脚本不 import 任何本地模块，所以自带一份（与 zapi.py 同款）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")


TBL_ROW = re.compile(r"^\|(.+)\|\s*$")
TBL_SEP = re.compile(r"^\|[\s:\-|]+\|\s*$")


def flatten_tables(body):
    """把 Markdown 表格压成「发现：证据」逐行——摘录清单里表格反而不好读。

    「关键发现」自 2026-09-20 起是两列表格，这里顺手压平；表头行丢掉。
    表头靠**它下面那一行是不是分隔行**（`|---|---|`）认出，不是靠「本段第一次遇到表格行」——
    同一节里可以有第二个表格，那种写法会把第二张表的表头当数据留下来。
    """
    out, held = [], None      # held = 上一行是表格行，等下一行来判它是表头还是数据
    for ln in body.split("\n"):
        s = ln.strip()
        if TBL_SEP.match(s):
            held = None       # 紧跟分隔行 ⇒ 上一行是表头，连同分隔行一起丢
            continue
        if held is not None:
            out.append(held)  # 上一行底下不是分隔行 ⇒ 它是数据行
            held = None
        m = TBL_ROW.match(s)
        if m:
            cells = [c.strip() for c in m.group(1).split("|") if c.strip()]
            held = "：".join(cells)
        else:
            out.append(ln)
    if held is not None:
        out.append(held)
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
    # papers/ 可能要等第一篇收编后才存在；新装环境先跑本脚本不该直接 FileNotFoundError
    dirs = sorted(os.listdir(PAPERS)) if os.path.isdir(PAPERS) else []
    if not dirs:
        print(f"! {PAPERS} 不存在或为空，本次没有可抽取的 summary.md")
    for d in dirs:
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
        json.dump(rows, open(os.path.join(ROOT, "state", "digest.json"), "w",
                             encoding="utf-8"),
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
