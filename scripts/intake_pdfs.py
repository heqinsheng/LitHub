#!/usr/bin/env python3
"""把 ~/LitHub/待整理/ 里的 PDF 收进 Zotero 库，并登记进 state/manifest.json。

逐份、幂等：
  1. 从 PDF 首页文本取 DOI（兜底用 pdfinfo 的 Subject；再兜底待整理/_doi_hints.json
     里的「文件名 → DOI」——出版方的作者接受稿常整篇不含 DOI，只能人工给）
  2. 库里已有同 DOI 的顶层条目 → 直接复用；没有 → 用 CrossRef 元数据新建条目
  3. 条目还没有 imported_file 的 PDF 附件 → 新建附件并三段式上传
  4. 写入/更新 manifest 记录（path 以 Zotero storage 实际落盘文件为准）

不做的事：分类与标签（由 classification.json + apply_to_zotero.py 负责）、
paper.md/summary.md（由 mineru_batch.py / summarize_batch.py 负责）。

用法:
  python3 scripts/intake_pdfs.py --dry-run
  python3 scripts/intake_pdfs.py
  python3 scripts/intake_pdfs.py --remove    # 成功收进 Zotero 后删掉待整理里的原件
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zapi
from attach_pdfs import upload_file

DRY = "--dry-run" in sys.argv
REMOVE = "--remove" in sys.argv
ROOT = os.path.expanduser("~/LitHub")
INBOX = os.path.join(ROOT, "待整理")
HINTS = os.path.join(INBOX, "_doi_hints.json")
MANIFEST = os.path.join(ROOT, "state", "manifest.json")
STORAGE = os.path.expanduser("~/Zotero/storage")

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pdf_pages(path):
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True,
                             timeout=60).stdout
        return int(re.search(r"^Pages:\s+(\d+)", out, re.M).group(1))
    except Exception:
        return None


def pdfinfo_field(path, field):
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True,
                             timeout=60).stdout
        m = re.search(rf"^{field}:\s+(.*)$", out, re.M)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def find_doi(path):
    """首页两页正文里的 DOI 优先，其次 pdfinfo 的 Subject。"""
    try:
        txt = subprocess.run(["pdftotext", "-f", "1", "-l", "2", path, "-"],
                             capture_output=True, text=True, timeout=120).stdout
    except Exception:
        txt = ""
    for hit in DOI_RE.findall(txt):
        doi = hit.rstrip(".,;)")
        if len(doi) > 12:
            return doi.lower(), "text"
    m = DOI_RE.search(pdfinfo_field(path, "Subject"))
    if m:
        return m.group(0).rstrip(".,;)").lower(), "pdfinfo"
    return None, None


def crossref(doi):
    req = urllib.request.Request(
        "https://api.crossref.org/works/" + urllib.parse.quote(doi),
        headers={"User-Agent": "LitHub/1.0 (mailto:you@example.com)"})
    d = json.loads(urllib.request.urlopen(req, timeout=60).read())["message"]
    # 出版日期优先取印刷版，退回在线版、最后退回首见日期
    parts = ((d.get("published-print") or d.get("published-online")
              or d.get("issued") or {}).get("date-parts") or [[None]])[0]
    date = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts) if p)
    creators = [{"creatorType": "author", "firstName": a.get("given", ""),
                 "lastName": a.get("family", "")}
                for a in d.get("author", []) if a.get("family")]
    return {
        "itemType": "journalArticle",
        "title": (d.get("title") or [""])[0],
        "creators": creators,
        "publicationTitle": (d.get("container-title") or [""])[0],
        "volume": d.get("volume", ""),
        "issue": d.get("issue", ""),
        "pages": d.get("page") or d.get("article-number") or "",
        "DOI": d.get("DOI", doi),
        "url": "https://doi.org/" + d.get("DOI", doi),
        "date": date,
        "ISSN": ", ".join(d.get("ISSN", [])),
        "abstractNote": re.sub(r"<[^>]+>", "", d.get("abstract", "") or "").strip(),
        "libraryCatalog": "CrossRef",
        "tags": [], "collections": [], "relations": {},
    }


def item_index():
    """DOI(小写) -> 条目；顺带回传全部顶层条目。"""
    items = [i for i in zapi.get_all("items/top")
             if i["data"]["itemType"] not in ("attachment", "note", "annotation")]
    idx = {}
    for it in items:
        doi = (it["data"].get("DOI") or "").strip().lower()
        if doi:
            idx.setdefault(doi, it)
    return idx, items


def existing_pdf_attachment(key):
    for c in zapi.children(key):
        d = c["data"]
        if (d.get("itemType") == "attachment"
                and d.get("contentType") == "application/pdf"
                and d.get("linkMode") in ("imported_file", "imported_url")):
            return c
    return None


def new_item(meta):
    st, r = zapi.write("POST", "/items", [meta])
    key = (r.get("success") or {}).get("0") if isinstance(r, dict) else None
    return key, st, r


def new_attachment(parent, filename):
    st, r = zapi.write("POST", "/items", [{
        "itemType": "attachment", "linkMode": "imported_file",
        "parentItem": parent, "title": "Full Text PDF",
        "filename": filename, "contentType": "application/pdf",
        "tags": [], "collections": [], "relations": {},
        "accessDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }])
    key = (r.get("success") or {}).get("0") if isinstance(r, dict) else None
    return key, st, r


def stored_path(akey, filename):
    hits = sorted(glob.glob(os.path.join(STORAGE, akey, "*.pdf")))
    if hits:
        return hits[0]
    return os.path.join(STORAGE, akey, filename)


def main():
    man = json.load(open(MANIFEST))
    known = {r["key"]: r for r in man}
    idx, _ = item_index()
    log(f"库内顶层条目 {len(idx)} 个带 DOI | 模式: "
        f"{'DRY-RUN' if DRY else '实际写入'}{' + 删原件' if REMOVE else ''}")

    files = sorted(f for f in os.listdir(INBOX) if f.lower().endswith(".pdf"))
    hints = json.load(open(HINTS)) if os.path.exists(HINTS) else {}
    if hints:
        log(f"DOI 提示表 {len(hints)} 条（{os.path.basename(HINTS)}）")
    added = attached = removed = skipped = 0
    for fn in files:
        path = os.path.join(INBOX, fn)
        size = os.path.getsize(path)
        if size < 20000:
            log(f"  ✗ {fn} 只有 {size} 字节，跳过（疑似未下载完）")
            skipped += 1
            continue
        hint = (hints.get(fn) or "").strip().lower()
        doi, how = (hint, "hint") if hint else find_doi(path)
        if not doi:
            log(f"  ✗ {fn} 找不到 DOI，跳过（需手工建条目）")
            skipped += 1
            continue

        it = idx.get(doi)
        if it:
            key, title = it["key"], it["data"].get("title", "")
            log(f"  {fn}\n      DOI {doi} ({how}) → 已有条目 {key} | {title[:60]}")
        else:
            if DRY:
                log(f"  {fn}\n      DOI {doi} ({how}) → 库里没有，将新建条目")
                added += 1
                continue
            meta = crossref(doi)
            key, st, r = new_item(meta)
            if not key:
                log(f"      ✗ 建条目失败 HTTP {st} {str(r)[:200]}")
                skipped += 1
                continue
            log(f"      + 新建条目 {key} | {meta['title'][:60]}")
            idx[doi] = {"key": key, "data": meta}
            added += 1
            time.sleep(0.4)

        att = existing_pdf_attachment(key)
        if att:
            akey = att["key"]
            log(f"      已有 PDF 附件 {akey}，跳过上传")
        elif DRY:
            log("      将新建 PDF 附件并上传")
            attached += 1
            continue
        else:
            akey, st, r = new_attachment(key, fn)
            if not akey:
                log(f"      ✗ 建附件失败 HTTP {st} {str(r)[:200]}")
                skipped += 1
                continue
            ok, msg = upload_file(akey, path)
            if not ok:
                log(f"      ✗ 上传失败: {msg}")
                skipped += 1
                continue
            attached += 1
            log(f"      + 附件 {akey} 上传成功（{size/1e6:.1f}MB）")
            time.sleep(0.4)

        if DRY:
            continue
        rec = {"key": key, "title": known.get(key, {}).get("title")
               or idx.get(doi, {}).get("data", {}).get("title", fn),
               "status": "pdf", "path": stored_path(akey, fn),
               "pages": pdf_pages(path), "ftcache": None}
        if key in known:
            known[key].update(rec)
        else:
            man.append(rec)
            known[key] = rec
        log(f"      manifest: {rec['status']} {rec['pages']}p {rec['path']}")

        if REMOVE:
            os.remove(path)
            removed += 1
            log(f"      - 已从待整理移除 {fn}")

    if not DRY:
        json.dump(man, open(MANIFEST, "w"), ensure_ascii=False, indent=1)
        log(f"manifest.json 已写入，共 {len(man)} 条")
    log(f"完成：新建条目 {added}，上传附件 {attached}，跳过 {skipped}，"
        f"移除原件 {removed}")


if __name__ == "__main__":
    main()
