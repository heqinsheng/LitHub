#!/usr/bin/env python3
"""把每篇文献的 paper.md（全文）与 summary.md（中文总结）作为「链接附件」挂到 Zotero 条目上。

用 linked_file 模式 —— 文件仍留在 ~/LitHub，Zotero 只存链接，不占云配额、不复制文件。
Zotero 里双击附件会用系统默认程序打开（.md 现在默认是 Obsidian）。

用法:
  python3 link_markdown.py --dry-run
  python3 link_markdown.py
"""
import sys, os, re, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zapi

DRY = "--dry-run" in sys.argv
ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")

WANT = [("paper.md", "全文 Markdown"), ("summary.md", "中文总结")]


def slug(title):
    return re.sub(r"[^\w\- ]", "", title)[:60].strip().replace(" ", "_")


def main():
    man = {r["key"]: r for r in json.load(open(os.path.join(ROOT, "state", "manifest.json")))}
    items = [i for i in zapi.get_all("items/top")
             if i["data"]["itemType"] not in ("attachment", "note", "annotation")]
    print(f"文献 {len(items)} 篇 | 模式: {'DRY-RUN' if DRY else '实际创建'}")

    batch, skipped, missing = [], 0, 0
    for it in items:
        k = it["data"]["key"]
        rec = man.get(k)
        if not rec:
            print(f"  ? 无 manifest 记录: {k}"); missing += 1; continue
        d = os.path.join(PAPERS, f"{k}_{slug(rec['title'])}")

        # 已有附件路径（幂等判断）
        existing = set()
        for c in zapi.children(k):
            p = c["data"].get("path") or ""
            if p:
                existing.add(p)

        for fname, title in WANT:
            full = os.path.join(d, fname)
            if not os.path.exists(full):
                print(f"  ! 缺文件: {full}"); missing += 1; continue
            if full in existing:
                skipped += 1
                continue
            batch.append({
                "itemType": "attachment", "linkMode": "linked_file",
                "parentItem": k, "title": title, "path": full,
                "contentType": "text/markdown",
                "tags": [], "collections": [], "relations": {},
            })

    print(f"待创建 {len(batch)} 个（已存在跳过 {skipped}，缺文件 {missing}）")
    if DRY:
        for b in batch[:4]:
            print(f"  {b['parentItem']}  {b['title']:12s}  {b['path'][-70:]}")
        return

    ok = 0
    for i in range(0, len(batch), 50):
        chunk = batch[i:i + 50]
        st, r = zapi.write("POST", "/items", chunk)
        n = len(r.get("success") or {}) if isinstance(r, dict) else 0
        ok += n
        print(f"  批次 {i//50+1}: HTTP {st}  成功 {n}/{len(chunk)}")
        if isinstance(r, dict) and r.get("failed"):
            print(f"    失败详情: {str(r['failed'])[:300]}")
        time.sleep(0.4)
    print(f"完成：创建 {ok}/{len(batch)} 个链接附件")


if __name__ == "__main__":
    main()
