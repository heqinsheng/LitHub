#!/usr/bin/env python3
"""把每篇文献的 paper.md（全文）与 summary.md（中文总结）作为「链接附件」挂到 Zotero 条目上。

用 linked_file 模式 —— 文件仍留在 ~/LitHub，Zotero 只存链接，不占云配额、不复制文件。
Zotero 里双击附件会用系统默认程序打开（.md 现在默认是 Obsidian）。

用法:
  python3 link_markdown.py --dry-run
  python3 link_markdown.py
"""
import glob, sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zapi

DRY = "--dry-run" in sys.argv
ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")

WANT = [("paper.md", "全文 Markdown"), ("summary.md", "中文总结")]


def paper_file(key, fname):
    """按 `papers/<key>_*/<fname>` 查文件。

    目录名里的标题 slug 会随 Zotero 标题变，重算 slug 一旦对不上就会静默报「缺文件」、
    链接永远建不起来；其它脚本（embed_figures.py / sync_properties.py / digest.py）
    也都是拿 key 前缀 glob 找的。
    """
    hits = sorted(glob.glob(os.path.join(PAPERS, f"{key}_*", fname)))
    if len(hits) > 1:
        print(f"  ! {key} 有多份 {fname}，取 {hits[0]}")
    return hits[0] if hits else None


def main():
    man = {r["key"]: r for r in json.load(
        open(os.path.join(ROOT, "state", "manifest.json"), encoding="utf-8"))}
    items = [i for i in zapi.get_all("items/top")
             if i["data"]["itemType"] not in ("attachment", "note", "annotation")]
    print(f"文献 {len(items)} 篇 | 模式: {'DRY-RUN' if DRY else '实际创建'}")

    batch, skipped, missing = [], 0, 0
    for it in items:
        k = it["data"]["key"]
        rec = man.get(k)
        if not rec:
            print(f"  ? 无 manifest 记录: {k}"); missing += 1; continue
        # 已有附件（幂等判断）：按 linkMode + 文件名比，不拿 path 原样比——Zotero 配了
        # 「链接附件根目录」时会把 path 规范化成 attachments:<相对名>，那样每轮都会重复
        # 建一个附件。文件名取不到时退回 path 尾名（链接附件不一定带 filename 字段）。
        existing = set()
        for c in zapi.children(k):
            d = c["data"]
            if d.get("itemType") != "attachment":
                continue
            name = d.get("filename") or os.path.basename((d.get("path") or "").rstrip("/"))
            if name:
                existing.add((d.get("linkMode") or "", name))

        for fname, title in WANT:
            full = paper_file(k, fname)
            if not full:
                print(f"  ! 缺文件: {PAPERS}/{k}_*/{fname}"); missing += 1; continue
            if ("linked_file", fname) in existing:
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
        # 判成功看 successful（文档口径的成功标志），success 只是 {索引: key} 的键表；
        # 两者在创建时都有值，但 no-op 更新 Zotero 回的是 unchanged，两个都空。
        n = len(r.get("successful") or r.get("success") or {}) if isinstance(r, dict) else 0
        ok += n
        print(f"  批次 {i//50+1}: HTTP {st}  成功 {n}/{len(chunk)}")
        if isinstance(r, dict) and r.get("failed"):
            print(f"    失败详情: {str(r['failed'])[:300]}")
        time.sleep(0.4)
    print(f"完成：创建 {ok}/{len(batch)} 个链接附件")


if __name__ == "__main__":
    main()
