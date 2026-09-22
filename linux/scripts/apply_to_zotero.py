#!/usr/bin/env python3
"""把分类与标签写回 Zotero 本地库（纯新增，不删除/不移动任何已有内容）。

用法：
  apply_to_zotero.py --dry-run    只打印将要做的改动
  apply_to_zotero.py              实际写入
"""
import json, os, sys, time, urllib.request, urllib.error

API = "http://localhost:23119/api/users/0"
ROOT = os.path.expanduser("~/LitHub")
DRY = "--dry-run" in sys.argv

import topic  # 分类法 / 标签词表都在 config/topic.json（见 scripts/topic.py）

TAXONOMY = topic.TAXONOMY
EXTRA_TOP = topic.EXTRA_TOP
SUB2CAT = topic.SUB2CAT
METHOD_KEYS = topic.METHOD_KEYS
MAT_KEYS = topic.MAT_KEYS
FORM_KEYS = topic.FORM_KEYS
SYSTEM_KEYS = topic.SYSTEM_KEYS

# 正交的「耦合方向」标签轴，词表在 config/topic.json 的 coupling 里，
# 与一级分类互不干扰、不参与日报打分。判定准则：作者自己主张了该方向的因果
# 关系即算，不要求给出直接证据（化学→力学 / 力学→化学 / 双向 / 本征力化学）。
COUPLING = topic.COUPLING


def server_id():
    r = urllib.request.Request(f"{API}/collections?limit=1",
                               headers={"Zotero-API-Version": "3"})
    return urllib.request.urlopen(r, timeout=20).headers["Zotero-Server-ID"]


KEYFILE = os.path.join(ROOT, "state", "zotero_local_key")
_SID = None


def sid():
    """Zotero-Server-ID，第一次真要写时才去取。

    模块导入阶段就调本地 API 的话，main() 里那句「缺少本地 API 密钥文件」的提示永远
    到不了——还没走到检查就先炸在连接/取响应头上了。
    """
    global _SID
    if _SID is None:
        _SID = server_id()
    return _SID


def api(method, path, body=None):
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Zotero-API-Version": "3", "Content-Type": "application/json",
                 "Zotero-Server-ID": sid(),
                 "Authorization": f"Bearer {open(KEYFILE, encoding='utf-8').read().strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400].decode("utf8", "replace")


def get_all(path, **p):
    out, start = [], 0
    while True:
        q = dict(p); q["limit"] = 100; q["start"] = start
        req = urllib.request.Request(
            f"{API}/{path}?" + "&".join(f"{k}={v}" for k, v in q.items()),
            headers={"Zotero-API-Version": "3"})
        with urllib.request.urlopen(req, timeout=60) as r:
            total = int(r.headers.get("Total-Results", 0))
            d = json.loads(r.read())
        out += d; start += len(d)
        if not d or start >= total:
            break
    return out


USAGE = """用法: apply_to_zotero.py [--dry-run]
  （无位置参数）
  --dry-run   只打印将要做的改动，不写 Zotero
  -h, --help  打印本用法，不做任何事"""


def _guard_argv(argv, usage, known=()):
    """参数护栏：`--help` 只打印用法、认不出的 `--` 开关报错退出 2，两者都不做事。

    本脚本原先把 `--` 开头的 token 一律丢掉，于是 `--help` 会**真的写一次 Zotero**
    （建目录、打标签、改条目）。
    """
    if any(a in ("-h", "--help") for a in argv):
        print(usage)
        sys.exit(0)
    bad = sorted({a for a in argv if a.startswith("--") and a.split("=", 1)[0] not in set(known)})
    if bad:
        print(usage, file=sys.stderr)
        print(f"认不出的开关：{' '.join(bad)}", file=sys.stderr)
        sys.exit(2)


