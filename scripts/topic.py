#!/usr/bin/env python3
"""领域配置的加载器：把「这个库研究什么」从代码里挪进 config/topic.json。

换研究方向只需改那个 json —— 分类法、每类检索式与关键词、相关性闸门正则、
期刊白名单都在里面；本模块只负责读与编译，不含任何领域内容。

用法：
    import topic as T
    T.QUERIES      # {类名: [检索式…]}
    T.CATEGORY_ORDER, T.TAXONOMY, T.CAT_KEYS, T.CAT_PREF
    T.MUST_TOPIC / T.EXCLUDE / T.OFFTOPIC …   # 已编译的正则
    T.JOURNALS

配置文件路径可用环境变量 LITHUB_TOPIC 覆盖（便于多主题并存）。
"""
import json
import os
import re

ROOT = os.path.expanduser("~/LitHub")
PATH = os.environ.get("LITHUB_TOPIC") or os.path.join(ROOT, "config", "topic.json")

with open(PATH, encoding="utf-8") as f:
    _cfg = json.load(f)

NAME = _cfg.get("name", "未命名主题")
CATEGORY_ORDER = list(_cfg["category_order"])
EXTRA_TOP = list(_cfg.get("extra_categories") or [])
FALLBACK_CATEGORY = _cfg.get("fallback_category") or CATEGORY_ORDER[0]
THEORY_CATEGORY = _cfg.get("theory_category") or ""
# 归档目录（一级目录名）：其下整棵子树是历史项目，tidy 不碰、日常也不扫描
ARCHIVE_COLLECTIONS = list(_cfg.get("archive_collections") or [])

CATEGORIES = _cfg["categories"]
# {子类标签: 一级分类}——写目录时要用
SUB2CAT = {s: c for c, v in CATEGORIES.items() for s in (v.get("sub") or [])}
# 建目录用：所有一级分类都要建（含「基础理论」这类没有子类的），子类没有就是空
TAXONOMY = {c: list(v.get("sub") or []) for c, v in CATEGORIES.items()}
QUERIES = {c: v.get("queries") or [] for c, v in CATEGORIES.items()}
CAT_KEYS = {c: v.get("keywords") or [] for c, v in CATEGORIES.items()}
CAT_PREF = {c: v["pref"] for c, v in CATEGORIES.items()
            if v.get("pref") not in (None, 1.0)}
JOURNALS = list(_cfg.get("journals") or [])
COUPLING = list(_cfg.get("coupling") or [])
CATS = list(CATEGORY_ORDER)

# 标签词表：{标签: [匹配关键词…]}。四张表都是「主键即标签名」，改写标签要连主键一起改。
#   sub       子类标签（建子目录用；同时是 classify() 的输出）
#   method    方法轴（方法:<标签>）
#   materials 材料体系轴（材料:<标签>）
#   form      形态轴（形态:<标签>）——单晶/多晶二次颗粒不是材料体系，单列一轴
_LBL = _cfg.get("labels") or {}
SUB_KEYS = _LBL.get("sub") or {}
METHOD_KEYS = _LBL.get("method") or {}
MAT_KEYS = _LBL.get("materials") or {}
FORM_KEYS = _LBL.get("form") or {}
# 体系轴：只标非默认情形（固态电解质、固液对比）——液态是默认，标了等于噪声
SYSTEM_KEYS = _LBL.get("system") or {}

# 闸门正则一律 re.I（构库时就是这么用的，换主题沿用同一约定）
_g = _cfg["gates"]


def _rx(key):
    return re.compile(_g[key], re.I) if _g.get(key) else re.compile(r"(?!)")


MUST_TOPIC = _rx("topic")          # 必须命中的体系词
THEORY_TOPIC = _rx("topic_theory")  # 「基础理论」类额外要落回的体系词
MUST_LI = _rx("must")              # 体系自证词（宽）
LITHIUM_STRONG = _rx("strong")     # 体系自证词（严）
EXCLUDE = _rx("exclude")           # 标题级排除
OFFTOPIC = _rx("offtopic")         # 摘要级离题词
