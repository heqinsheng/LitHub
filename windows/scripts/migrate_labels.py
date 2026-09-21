#!/usr/bin/env python3
"""标签改名 / 游离标签清理 / 目录原地改名（幂等，可重跑）。

⚠️ 这是**作者的库一次性迁移工具，不是通用工具**：下面的 RENAMES / DROPS / COLLECTIONS
是作者 2026-09 那次库内清理的待迁表，标签名与目录名都是那个库里的旧名字。别人在自己的
库上跑，这些名字根本不存在，结果是**空跑**（一条都不会改，也不会删错东西）。保留它是
因为这几张表同时是「按表改名 / 删游离标签 / 原地改目录名」的参考实现——换领域时要整体
替换成自己库的待迁表，且改标签名前后三处要一起改：`config/topic.json`、
`state/classification.json`、Zotero 本身。

用法：
  python3 scripts/migrate_labels.py            # 只报告（默认）
  python3 scripts/migrate_labels.py --apply    # 备份后写入

为什么需要它：`apply_to_zotero.py` 是**纯新增**，改了标签名它只会把新名字加上去，
旧名字会一直残留在条目上。本脚本补这一环——按 RENAMES 换名、按 DROPS 删除；
目录用 `PATCH /collections/<key>` 原地改名（不新建、不搬移、不删除）。

写前把受影响条目的**完整数据**（全字段 + 目录归属）与目录快照存进
`state/label_migration_backup_<日期>.json`（同日再跑退到 `<日期>_<时分秒>`，免得把唯一
可回滚的快照覆盖掉），回滚时照着改回来。
"""
import datetime
import json
import os
import sys
import time

import zapi
import topic

ROOT = os.path.expanduser("~/LitHub")
STATE = os.path.join(ROOT, "state")
DRY = "--apply" not in sys.argv

# 标签改名；跨轴也走这里（材料:单晶 → 形态:单晶）
RENAMES = {
    "chemo-mechanics": "化学-力学耦合",
    "Li 扩散": "锂离子扩散",
    "材料:富锂LMR": "材料:富锂锰基",
    "材料:单晶": "形态:单晶",
    "材料:多晶二次颗粒": "形态:多晶二次颗粒",
    "电解液浸泡": "电解液浸润",
    "AIMD": "方法:AIMD",
}

# 游离标签：受控标签的英文/变体重复，或占位标签（信息已由其它轴承载）
DROPS = [
    "Cation_disorder",   # 已有 阳离子无序
    "Reviews",           # 已有 综述
    "J-T_distortions",   # 已有 晶格畸变
    "Li_diffusion",      # 已有 锂离子扩散
    "LMR",               # 已有 材料:富锂锰基
    "Strategy",          # 散标
    "Doping",            # 散标
    "材料:通用/方法论",    # 不是材料体系
    "材料:电解质/界面",    # 2026-09-20 移出材料轴 → 体系:固态电解质 / 体系:固液对比（由 apply 打）
]

# 目录原地改名：(父目录名, 旧名, 新名)
def _parent(sub):
    """父目录名取自 `config/topic.json`——新名字所属的一级分类就是它（领域内容不写死在
    代码里）。查不到（别人的库、换过领域）返回 None，rename_collections() 直接跳过。"""
    return topic.SUB2CAT.get(sub)


COLLECTIONS = [(_parent(new), old, new) for old, new in
               (("chemo-mechanics", "化学-力学耦合"), ("Li 扩散", "锂离子扩散"))]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def rename_collections():
    """返回 [(key, 旧名, 新名, version, 父名), …]；已改好的不再出现。"""
    cols = zapi.get_all("collections")
    name_of = {c["data"]["key"]: c["data"]["name"] for c in cols}
    todo = []
    for parent_name, old, new in COLLECTIONS:
        if not parent_name:
            log(f"  · config/topic.json 里没有以「{new}」为子类的分类，跳过 {old}")
            continue
        pk = None
        for c in cols:
            if c["data"]["name"] == parent_name and not c["data"].get("parentCollection"):
                pk = c["data"]["key"]
                break
        if pk is None:
            log(f"  ? 找不到父目录 {parent_name}，跳过 {old}")
            continue
        hit = next((c for c in cols
                    if c["data"].get("parentCollection") == pk and c["data"]["name"] == old), None)
        if hit is None:
            exists = any(c["data"].get("parentCollection") == pk and c["data"]["name"] == new
                         for c in cols)
            log(f"  · {parent_name}/{old} 不存在"
                f"{'（已是 ' + new + '）' if exists else ''}，跳过")
            continue
        todo.append((hit["data"]["key"], old, new, hit["version"], parent_name))
    return todo, name_of