def main():
    _guard_argv(sys.argv[1:], USAGE, known=("--dry-run",))
    if not os.path.exists(KEYFILE):
        sys.exit(f"缺少本地 API 密钥文件 {KEYFILE}")
    cls = json.load(open(os.path.join(ROOT, "state", "classification.json"), encoding="utf-8"))
    print(f"分类条目 {len(cls)} 篇 | 模式: {'DRY-RUN（不写入）' if DRY else '实际写入'}")

    # ---------- 1. 建立分类目录 ----------
    existing = {c["data"]["name"]: c["key"] for c in get_all("collections")}
    want = {}
    for top, subs in TAXONOMY.items():
        want[top] = subs
    for t in EXTRA_TOP:
        want.setdefault(t, [])

    # 先建索引：(父key, 名称) -> 目录key，用于幂等
    idx = {(c["data"].get("parentCollection") or False, c["data"]["name"]): c["key"]
           for c in get_all("collections")}

    def ensure(name, parent):
        """返回目录 key，不存在则创建；已存在则复用。"""
        hit = idx.get((parent, name))
        if hit:
            return hit, False
        if DRY:
            return f"<新建 {name}>", True
        st, r = api("POST", "/collections", [{"name": name, "parentCollection": parent}])
        k = (r.get("success") or {}).get("0")
        if not k:
            print(f"  ! 建目录失败 {name}: HTTP {st} {str(r)[:200]}")
            return None, False
        idx[(parent, name)] = k
        return k, True

    ckey = {}
    created = 0
    for top, subs in want.items():
        k, new = ensure(top, False)
        if k is None:
            continue
        ckey[top] = k
        created += new
        for sub in subs:
            k2, new2 = ensure(sub, ckey[top])
            if k2:
                ckey[f"{top}/{sub}"] = k2
                created += new2
    print(f"目录：新建 {created} 个，可用 {len(ckey)} 个")

    # ---------- 2. 组装每篇的标签与目录 ----------
    sub2parent = {s: t for t, ss in TAXONOMY.items() for s in ss}

    # 一级分类是单值的，交叉主题靠两套附加标签补：
    #   耦合:<方向>     论文主张的耦合机制（classification.json 的 coupling 字段）。
    #                   只标注、不参与 daily_digest 打分——打分只看 primary/sub。
    #   兼属:<一级分类>  该文的第二主题（classification.json 的 secondary 字段），
    #                   同时把条目挂进对应的一级目录，使其在 Zotero 里多归属。
    # 注意 apply 是纯新增：secondary 或 coupling 日后改动时，旧标签会残留，需手工清理
    # （改名/删标签用 scripts/migrate_labels.py）。

    # 词表校验：五个标签轴的可选值都在 config/topic.json（labels.* 与 coupling），
    # 这里只报警不拦写——自由字符串一度是「游离标签」（Cation_disorder、LMR…）的来源。
    VOCAB = {"子类": set(SUB2CAT), "方法": set(METHOD_KEYS), "材料": set(MAT_KEYS),
             "形态": set(FORM_KEYS), "体系": set(SYSTEM_KEYS), "耦合": set(COUPLING)}
    FIELDS = {"子类": "sub", "方法": "method", "材料": "materials",
              "形态": "form", "体系": "system", "耦合": "coupling"}
    # 耦合轴单独算：写标签那一步用 `if cp in COUPLING` 把它们滤掉了，所以下面必须说
    # 「已跳过」。以前同一批值会先报「仍会照写」再报「已跳过」，两条提示互相打脸。
    off, off_coupling = {}, {}
    for c in cls:
        bad = [f"{ax}:{v}" for ax, field in FIELDS.items() if ax != "耦合"
               for v in (c.get(field) or []) if v not in VOCAB[ax]]
        if bad:
            off[c["key"]] = bad
        bad_coupling = [f"耦合:{cp}" for cp in (c.get("coupling") or [])
                        if cp not in COUPLING]
        if bad_coupling:
            off_coupling[c["key"]] = bad_coupling
    if off:
        print(f"  ! 词表外的取值 {len(off)} 篇（仍会照写，请补进 config/topic.json 或改标准标签）:")
        for k, v in off.items():
            print(f"      {k}: {'、'.join(v)}")
    if off_coupling:
        print(f"  ! 词表外的耦合取值 {len(off_coupling)} 篇（已跳过不写，"
              f"取值见 config/topic.json 的 coupling）:")
        for k, v in off_coupling.items():
            print(f"      {k}: {'、'.join(v)}")

    plans = {}
    for c in cls:
        tags = list(c.get("sub") or [])
        tags += [f"方法:{m}" for m in c.get("method", [])]
        tags += [f"材料:{m}" for m in c.get("materials", [])]
        tags += [f"形态:{m}" for m in c.get("form", [])]
        tags += [f"体系:{m}" for m in c.get("system", [])]
        tags += [f"耦合:{cp}" for cp in (c.get("coupling") or []) if cp in COUPLING]
        if c.get("is_review"):
            tags.append("综述")
        if c.get("seed"):
            tags.append("种子")
        cols = []
        for s in (c.get("sub") or []):
            p = sub2parent.get(s)
            if p and f"{p}/{s}" in ckey:
                cols.append(ckey[f"{p}/{s}"])
        if c.get("primary") in ckey:
            cols.append(ckey[c["primary"]])
        sec = c.get("secondary")
        if sec:
            tags.append(f"兼属:{sec}")
            if sec in ckey:
                cols.append(ckey[sec])
        if c.get("is_review") and "综述" in ckey:
            cols.append(ckey["综述"])
        if c.get("seed") and "种子文献" in ckey:
            cols.append(ckey["种子文献"])
        plans[c["key"]] = {"tags": sorted(set(tags)), "cols": sorted(set(cols))}

    # ---------- 3. 写入 ----------
    items = get_all("items/top")
    items = [i for i in items if i["data"]["itemType"] not in
             ("attachment", "note", "annotation")]
    print(f"库内顶层文献 {len(items)} 篇")

    batch, ntag, ncol = [], 0, 0
    for it in items:
        k = it["data"]["key"]
        pl = plans.get(k)
        if not pl:
            print(f"  ? 未分类：{k} {it['data'].get('title','')[:40]}")
            continue
        old_tags = {t["tag"] for t in it["data"].get("tags", [])}
        new_tags = sorted(old_tags | set(pl["tags"]))
        old_cols = set(it["data"].get("collections", []))
        new_cols = sorted(old_cols | set(pl["cols"]))
        ntag += len(set(new_tags) - old_tags)
        ncol += len(set(new_cols) - old_cols)
        if new_tags == sorted(old_tags) and new_cols == sorted(old_cols):
            continue
        d = dict(it["data"])
        d["tags"] = [{"tag": t} for t in new_tags]
        d["collections"] = new_cols
        d["version"] = it["version"]
        batch.append(d)

    print(f"待更新 {len(batch)} 篇；将新增标签 {ntag} 个、目录归属 {ncol} 处")
    if DRY:
        for b in batch[:3]:
            print("  样例:", b["key"], [t["tag"] for t in b["tags"]][-6:],
                  b["collections"][-3:])
        return

    ok = 0
    for i in range(0, len(batch), 25):
        chunk = batch[i:i + 25]
        st, r = api("POST", "/items", chunk)
        succ = len(r.get("successful", {})) if isinstance(r, dict) else 0
        ok += succ
        print(f"  批次 {i//25+1}: HTTP {st} 成功 {succ}/{len(chunk)}")
        if succ < len(chunk):
            print("    细节:", str(r)[:300])
        time.sleep(0.5)
    print(f"完成：{ok}/{len(batch)} 篇已更新")


if __name__ == "__main__":
    main()
