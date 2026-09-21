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
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zapi

DRY = "--dry-run" in sys.argv
REMOVE = "--remove" in sys.argv
ROOT = os.path.expanduser("~/LitHub")
INBOX = os.path.join(ROOT, "待整理")
HINTS = os.path.join(INBOX, "_doi_hints.json")
MANIFEST = os.path.join(ROOT, "state", "manifest.json")
MAILTO = os.environ.get("LITHUB_MAILTO", "you@example.com")
# Zotero 数据目录可配置：默认 ~/Zotero/storage，换数据目录/多 profile 时用
# LITHUB_ZOTERO_STORAGE 覆盖。manifest 的 path 是**绝对路径**，mineru_batch.py 直接
# 拿它当 PDF 源，所以这个目录一旦对不上，写出来的记录就是永远失效的死路径。
STORAGE = (os.environ.get("LITHUB_ZOTERO_STORAGE")
           or os.path.expanduser("~/Zotero/storage"))

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pdf_pages(path):
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=60).stdout
        return int(re.search(r"^Pages:\s+(\d+)", out, re.M).group(1))
    except Exception:
        return None


def pdfinfo_field(path, field):
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=60).stdout
        m = re.search(rf"^{field}:\s+(.*)$", out, re.M)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def find_doi(path):
    """首页两页正文里的 DOI 优先，其次 pdfinfo 的 Subject。"""
    try:
        txt = subprocess.run(["pdftotext", "-f", "1", "-l", "2", path, "-"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=120).stdout
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
        headers={"User-Agent": f"LitHub/1.0 (mailto:{MAILTO})"})
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
    """该条目下自称 PDF 的附件条目（**metadata 层面**）。

    它不能证明文件真的传上去了：上传中断会留下一个空附件条目。所以调用方必须
    再用 stored_file() 确认文件在不在（帮助手册 §7.8：验证上传看文件系统，
    不看 API 的 path 字段——imported_file 的 path 恒为 None）。
    """
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


def upload_file(akey, path):
    """三段式上传：授权 -> 传字节 -> 注册"""
    data = open(path, "rb").read()
    md5 = hashlib.md5(data).hexdigest()
    fn = os.path.basename(path)
    mtime = int(os.path.getmtime(path) * 1000)

    # --- 1) 请求上传授权 ---
    body = urllib.parse.urlencode({"md5": md5, "filename": fn,
                                   "filesize": len(data), "mtime": mtime}).encode()
    h = {"Zotero-API-Version": "3", "Zotero-Server-ID": zapi.SID,
         "Authorization": f"Bearer {zapi.KEY}",
         "Content-Type": "application/x-www-form-urlencoded",
         "If-None-Match": "*"}
    req = urllib.request.Request(f"{zapi.API}/items/{akey}/file", data=body,
                                 method="POST", headers=h)
    try:
        auth = json.loads(urllib.request.urlopen(req, timeout=90).read())
    except urllib.error.HTTPError as e:
        return False, f"授权失败 {e.code} {e.read()[:200].decode('utf8','replace')}"

    if auth.get("exists"):
        return True, "服务端已存在该文件，跳过上传"

    url = auth["url"]
    ctype = auth.get("contentType", "application/pdf")
    prefix = (auth.get("prefix") or "").encode()
    suffix = (auth.get("suffix") or "").encode()
    ukey = auth["uploadKey"]

    # --- 2) 上传字节 ---
    h2 = {"Content-Type": ctype, "Zotero-API-Version": "3",
          "Zotero-Server-ID": zapi.SID, "Authorization": f"Bearer {zapi.KEY}"}
    req2 = urllib.request.Request(url, data=prefix + data + suffix,
                                  method="POST", headers=h2)
    try:
        urllib.request.urlopen(req2, timeout=300).read()
    except urllib.error.HTTPError as e:
        return False, f"上传失败 {e.code} {e.read()[:200].decode('utf8','replace')}"

    # --- 3) 注册 ---
    body3 = urllib.parse.urlencode({"upload": ukey}).encode()
    h3 = dict(h); h3["Content-Type"] = "application/x-www-form-urlencoded"
    req3 = urllib.request.Request(f"{zapi.API}/items/{akey}/file", data=body3,
                                  method="POST", headers=h3)
    try:
        urllib.request.urlopen(req3, timeout=90).read()
    except urllib.error.HTTPError as e:
        return False, f"注册失败 {e.code} {e.read()[:200].decode('utf8','replace')}"
    return True, "ok"


