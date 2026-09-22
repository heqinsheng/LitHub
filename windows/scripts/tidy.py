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
笔记没有文件名，一律算「不同的子项」搬过去，不参与同名判定。某个子项没搬成时**整条跳过**
（父条目不删、同名子项也不删），跑完只要有失败就非零退出，好让调用方看得见。

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
# apply_to_zotero.py 每轮都会把整个 taxonomy（全部一级分类 + 全部子类）与
# extra_categories 建出来，所以这些名字都不能当空目录删——否则两个脚本每跑一轮就
# 互相抵消一次，暂时没挂文献的子类会被「建 → 删 → 换 key 重建」反复折腾
MANAGED_NAMES = (set(topic.CATS) | set(topic.TAXONOMY)
                 | set(topic.SUB2CAT) | set(topic.EXTRA_TOP))
FAILURES = []


def log(m):
    print(m, flush=True)


def fail(m):
    """记一条失败：日志里看得见，跑完还进退出码（systemd / 脚本串联靠它判成败）。"""
    log(f"  ! {m}")
    FAILURES.append(m)


def norm_title(t):
    """标题归一化（只留字母数字、转小写），判重用的。

    **不截断**：截断会把同系列 Part I / Part II 这类只差尾巴的标题并成一组，而判重的
    后果是合并、甚至硬删条目。归一化结果为空（纯中文标题等）时返回空串，调用方据此
    跳过标题判重，只留 DOI 判重。
    """
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


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

    # 同一个 key 前缀可能对应多个 papers/<key>_*（标题改过一次就会多一个目录），用 dict
    # 收集会让后一个盖掉前一个——孤儿目录检测与 pick_survivor() 的 known 加分都会漏
    dirs = collections.defaultdict(list)
    for p in glob.glob(os.path.join(PAPERS, "*")):
        dirs[os.path.basename(p).split("_")[0]].append(p)
    orphan = sorted(k for k in dirs if k not in live)

    cnt = collections.Counter()
    for it in zapi.get_all("items"):
        for c in it["data"].get("collections") or []:
            cnt[c] += 1
    all_col = zapi.get_all("collections")
    by_key = {c["key"]: c["data"] for c in all_col}
    kids = collections.Counter((c["data"].get("parentCollection") or None)
                               for c in all_col)
    # 归档子树（config/topic.json 的 archive_collections，其下是过时的历史项目）整体跳过：
    # 那些项目不参与日常整理，也不归 tidy 管
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
    # 所以只要还有子目录就一律不动，留给人工判断。taxonomy / extra_categories 例外
    # （见 MANAGED_NAMES：那些名字 apply_to_zotero.py 每轮都要建出来）。
    empty_col = [(c["key"], c["data"]["name"]) for c in all_col
                 if cnt[c["key"]] == 0 and kids[c["key"]] == 0
                 and c["key"] not in archived
                 and c["data"]["name"] not in MANAGED_NAMES]
    return (empty, attach_only, groups,
            [(k, p) for k in orphan for p in sorted(dirs[k])],
            empty_col, dirs, archived)


