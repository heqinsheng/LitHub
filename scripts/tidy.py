#!/usr/bin/env python3
"""常规清理：空条目、重复条目、孤儿 papers/ 目录、空分类目录。

四类目标：
  空条目   Zotero 顶层条目没有任何子项（无附件、无笔记）——只剩元数据的空壳
  重复条目 DOI 相同，或标题归一化后相同
  孤儿目录 papers/<key>_* 在 Zotero 里已无对应条目（多为合并重复后留下的残骸）
  空目录   Zotero 里没有任何条目归属的分类目录

默认只报告。加 --apply 才会动手，动手前先把要删的东西全量导出到
state/tidy_backup_<日期>.json（条目全字段 + 目录名 + 子项），把孤儿目录**移动**到
state/trash/<日期>/ 而不是删除。

重复条目里**有子项**的（多数是「已入库的那条 + 早先只挂 PDF 的那条」）默认只报告，加
`--merge-dups` 才合并：把重复条目的子项挂到留存条目（同一份文件——同类型同文件名——不重复
搬，随重复条目一起删），标签/目录并过去，再删重复条目。留存条目按 `pick_survivor()` 挑，
**优先留流水线认得的那条**（papers/ 目录、manifest、classification.json 都按 key 认人）。

⚠️ 本地 API 的 DELETE /items/<key> 是**硬删除**，不进回收站
（见 帮助手册.md §7.7），所以默认不写、写了必先备份。

用法:
  python3 scripts/tidy.py                                  # 只报告
  python3 scripts/tidy.py --apply                          # 备份后清理
  python3 scripts/tidy.py --apply --keep-dups              # 不动重复条目
  python3 scripts/tidy.py --apply --merge-dups             # 连有子项的重复条目一起合并
"""
import collections
import glob
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topic
import zapi

ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")
APPLY = "--apply" in sys.argv
KEEP_DUPS = "--keep-dups" in sys.argv
MERGE_DUPS = "--merge-dups" in sys.argv
ARCHIVE_NAMES = set(topic.ARCHIVE_COLLECTIONS)
# apply_to_zotero.py 每次都会把 extra_categories 建出来（「其他」「综述」），
# 所以 tidy 不能把它们当空目录删——否则两个脚本每跑一轮就互相抵消一次
EXTRA_NAMES = set(topic.EXTRA_TOP)


def log(m):
    print(m, flush=True)


def norm_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())[:60]


def fetch():
    items = [i for i in zapi.get_all("items/top")
             if i["data"]["itemType"] not in ("attachment", "note", "annotation")]
    live, children = {}, {}
    for it in items:
        k = it["data"]["key"]
        live[k] = it
        children[k] = zapi.children(k)
    return live, children


def scan(live, children):
    """返回 (空条目, 无PDF但有子项, 重复组, 孤儿目录, 空目录)"""
    empty, attach_only = [], []
    for k, it in live.items():
        ch = children[k]
        att = [c for c in ch if c["data"]["itemType"] == "attachment"]
        notes = [c for c in ch if c["data"]["itemType"] == "note"]
        if not att and not notes:
            empty.append(k)
        elif att and not any(c["data"].get("contentType") == "application/pdf" for c in att):
            attach_only.append((k, [c["data"].get("contentType") for c in att]))

    bydoi, bytitle = collections.defaultdict(list), collections.defaultdict(list)
    for k, it in live.items():
        doi = (it["data"].get("DOI") or "").strip().lower()
        if doi:
            bydoi[doi].append(k)
        t = norm_title(it["data"].get("title"))
        if t:
            bytitle[t].append(k)

    groups, seen = [], set()
    for key_, ks in list(bydoi.items()) + list(bytitle.items()):
        if len(ks) < 2:
            continue
        sig = tuple(sorted(ks))
        if sig in seen:
            continue
        seen.add(sig)
        groups.append(sorted(ks))

    dirs = {os.path.basename(p).split("_")[0]: p for p in glob.glob(os.path.join(PAPERS, "*"))}
    orphan = sorted(k for k in dirs if k not in live)

    cnt = collections.Counter()
    for it in zapi.get_all("items"):
        for c in it["data"].get("collections") or []:
            cnt[c] += 1
    all_col = zapi.get_all("collections")
    by_key = {c["key"]: c["data"] for c in all_col}
    kids = collections.Counter((c["data"].get("parentCollection") or None)
                               for c in all_col)
    # 归档子树（如「归档/浙工大项目」）整体跳过：那是历史项目，不归 tidy 管
    archived = {c["key"] for c in all_col
                if not c["data"].get("parentCollection")
                and c["data"]["name"] in ARCHIVE_NAMES}
    while True:
        nxt = {c["key"] for c in all_col
               if (c["data"].get("parentCollection") or None) in archived}
        if nxt <= archived:
            break
        archived |= nxt
    # 只删「叶子空目录」：删父目录会**连带删除子目录**（含装着文献的），
    # 所以只要还有子目录就一律不动，留给人工判断。extra_categories 例外（见 EXTRA_NAMES）。
    empty_col = [(c["key"], c["data"]["name"]) for c in all_col
                 if cnt[c["key"]] == 0 and kids[c["key"]] == 0
                 and c["key"] not in archived
                 and c["data"]["name"] not in EXTRA_NAMES]
    return (empty, attach_only, groups, [(k, dirs[k]) for k in orphan],
            empty_col, dirs, archived)


