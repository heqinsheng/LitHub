#!/usr/bin/env python3
"""把 `state/classification.json` 渲染成「分类与标签总表.md」。

这个脚本做什么
--------------
`state/classification.json` 是**逐篇分类的机器可读版**（每篇一条记录：`primary` /
`secondary` / `sub` / `method` / `materials` / `form` / `system` / `coupling` /
`is_review` …）。本脚本把它汇总成一张给人看的表，共三部分：

  1. **分类统计总览** —— 一级分类与各标签轴的分布
  2. **主表** —— 全部条目逐行列出（分类、各轴标签、年份、标题、Zotero key）
  3. **按一级分类分组** —— 便于按方向翻阅

**它只读 `classification.json`**：不改分类数据、不碰 Zotero、不联网、不调 LLM。
要改分类就改 `state/classification.json`，再跑一遍本脚本刷新总表。
（分类**写回 Zotero** 是另一件事，见 `scripts/apply_to_zotero.py`。）

什么时候跑
----------
- 收编新文献、往 `classification.json` 里补了条目之后
- `scripts/tidy.py --apply` 之后（它会清掉指向已消失条目的分类记录）

分类法与标签轴的可选值全部来自 `config/topic.json`（装载器 `scripts/topic.py`），
**本脚本不写死任何领域内容**——换方向只改那个 json。

⚠️ 表尾的「值得注意的发现」是**人工章节**：脚本按 `## 4. 值得注意的发现` 这个锚点，
把旧总表里的那一节**原样**带到新文件末尾，不会重写、不会格式化它。
要更新那一节请直接编辑总表，重跑本脚本不会丢。

用法：
  python3 scripts/sync_classification.py
"""
import collections
import json
import os
import re
import sys

ROOT = os.path.expanduser("~/LitHub")
CLS = os.path.join(ROOT, "state", "classification.json")
SUM = os.path.join(ROOT, "分类与标签总表.md")
ANCHOR = re.compile(r"^## 4\. 值得注意的发现", re.M)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topic  # noqa: E402  分类法 / 标签词表都在 config/topic.json

cls = json.load(open(CLS, encoding="utf-8"))
n = len(cls)

# 版面顺序 = 一级分类 + 其他/综述这类附加目录。数据里出现、但两个来源都没有的分类
# 追加到末尾——**不静默丢掉**（静默丢会让总表看起来"少了几个分类"，很难察觉）。
ORDER = list(topic.CATS) + [c for c in topic.EXTRA_TOP if c not in topic.CATS]

UNCATEGORIZED = "（未分类）"


def prim(c):
    """一级分类。`classification.json` 是给人手工编辑的，漏一个字段不该让整张总表
    生成不出来——缺失时给个显眼的占位符，好过 KeyError 把脚本崩掉。"""
    return c.get("primary") or UNCATEGORIZED


ORDER += [k for k in sorted({prim(c) for c in cls} - set(ORDER))]


def axis(counts, headers, note=""):
    """渲染一个标签轴的表格小节。headers = (小节标题, 列名)。"""
    title, col = headers
    out = [f"### {title}\n"]
    if note:
        out.append(note + "\n")
    out.append(f"| {col} | 篇数 |")
    out.append("|---|---:|")
    for k, v in counts.most_common():
        out.append(f"| {k} | {v} |")
    out.append("")
    return out


def collect(field):
    return collections.Counter(x for c in cls for x in (c.get(field) or []))


pc = collections.Counter(prim(c) for c in cls)
subc, methc, matc = collect("sub"), collect("method"), collect("materials")
formc, sysc, cplc = collect("form"), collect("system"), collect("coupling")
reviews = [c for c in cls if c.get("is_review")]

# 旧总表末尾的人工「值得注意的发现」章节：按锚点原样保留
old = open(SUM, encoding="utf-8").read() if os.path.exists(SUM) else ""
m = ANCHOR.search(old)
analysis = old[m.start():].rstrip() + "\n" if m else ""

L = []
L.append("# 文献分类与标签总表\n")
L.append(f"> 由 `scripts/sync_classification.py` 从 `state/classification.json` 生成，"
         f"共 **{n}** 篇。分类法与各标签轴的可选值见 `config/topic.json`。\n")
