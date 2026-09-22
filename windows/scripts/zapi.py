#!/usr/bin/env python3
"""Zotero 本地 API 封装（含写入），供本目录各脚本复用。

**import 本模块不做任何 I/O**：服务器 ID 与本地密钥都是第一次真正发请求时才取
（`_sid()` / `_key()` 内部缓存）。否则「Zotero 没开」或「还没做写入授权」会让
`tidy.py`、`link_markdown.py` 这类只借一两个函数的脚本在 import 阶段就抛裸异常。

写接口的返回约定：`write()` 成功时返回 `(状态码, dict)`，HTTP 错误时返回
`(状态码, 错误正文 str)` —— **调用方必须先用 `isinstance(r, dict)` 判一下**再取字段，
否则 403/412 会变成一句 `AttributeError`。`create_items()` / `update_items()` 已经把
这件事做掉了，返回的 `r` 仍是上面两种类型之一。

判写入是否成功看响应的 `successful`（HTTP 200 也可能是 `unchanged` 的静默失败），
取 key 用 `success` —— 两者的区别见 帮助手册.md §7.5。
"""
import json, os, socket, sys, time, urllib.parse, urllib.request, urllib.error

# ── 控制台编码兜底 ────────────────────────────────────────────────────
# Windows 中文控制台的 Python 默认编码是 cp936：print 文献数据里的 Å / ö / Π
# （标题、作者、图注里很常见）会抛 UnicodeEncodeError，输出断在半路（实测
# digest.py、sync_properties.py 都这么挂过）。只放宽错误策略、不改 encoding——
# 编不出来时退化成 "?"，UTF-8 环境下的输出字节一个都不变。与下面那段 IPv4 补丁
# 同一套路：import 本模块即生效，不必每个调用方各写一遍。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

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

_sid_cache = None
_key_cache = None


def _sid():
    """Server-ID（写请求的必需头）。第一次调用才发请求，之后走缓存。"""
    global _sid_cache
    if _sid_cache is None:
        try:
            req = urllib.request.Request(f"{API}/collections?limit=1",
                                         headers={"Zotero-API-Version": "3"})
            _sid_cache = urllib.request.urlopen(req, timeout=20).headers["Zotero-Server-ID"]
        except Exception as e:
            raise RuntimeError(
                f"连不上 Zotero 本地 API（{API}）——Zotero 桌面版开着吗？\n"
                f"  原始错误：{e}") from None
    return _sid_cache


def _key():
    """本地 API 密钥（写请求的必需头）。第一次调用才读盘，之后走缓存。"""
    global _key_cache
    if _key_cache is None:
        if not os.path.exists(KEYFILE):
            raise RuntimeError(
                f"缺少本地 API 密钥文件 {KEYFILE}\n"
                "  先在 Zotero 里做一次写入授权（见 帮助手册.md §7.2），脚本会把它写到那里。")
        with open(KEYFILE, encoding="utf-8") as f:
            _key_cache = f.read().strip()
    return _key_cache


def hdr(write=False):
    """请求头。`write=True` 时带上授权密钥（这时候才会去读 `state/zotero_local_key`）。

    公开而不是私有，是因为 PDF 三段式上传（`intake_pdfs.py`）要换掉 `Content-Type`
    再发：它应当在这个基础上改，而不是自己拼 `Zotero-Server-ID` / `Authorization`——
    手拼的那份在 2026-09-21 漏改过一次（zapi 换成惰性 `_sid()` / `_key()` 之后它没跟上，
    编译与 import 全绿、到真机收编 PDF 才炸）。
    """
    h = {"Zotero-API-Version": "3", "Zotero-Server-ID": _sid()}
    if write:
        h["Authorization"] = f"Bearer {_key()}"
        h["Content-Type"] = "application/json"
    return h


def _q(params):
    # 逗号不转义（`items?itemKey=a,b` 靠它），其余按 RFC 3986 转义 —— 否则带空格、
    # `&` 或中文的参数会拼出一个语义不同的 URL。
    return "&".join(f"{k}={urllib.parse.quote(str(v), safe=',')}"
                    for k, v in params.items())


def _hget(headers, name, default=None):
    """响应头大小写不敏感地取：Zotero 经不同 HTTP 栈回来的头部大小写并不稳定，
    而 `dict(r.headers)` 之后 `.get` 是大小写敏感的。"""
    want = name.lower()
    for k, v in headers.items():
        if k.lower() == want:
            return v
    return default


def get(path, **params):
    q = _q(params)
    url = f"{API}/{path}" + (f"?{q}" if q else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers=hdr()), timeout=60) as r:
        return json.loads(r.read()), dict(r.headers)


def get_all(path, **params):
    out, start = [], 0
    while True:
        p = dict(params); p["limit"] = 100; p["start"] = start
        d, h = get(path, **p)
        out += d
        if not d:
            break
        start += len(d)
        total = _hget(h, "Total-Results")
        if total is not None:
            if start >= int(total):
                break
        elif len(d) < 100:
            # 没有 Total-Results 时不能就此收工：那等于「全库只剩前 100 条」而且是静默的。
            break
    return out


def write(method, path, body=None, version=None):
    h = hdr(write=True)
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


def _success(r):
    """从写响应里取 {索引: key}。HTTP 错误时 r 是字符串，一律退成空 dict。"""
    return (r.get("success") or {}) if isinstance(r, dict) else {}


def create_items(objs):
    """批量创建，返回 ({索引: key}, 状态码, 原始响应)。"""
    st, r = write("POST", "/items", objs)
    return _success(r), st, r


def update_items(objs):
    """批量更新（对象需带 key 与 version，字段平铺），返回同 `create_items`。"""
    st, r = write("POST", "/items", objs)
    return _success(r), st, r


def delete_item(key, version):
    """**硬删除**一条 —— `DELETE /items/<key>` 不进回收站，删了就没了（帮助手册.md §7.7）。

    没有批量版：本地 API 的 DELETE 要逐条带 `If-Unmodified-Since-Version`，
    而「DELETE + body 批量删」不是文档化的形式（旧版 `delete_items` 就是这么写的）。
    """
    return write("DELETE", f"/items/{key}", version=version)


def delete_items(keys):
    """逐条硬删除；`keys` 是 `[(key, version), …]`，返回 `[(key, 状态码), …]`。"""
    return [(k, delete_item(k, v)[0]) for k, v in keys]


def children(key):
    return get_all(f"items/{key}/children")


def fmt_items(keys):
    """按 `itemKey=` 批量取条目。该参数一次最多 50 个 key，所以要分块；
    空列表直接返回空 —— 旧写法会拼出 `itemKey=` 空参数，那等于请求全库。"""
    keys = list(keys)
    out = []
    for i in range(0, len(keys), 50):
        out += get_all("items", itemKey=",".join(keys[i:i + 50]))
    return out
