#!/usr/bin/env python3
"""把重新下载的 PDF 回挂到 Zotero 已有的附件条目上（Zotero 10 本地 API 三段式上传）。

用法:
  python3 attach_pdfs.py --dry-run
  python3 attach_pdfs.py
"""
import sys, os, json, hashlib, urllib.parse, urllib.request, urllib.error, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zapi

DRY = "--dry-run" in sys.argv
ROOT = os.path.expanduser("~/LitHub")
PDFDIR = os.path.join(ROOT, "state", "pdfs")


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


def main():
    dl = json.load(open(os.path.join(ROOT, "state", "downloaded.json")))
    todo = {k: v for k, v in dl.items() if v}
    print(f"待回挂 {len(todo)} 篇 | 模式: {'DRY-RUN' if DRY else '实际上传'}")

    for key, info in todo.items():
        pdf = info["file"]
        if not os.path.exists(pdf):
            print(f"  ✗ {key} 本地文件不存在 {pdf}"); continue
        ch = zapi.children(key)
        pdfatt = [c for c in ch if c["data"]["itemType"] == "attachment"
                  and c["data"].get("contentType") == "application/pdf"
                  and c["data"].get("linkMode") == "imported_file"]
        if pdfatt:
            a = pdfatt[0]; akey = a["key"]; mode = a["data"]["linkMode"]
            print(f"  {key} → 写入已有附件 {akey} (linkMode={mode}) "
                  f"← {os.path.basename(pdf)} {os.path.getsize(pdf)/1e6:.1f}MB")
        else:
            others = [c["data"].get("contentType") for c in ch
                      if c["data"]["itemType"] == "attachment"]
            print(f"  {key} → 无 PDF 附件（现有附件类型 {others}），将新建附件 "
                  f"← {os.path.basename(pdf)} {os.path.getsize(pdf)/1e6:.1f}MB")
            if DRY:
                continue
            fn = os.path.basename(pdf)
            st, r = zapi.write("POST", "/items", [{
                "itemType": "attachment", "linkMode": "imported_file",
                "parentItem": key, "title": "Full Text PDF",
                "filename": fn, "contentType": "application/pdf",
                "tags": [], "collections": [], "relations": {},
                "accessDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }])
            akey = (r.get("success") or {}).get("0")
            if not akey:
                print(f"      ✗ 新建附件失败 HTTP {st} {str(r)[:200]}"); continue
            print(f"      新附件 key = {akey}")
        if DRY:
            continue
        ok, msg = upload_file(akey, pdf)
        print(f"      {'✓' if ok else '✗'} {msg}")
        time.sleep(0.4)


if __name__ == "__main__":
    main()