def child_sig(c):
    """子项身份：同类型 + 同文件名即视为同一份文件。

    PDF 用 `filename`，markdown 链接附件没有 filename、用 `path` 的尾名（paper.md / summary.md）。
    """
    d = c["data"]
    name = d.get("filename") or os.path.basename((d.get("path") or "").rstrip("/"))
    return (d.get("itemType"), d.get("contentType") or "", name)


def pick_survivor(ks, live, children, known):
    """留下信息最全的一条：**流水线认得它** > 有子项 > 有 PDF > 字段多 > 加库早。

    第一条最关键：`papers/<key>_*`、`manifest.json`、`classification.json` 都按 key 认人，
    留错 key 就得连带改名一堆东西（2026-09-20 加：此前只看子项/PDF，会把「只有 PDF 的
    重复条目」选中、把已入库那条删掉）。
    """
    def score(k):
        ch = children[k]
        has_pdf = any(c["data"].get("contentType") == "application/pdf" for c in ch)
        filled = sum(1 for v in live[k]["data"].values() if v not in (None, "", [], {}))
        # dateAdded 想留「加库早」的那条：取负数，好在 max() 里当加分项
        recency = -int(re.sub(r"\D", "", live[k]["data"].get("dateAdded", ""))[:14] or 0)
        return (k in known, bool(ch), has_pdf, filled, recency)
    return max(ks, key=score)


def fix_manifest_path(key, new_path):
    """附件换了家：manifest 里那篇的 path 指向不存在的文件时改成新路径。

    只在旧路径确实不存在时才改——正常的重跑不该覆盖正确的记录。
    """
    p = os.path.join(ROOT, "state", "manifest.json")
    man = json.load(open(p))
    for rec in man:
        if rec.get("key") != key:
            continue
        old = rec.get("path")
        if old and not os.path.exists(old) and os.path.exists(new_path):
            rec["path"] = new_path
            json.dump(man, open(p, "w"), ensure_ascii=False, indent=1)
            log(f"    manifest: {key} path 修正为 {new_path}")
        return


def absorb(k, surv, live, colname):
    """把重复条目 k 的标签与目录并进留存条目 surv，成功了再删 k。返回是否成功。"""
    s = live[surv]
    tags = {t["tag"] for t in s["data"].get("tags", [])} | \
           {t["tag"] for t in live[k]["data"].get("tags", [])}
    cols = set(s["data"].get("collections") or []) | \
           set(live[k]["data"].get("collections") or [])
    body = {"key": surv, "version": s["version"],
            "tags": [{"tag": t} for t in sorted(tags)], "collections": sorted(cols)}
    st, r = zapi.write("POST", "/items", [body])
    # 标签/目录完全一致时 Zotero 回 `unchanged` 而不是 `successful`——那也是成功
    ok = isinstance(r, dict) and (r.get("successful") or r.get("unchanged"))
    log(f"  合并 {k} → {surv}: 标签 {len(tags)} 个 / 目录 "
        f"{[colname.get(c, c) for c in sorted(cols)]} {'OK' if ok else '失败 ' + str(r)[:120]}")
    if not ok:
        return False
    time.sleep(0.3)
    st, _ = zapi.write("DELETE", f"/items/{k}", version=live[k]["version"])
    log(f"    删除重复条目 {k}  HTTP {st}")
    time.sleep(0.2)
    return True


