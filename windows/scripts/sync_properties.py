#!/usr/bin/env python3
"""把 summary.md 的属性区（Obsidian Properties / YAML frontmatter）对齐到 Zotero 元数据。

**为什么要脚本写**：summary.md 由 `kimi -p` 依据 MinerU 转出的 paper.md 生成，而 MinerU
常常解析不出 DOI、刊名、卷期页，模型只能写 unknown 或按正文猜。这些信息 Zotero 建条目时
已经有了（来自 CrossRef 或出版商页面），所以属性区改由脚本写，模型只负责正文。

写入字段（顺序固定）：

    title    论文标题（Zotero 原文）
    author   全部作者，按 Zotero 顺序，逗号分隔
    year     4 位年份
    journal  期刊全称（`build_profile.py` 的刊名统计依赖它，不要换成简写）
    cite     短引用：`<末位作者全名>, <年份>, <期刊简写>, <卷期页>, <DOI>`
    doi      DOI

`cite` 里的期刊简写取自 `state/journal_abbr.json`（刊名全称 → 简写，手工可改）；
表里没有的刊自动补（先查 CrossRef 的 short-container-title，退回 Zotero 的
journalAbbreviation，再退回全称），但只报告模式只算不落盘，`--apply` 才写回表。
卷期页缺项时该槽位整体省略，不写 unknown。

用法:
  python3 scripts/sync_properties.py                 # 只报告差异（默认）
  python3 scripts/sync_properties.py --apply         # 写回所有 summary.md
  python3 scripts/sync_properties.py --apply --key ABCD1234   # 只处理一篇
  python3 scripts/sync_properties.py --build-abbr    # 只补期刊简写表
"""
import argparse
import glob
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zapi

# ── 控制台编码兜底 ────────────────────────────────────────────────────
# Windows 中文控制台的 Python 默认编码是 cp936：print 文献数据里的 Å / ö / Π
# （标题、作者、图注里很常见）会抛 UnicodeEncodeError，输出断在半路（本脚本的
# 「属性区」对照行就挂过）。只放宽错误策略、不改 encoding——编不出来时退化成
# "?"，UTF-8 环境下的输出字节一个都不变。与 zapi.py 的 IPv4 补丁同一套路：
# import 本模块即生效（summarize_batch.py 就是靠这一条拿到的）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")
MANIFEST = os.path.join(ROOT, "state", "manifest.json")
ABBR_PATH = os.path.join(ROOT, "state", "journal_abbr.json")
CROSSREF = "https://api.crossref.org/works/"
UA = {"User-Agent": os.environ.get("LITHUB_UA",
                                  "LitHub/1.0 (mailto:you@example.com)")}

FM_RE = re.compile(r"\A\s*---\r?\n.*?\r?\n---\r?\n?", re.S)
YAML_WORDS = {"true", "false", "null", "yes", "no", "on", "off", "~"}
BLANK = {"", "unknown", "未知", "无", "none", "n/a", "null", "?"}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def unesc(s):
    return html.unescape(str(s or "")).replace("\u00a0", " ").strip()


def norm(s):
    return re.sub(r"[^0-9a-z]+", "", unesc(s).lower())


def field(d, *names):
    for n in names:
        v = unesc(d.get(n))
        if v:
            return v
    return ""


# ────────────────────────────────────────────── 期刊简写表