def stored_file(akey):
    """storage 里该附件的实际 PDF；返回 None 说明文件并不存在。

    只认 .pdf：同一个 storage 目录里还可能有 Zotero 自己的 `.zotero-ft-cache`
    之类的旁挂文件，只看「目录里有东西」会把它当成上传成功。
    """
    hits = sorted(h for h in glob.glob(os.path.join(STORAGE, akey, "*"))
                  if h.lower().endswith(".pdf"))
    return hits[0] if hits else None


def stored_path(akey, filename):
    p = stored_file(akey)
    if p:
        return p
    log(f"      ! storage/{akey}/ 里没有文件，manifest 只能记预期路径 {filename}")
    return os.path.join(STORAGE, akey, filename)


def save_manifest(man):
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)


def main():
    # storage 对不上就必须停：manifest 记的是它拼出来的绝对路径，写下去就是死路径，
    # 后面的 mineru_batch.py 会一片「文件不存在」。
    if not os.path.isdir(STORAGE):
        log(f"✗ Zotero 数据目录不存在：{STORAGE}")
        log("  manifest 的 path 用它拼绝对路径（mineru_batch.py 拿它读 PDF）；"
            "请设 LITHUB_ZOTERO_STORAGE 指向真实的 storage 目录后重跑")
        sys.exit(2)
    if "LITHUB_MAILTO" not in os.environ:
        log(f"提示：未设 LITHUB_MAILTO，CrossRef 请求用占位邮箱 {MAILTO}")
    man = json.load(open(MANIFEST, encoding="utf-8"))
    known = {r["key"]: r for r in man}
    idx, _ = item_index()
    log(f"库内顶层条目 {len(idx)} 个带 DOI | 模式: "
        f"{'DRY-RUN' if DRY else '实际写入'}{' + 删原件' if REMOVE else ''}")

    files = sorted(f for f in os.listdir(INBOX) if f.lower().endswith(".pdf"))
    hints = json.load(open(HINTS, encoding="utf-8")) if os.path.exists(HINTS) else {}
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
            try:
                meta = crossref(doi)
            except Exception as e:
                # 一个查不到的 DOI（或 _doi_hints.json 里写错一个字）不该带走整轮：
                # 没有这层保护时异常直接冒到循环外，本轮已成功收编的条目全丢了。
                log(f"      ✗ CrossRef 取不到元数据（DOI {doi}）：{e}")
                skipped += 1
                continue
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
        akey = att["key"] if att else None
        if akey and stored_file(akey):
            log(f"      已有 PDF 附件 {akey}，跳过上传")
        elif DRY:
            log(f"      将重传空附件 {akey}" if akey else "      将新建 PDF 附件并上传")
            attached += 1
            continue
        else:
            # 附件条目在、文件不在（上次上传没走完，或文件被手工删了）：往同一个
            # 附件条目重传，既不会堆出一串空附件，也不会误记成「已完成」。
            if not akey:
                akey, st, r = new_attachment(key, fn)
                if not akey:
                    log(f"      ✗ 建附件失败 HTTP {st} {str(r)[:200]}")
                    skipped += 1
                    continue
            else:
                log(f"      附件 {akey} 在 storage 里没有文件，重传")
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
        # 逐条落盘：一批里后面某篇出错（或 Ctrl-C）也不该让前面已收编的条目
        # 在 manifest 里消失，否则下一轮会把它们再收一遍。
        save_manifest(man)
        log(f"      manifest: {rec['status']} {rec['pages']}p {rec['path']}")

        if REMOVE:
            os.remove(path)
            removed += 1
            log(f"      - 已从待整理移除 {fn}")

    if not DRY:
        log(f"manifest.json 共 {len(man)} 条（每收编一条即落盘）")
    log(f"完成：新建条目 {added}，上传附件 {attached}，跳过 {skipped}，"
        f"移除原件 {removed}")


if __name__ == "__main__":
    main()