def main():
    live, children = fetch()
    empty, attach_only, groups, orphans, empty_col, dirs, archived = scan(live, children)

    log(f"库内顶层条目 {len(live)} 篇 | 模式: {'APPLY（备份后清理）' if APPLY else '只报告'}")
    log("")
    # 重复条目的处置计划要先算：待删的重复条目哪怕已经没子项，也得走「并标签/目录再删」，
    # 不能当空条目直接硬删——那会把它的标签与目录归属一起丢掉
    known = set(dirs)
    plan_dups, plan_merges, dup_lines = [], [], []
    for g in ([] if KEEP_DUPS else groups):
        surv = pick_survivor(g, live, children, known)
        surv_sigs = {child_sig(c) for c in children[surv]}
        for k in g:
            dup_lines.append(f"   {k}  {'← 保留' if k == surv else '待删'}  "
                             f"{live[k]['data'].get('title','')[:56]}")
        for k in g:
            if k == surv:
                continue
            if not children[k]:
                plan_dups.append((k, surv))
                continue
            if not MERGE_DUPS:
                dup_lines.append(f"      ! {k} 有子项，跳过（要合并请加 --merge-dups）")
                continue
            keep = [c["key"] for c in children[k] if child_sig(c) not in surv_sigs]
            drop = [c["key"] for c in children[k] if child_sig(c) in surv_sigs]
            names = [child_sig(c)[2] or c["key"] for c in children[k]]
            plan_merges.append((k, surv, keep, drop))
            dup_lines.append(f"      ↳ 合并：子项 {names} → 搬 {len(keep)} 个"
                             f"{f'、删同名 {len(drop)} 个' if drop else ''}，再删条目")
        dup_lines.append("")

    dup_keys = {k for k, _ in plan_dups} | {k for k, *_ in plan_merges}
    empty = [k for k in empty if k not in dup_keys]

    log(f"[空条目] {len(empty)} 条（无附件、无笔记）")
    for k in empty:
        log(f"   {k}  {live[k]['data'].get('title','')[:64]}")
    log(f"[无 PDF 但有其它子项] {len(attach_only)} 条（不自动删，人工判断）")
    for k, ts in attach_only:
        log(f"   {k}  附件类型 {ts}  {live[k]['data'].get('title','')[:52]}")
    log(f"[重复条目] {len(groups)} 组"
        + ("（--keep-dups：只看不动）" if KEEP_DUPS else ""))
    for ln in dup_lines:
        log(ln)
    log(f"[孤儿 papers/ 目录] {len(orphans)} 个（Zotero 已无对应条目）")
    for k, p in orphans:
        log(f"   {k}  {p}")
    log(f"[空分类目录] {len(empty_col)} 个（只含叶子空目录；有子目录的一律不动）")
    for k, n in empty_col:
        log(f"   {k}  {n}")
    if archived:
        log(f"[归档子树] {len(archived)} 个目录跳过："
            f"{'、'.join(sorted(ARCHIVE_NAMES))}")

    if not APPLY:
        log("\n未做任何改动。加 --apply 执行。")
        return

    stamp = time.strftime("%Y-%m-%d")
    bak = {"applied_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "note": "DELETE /items/<key> 是硬删除，不进回收站；此文件是唯一恢复依据",
           "empty_items": [], "duplicates": [], "orphan_dirs": [], "empty_collections": []}

    colname = {c["key"]: c["data"]["name"] for c in zapi.get_all("collections")}
    for k in empty:
        bak["empty_items"].append({"key": k, "item": live[k]["data"],
                                   "collections_named": [colname.get(c, c) for c in
                                                         (live[k]["data"].get("collections") or [])]})
    for k, surv, kids in ([(k, s, []) for k, s in plan_dups]
                          + [(k, s, [c["data"] for c in children[k]])
                             for k, s, _keep, _drop in plan_merges]):
        bak["duplicates"].append({"key": k, "survivor": surv, "item": live[k]["data"],
                                  "children": kids,
                                  "survivor_title": live[surv]["data"].get("title")})
    for k, p in orphans:
        bak["orphan_dirs"].append({"key": k, "dir": p})
    for k, n in empty_col:
        bak["empty_collections"].append({"key": k, "name": n})

    bpath = os.path.join(ROOT, "state", f"tidy_backup_{stamp}.json")
    if os.path.exists(bpath):
        bpath = os.path.join(ROOT, "state",
                             f"tidy_backup_{time.strftime('%Y-%m-%d_%H%M%S')}.json")
    json.dump(bak, open(bpath, "w"), ensure_ascii=False, indent=1)
    log(f"\n备份已写 {bpath}")
    if not any([empty, plan_dups, plan_merges, orphans, empty_col]):
        log("没有需要清理的对象。")
        return

    # 1) 空条目 —— 硬删除
    for k in empty:
        st, _ = zapi.write("DELETE", f"/items/{k}", version=live[k]["version"])
        log(f"  删除空条目 {k}  HTTP {st}")
        time.sleep(0.2)

    # 2a) 有子项的重复条目 —— 先把子项挂到留存条目，再并标签/目录、删条目
    for k, surv, keep, drop in plan_merges:
        for ck in keep:
            cd = next((c for c in children[k] if c["key"] == ck), None)
            if not cd:
                continue
            d = dict(cd["data"])
            d["parentItem"] = surv
            d["version"] = cd["version"]
            st, r = zapi.write("POST", "/items", [d])
            ok = isinstance(r, dict) and r.get("successful")
            log(f"  搬子项 {ck}（{child_sig(cd)[2]}）→ {surv}: HTTP {st}"
                f"{'' if ok else ' ' + str(r)[:120]}")
            if not ok:
                continue
            if cd["data"].get("contentType") == "application/pdf":
                fn = cd["data"].get("filename") or ""
                fix_manifest_path(surv, os.path.join(
                    os.path.expanduser("~"), "Zotero", "storage", ck, fn))
            time.sleep(0.3)
        # 同一份文件的重复子项显式删掉，不依赖「删父条目会不会级联删子项」的未知行为
        for ck in drop:
            cd = next((c for c in children[k] if c["key"] == ck), None)
            st, _ = zapi.write("DELETE", f"/items/{ck}", version=cd["version"])
            log(f"    删除同名子项 {ck}  HTTP {st}")
            time.sleep(0.2)
        absorb(k, surv, live, colname)

    # 2b) 无子项的重复条目 —— 并标签/目录后删
    for k, surv in plan_dups:
        absorb(k, surv, live, colname)

    # 3) 孤儿目录 —— 移到 state/trash/ 而不是删
    if orphans:
        dest = os.path.join(ROOT, "state", "trash", stamp)
        os.makedirs(dest, exist_ok=True)
        for k, p in orphans:
            shutil.move(p, os.path.join(dest, os.path.basename(p)))
            log(f"  移动孤儿目录 {p} → {dest}/")

    # 4) 空目录 —— 硬删除
    for k, n in empty_col:
        d, _ = zapi.get(f"collections/{k}")
        st, _ = zapi.write("DELETE", f"/collections/{k}", version=d["version"])
        log(f"  删除空目录 {n} ({k})  HTTP {st}")
        time.sleep(0.2)

    # 5) 本地派生产物同步：登记只保留库里还活着的条目
    live_now = {i["data"]["key"] for i in zapi.get_all("items/top")
                if i["data"]["itemType"] not in ("attachment", "note", "annotation")}
    for fn in ("state/classification.json", "state/manifest.json"):
        p = os.path.join(ROOT, fn)
        data = json.load(open(p))
        before = len(data)
        gone = [x["key"] for x in data if x["key"] not in live_now]
        data = [x for x in data if x["key"] in live_now]
        if before != len(data):
            json.dump(data, open(p, "w"), ensure_ascii=False, indent=1)
            log(f"  {fn}: {before} → {len(data)}（清掉 {gone}）")
    log("\n完成。接着跑：python3 scripts/sync_classification.py && "
        "python3 scripts/digest.py json")


if __name__ == "__main__":
    main()
