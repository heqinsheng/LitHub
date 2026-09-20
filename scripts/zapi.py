#!/usr/bin/env python3
"""Zotero 本地 API 封装（含写入），供本目录各脚本复用。"""
import json, os, socket, time, urllib.request, urllib.error

# ── 强制 IPv4 ──────────────────────────────────────────────────────────
# 本机曾出现「有全局 IPv6 地址与默认路由但实际 100% 丢包」；Python 没有 curl
# 那样的 happy-eyeballs 回退，会一直卡到超时才换 IPv4（实测同一请求 181s vs 0.72s）。
# 这里把 getaddrinfo 的 IPv6 结果滤掉，import 本模块即生效。
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_v4(host, port, family=0, *args, **kwargs):
    infos = _orig_getaddrinfo(host, port, family, *args, **kwargs)
    v4 = [i for i in infos if i[0] == socket.AF_INET]
    return v4 or infos


socket.getaddrinfo = _getaddrinfo_v4

API = "http://localhost:23119/api/users/0"
ROOT = os.path.expanduser("~/LitHub")
KEYFILE = os.path.join(ROOT, "state", "zotero_local_key")


def _sid():
    r = urllib.request.Request(f"{API}/collections?limit=1",
                               headers={"Zotero-API-Version": "3"})
    return urllib.request.urlopen(r, timeout=20).headers["Zotero-Server-ID"]


SID = _sid()
KEY = open(KEYFILE).read().strip()


def _hdr(write=False):
    h = {"Zotero-API-Version": "3", "Zotero-Server-ID": SID}
    if write:
        h["Authorization"] = f"Bearer {KEY}"
        h["Content-Type"] = "application/json"
    return h


def get(path, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API}/{path}" + (f"?{q}" if q else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers=_hdr()), timeout=60) as r:
        return json.loads(r.read()), dict(r.headers)


def get_all(path, **params):
    out, start = [], 0
    while True:
        p = dict(params); p["limit"] = 100; p["start"] = start
        d, h = get(path, **p)
        out += d
        start += len(d)
        if not d or start >= int(h.get("Total-Results", 0)):
            break
    return out


def write(method, path, body=None, version=None):
    h = _hdr(write=True)
    if version is not None:
        h["If-Unmodified-Since-Version"] = str(version)
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:600].decode("utf8", "replace")


def create_items(objs):
    """批量创建，返回 {索引: key}"""
    st, r = write("POST", "/items", objs)
    return (r.get("success") or {}), st, r


def update_items(objs):
    """批量更新（对象需带 key 与 version，字段平铺）"""
    st, r = write("POST", "/items", objs)
    return (r.get("success") or {}), st, r


def delete_items(keys):
    st, r = write("DELETE", "/items", [{"key": k, "version": v} for k, v in keys])
    return st, r


def trash_item(key, version):
    """把条目移入回收站"""
    return write("DELETE", f"/items/{key}", version=version)


def children(key):
    return get_all(f"items/{key}/children")


def fmt_items(keys):
    return get_all("items", itemKey=",".join(keys))