class Abbr:
    def __init__(self, path=ABBR_PATH):
        self.path = path
        self.raw = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        self.idx = {norm(k): v for k, v in self.raw.items() if v}

    def get(self, journal):
        return self.idx.get(norm(journal), "")

    def add(self, journal, abbr):
        j = unesc(journal)
        if not j or j in self.raw:
            return False
        self.raw[j] = abbr
        self.idx[norm(j)] = abbr
        return True

    def save(self):
        json.dump(self.raw, open(self.path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1, sort_keys=True)


def crossref_sct(doi):
    """CrossRef 的 short-container-title；没有返回空串。"""
    try:
        req = urllib.request.Request(CROSSREF + urllib.parse.quote(doi), headers=UA)
        m = json.loads(urllib.request.urlopen(req, timeout=45).read())["message"]
        return unesc((m.get("short-container-title") or [""])[0])
    except Exception:
        return ""


def build_abbr(items, manifest, save=True):
    """补表：只加不存在的刊，已有条目一律不动（手工改过的值不会被覆盖）。

    save=False（「只报告」模式）只算不写盘——没传 `--apply` 就不该动 state/。
    """
    ab = Abbr()
    want, zug = {}, {}
    for r in manifest:
        d = items.get(r["key"])
        if not d:
            continue
        j = field(d, "publicationTitle", "bookTitle", "proceedingsTitle")
        if not j:
            continue
        if field(d, "journalAbbreviation"):
            zug.setdefault(norm(j), field(d, "journalAbbreviation"))
        if norm(j) not in ab.idx and j not in want:
            want[j] = field(d, "DOI")
    added = 0
    for j, doi in sorted(want.items()):
        sct = crossref_sct(doi) if doi else ""
        zug_j = zug.get(norm(j), "")
        # SCT 只有在确实是缩写时才算数（不少刊回的是全称本身）
        pick = sct if sct and norm(sct) != norm(j) else (
            zug_j if zug_j and norm(zug_j) != norm(j) else j)
        if ab.add(j, pick):
            added += 1
            log(f"  + {j} → {pick}{'（未查到缩写，用全称）' if pick == j else ''}")
    if added:
        if save:
            ab.save()
        else:
            log(f"  （只报告模式：新增的 {added} 条未落盘，加 --apply 才写回 {ab.path}）")
    log(f"期刊简写表 {len(ab.raw)} 条（新增 {added}）→ {ab.path}")
    return ab


# ────────────────────────────────────────────── 字段组装

def creators(d):
    out = []
    for c in d.get("creators", []):
        if c.get("creatorType") not in ("author", "", None):
            continue
        name = unesc(c.get("name")) or " ".join(
            p for p in (unesc(c.get("firstName")), unesc(c.get("lastName"))) if p)
        if name:
            out.append(name)
    if not out:  # 条目没有 author 类型时退回全部 creator
        for c in d.get("creators", []):
            name = unesc(c.get("name")) or " ".join(
                p for p in (unesc(c.get("firstName")), unesc(c.get("lastName"))) if p)
            if name:
                out.append(name)
    return out


def cite_author(d):
    """短引用的作者取**末位作者**（本领域的通讯作者惯例），写全名而不是姓氏：
    `Yang-Kook Sun`，不是 `Sun`。"""
    cs = creators(d)
    return cs[-1] if cs else ""


def year_of(d):
    m = re.search(r"(19|20)\d{2}", field(d, "date"))
    return m.group(0) if m else ""


def journal_of(d):
    return field(d, "publicationTitle", "bookTitle", "proceedingsTitle")


def volpages(d):
    vol, iss, pg = field(d, "volume"), field(d, "issue"), field(d, "pages")
    pg = re.sub(r"\s*[-–—]{1,2}\s*", "–", pg)
    head = vol + (f"({iss})" if iss else "")
    if head and pg:
        return f"{head}, {pg}"
    return head or pg


def cite_line(d, ab, year="", journal="", doi=""):
    """短引用：<末位作者全名>, <年份>, <期刊简写>, <卷期页>, <DOI>。
    构件少于 3 个时返回空——「某某」这种半截引用不如不写。"""
    parts = [p for p in (cite_author(d), year, ab.get(journal) or journal,
                         volpages(d), doi) if p]
    return ", ".join(parts) if len(parts) >= 3 else ""


def scalar(s):
    """YAML 标量：只有真正会歧义的值才加引号（逗号在块状标量里安全，不必引）。"""
    s = unesc(s)
    if not s:
        return '""'
    if (s.lower() in YAML_WORDS or re.search(r"[:#\[\]{}&*!|>'\"%@`]", s)
            or s != s.strip() or s[0] in "-?"):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def parse_fm(text):
    out = {}
    for k, v in re.findall(r"^(\w+):\s*(.*)$", text, re.M):
        out[k] = unesc(v).strip().strip('"')
    return out


def props(d, ab, old=""):
    """Zotero 为准，Zotero 没有的字段沿用旧属性区（别把已有的值覆盖成空）。"""
    of = parse_fm(old)

    def pick(zv, k):
        zv = unesc(zv)
        if zv and zv.lower() not in BLANK:
            return zv
        ov = of.get(k, "")
        return "" if ov.lower() in BLANK else ov

    title = pick(field(d, "title"), "title")
    author = pick(", ".join(creators(d)), "author")
    year = pick(year_of(d), "year")
    journal = pick(journal_of(d), "journal")
    doi = pick(field(d, "DOI"), "doi")
    return [("title", title), ("author", author), ("year", year), ("journal", journal),
            ("cite", cite_line(d, ab, year, journal, doi)), ("doi", doi)]


def frontmatter(d, ab, old=""):
    return "---\n" + "\n".join(f"{k}: {scalar(v)}" for k, v in props(d, ab, old)) + "\n---\n"


def meta_block(d, ab):
    """喂给总结模型的元数据块：与属性区同一来源，正文别再说「原文未给出」。"""
    return "\n".join(f"  {k}: {v or '（缺）'}" for k, v in props(d, ab))


def split_fm(text):
    m = FM_RE.match(text)
    return (text[:m.end()], text[m.end():]) if m else ("", text)


def rewrite_text(text, d, ab):
    old, body = split_fm(text)
    new = frontmatter(d, ab, old)
    return old.strip(), new, new + "\n" + body.lstrip("\n")


def rewrite_file(path, d, ab, apply=False):
    """返回 (旧属性区, 新属性区, 是否变化)；apply=True 时写回。"""
    text = open(path, encoding="utf-8", errors="replace").read()
    old, new, out = rewrite_text(text, d, ab)
    changed = out != text
    if apply and changed:
        # newline=""：这两个 .md 要在 git 与 Obsidian 里跨平台用，别让 Windows 把 LF 翻成 CRLF
        open(path, "w", encoding="utf-8", newline="").write(out)
    return old, new, changed


# ────────────────────────────────────────────── 入口

def load_items():
    return {i["key"]: i["data"] for i in zapi.get_all("items/top")}


def main():
    # allow_abbrev=False：禁前缀缩写，打错的开关（如 --a / --f）必须报错退出 2，不能当真开关执行
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--apply", action="store_true", help="写回文件（默认只报告）")
    ap.add_argument("--key", default="", help="只处理这一篇（Zotero key）")
    ap.add_argument("--build-abbr", action="store_true", help="只补期刊简写表")
    a = ap.parse_args()

    items = load_items()
    if a.build_abbr:
        build_abbr(items, json.load(open(MANIFEST, encoding="utf-8")))
        return
    # 默认路径也补表（文档承诺「表里没有的刊自动补」）：只在 --build-abbr 里补的话，
    # 新环境跑 --apply 表永远补不上、cite 一直退回刊名全称。manifest 只有
    # intake_pdfs.py 会建（新环境没有），所以这里以 Zotero 顶层条目为准——它是 manifest
    # 的超集。只报告模式不落盘：save=a.apply。
    ab = build_abbr(items, [{"key": k} for k in items], save=a.apply)

    todo = []
    for d in sorted(glob.glob(os.path.join(PAPERS, "*"))):
        summ = os.path.join(d, "summary.md")
        if not os.path.isfile(summ):
            continue
        key = os.path.basename(d).split("_")[0]
        if a.key and key != a.key:
            continue
        todo.append((key, summ))

    log(f"summary.md {len(todo)} 份 | 模式: {'写入' if a.apply else '只报告'}")
    changed = missing = same = 0
    planned, backup = [], {}
    for key, summ in todo:
        d = items.get(key)
        if not d:
            log(f"  ✗ {key} Zotero 里没有该条目，跳过")
            missing += 1
            continue
        old, new, ch = rewrite_file(summ, d, ab, apply=False)
        if not ch:
            same += 1
            continue
        changed += 1
        planned.append((key, summ, d, old, new))
        backup[key] = {"old": old, "new": new.strip()}
    if a.apply and backup:  # 先备份，再动文件
        path = os.path.join(ROOT, "state", f"properties_backup_{time.strftime('%Y%m%d')}.json")
        if os.path.exists(path):  # 同一天再跑：留最早的那份「旧」，别把备份覆盖掉
            old = json.load(open(path, encoding="utf-8"))
            for k, v in backup.items():
                v["old"] = old.get(k, v)["old"]
            backup = {**old, **backup}
        json.dump(backup, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log(f"改前属性区已备份到 {path}（{len(backup)} 条）")
    for i, (key, summ, d, old, new) in enumerate(planned, 1):
        if a.apply:
            rewrite_file(summ, d, ab, apply=True)
        log(f"  {'✓' if a.apply else '·'} {key} {field(d, 'title')[:50]}")
        if a.key or i <= 3:
            for line in old.splitlines():
                log(f"      旧 | {line}")
            for line in new.splitlines():
                log(f"      新 | {line}")
    log(f"完成：改动 {changed}{'（已写入）' if a.apply else '（未写入，加 --apply 生效）'}，"
        f"无需改动 {same}，无对应条目 {missing}")
    if not a.apply and changed:
        log("预览无误后跑：python3 scripts/sync_properties.py --apply")


if __name__ == "__main__":
    main()