def main():
    log(f"模式: {'DRY-RUN（不写入）' if DRY else '实际写入'}")

    # ---------- 1. 目录改名 ----------
    col_todo, name_of = rename_collections()
    if col_todo:
        log(f"目录改名 {len(col_todo)} 个:")
        for k, old, new, _v, p in col_todo:
            log(f"    {p}/{old} → {p}/{new}  ({k})")

    # ---------- 2. 条目标签 ----------
    items = [i for i in zapi.get_all("items/top")
             if i["data"]["itemType"] not in ("attachment", "note", "annotation")]
    log(f"库内顶层条目 {len(items)} 篇")

    batch, n_ren, n_drop = [], 0, 0
    for it in items:
        tags = [t["tag"] for t in it["data"].get("tags", [])]
        new, seen = [], set()
        for t in tags:
            t2 = RENAMES.get(t, t)
            if t2 in DROPS:
                n_drop += 1
                continue
            if t2 not in seen:
                seen.add(t2)
                new.append(t2)
            if t2 != t:
                n_ren += 1
        if sorted(new) == sorted(tags):
            continue
        d = dict(it["data"])
        d["tags"] = [{"tag": t} for t in sorted(new)]
        d["version"] = it["version"]
        batch.append(d)

    log(f"待改条目 {len(batch)} 篇；改名 {n_ren} 处、删除 {n_drop} 处")
    for b in batch[:3]:
        old = {t["tag"] for t in next(i for i in items if i["data"]["key"] == b["key"])["data"]["tags"]}
        new = {t["tag"] for t in b["tags"]}
        log(f"    样例 {b['key']}  删 {sorted(old - new)}  加 {sorted(new - old)}")

    if DRY:
        log("只报告。加 --apply 执行。")
        return

    # ---------- 3. 备份 ----------
    day = datetime.date.today().isoformat()
    bak = os.path.join(STATE, f"label_migration_backup_{day}.json")
    # 同日第二次 --apply 不能覆盖上一份：它是唯一的回滚依据（tidy.py 同款兜底）
    if os.path.exists(bak):
        bak = os.path.join(STATE, "label_migration_backup_"
                           + datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".json")
    json.dump({
        "date": day,
        "renames": RENAMES,
        "drops": DROPS,
        "collection_renames": COLLECTIONS,
        "collections_before": name_of,
        "items_before": [i["data"] for i in items if i["data"]["key"] in
                         {b["key"] for b in batch}],
    }, open(bak, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"备份已写 {bak}")

    # ---------- 4. 写目录 ----------
    for k, old, new, ver, p in col_todo:
        st, r = zapi.write("PATCH", f"/collections/{k}", {"name": new}, version=ver)
        log(f"    目录改名 {old} → {new}: HTTP {st} {str(r)[:120]}")
        time.sleep(0.3)

    # ---------- 5. 写条目 ----------
    ok = 0
    for i in range(0, len(batch), 25):
        chunk = batch[i:i + 25]
        st, r = zapi.write("POST", "/items", chunk)
        succ = len(r.get("successful", {})) if isinstance(r, dict) else 0
        ok += succ
        log(f"  批次 {i // 25 + 1}: HTTP {st} 成功 {succ}/{len(chunk)}")
        if succ < len(chunk):
            log(f"    细节: {str(r)[:300]}")
        time.sleep(0.5)
    log(f"完成：目录 {len(col_todo)} 个、条目 {ok}/{len(batch)} 篇已更新")


if __name__ == "__main__":
    main()