L.append("> 表尾「值得注意的发现」是**人工撰写**的章节，本脚本只按锚点原样保留，不会重写。\n")

L.append("\n## 1. 分类统计总览\n")
L.append(f"### 1.1 一级分类（共 {n} 篇）\n")
L.append("| 一级分类 | 篇数 | 占比 |")
L.append("|---|---:|---:|")
for k in ORDER:
    if pc.get(k):
        L.append(f"| {k} | {pc[k]} | {pc[k] * 100 // max(n, 1)}% |")
L.append("")
L.extend(axis(subc, ("1.2 子类标签", "子类标签"),
               f"共 {sum(subc.values())} 个次；一篇可跨多个子类、也可跨一级分类。\n"))
L.extend(axis(methc, ("1.3 方法标签", "方法")))
L.extend(axis(matc, ("1.4 材料体系标签", "材料体系")))
L.extend(axis(formc, ("1.5 形态标签（正交轴）", "形态"),
               "单晶 / 多晶二次颗粒描述的是**形貌**而非材料家族，所以单列一轴"
               "（可选值见 `config/topic.json` 的 `labels.form`）。\n"))
L.extend(axis(sysc, ("1.6 体系标签（正交轴）", "体系"),
               "只标**非默认**的体系（可选值见 `config/topic.json` 的 `labels.system`）。\n"))
L.extend(axis(cplc, ("1.7 耦合方向标签（正交轴）", "耦合方向"),
               "一级分类是单值的，交叉主题另用两套标签表达：Zotero 里的 `耦合:<方向>`"
               "（**只标注、不参与日报打分**，取值见 `config/topic.json` 的 `coupling`）与 "
               "`兼属:<一级分类>`（同时挂进对应一级目录，实现多归属）。\n"))
L.append(f"其中综述 **{len(reviews)}** 篇；带耦合方向标签 "
         f"{sum(1 for c in cls if c.get('coupling'))} 篇。\n")

L.append(f"\n## 2. 主表（{n} 篇）\n")
L.append("| # | 一级分类 | 二级分类 | 子类标签 | 耦合 | 综述 | 方法 | 材料体系 | 形态 | 体系 | 年份 | 标题 | key |")
L.append("|---:|---|---|---|:--:|:--:|---|---|---|---|---:|---|---|")
# year 统一转成字符串再排：classification.json 是手工编辑的，混进一个整数年份就会
# TypeError: '<' not supported between 'int' and 'str'，整张表生成不出来
for i, c in enumerate(sorted(cls, key=lambda x: (ORDER.index(prim(x))
                                                 if prim(x) in ORDER else 99,
                                                 str(x.get("year") or ""))), 1):
    L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
        i, prim(c), c.get("secondary") or "—",
        "、".join(c.get("sub") or []) or "—",
        "、".join(c.get("coupling") or []) or "—",
        "是" if c.get("is_review") else "",
        "、".join(c.get("method") or []) or "—",
        "、".join(c.get("materials") or []) or "—",
        "、".join(c.get("form") or []) or "—",
        "、".join(c.get("system") or []) or "—",
        c.get("year") or "?", str(c.get("title", "")).replace("|", "/")[:70], c["key"]))
L.append("")

L.append("\n## 3. 按一级分类分组\n")
for k in ORDER:
    grp = [c for c in cls if prim(c) == k]
    if not grp:
        continue
    L.append(f"\n### {k}（{len(grp)} 篇）\n")
    for c in sorted(grp, key=lambda x: str(x.get("year") or "")):
        L.append(f"- **[{c['key']}]** {str(c.get('title', ''))[:88]} ({c.get('year') or '?'})")
    L.append("")

if analysis:
    L.append("\n---\n")
    L.append(analysis)

open(SUM, "w", encoding="utf-8").write("\n".join(L))

print(f"总表已重写：{n} 篇，{os.path.getsize(SUM)} 字节")
print("  一级分类:", dict(pc.most_common()))
print("  综述:", len(reviews),
      "| 带耦合标签:", sum(1 for c in cls if c.get("coupling")),
      "| 保留人工分析章节:", bool(analysis))