def child_sig(c):
    """子项身份：同类型 + 同文件名即视为同一份文件。

    PDF 用 `filename`，markdown 链接附件没有 filename、用 `path` 的尾名（paper.md / summary.md）。
    取不到名字的子项（笔记既没有 filename 也没有 path）按 key 认——否则它们的签名全都一样，
    会被当成「同名重复子项」在 --merge-dups 时当重复文件硬删掉，笔记正文就只剩在备份 json 里。
    """
    d = c["data"]
    name = d.get("filename") or os.path.basename((d.get("path") or "").rstrip("/"))
    if not name:
        return (d.get("itemType"), d.get("contentType") or "", c["key"])
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
    man = json.load(open(p, encoding="utf-8"))
    for rec in man:
        if rec.get("key") != key:
            continue
        old = rec.get("path")
        if old and not os.path.exists(old) and os.path.exists(new_path):
            rec["path"] = new_path
            json.dump(man, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            log(f"    manifest: {key} path 修正为 {new_path}")
        return


def absorb(k, surv, live, colname):
    """把重复条目 k 的标签与目录并进留存条目 surv，成功了再删 k。返回 k 是否真的被处理掉。"""
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
    # 并了标签却没删成，同样算没处理干净：调用方要靠这个返回值记失败
    return 200 <= st < 300


USAGE = """用法: tidy.py [--apply] [--keep-dups] [--merge-dups]
  （无位置参数）默认只报告，不删任何东西。
  --apply       备份后执行清理（空条目 / 重复条目 / 孤儿 papers/ 目录 / 空目录）
  --keep-dups   不动重复条目
  --merge-dups  连有子项的重复条目一起合并（与 --apply 同用才生效）
  -h, --help    打印本用法，不做任何事"""


def _guard_argv(argv, usage, known=()):
    """参数护栏：`--help` 只打印用法、认不出的 `--` 开关报错退出 2，两者都不做事。

    本脚本是 `"--apply" in sys.argv` 式的手工解析（没有 argparse），打错的开关原来会被
    **静默忽略**：`--a` 静默退回「只报告」，而反过来把 `--dry-run` 写成 `--dryrun`
    这类笔误就是「安全开关被忽略、真的动手」。护栏把笔误挡在解析阶段。
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
    _guard_argv(sys.argv[1:], USAGE, known=("--apply", "--keep-dups", "--merge-dups"))
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
    # 留存条目也不能当空条目删：合并要往它里面搬子项、并标签，先把它删掉就是往死条目里写
    # （实测触发路径：留存条目有 papers/ 目录但 Zotero 侧子项已被删，而重复条目还挂着 PDF）
    survivors = {s for _, s in plan_dups} | {s for _, s, *_ in plan_merges}
    empty = [k for k in empty if k not in dup_keys and k not in survivors]

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
    json.dump(bak, open(bpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"\n备份已写 {bpath}")
    if not any([empty, plan_dups, plan_merges, orphans, empty_col]):
        log("没有需要清理的对象。")
        return

    # 1) 空条目 —— 硬删除
    for k in empty:
        st, _ = zapi.write("DELETE", f"/items/{k}", version=live[k]["version"])
        log(f"  删除空条目 {k}  HTTP {st}")
        if not 200 <= st < 300:
            fail(f"空条目 {k} 未删除（HTTP {st}）")
        time.sleep(0.2)

    # 2a) 有子项的重复条目 —— 先把子项挂到留存条目，再并标签/目录、删条目
    for k, surv, keep, drop in plan_merges:
        stuck = []
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
                stuck.append(f"{ck}（{child_sig(cd)[2]}）")
                continue
            if cd["data"].get("contentType") == "application/pdf":
                fn = cd["data"].get("filename") or ""
                # Zotero 数据目录可配置（首选项 → 高级 → 文件和文件夹），这里只覆盖默认位置；
                # 路径猜错时 fix_manifest_path() 什么都改不了，所以显式说一声，别静默。
                zdir = os.path.join(os.path.expanduser("~"), "Zotero", "storage", ck)
                if not os.path.isdir(zdir):
                    log(f"  [提示] Zotero storage 目录不存在（数据目录可能不在默认位置）: {zdir}")
                fix_manifest_path(surv, os.path.join(zdir, fn))
            time.sleep(0.3)
        if stuck:
            # 子项没搬完就删父条目 = 那些附件/笔记再无归属，而 DELETE 不可撤销；同名子项
            # 这时也不能删——它们是给「父条目即将消失」准备的，现在父条目要留着
            fail(f"重复条目 {k} 有子项没搬走（{'、'.join(stuck)}），跳过整个合并")
            continue
        # 同一份文件的重复子项显式删掉，不依赖「删父条目会不会级联删子项」的未知行为
        for ck in drop:
            cd = next((c for c in children[k] if c["key"] == ck), None)
            st, _ = zapi.write("DELETE", f"/items/{ck}", version=cd["version"])
            log(f"    删除同名子项 {ck}  HTTP {st}")
            if not 200 <= st < 300:
                fail(f"同名子项 {ck} 未删除（HTTP {st}）")
            time.sleep(0.2)
        if not absorb(k, surv, live, colname):
            fail(f"重复条目 {k} → {surv}：标签/目录合并或删除没成功")

    # 2b) 无子项的重复条目 —— 并标签/目录后删
    for k, surv in plan_dups:
        if not absorb(k, surv, live, colname):
            fail(f"重复条目 {k} → {surv}：标签/目录合并或删除没成功")

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
        if not 200 <= st < 300:
            fail(f"空目录 {n} ({k}) 未删除（HTTP {st}）")
        time.sleep(0.2)

    # 5) 本地派生产物同步：登记只保留库里还活着的条目
    live_now = {i["data"]["key"] for i in zapi.get_all("items/top")
                if i["data"]["itemType"] not in ("attachment", "note", "annotation")}
    for fn in ("state/classification.json", "state/manifest.json"):
        p = os.path.join(ROOT, fn)
        data = json.load(open(p, encoding="utf-8"))
        before = len(data)
        gone = [x["key"] for x in data if x["key"] not in live_now]
        data = [x for x in data if x["key"] in live_now]
        if before != len(data):
            json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            log(f"  {fn}: {before} → {len(data)}（清掉 {gone}）")
    if FAILURES:
        log(f"\n完成，但有 {len(FAILURES)} 项失败：")
        for m in FAILURES:
            log(f"  ! {m}")
    else:
        log("\n完成。")
    log("接着跑：python3 scripts/sync_classification.py && python3 scripts/digest.py json")
    # 有失败就非零退出：systemd / 脚本串联原本只看得到「完成。」和退出码 0
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
