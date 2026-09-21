#!/usr/bin/env python3
"""每日文献日报：三通道选出层状氧化物正极文献，输出中文日报。

三个通道（同一套检索式，各自过闸后统一打分）：
  fresh  Crossref 近 --days 天新入库的 journal-article（每个检索式 rows × pages 条，
         两个值在 config/runtime.json 的 pool 段）。窗口固定为 --days，凑不满由
         另外两个通道补，不再拉长窗口用旧文冒充新文。fresh 的缓存键含 since
         （每天变），所以每期必然重取「检索式数 × pages」个请求（2026-09 补过力学/
         腐蚀检索式后检索式从 27 升到 40）——pages 是每日耗时的主因（本包现值 7 页 =
         每天最多 280 个请求，嫌慢就先降它）；rows 则几乎不花时间：实测 rows=100
         中位 4.0 s、rows=300 中位 4.1 s，三倍候选而耗时不变（瓶颈是固定延迟，不是
         响应体大小），旧文档「耗时随 rows 涨」是被单次 7.9 s vs 3.0 s 的噪声误导了。
         所以「想多要候选先把 rows 拉满，再考虑 pages」，详见帮助手册 §6.2.3。
  query  同一检索式但不加日期过滤，按 relevance 分页多取（--query-pages，默认 3），
         默认**不限年份**（--min-year 0）。旧默认是 2010 年起的硬闸门，与「经典
         不该因年龄掉权重」直接冲突（1980 年的 LiCoO2 开山作、1996 年的 PBE 都在
         闸门外）；年份下限仍保留为可选参数，要用时显式给值。缓存键带刷新周期桶
         （pool.refresh_days，默认 30 天），同周期内重跑零网络、跨周期自动刷新一次排行。
  refs   库内引文推荐：state/citation_graph.json 的 co_citation（库内文献的参考
         文献共被引）。先用 ref_meta 自带的期刊/标题字段做零网络初筛，剩下的才
         按 DOI 批量取元数据（批次缓存键同样带刷新周期）。参考文献的刊名是 ISO 缩写
         （J. Power Sources / Adv. Energy Mater.），先用 cited_journal_ok()
         （在 journal_if.py 里，与 IF 表共用同一套缩写展开）展开成全名再交
         fc.journal_ok() 判定，白名单仍是最终权威。

打分：B = 0.25*R + 0.15*S + 0.22*C + 0.20*X + 0.13*J' + 0.05*A（S 不可用时 R 取 0.40），
      final = B * (1 + 0.35*F)，综述再 ×0.9
  R 检索排名：1/(1+rank/5)，**不再乘内容分类权重**。rank 本身就来自画像生成的检索式，
    再乘一次候选的内容分类权重就是三重加权（检索式条数 × rank × w_cat）；而且
    cls.primary 是按关键词数出来的，实测与命中通道 hit_cat 只有 30% 一致（1362 篇
    里 413 篇），两者相乘等于拿一个有噪声的量去修正另一个。分类权重现在只经 C 的 q̄
    生效（见下一条），调 CAT_PREF 不再直接改 R。
    ⚠️ refs 通道没有排名，R=0，引文推荐的候选在 R 上一分不得，只能靠 C 与 S。
  S 库内语义相似度：候选（title + abstract，摘要空则只用标题）与库内 147 篇的**最大**
    余弦相似度。库内向量由 scripts/build_embeddings.py 离线编码 title+concl+method，
    存 state/work/lib_emb.npz，两侧必须用同一个模型。取 max 不取平均：库里 147 篇，
    任何候选与「平均文献」的相似度都趋同，平均会把真正的近邻稀释掉。
    ⚠️ 必须能优雅降级：库索引缺失、sentence_transformers 导不进来、候选文本为空，
    S 一律记 0 并由日志说明原因；此时它的 0.15 **回补给 R**（R 用 0.40）。不回补就会
    让总分尺度整体变小，--min-score 门槛与版面排名跟着一起偏（见 scoring_weights）。
  C 库内共被引：min(1, ln(1+n)/ln(31)) · (0.65+0.35·q̄) · (0.75+0.25·r̄)
    n = 被几篇库内文献引用；q̄ = 引用者平均分类权重比（w_i/w_max）。分类权重 w 来自
    state/profile.json，由 build_profile.py 按「库内占比 70% + 近三年活跃度 30%」算出，
    再乘一份**手调偏好** CAT_PREF（同一个文件顶部，力学耦合 ×2.5、界面反应 ×1.6）；
    想改就改 CAT_PREF 再重跑 build_profile.py，别手改 profile.json（每月会覆盖）。
    这是分类权重**唯一**参与打分的地方：R 已与它解耦；
    r̄ = 引用者平均 exp(-Δy/3)，Δy 是引用者年龄、单位**年**。
    年龄只做 ≤25% 的有界调节：旧公式的 Σ exp(-age_i/730天) 让 10 年前的引用
    只剩 0.7% 权重，「被很多篇库内文献引用」实际上退化成了年龄惩罚，现已改掉。
  X 经典度：min(1, ln(1+被引数)/ln(5001))，只看绝对被引量、完全不看年份
    （2000 被引 → 0.90、500 → 0.73、74 → 0.51、5 → 0.21）。被引数取 Crossref 的
    is-referenced-by-count，三个通道都取（refs 走的是批量元数据那条路）。
  J' 期刊层级：clamp(ln(IF/4)/ln(8), 0, 1) − 0.5，以白名单中位刊（IF≈4）为零点。
    IF 取 state/journal_if.json（journal_if.py 从 OpenAlex 的
    summary_stats.2yr_mean_citedness 取，代理指标、不是 JCR，顶端被压扁：实测
    Nature≈18.8、Science≈20.9）；查不到 IF 的按 0（中性）处理。减 0.5 不能省：
    直接留 [0,1] 会让所有白名单期刊白拿 0.1~0.15，整体抬高总分、门槛失去意义。
  A 开放获取
  F 新鲜度，按 published 日期算（180 天半衰期）——不用 Crossref 的 created，
    那是入库日，旧文被重新 deposit 会骗过新鲜度
A 只在「有资格入选的候选」范围内取值一致：先按**不含 A** 的 pre 分排短名单
（各通道配额+2）与全局前 --oa-top 名，只有这批候选去查 Unpaywall 拿 A，其余 A=0。
旧代码只给短名单查 OA，却拿含 A 的分做全局排序与门槛判定——进短名单的白送 0.15，
没进的 1300 篇候选静默扣分（它们从没被查过），这是 bug 不是设计。
排序后分三轮发版面：① 通道配额轮（refs 25% / fresh 40% / 其余 query，但只发
limit − 保底名额 张票，否则第一轮就把版面填满、保底永远轮不到）；② 分类保底轮
（--cat-floor，按分类权重从高到低给指定分类留席位）；③ 全局补位轮，空出的名额让给
全局分数最高的其他候选。三轮里 final < --min-score 的候选一律不入选——权重体现在
打分上，不靠配额硬保弱相关的新文献。
分类保底席位是 2026-09-16 去掉 R 的 w_cat 之后补的：w_cat 当时真正在干的是**补偿
供给不足**（力学耦合入池仅 91 篇、结构退化 717 篇），打分的职能删对了，保底的职能
得有人接。保底只动配额、不动分数，不会把刚删掉的三重加权请回来；它复用同一个
take()，所以 --min-score 与每类 cap 照旧管着它，达不成就空着并在日志里写明原因。
门槛值随公式一起重标定过（R 的权重 0.55→0.40、A 的 0.15→0.05 让相关度主导的分数
整体下移，X 与 J' 又把经典和好刊抬回来），现默认 0.04：实测挡掉 final ≲ 0.035 的
噪声（R≈0.02、0 被引、普通刊），但不误杀 fresh 版面那一档（默认 limit 下最低入选
实测 0.076）。
⚠️ 门槛是在**没有 S 的机器上**标定的。R 拆成 R 0.25 + S 0.15 后，S 可用时 R 那一半
变弱、S 顶上（两者都在 [0,1]，但分布不同：R 是检索式内的名次、越往后衰减越快，
S 是连续相似度），换机器跑要按 --diag-cats 的输出复核一遍门槛。

成本控制：
  - 检索/摘要/OA 链接全部走免费接口（Crossref / Semantic Scholar / Unpaywall）
  - 只有「标题+摘要翻译」这一步调用 LLM，且关闭思考链；按 TRANS_CHUNK（默认 4）篇
    一块发请求、每块最多 TRANS_RETRY（默认 3）次重试，每块成功即落盘
  - 库内关联（谁引用了它 / 谁和它同类）纯读本地引文图与分类，零 API 花销
  - S 项的库内向量由 scripts/build_embeddings.py 离线算好（纯本地，零 API 花销），
    日报这边只在线编码候选；缺库索引或 sentence-transformers 时回补权重、照常出报
  - 按左一档模型（空闲时段 输入 1 元/M、输出 4 元/M）实测：旧版一次请求翻 16 篇约
    输入 3.9K / 输出 3.6K tokens ≈ 1.8 分/天；分块 + 中文概述实测 输入 3.75K /
    输出 4.17K ≈ 2.0 分/天（见 logs/daily_digest.log 的「翻译第 i/n 块」行）
  - 所有原始响应与译文都带磁盘缓存，同一刷新周期内重跑零网络零花销。译文缓存在
    state/work/cache_translate_v2.json：zh_summary 从「2–3 句概述」改成 90–150 字
    提要后 v1 的概述不再符合新语义，故换名重译一次（实测约 0.15 元，见 TRANS_CACHE）
  - 每日耗时：本包这套工作点（rows=300 / pages=7 / 40 条检索式）同周期内重跑约 10 分钟、
    整期冷缓存约 28 分钟（两档实测见帮助手册 §6.2.3），差异几乎全在 fresh 那
    「检索式数 × pages」个每期必冷的请求上；query / refs 命中周期缓存，同周期内重跑零网络。
    定 A 用的 Unpaywall 查询比旧版多（短名单 22 + 全局前 48，去重后实测量 55–63 篇），
    首次约 +30–50 s，之后命中 state/work/cache_unpaywall 零成本
  - 跨刷新周期（pool.refresh_days，默认 30 天）的那一次要把 query 与 refs 整体重取
    （40 × query_pages 个请求 + 8 个元数据批次），这就是上面「整期冷缓存」那一档
  - 加 --no-llm 则完全不调用任何 LLM，日报只给英文标题与标签

版面：
  开头直接进文献，每条给：中文标题 / 推荐性分数（final，各分量加权后的总分，不给细则）
  / 来源 / 期刊 / 作者 / 单位（Crossref 的作者单位，无则整行不输出）/ 分类 / 材料方法 /
  链接 / 概述（90–150 字提要）/ 摘要（中文翻译）/ 库内关联；其后是下载清单。
  检索口径、收录命中与概览表一律挪到最后的「统计口径」，只在翻译不完整时在标题下加
  一行 ⚠️ 提示。

产出：
  文献日报/YYYY-MM-DD.md        中文日报
  文献日报/YYYY-MM-DD.urls.txt  纯下载链接清单（一行一个 PDF/原文页）
  state/recommendations.json    每个推过的 DOI 的
                                {first_pushed, last_pushed, times_pushed,
                                 channel, score, status}；
                                --cooldown-days（默认 60）内不重推，超期后高分
                                老文可再推一次（times_pushed 递增），
                                status=="in_library" 的永久跳过。
                                旧的 state/digest_seen.json 只读迁移、不删除。
  state/last_push.json          上次成功出报的日期，频率闸门（schedule.every_n_days）
                                用它判断该不该跑这一期
  logs/daily_digest.log         运行日志

运行参数（频率 / 篇数 / 池子 / 打分权重）全在 config/runtime.json，装载器是
scripts/runtime.py；默认值与原硬编码一致，文件缺了也照跑。领域内容不在这里，
在 config/topic.json。改运行参数改完即生效，不用重跑 build_profile.py。

用法：
  python3 scripts/daily_digest.py                      # 默认：近 14 天，取 16 篇
  python3 scripts/daily_digest.py --days 7 --limit 12 --min-score 0 --citer-years 1.5
  python3 scripts/daily_digest.py --min-year 2015 --min-score 0.06    # 显式恢复年份下限
  python3 scripts/daily_digest.py --no-llm --date 2026-09-12
  python3 scripts/daily_digest.py --dry-run --diag-pagination
  python3 scripts/daily_digest.py --dry-run --diag-cats       # 调权重时看各类候选
  python3 scripts/daily_digest.py --show-config               # 打印生效的运行配置就退出
  python3 scripts/daily_digest.py --force                     # 无视频率限制，强制出一期
"""
import argparse
import gzip
import hashlib
import html
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
CACHE = STATE / "work"
NEWS = ROOT / "文献日报"
LOGS = ROOT / "logs"
LIB_INDEX = STATE / "library_index.json"       # 权威 DOI 索引（只读）
CITE_GRAPH = STATE / "citation_graph.json"     # 库内引文图 + ref_meta（只读）
RECS = STATE / "recommendations.json"          # 本脚本的推送状态（可写）
RECS_LEGACY = STATE / "digest_seen.json"       # 旧格式，只读迁移，不删
LAST_RUN = STATE / "last_push.json"            # 上次成功出报的日期（频率闸门读它，可写）
# 译文缓存。zh_summary 的要求从「2–3 句概述」改成 90–150 字提要（要覆盖方法核心 /
# 主要贡献 / 关键结果）后语义变了，v1 的概述不能复用，故换文件名一次性重译；
# 缓存按 DOI 存，混着新旧两种概述的日报没法看，所以是换名而不是打版本字段。
TRANS_CACHE = CACHE / "cache_translate_v2.json"
MAILTO = os.environ.get("LITHUB_MAILTO", "you@example.com")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 复用 find_new.py 里的相关性闸门与文本工具，避免两套规则漂移
import find_new as fc  # noqa: E402
# 刊名缩写展开（cited_journal_ok）与 IF 表（if_lookup）都集中在 journal_if.py：
# 引文通道的缩写展开与 J' 的 IF 查表共用同一套判定，避免两份规则漂移
from journal_if import cited_journal_ok, if_lookup, load_table as load_if_table  # noqa: E402
# 运行参数（频率 / 篇数 / 池子 / 打分权重）集中在 config/runtime.json，
# 装载器不含任何领域内容；文件缺失则用它内置的默认值（与原硬编码一致）
import runtime as _rt  # noqa: E402

# ---------------------------------------------------------------- 打分权重
# B = WR·R + WS·S + WC·C + WX·X + WJ·J' + WA·A，final = B·(1 + F_GAIN·F)，综述 ×0.9
# R 的 0.40 在有 S 时拆成 R 0.25 + S 0.15；S 用不了时那 0.15 必须回补给 R（唯一
# 一处表达这个机制的地方是 scoring_weights），否则总分尺度整体变小、门槛与排名全偏。
# 权重与各分量的常数都取自 config/runtime.json 的 scoring 段（默认值与原硬编码一致），
# 变量名保持不变，下面各处的公式只认这些名字。
_WS_CFG = _rt.SCORING
W_R, W_S = _WS_CFG["w_r"], _WS_CFG["w_s"]
WC, WX, WJ, WA = _WS_CFG["w_c"], _WS_CFG["w_x"], _WS_CFG["w_j"], _WS_CFG["w_a"]
F_GAIN = _WS_CFG["f_gain"]            # 新鲜度加成上限（老文 ×1.0、新文最多 ×1.35）
F_HALF_LIFE = _WS_CFG["f_half_life"]  # F = exp(-age/180)，按 published 日期
X_SAT = _WS_CFG["x_sat"]              # X = ln(1+cited)/ln(5001)：5000 被引顶到 1
J_IF_ZERO = _WS_CFG["j_if_zero"]      # J' 的零点：IF=4 的白名单中位刊不奖不罚
J_IF_TOP = _WS_CFG["j_if_top"]        # J' 的饱和点：clamp(ln(IF/4)/ln(8), 0, 1) - 0.5
C_N_SAT = _WS_CFG["c_n_sat"]          # C 的引用篇数饱和点：ln(1+n)/ln(31)
C_W_FLOOR = _WS_CFG["c_w_floor"]      # C 的引用者分类权重地板：(0.65 + 0.35·q̄)
C_AGE_FLOOR = _WS_CFG["c_age_floor"]  # C 的引用者年龄地板：(0.75 + 0.25·r̄) ∈ [0.75, 1]
C_AGE_TAU_Y = _WS_CFG["c_age_tau_y"]  # r̄ = exp(-Δy/3)，Δy 是引用者年龄、单位年

_orig = socket.getaddrinfo
def _v4(host, port, family=0, *a, **k):
    return [r for r in _orig(host, port, family, *a, **k) if r[0] == socket.AF_INET]
socket.getaddrinfo = _v4


# ---------------------------------------------------------------- 分类规则
# 关键词表在 config/topic.json 的 categories.<类名>.keywords 里（见 scripts/topic.py）
import topic as _topic  # noqa: E402

CAT_KEYS = _topic.CAT_KEYS

# 四张标签词表（子类 / 方法 / 材料 / 形态）都在 config/topic.json 的 labels 里——
# 主键即标签名，所以改标签要连那里的主键一起改，见 scripts/topic.py。
SUB_KEYS = _topic.SUB_KEYS
METHOD_KEYS = _topic.METHOD_KEYS
MAT_KEYS = _topic.MAT_KEYS
FORM_KEYS = _topic.FORM_KEYS
SYSTEM_KEYS = _topic.SYSTEM_KEYS

REVIEW_PAT = re.compile(
    r"\breview\b|\bperspective\b|progress and prospect|recent advances|overview|"
    r"advances and perspectives|challenges and strateg|roadmap|\bmini-review\b", re.I)

# 日报只收层状氧化物正极：标题或摘要里必须出现这些体系词
# ⚠️ `lini` 必须带词边界：裸子串会命中 crystallinity / lining 这类词，于是任何提到
# 「结晶度」的材料论文都能过闸，还会被标成 材料:NCM/NCA（实测 2026-09-15 因此放进来
# 一篇二维硼烯生物医学论文）。
LAYERED_PAT = re.compile(
    r"layered oxide|layered cathode|layered li|layered transition metal oxide|"
    r"\bncm\d*\b|\bnca\d*\b|\blini(?:o2|[^a-z])|li-rich|lithium-rich|\blmr\b|"
    r"licoo2|\blco\b|ni-rich|nickel-rich|high-nickel|ultrahigh-nickel|overlithiated|"
    r"li1\.[0-9]|li2mno3|lithium- and manganese-rich", re.I)


def hits(text, keys):
    # 出版方摘要常用 U+2010 连字符，先归一化再匹配，否则 ni-rich/single-crystal 等全部漏判
    t = fc.dashes(text).lower()
    return [k for k in keys if k in t]


def hits_labels(text, table):
    """table 是 {标签: [关键词...]}，返回命中的标签。"""
    t = fc.dashes(text).lower()
    return [label for label, keys in table.items() if any(k in t for k in keys)]


def classify(title, abstract):
    """纯规则分类，零 API 花费。返回 primary / sub / method / materials / form / system / is_review。"""
    text = f"{title} {abstract}"
    scores = {c: len(hits(text, k)) for c, k in CAT_KEYS.items()}
    primary = max(scores, key=lambda c: (scores[c], c))
    if scores[primary] == 0:
        primary = (_topic.THEORY_CATEGORY
                   if re.search(r"dft|simulation|model|machine learning|comput", text, re.I)
                   else _topic.FALLBACK_CATEGORY)
    return {
        "primary": primary,
        "scores": scores,
        "sub": hits_labels(text, SUB_KEYS)[:3],
        "method": hits_labels(text, METHOD_KEYS)[:3],
        "materials": hits_labels(text, MAT_KEYS)[:2],
        "form": hits_labels(text, FORM_KEYS)[:2],
        "system": hits_labels(text, SYSTEM_KEYS)[:1],
        "is_review": bool(REVIEW_PAT.search(title)),
    }


# ---------------------------------------------------------------- 网络工具
def get_json(url, tries=3, sleep=0.5, timeout=45):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": f"LitHub/1.0 (mailto:{MAILTO})"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            time.sleep(sleep)
            return data
        except urllib.error.HTTPError as e:
            if i == tries - 1:
                raise
            time.sleep(15 * (i + 1) if e.code in (429, 503) else 3 * (i + 1))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def cached_json(url, key, sub, tries=3, sleep=0.5):
    d = CACHE / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / (re.sub(r"\W+", "_", key)[:140] + ".json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = get_json(url, tries=tries, sleep=sleep)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


CROSSREF_SELECT = ("DOI,title,container-title,published,published-online,abstract,"
                   "is-referenced-by-count,author,URL,created")
# created（Crossref 入库日）仍然取回，但只是参考：新鲜度一律按 published 算，
# 否则旧文被重新 deposit 会被当成新文。字段保留在 select 里以免作废磁盘缓存。
# DOI 里可能出现、但会破坏 query string 的字符（实测库内 DOI 均不含，防御性编码）
HOSTILE = str.maketrans({c: urllib.parse.quote(c, safe="")
                         for c in "&#?+% \t"})

# 单位串里的噪声：URL / 邮箱 / ORCID / 纯数字片段（邮编、上标脚注号）。
# 数字写成 `(?<![\w.])\d+(?![\w.])` 而不是 `\b\d+\b`：后者的词边界会把化学式拆开
# （LiFePO4 的 4、Li1.2 的小数点），而这里要求数字两侧都不是字母/数字/点。
AFFIL_NOISE = re.compile(
    r"https?://\S+|\bwww\.\S+"
    r"|[\w.+-]+@[\w-]+\.[\w.-]+"
    r"|orcid\.org/\S+|\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b"
    r"|(?<![\w.])\d+(?![\w.])", re.I)
# CamelCase 还原只切「字母后面紧跟一个大写开头的新单词」的边界。全大写缩写与化学式的
# 小写紧跟大写处与这个边界长得一样（LiFePO4 的 Fe、NCM811），区分不了，所以下面按
# 片段分别处理：含数字的片段一律不切，判别交给「是不是缩写/化学式」这件事本身。
CAMEL_PAT = re.compile(r"(?<=[A-Za-z])(?=[A-Z][a-z])")
# 连续 18 个以上字母还没被空格/符号隔开：整段院系名被压成一串（无大小写边界可分）
# 或 PDF 抄下来的粘连串，都是噪声
GLUED_PAT = re.compile(r"[A-Za-z]{18,}")


def _split_glued(run):
    """粘连字母串：能靠 CamelCase 切开就切开，切不开返回空串（判为噪声丢弃）。"""
    parts = CAMEL_PAT.sub(" ", run).split()
    return " ".join(parts) if len(parts) > 1 else ""


def _camel(token):
    """CamelCase 还原：含数字的片段（LiFePO4 / NCM811 / N2 L 3G1）整个不动。"""
    return token if any(c.isdigit() for c in token) else CAMEL_PAT.sub(" ", token)


def clean_affil(names):
    """作者单位原始串列表 -> 一条 `A; B` 串；没有可用单位时返回空串。

    Crossref 的 affiliation.name 脏得很有规律：HTML 实体（`&amp;`）、U+2010 连字符、
    邮编与上标脚注号、院系名之间缺分隔直接粘连、同一单位大小写不同的重复、以及
    `Bar-Ilan University , , - ,` 这种只剩标点的尾部。规则链必须是
    「先去噪声、再拆粘连、最后去重」：反过来的话去重键被噪声污染，同一单位会算两条。
    find_new.clean 只做 7 个具名实体的手工替换、且不在单位这条路径上（parse_item 存
    的是原始串），所以这里自己 html.unescape。
    没有可用单位时返回空串，而不是像 hermes-arxiv-agent 那样写「未找到单位信息」：
    那边是 IM 推送、每条必须整段成文，这边日报每篇都跟一句兜底句就只是噪声。
    """
    out, seen = [], set()
    for raw in names or []:
        s = AFFIL_NOISE.sub(" ", raw or "")
        s = fc.dashes(s)                     # U+2010 等连字符归一成 ASCII，先归一才好跨行接词
        s = re.sub(r"-\s*\n\s*", "", s)      # 跨行断词：Chemi-\nstry 接回 Chemistry
        s = html.unescape(s)
        s = " ".join(_camel(t) for t in s.split())
        s = GLUED_PAT.sub(lambda m: _split_glued(m.group()), s)
        s = re.sub(r"\s+([,;.])", r"\1", s)  # 去掉数字片段后留下的 `Beijing , China`
        s = re.sub(r"\s+", " ", s).strip(" ,;.-")
        key = re.sub(r"\s+", "", s).lower()
        if len(s) < 3 or key in seen:
            continue
        seen.add(key)
        out.append(s)
    return "; ".join(out)


def raw_affils(it):
    """Crossref 记录 -> 作者单位原始串（按出现顺序去重，不做清洗）。

    清洗统一交给 clean_affil：检索路径与批量取元数据路径共用 parse_item，两边各写
    一套清洗规则必然漂移。
    """
    out = []
    for au in it.get("author") or []:
        for af in au.get("affiliation") or []:
            n = (af.get("name") or "").strip()
            if n and n not in out:
                out.append(n)
    return out


def parse_item(it, rank=0):
    """Crossref 的 work 记录 -> 统一候选 dict（检索与批量取元数据共用）。"""
    dp = ((it.get("published") or it.get("published-online") or {})
          .get("date-parts") or [[""]])[0]
    return {
        "title": fc.clean((it.get("title") or [""])[0]),
        "journal": fc.clean((it.get("container-title") or [""])[0]),
        "date": "-".join(f"{x:02d}" if isinstance(x, int) else str(x) for x in dp),
        "doi": (it.get("DOI") or "").lower(),
        "cited": it.get("is-referenced-by-count", 0),
        "authors": [a.get("family", "") for a in (it.get("author") or [])[:5]],
        "affils": raw_affils(it),
        "abstract": fc.clean(it.get("abstract", "")),
        "rank": rank,
        "created": (it.get("created") or {}).get("date-time", "")[:10],
    }


def search_crossref(query, since=None, rows=30, pages=1, pub_from=None, stats=None, tag=None):
    """Crossref 检索。since=from-created-date（fresh 通道），pub_from=from-pub-date
    （query 通道的年份下限），两者都 None 则不带日期过滤。

    分页实测结论（2026-09-14 本机）：Crossref 支持 cursor=* 深度分页，响应的
    next-cursor 可继续翻页，带/不带日期过滤都可用，故不退回 offset
    （offset 上限约 1 万，且并发下容易跳条）。
    rank 是跨页的全局相关性排名，从 0 开始；stats 非空时收集 total-results。
    tag 非空时并进缓存键：query 通道传刷新周期桶（见 period_tag），让「相关旧文」
    排行榜跨周期自动刷新一次（否则键里没有日期，第一次跑完就永远冻在磁盘上）。
    """
    filt = ["type:journal-article"]
    if since:
        filt.insert(0, f"from-created-date:{since}")
    if pub_from:
        filt.append(f"from-pub-date:{pub_from}")
    base = ("https://api.crossref.org/works?query.bibliographic="
            + urllib.parse.quote(query)
            + "&filter=" + ",".join(filt)
            + "&select=" + CROSSREF_SELECT
            + f"&rows={rows}&sort=relevance&order=desc&mailto={MAILTO}&cursor=")
    out, cursor = [], "*"
    for page in range(max(1, pages)):
        url = base + urllib.parse.quote(cursor)
        # 缓存键用 (检索式, 过滤, rows, 页号, 刷新周期桶)：同一 query 的 cursor 序列是确定的
        key = (f"{tag or ''}p{page}_{since or 'all'}_{pub_from or 'all'}_{rows}_{query}"
               if tag else f"p{page}_{since or 'all'}_{pub_from or 'all'}_{rows}_{query}")
        try:
            data = cached_json(url, key, "cache_daily")
        except Exception as e:
            print(f"  ! 检索失败（第 {page + 1} 页）: {type(e).__name__} {e}")
            break
        items = data.get("message", {}).get("items", [])
        if stats is not None:
            stats.append(int(data.get("message", {}).get("total-results") or 0))
        if not items:
            break
        for it in items:
            out.append(parse_item(it, rank=len(out)))
        cursor = data.get("message", {}).get("next-cursor") or ""
        if not cursor:
            break
    return out


def fetch_doi_meta(dois, log, tag=None):
    """按 DOI 批量取元数据（Crossref filter=doi:X,doi:Y,...，单批 <=100）。

    必须重复 doi: 键做 OR；写成 doi:A,B 会 400。单篇路由 /works/{doi} 不支持
    select，批量路由支持（含 abstract）。返回 {doi: 候选 dict}。
    tag 非空时并进缓存键（refs 通道传刷新周期桶，跨周期刷新一次候选）。
    """
    out = {}
    for i in range(0, len(dois), 100):
        chunk = dois[i:i + 100]
        filt = ",".join("doi:" + d.translate(HOSTILE) for d in chunk)
        url = (f"https://api.crossref.org/works?filter={filt}"
               f"&select={CROSSREF_SELECT}&rows={len(chunk)}&mailto={MAILTO}")
        h = hashlib.sha1(((tag or "") + url).encode()).hexdigest()[:20]
        try:
            data = cached_json(url, "batch_" + h, "cache_daily")
        except Exception as e:
            log(f"  ! 批量取元数据失败（{len(chunk)} 个 DOI）：{type(e).__name__} {e}")
            continue
        for it in data.get("message", {}).get("items", []):
            r = parse_item(it)
            if r["doi"]:
                out[r["doi"]] = r
    return out


def strip_abs(s):
    """去掉出版方摘要开头的 Abstract / 摘要 标记。"""
    return re.sub(r"^\s*(abstract|摘要|graphical abstract)\b[:：\s\-–]*", "", s or "",
                  flags=re.I).strip()


# ── 出版社 landing page 的摘要兜底 ──────────────────────────────────────
# 有些付费刊的摘要 Crossref 与 Semantic Scholar 都没有，但**出版社自己的页面有**。
# 实测（2026-09-20）：nature.com 的 <meta name="dc.description"> 是完整摘要、无反爬；
# 而 sciencedirect.com 直接 403（Elsevier 付费刊只能等收编后从 PDF 拿）。
# 所以这里只对白名单前缀尝试——不为一个已知会被挡的出版方白花请求。
LANDING_ABS_PREFIX = ("10.1038/",)          # Nature 系（含 Nature Energy/Materials、npj*）
LANDING_ABS_META = ("citation_abstract", "dc.description", "og:description", "description")
# 出版社多按 UA 拦机器人，裸 urllib 的默认 UA 会吃 403
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_LAND = {"n_try": 0, "n_got": 0}


def landing_absolute(doi):
    """从出版社 landing page 的 meta 标签抠摘要；抠不到返回空串。

    结果（**含「取到页面但没抠到摘要」**）缓存在 state/work/cache_landing/，重跑零网络。
    缓存里存的是抠出来的纯文本而不是整页 HTML：日报只要这一点内容，没必要把
    几百 KB 的页面留在磁盘上。**请求本身失败（超时 / 被挡）不写缓存**——偶发一次失败
    就把该 DOI 永久钉成「没有摘要」，之后只能手删缓存文件才能重试。

    实测 2026-09-20：10 篇「Crossref/S2 都取不到」的 Nature 系 **10/10 都补到了**，
    其中 9 篇是 1300–2000 字符的完整摘要。剩 1 篇页面只给了约 300 字符的截断 teaser
    （该页连 `#Abs1-content` 区块都没有），那种也只能照收——比「未取到」强。
    """
    if not doi or not doi.lower().startswith(LANDING_ABS_PREFIX):
        return ""
    _LAND["n_try"] += 1
    p = CACHE / "cache_landing" / ("land_" + re.sub(r"[^\w.-]", "_", doi)[:120] + ".json")
    if p.exists():
        try:
            got = json.loads(p.read_text(encoding="utf-8")).get("abstract", "")
            _LAND["n_got"] += 1 if got else 0
            return got
        except Exception:
            pass
    try:
        req = urllib.request.Request(
            "https://doi.org/" + urllib.parse.quote(doi),
            headers={"User-Agent": BROWSER_UA,
                     "Accept": "text/html,application/xhtml+xml",
                     "Accept-Language": "en-US,en;q=0.9",
                     "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            page = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""          # 没取到页面：这一次不算「这篇没有摘要」，不写缓存
    best = ""
    # 逐个 <meta> 标签看而不是写死「name 在 content 前」：属性顺序不保证，
    # 写死顺序会在部分页面上静默失配
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        if not any(re.search(r'(?:name|property)="%s"' % re.escape(n), tag, re.I)
                   for n in LANDING_ABS_META):
            continue
        m = re.search(r'content="([^"]*)"', tag, re.I)
        if not m:
            continue
        cand = fc.clean(html.unescape(m.group(1)))
        if len(cand) > len(best):
            best = cand
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"doi": doi, "abstract": best}, ensure_ascii=False),
                 encoding="utf-8")
    _LAND["n_got"] += 1 if best else 0
    return best


def landing_stats():
    """(试了几次, 拿到几篇)——主流程记日志用。"""
    return _LAND["n_try"], _LAND["n_got"]


def fill_abstract(doi, cur):
    if len(cur) >= 200 or not doi:
        return cur
    for sub, url, pick in (
        ("cache_crdoi", f"https://api.crossref.org/works/{urllib.parse.quote(doi)}",
         lambda d: fc.clean((d.get("message") or {}).get("abstract", ""))),
        ("cache_s2", "https://api.semanticscholar.org/graph/v1/paper/DOI:"
         + urllib.parse.quote(doi) + "?fields=abstract",
         lambda d: fc.clean(d.get("abstract", "") or "")),
    ):
        try:
            extra = pick(cached_json(url, ("doi_" if sub == "cache_crdoi" else "s2_") + doi,
                                     sub, sleep=1.5))
        except Exception:
            continue
        if len(extra) > len(cur):
            return extra
    # 第三跳：出版社页面（只对 LANDING_ABS_PREFIX 里的前缀，见上面的注释）
    extra = landing_absolute(doi)
    if len(extra) > len(cur):
        return extra
    return cur


def oa_info(doi):
    """返回 (pdf_url, page_url, is_oa)。is_oa 仅当 Unpaywall/出版方明确开放获取。

    非开放获取时仍返回出版方链接，但调用方必须区分标注，不要谎称「开放获取」。
    """
    if not doi:
        return "", "", False
    try:
        d = cached_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={MAILTO}",
                        "upw_" + doi, "cache_unpaywall")
        if d.get("is_oa"):
            best = d.get("best_oa_location") or {}
            pdf = best.get("url_for_pdf") or d.get("best_oa_location", {}).get("url") or ""
            page = best.get("url") or d.get("doi_url") or ""
            if pdf or page:
                return pdf or page, page, True
    except Exception:
        pass
    try:
        d = cached_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}",
                        "doi_" + doi, "cache_crdoi")
        links = (d.get("message") or {}).get("link") or []
        pdf = next((l.get("URL") for l in links
                    if l.get("content-type") == "application/pdf"), "")
        page = next((l.get("URL") for l in links
                     if str(l.get("content-type", "")).startswith("text/html")), "")
        return pdf, page, False
    except Exception:
        return "", "", False


# 翻译分块与重试：一次请求翻全部 16 篇时，任何一个坏响应都会让整期日报退回英文标题
# ——2026-09-15 实测就是如此（HTTP 200 但响应体没有 choices，KeyError 'choices'）。
# 改成 4 篇一块、每块最多 3 次重试后，单点失败的爆炸半径从 16 篇降到 4 篇，且每块
# 成功即落盘，重跑只为缺口付费。代价只有重复的系统提示（输入 +15% 量级），重试本身
# 也只重付输入、不付输出（实测约 0.004 元/次）。
TRANS_CHUNK = 4
TRANS_RETRY = 3
TRANS_BACKOFF = 4.0

# DeepSeek 价目（元/百万 token），空闲时段、左一档模型：缓存命中 0.02 / 未命中 1.0 /
# 输出 4.0。高峰时段是这套的 2 倍。价格变了改这里，日报「统计口径」按它算翻译花销。
PRICE_IN_HIT = 0.02
PRICE_IN_MISS = 1.0
PRICE_OUT = 4.0


def usage_cost(u):
    """按响应里的 usage 算这一块的费用（元）。

    只有 API 明确报出 usage 的请求才算得进来；坏响应（拿不到 usage）不计费，
    所以这个数是**下界**——但线上重试本来就只重付输入，误差可忽略。
    """
    pin = u.get("prompt_tokens", 0) or 0
    hit = u.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    pout = u.get("completion_tokens", 0) or 0
    return ((max(0, pin - hit) * PRICE_IN_MISS + hit * PRICE_IN_HIT
             + pout * PRICE_OUT) / 1e6)


def usage_split(u):
    """(输入, 缓存命中, 输出) 三个 token 数，供日志打印。"""
    pin = u.get("prompt_tokens", 0) or 0
    hit = u.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    return pin, hit, u.get("completion_tokens", 0) or 0


def translate(items, model, log):
    """分块翻译标题与摘要，并生成 90–150 字中文提要；返回 (zh, 新译篇数, 失败篇数, 本次花费)。

    坏响应只影响本块：某块 3 次都失败时其余块照常落盘，日报里那 4 篇退回英文。
    缓存是 TRANS_CACHE（cache_translate_v2.json），键为 DOI。
    """
    if not items:
        return {}, 0, 0, 0.0
    try:
        import tomllib
        cfg = tomllib.loads((Path.home() / ".kimi-code" / "config.toml").read_text(encoding="utf-8"))
        key = cfg["providers"]["deepseek"]["api_key"]
    except Exception as e:
        log(f"  翻译跳过：读不到 DeepSeek key（{e}）")
        return {}, 0, len(items), 0.0

    cache_path = TRANS_CACHE
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [x for x in items if x["doi"] not in cache]
    n_failed = 0
    if todo:
        sys_p = ("你是锂电正极材料的科技翻译。输入是 JSON 数组，每项含 i(标识) / title / abstract。"
                 "输出严格 JSON 数组，每项 {i, zh_title, zh_abstract, zh_summary}："
                 "zh_title 为准确简洁的中文标题；"
                 "zh_abstract 为摘要的中文翻译，必须保留全部数值、单位、电位、容量、温度、循环数与"
                 "化学式不变，不要增删信息；"
                 "zh_summary 为 90–150 字的中文提要，必须覆盖方法核心、主要贡献、关键结果三件事，"
                 "不要分点、不要「本文提出了一种…」这类套话；"
                 "写完自检：不足 90 字、或与其他论文的摘要高度雷同，就重写；"
                 "abstract 为空时 zh_abstract 与 zh_summary 都返回空字符串。"
                 "只输出 JSON，不要任何解释或代码块标记。")
        chunks = [todo[i:i + TRANS_CHUNK] for i in range(0, len(todo), TRANS_CHUNK)]
        n_done, cost = 0, 0.0
        for ci, chunk in enumerate(chunks, 1):
            body = json.dumps({
                "model": model,
                "thinking": {"type": "disabled"},
                "messages": [{"role": "system", "content": sys_p},
                             {"role": "user", "content": json.dumps(
                                 [{"i": x["doi"], "title": x["title"],
                                   "abstract": x["abstract"][:2600]} for x in chunk],
                                 ensure_ascii=False)}],
                "max_tokens": 16000, "temperature": 0.2, "stream": False,
            }).encode()
            got = None
            for attempt in range(1, TRANS_RETRY + 1):
                t0 = time.time()
                try:
                    req = urllib.request.Request(
                        "https://api.deepseek.com/chat/completions", data=body,
                        headers={"Authorization": "Bearer " + key,
                                 "Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=300) as r:
                        resp = json.load(r)
                    if "choices" not in resp:
                        raise RuntimeError("响应里没有 choices："
                                           + json.dumps(resp, ensure_ascii=False)[:200])
                    txt = resp["choices"][0]["message"]["content"].strip()
                    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
                    got = json.loads(txt)
                    u = resp.get("usage", {})
                    pin, phit, pout = usage_split(u)
                    c = usage_cost(u)
                    cost += c
                    log(f"  翻译第 {ci}/{len(chunks)} 块 {len(chunk)} 篇：{time.time()-t0:.1f}s，"
                        f"输入 {pin}（缓存命中 {phit}）"
                        f" / 输出 {pout} tokens ≈ {c:.4f} 元")
                    break
                except Exception as e:
                    msg = f"{type(e).__name__} {e}"
                    if attempt < TRANS_RETRY:
                        wait = TRANS_BACKOFF * attempt
                        log(f"  ! 翻译第 {ci}/{len(chunks)} 块第 {attempt} 次失败"
                            f"（{msg}），{wait:.0f}s 后重试")
                        time.sleep(wait)
                    else:
                        log(f"  ! 翻译第 {ci}/{len(chunks)} 块 {TRANS_RETRY} 次均失败"
                            f"（{msg}），本块 {len(chunk)} 篇退回英文")
            if got is None:
                n_failed += len(chunk)
                continue
            for e in got:
                i = str(e.get("i") or "").strip()
                if not i:
                    continue
                cache[i] = {"zh_title": e.get("zh_title") or "",
                            "zh_abstract": e.get("zh_abstract") or "",
                            "zh_summary": e.get("zh_summary") or ""}
                n_done += 1
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        log(f"  翻译落盘 {n_done} 篇（共 {len(chunks)} 块，失败 {n_failed} 篇），"
            f"本次约 {cost:.4f} 元（空闲时段价，高峰翻倍）")
    else:
        log(f"  翻译全部命中缓存（{len(items)} 篇），本次零 LLM 花销")
        cost = 0.0

    used = {x["doi"]: cache.get(x["doi"], {}) for x in items}
    return used, len(todo) - n_failed, n_failed, cost


# ---------------------------------------------------------------- 主流程
CAT_ORDER = _topic.CATEGORY_ORDER
CHANNELS = ["fresh", "query", "refs"]
PUB_RE = re.compile(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?")


def w_desc(weights, w_max):
    """分类权重系数 w/w_max（诊断用）。

    权重本身写在 state/profile.json（由 build_profile.py 生成，含手调偏好 CAT_PREF）。
    R 与分类权重解耦后，这串系数**不再进 R 的打分**，只经 citation_term 的 q̄ 生效
    （库内权重高的方向引用了它）；打印它是为了复核画像折算有没有按预期生效，不再
    代表某类文献在版面里的分量。
    """
    return "、".join(f"{c} {weights.get(c, 1.0) / w_max:.2f}" for c in CAT_ORDER)


def parse_cat_floors(text):
    """`力学耦合=3,界面反应=2` -> {分类: 名额}；空串 = 关闭保底。

    分类名必须校验并报错退出：静默忽略认不出的名字，会让人以为调过了、其实没生效
    （默认值里的两个分类是照着「去掉 w_cat 之前」的实测构成定的）。
    """
    out = {}
    for part in (text or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, val = part.partition("=")
        name = name.strip()
        if not sep or name not in CAT_ORDER:
            raise ValueError(f"--cat-floor 的「{part}」不是「分类=整数」且分类合法的一项；"
                             f"合法分类：{'、'.join(CAT_ORDER)}")
        try:
            n = int(val.strip())
        except ValueError:
            raise ValueError(f"--cat-floor 里「{name}」的名额「{val.strip()}」不是整数")
        if n < 0:
            raise ValueError(f"--cat-floor 里「{name}」的名额不能是负数")
        if n:
            out[name] = n
    return out


def resolve_floors(spec, cap, limit, log):
    """保底名额过两道约束，返回 (floors, reserved)。

    单类名额超过每类上限 cap 时保底必然撞 cap（天天报不满、日志天天告警），压到 cap；
    名额合计 ≥ --limit 时第一轮就没版面可分，按比例缩到 limit//2，缩成 0 的类直接退出
    （留着只会在日志里排一排 0/0 的空席位）。
    """
    floors = {}
    for c, n in spec.items():
        if n > cap:
            log(f"  ! 保底席位：{c} {n} 篇超过每类上限 cap={cap}，压到 {cap} 篇")
            n = cap
        if n > 0:
            floors[c] = n
    reserved = sum(floors.values())
    if reserved >= limit:
        keep = limit // 2
        log(f"  ! 保底席位：名额合计 {reserved} 篇 ≥ --limit {limit}，按比例缩到 {keep} 篇")
        floors = {c: int(n * keep / reserved) for c, n in floors.items()}
        floors = {c: n for c, n in floors.items() if n > 0}
        reserved = sum(floors.values())
    return floors, reserved


def parse_pub_date(s):
    """把 'YYYY-MM-DD' / 'YYYY-MM' / 'YYYY' 解析成 date；解析不出返回 None。

    只给年份时按 1 月 1 日算（偏保守，等同于最老的解读）。
    """
    m = PUB_RE.match(str(s or "").strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def period_tag(today, refresh_days):
    """query / refs 通道的缓存键用的**刷新周期桶**（如 'P30-74000'）。

    这两个通道的检索式里没有日期，键里不带周期就会一次跑完永远冻在磁盘上，
    新的中间相关度论文再也进不来。周期长度取 config/runtime.json 的 pool.refresh_days
    （默认 30 天），同一周期内重跑零网络、跨周期自动刷新（原先写死成 ISO 年月）。
    键里带上周期长度本身，改了 refresh_days 自然换桶，新旧周期不会串味。
    """
    n = max(1, int(refresh_days))
    d = date.fromisoformat(str(today or date.today().isoformat())[:10])
    return f"P{n}-{d.toordinal() // n}"


def last_push_date():
    """state/last_push.json 里上次成功出报的日期；文件缺失或读不出返回 None。

    频率闸门（schedule.every_n_days）只看这个日期——不猜「日报文件在不在」，因为
    没候选、翻译全失败、手工删过报都会让文件与「跑过了」对不上。
    """
    try:
        v = json.loads(LAST_RUN.read_text(encoding="utf-8")).get("last_push") or ""
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def schedule_skip(today_d, last, manual):
    """按 config/runtime.json 的 schedule 段判断这期该不该跳过；返回原因串（空串 = 照跑）。

    manual（--dry-run / 显式 --date / --force）一律放行：dry-run 要能看到真实结果，
    回填历史与手动强制都必须跑得动。日期比对用**绝对差**，所以 last_push 落在今天
    之后（回填过历史或未来日期）同样算「刚跑过」。
    """
    if manual:
        return ""
    if not _rt.SCHEDULE["enabled"]:
        return "config/runtime.json 的 schedule.enabled = false"
    every = _rt.SCHEDULE["every_n_days"]
    if every <= 1 or last is None:
        return ""
    gap = abs((today_d - last).days)
    if gap < every:
        return (f"上次出报 {last.isoformat()}，与今天 {today_d.isoformat()} 相差 {gap} 天，"
                f"未到 schedule.every_n_days={every} 天")
    return ""


def load_library(log):
    """读库内索引（全部只读）。

    返回 (lib_dois, lib_titles, doi2key, doi_year, key2primary, key2meta)：
    key2meta 是 {key: {title, primary, sub, materials, form}}，日报的「库内关联」用它
    把引用者 DOI 换成可读的篇名与分类。
    """
    lib_dois, lib_titles, doi2key, doi_year = set(), [], {}, {}
    if LIB_INDEX.exists():
        for x in json.loads(LIB_INDEX.read_text(encoding="utf-8")).get("items", []):
            if x.get("norm_title"):
                lib_titles.append(x["norm_title"])
            if x.get("doi"):
                d = x["doi"].lower()
                lib_dois.add(d)
                doi2key[d] = x.get("key")
                doi_year[d] = x.get("year")
    else:
        log("  ! 缺 state/library_index.json（跑 scripts/library_index.py 生成），"
            "DOI 级去重会退化")
    # 向后兼容：digest.json 里若有 doi 字段也并进来（当前实测 0 条）
    try:
        for x in json.loads((STATE / "digest.json").read_text(encoding="utf-8")):
            d = str(x.get("doi") or "").lower()
            if d.startswith("10."):
                lib_dois.add(d)
    except Exception as e:
        log(f"  ! 读 state/digest.json 失败（{e}），只用以 library_index.json 为准的 DOI 集")
    key2primary, key2meta, titles_cls = {}, {}, []
    cls_path = STATE / "classification.json"
    if not cls_path.exists():
        # 唯一必须人工撰写的核心数据，缺了就没有分类 / 库内关联 / 标签可打，不是
        # 「能降级跑」的那一类输入；裸 FileNotFoundError 只说文件不在，不说该怎么补
        raise SystemExit(f"缺 {cls_path}：日报的分类、库内关联与标签都读它。"
                         f"冷启动可先 `echo '[]' > {cls_path}` 占位，"
                         f"再按 帮助手册.md §5.4 的格式逐篇补")
    for c in json.loads(cls_path.read_text(encoding="utf-8")):
        titles_cls.append(fc.norm(c["title"]))
        if c.get("key") and c.get("primary"):
            key2primary[c["key"]] = c["primary"]
            key2meta[c["key"]] = {"title": c.get("title") or c["key"],
                                  "primary": c["primary"],
                                  "sub": c.get("sub") or [],
                                  "materials": c.get("materials") or [],
                                  "form": c.get("form") or [],
                                  "system": c.get("system") or []}
    # library_index 的 norm_title 并进 known() 的精确匹配集合（比只靠 difflib 0.90 更硬）
    for t in titles_cls:
        if t and t not in lib_titles:
            lib_titles.append(t)
    log(f"  库内文献 {len(lib_titles)} 条标题，其中 DOI 去重集 {len(lib_dois)} 个"
        f"（{len(doi2key)} 篇能联表到分类）")
    return lib_dois, lib_titles, doi2key, doi_year, key2primary, key2meta


def lib_links(doi, cls, co_map, doi2key, key2meta, limit=4):
    """库内关联：谁引用了它 / 库内谁和它同类。零 API 花销，只读本地引文图与分类。

    只转述本地事实（引用边、标签重合），不推断内容关系。库内既没引用、标签也
    无交集时返回空串——宁可不写，也不编一句「相关性」。
    """
    citers = ((co_map.get(doi) or co_map.get(doi.lower()) or {}).get("citers") or [])
    metas = [key2meta[doi2key[d]] for d in citers
             if d in doi2key and doi2key[d] in key2meta]
    if metas:
        dist = Counter(m["primary"] for m in metas)
        dist_s = "、".join(f"{k} {v}" for k, v in dist.most_common())
        # 同类先列：候选自己的分类和哪些引用者对得上，是最省事的判断依据
        metas.sort(key=lambda m: (m["primary"] != cls["primary"], m["primary"], m["title"]))
        names = "；".join(f"《{_short(m['title'])}》（{m['primary']}）"
                          for m in metas[:limit])
        tail = f"，共 {len(metas)} 篇" if len(metas) > limit else ""
        return f"被库内 {len(citers)} 篇引用（{dist_s}）：{names}{tail}"
    subs = set(cls.get("sub") or [])
    mats = set(cls.get("materials") or [])
    forms = set(cls.get("form") or [])
    systems = set(cls.get("system") or [])
    hits = []
    for m in key2meta.values():
        s = (2 * len(subs & set(m["sub"])) + len(mats & set(m["materials"]))
             + len(forms & set(m["form"])) + len(systems & set(m["system"])))
        if s:
            hits.append((s, m))
    if not hits:
        return ""
    hits.sort(key=lambda x: (-x[0], x[1]["primary"], x[1]["title"]))
    names = "；".join(f"《{_short(m['title'])}》（{m['primary']}）" for _, m in hits[:3])
    return f"库内暂无引用；与库内 {len(hits)} 篇标签重合（{cls['primary']}），如：{names}"


def _short(s, n=60):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def make_known(lib_titles):
    import difflib

    def known(title):
        n = fc.norm(title)
        if not n:
            return True
        if n in lib_titles:
            return True
        for t in lib_titles:
            if len(t) > 30 and (n.startswith(t[:60]) or t.startswith(n[:60])):
                return True
            if difflib.SequenceMatcher(None, n, t).ratio() >= 0.90:
                return True
        return False
    return known


def load_recs():
    """推送状态：{doi: {first_pushed, last_pushed, times_pushed, channel, score, status}}。

    旧 state/digest_seen.json 的 {doi: 日期} 会迁移过来（缺的字段补默认值），
    旧文件**保留不删**——那是用户数据。
    """
    recs = {}
    if RECS.exists():
        try:
            recs = json.loads(RECS.read_text(encoding="utf-8"))
        except Exception:
            recs = {}
    migrated = 0
    if RECS_LEGACY.exists():
        try:
            for d, dt in json.loads(RECS_LEGACY.read_text(encoding="utf-8")).items():
                if d in recs:
                    continue
                recs[d] = {"first_pushed": dt, "last_pushed": dt, "times_pushed": 1,
                           "channel": "fresh", "score": None, "status": "pushed"}
                migrated += 1
        except Exception:
            pass
    return recs, migrated


def blocked_by_state(doi, recs, today, cooldown_days):
    """冷却期内 / 已在库的，跳过。"""
    rec = recs.get(doi)
    if not rec:
        return False
    if rec.get("status") == "in_library":
        return True
    if rec.get("status") != "pushed":
        return False
    d0 = parse_pub_date(rec.get("last_pushed"))
    if d0 is None:
        return False
    return (date.fromisoformat(today) - d0).days < cooldown_days


def gate_candidates(res, cat, pool, pool_titles, lib_dois, recs, today, cooldown_days,
                    known, channel, min_year=None):
    """把一批检索结果过闸塞进 pool，返回本批新入池条数。

    闸门顺序与旧逻辑一致：期刊白名单 → 标题去重 → 相关性 → 层状氧化物正极。
    检索类的通道（fresh / query）额外可加 --min-year 年份下限。
    """
    n = 0
    for r in res:
        doi, title = r["doi"], r["title"]
        if not title or not doi:
            continue
        cur = pool.get(doi)
        if cur is not None:
            # 同一通道内被多条检索式命中：rank 取最好（最小）的一个。
            # 跨通道不改：fresh 的 rank 优先，两条相关度排序不可比。
            if cur["channel"] == channel and r["rank"] < cur["rank"]:
                cur["rank"] = r["rank"]
                cur["hit_cat"] = cat
            continue
        if doi in lib_dois or blocked_by_state(doi, recs, today, cooldown_days):
            continue
        if not fc.journal_ok(r["journal"]):
            continue
        if known(title):
            continue
        if min_year:
            d0 = parse_pub_date(r["date"])
            if d0 and d0.year < min_year:
                continue
        if not fc.relevant(title, r["abstract"], cat):
            continue
        # 必须落在层状氧化物正极体系
        if not LAYERED_PAT.search(f"{fc.dashes(title)} {fc.dashes(r['abstract'][:2000])}"):
            continue
        # 同一篇的德文版/国际版等不同 DOI 同标题记录，只留一条
        tk = fc.norm(title)
        if tk in pool_titles:
            continue
        r["hit_cat"] = cat
        r["channel"] = channel
        r["cited_by"] = []
        pool[doi] = r
        pool_titles[tk] = doi
        n += 1
    return n


def citation_term(citers, weights, w_max, doi2key, key2primary, doi_year, today_d,
                  tau_years=C_AGE_TAU_Y, age_floor=C_AGE_FLOOR):
    """C = min(1, ln(1+n)/ln(31)) · (0.65+0.35·q̄) · (0.75+0.25·r̄)。

    n  = 被几篇库内文献引用（主信号，对数饱和：n=1→0.20、n=10→0.69、n=30→1.00）
    q̄  = 引用者的平均分类权重比（w_i/w_max），联不上分类的用已联表者的平均
    r̄  = 引用者的平均 exp(-Δy/tau)，Δy 是引用者年龄、单位**年**（tau 默认 3 年）
    年龄项被地板 age_floor（默认 0.75）限住，最多只做 25% 的调节：旧公式的
    Σ exp(-age_i/730天)/4 让 10 年前的引用只剩 0.7% 权重，实际上把「被多少篇
    库内文献引用」退化成了年龄惩罚，这才是老经典掉分的根源。
    引用者年份按当年 7 月 1 日近似；年份缺失的用已知者的平均年龄。
    """
    n = len(citers)
    known_w, known_age = [], []
    for d in citers:
        cat = key2primary.get(doi2key.get(d) or "")
        if cat:
            known_w.append(max(weights.get(cat, 1.0), 0.1))
        y = doi_year.get(d)
        if y:
            known_age.append(max(0.0, (today_d - date(y, 7, 1)).days / 365.25))
    w_avg = (sum(known_w) / len(known_w) if known_w else 1.0) / w_max
    age_avg = sum(known_age) / len(known_age) if known_age else 3.0
    q_sum, r_sum, n_w, n_y = 0.0, 0.0, 0, 0
    for d in citers:
        cat = key2primary.get(doi2key.get(d) or "")
        if cat:
            w = max(weights.get(cat, 1.0), 0.1) / w_max
            n_w += 1
        else:
            w = w_avg
        y = doi_year.get(d)
        if y:
            dy = max(0.0, (today_d - date(y, 7, 1)).days / 365.25)
            n_y += 1
        else:
            dy = age_avg
        q_sum += w
        r_sum += math.exp(-dy / tau_years)
    if not n:
        return 0.0, n_w, n_y
    q_bar, r_bar = q_sum / n, r_sum / n
    base = min(1.0, math.log1p(n) / math.log(C_N_SAT))
    return (base * (C_W_FLOOR + (1.0 - C_W_FLOOR) * q_bar)
            * (age_floor + (1.0 - age_floor) * r_bar)), n_w, n_y


def classic_term(cited):
    """X = min(1, ln(1+cited)/ln(5001))：经典度只看绝对被引量，与年龄无关。

    5000 被引顶到 1；2000→0.90、500→0.73、74→0.51、5→0.21、0→0。用对数饱和
    而不是线性，高被引经典顶到接近 1、普通论文也仍有分数，不会「差 10 倍就归零」。
    cited 取 Crossref 的 is-referenced-by-count（三个通道都取），取不到按 0 算。
    """
    try:
        c = float(cited or 0)
    except (TypeError, ValueError):
        c = 0.0
    return min(1.0, math.log1p(max(c, 0.0)) / math.log(X_SAT))


def journal_term(ifv):
    """J' = clamp(ln(IF/4)/ln(8), 0, 1) - 0.5，以白名单中位刊（IF≈4）为零点。

    IF 是 state/journal_if.json 里的 OpenAlex 2yr_mean_citedness（代理指标，
    不是 JCR）。IF=8 → -0.17、IF=15 → +0.14、IF=31 → +0.49、IF=3.5 → -0.50。
    减 0.5 不能省：直接留 [0,1] 会让所有白名单期刊白拿 0.1~0.15，把总分整体抬高、
    --min-score 门槛失去意义；减完是「好刊加分、普通刊小扣」，净效应接近零。
    查不到 IF 返回 0（中性，不奖也不罚）。
    """
    if not ifv or ifv <= 0:
        return 0.0
    v = min(1.0, max(0.0, math.log(ifv / J_IF_ZERO) / math.log(J_IF_TOP / J_IF_ZERO)))
    return v - 0.5



# 引文候选的刊名是 ISO 缩写（J. Power Sources / Adv. Energy Mater.），而
# find_new.JOURNALS 只收全名，journal_ok 的字母数字比对对不上（实测 759 个已过
# LAYERED_PAT 的候选被这一步误杀）。缩写展开这套逻辑（J_ABBR / j_tokens /
# j_token_match / cited_journal_ok）已集中到 journal_if.py，与 IF 查表共用同一份
# 实现；本文件在文件头 from journal_if import cited_journal_ok 直接引用。


def build_refs_pool(co, meta, lib_dois, recs, today, cooldown_days, log, tag=None):
    """引文通道：先用 ref_meta 做零网络初筛，剩下的才按 DOI 批量取元数据。

    tag 是缓存键的刷新周期标记：refs 的 DOI 批次键不含日期，不带它就永远冻住。
    """
    n0 = len(co)
    s0, s1, matched = 0, {}, {}
    for d, v in co.items():
        if d in lib_dois or blocked_by_state(d, recs, today, cooldown_days):
            continue
        s0 += 1
        ok, jn = cited_journal_ok((meta.get(d) or {}).get("j") or "")
        if not ok:
            continue
        s1[d] = v
        matched[d] = jn
    log(f"  引文通道：co_citation {n0} → 剔库内/冷却 {s0}"
        f" → 期刊白名单 {len(s1)}（缩写展开，零网络）")
    s2 = {d: v for d, v in s1.items()
          if LAYERED_PAT.search(fc.dashes((meta.get(d) or {}).get("t") or ""))}
    log(f"             → 层状正极标题（ref_meta.t）{len(s2)}（仍零网络）")
    if not s2:
        return {}
    fetched = fetch_doi_meta(sorted(s2), log, tag=tag)
    log(f"             → 批量取元数据命中 {len(fetched)}/{len(s2)}"
        f"（Crossref 批量 {(len(s2) + 99) // 100} 批，不逐条取）")
    pool, titles, kept = {}, {}, 0
    for d in sorted(s2):
        r = fetched.get(d)
        if not r or not r["title"]:
            continue
        tk = fc.norm(r["title"])
        if not tk or tk in titles:
            continue
        cls = classify(r["title"], r["abstract"])
        if not fc.relevant(r["title"], r["abstract"], cls["primary"]):
            continue
        r["hit_cat"] = None
        r["channel"] = "refs"
        r["cited_by"] = s2[d]["citers"]
        r["cited_journal"] = matched.get(d)      # 缩写展开后匹配到的白名单刊名
        r["cls"] = cls
        pool[d] = r
        titles[tk] = d
        kept += 1
    log(f"             → 过 relevant() + 标题去重 {kept} 篇"
        f"（这些是要花网络请求的候选）")
    return pool


def freshness(date_str, today_d):
    """F = exp(-age_days/180)。published 解析不出返回 (0.0, None)，保守不给奖励。

    F 只做加成：老文 ×1.0、新文最多 ×(1+F_GAIN)。必须用 published（r["date"]），
    不能用 Crossref 的 created：created 是入库日，旧文被重新 deposit 会被误判成新文。
    """
    d0 = parse_pub_date(date_str)
    if d0 is None:
        return 0.0, None
    age = max(0, (today_d - d0).days)
    return math.exp(-age / F_HALF_LIFE), age


# ---------------------------------------------------------------- S 项：库内语义相似度
LIB_EMB = CACHE / "lib_emb.npz"        # 由 scripts/build_embeddings.py 离线生成
SIM_MODEL = "all-MiniLM-L6-v2"         # npz 里没记模型名时的兜底（两侧必须同模型）

_SIM = {"state": "unprobed", "why": "", "model": None, "mat": None,
        "n_err": 0, "n_empty": 0}


def sim_probe():
    """探测 S 能不能用，返回 (可用?, 原因)；只探测一次，之后复用。

    必须在打分**之前**调：R/S 的权重取决于这个结果。不可用属于正常情况（没装
    sentence-transformers、没跑 build_embeddings.py、权重下不下来、库索引读坏），
    所以这里只探测、不抛异常——日报没有 S 也必须照常出，只是权重回补给 R。
    模型在这里加载一次、之后所有候选复用：每个候选重载一次模型是跑不完的量级。
    """
    if _SIM["state"] != "unprobed":
        return _SIM["state"] == "ready", _SIM["why"]
    if not LIB_EMB.exists():
        _SIM.update(state="missing",
                    why=f"没有 {LIB_EMB.relative_to(ROOT)}（跑 scripts/build_embeddings.py 生成）")
        return False, _SIM["why"]
    try:
        import numpy as np
        with np.load(LIB_EMB) as z:
            mat = np.asarray(z["mat"], dtype="float32")
            model_name = str(z["model"]) if "model" in z.files else SIM_MODEL
    except Exception as e:
        _SIM.update(state="broken", why=f"读 {LIB_EMB.name} 失败：{type(e).__name__} {e}")
        return False, _SIM["why"]
    if mat.ndim != 2 or not len(mat):
        _SIM.update(state="broken", why=f"{LIB_EMB.name} 的向量矩阵形状不对 {mat.shape}")
        return False, _SIM["why"]
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        _SIM.update(state="nodep",
                    why=f"缺 sentence-transformers（{e}）；装上并跑 "
                        f"scripts/build_embeddings.py 后 S 才生效")
        return False, _SIM["why"]
    try:
        model = SentenceTransformer(model_name)
    except Exception as e:
        _SIM.update(state="broken", why=f"加载 {model_name} 失败：{type(e).__name__} {e}")
        return False, _SIM["why"]
    _SIM.update(state="ready", why=f"库内 {len(mat)} 篇，模型 {model_name}",
                model=model, mat=mat)
    return True, _SIM["why"]


def profile_similarity(r):
    """S = 候选与库内最相似那一篇的余弦（取 max，不取平均）。

    平均会被大片不相关篇目稀释：库里 147 篇，任何候选与「平均文献」的相似度都趋同，
    max 才回答「库里有没有和它很像的工作」。候选文本是 title + abstract；摘要常常
    是空的（Elsevier 系多不给开放摘要，那天填摘要的短名单才有），这时只用标题——
    弱归弱，也远好过把候选一律当成不相关。
    不可用（sim_probe 判定）或文本为空时返回 0.0：原因已经由 sim_probe 记进日志，
    权重也早就回补给 R 了，这里不再重复报。
    """
    if _SIM["state"] != "ready":
        return 0.0
    text = " ".join(x for x in [(r.get("title") or "").strip(),
                                (r.get("abstract") or "").strip()] if x)
    if not text:
        # 标题与摘要都空：没有可比对的文本，记 0 并计数，主流程在日志里报总数
        _SIM["n_empty"] += 1
        return 0.0
    try:
        v = _SIM["model"].encode([text], normalize_embeddings=True,
                                 convert_to_numpy=True)[0]
    except Exception:
        # 单篇编码失败不该拖垮整期日报，记 0 继续，最后在日志里报总数
        _SIM["n_err"] += 1
        return 0.0
    # 其余各项都在 [0,1]（J' 是唯一的双极项），S 只做正向加分，负余弦按 0 算
    return max(0.0, min(1.0, float((_SIM["mat"] @ v).max())))


def sim_stats():
    """S 降级到 0 的计数（主流程记日志用）：编码失败 / 候选无文本。"""
    return _SIM["n_err"], _SIM["n_empty"]


# ---------------------------------------------------------------- 打分
def scoring_weights(use_s):
    """本次运行生效的打分权重：S 用不了时把它的 0.15 **回补给 R**。

    只在主流程按 sim_probe() 的结果算一次再往下传，不写成两套散落的常量——同一批
    候选在不同机器（装没装 sentence-transformers）上必须落在同一个分数尺度上，
    否则 --min-score 门槛与版面排名会随机器变。
    """
    return {"R": W_R - W_S if use_s else W_R, "S": W_S if use_s else 0.0,
            "C": WC, "X": WX, "J": WJ, "A": WA}


def base_score(r, sc):
    """B = WR·R + WS·S + WC·C + WX·X + WJ·J' + WA·A（不含新鲜度）。"""
    return (sc["R"] * r["R"] + sc["S"] * r["S"] + sc["C"] * r["C"]
            + sc["X"] * r["X"] + sc["J"] * r["J"] + sc["A"] * r["A"])


def pre_score(r, sc):
    """不含 A 的 B，用来排短名单与 OA 查询集——A 要花钱查才知道，不能让它定谁入选。"""
    return (sc["R"] * r["R"] + sc["S"] * r["S"] + sc["C"] * r["C"]
            + sc["X"] * r["X"] + sc["J"] * r["J"])


def final_score(r, sc):
    """B * (1 + F_GAIN*F)；综述 ×0.9。"""
    s = base_score(r, sc) * (1.0 + F_GAIN * r["F"])
    if r["cls"]["is_review"]:
        s *= 0.9
    return s


def diag_categories(ordered, log, top=6):
    """调检索式/权重时的诊断：各类候选有多少、从哪来、最高分多少。

    最终版面要跟通道配额与每类上限（limit 的 40%）竞争，所以「某一类为什么没进来」
    光看分数不够。分类权重现在只经 C 的 q̄ 生效（R 已与它解耦），要抬某类主要靠加
    检索式；调完想知道效果，看这个：候选数量、命中来源（哪条检索式捞到的）、以及
    离入选线差多少分。`hit_cat` 与 `cls.primary` 差得越远，说明「哪条检索式命中」和
    「这篇属于哪类」越对不上（R 现在只看 rank，与两者都无关）。
    """
    for c in CAT_ORDER:
        grp = [r for r in ordered if r["cls"]["primary"] == c]
        if not grp:
            continue
        hc = Counter(r.get("hit_cat") or "refs通道" for r in grp)
        log(f"    [{c}] {len(grp)} 篇，命中来源："
            + "、".join(f"{k} {v}" for k, v in hc.most_common()))
        log(f"    [{c}] 最高分：" + " | ".join(
            f"{r['score']:.3f}(R={r['R']:.2f} S={r['S']:.2f} C={r['C']:.2f} X={r['X']:.2f}"
            f" F={r['F']:.2f}) {r['title'][:34]}" for r in grp[:top]))


def diag_pagination(queries, a, since, lib_dois, recs, today, known, log):
    """对比 rows=30 单页 vs rows=200 多页：候选少是 rows 截断还是领域真的没新文。

    同时打印 Crossref 的 total-results，那是该窗口内存在记录数的上限；
    total-results 明显小于「rows × 页数」时，多翻页捞不到更多东西。
    """
    log(f"  分页诊断（窗口 {since} 起）：")
    for rows, pages in ((30, 1), (200, 3)):
        pool, titles, n_raw, totals = {}, {}, 0, []
        for cat, qs in queries.items():
            for q in qs:
                try:
                    res = search_crossref(q, since, rows=rows, pages=pages, stats=totals)
                except Exception as e:
                    log(f"    ! {cat} / {q}: {e}")
                    continue
                n_raw += len(res)
                gate_candidates(res, cat, pool, titles, lib_dois, recs, today,
                                a.cooldown_days, known, "fresh")
        label = "单页" if pages == 1 else f"分页（{pages} 页）"
        # total-results 是「该检索式在窗口内的记录总数」，每页返回同一个值，故只报最大
        log(f"    rows={rows:<3} {label}：检索 {n_raw} 条 → 通过闸门 {len(pool)} 篇"
            + (f"（Crossref total-results 最大 {max(totals)} 条/检索式）"
               if totals else ""))
    log("    （diagnose 用同一条闸门链，不影响后续正常流程）")


def main():
    ap = argparse.ArgumentParser(
        description="每日文献日报。运行参数的默认值来自 config/runtime.json，"
                    "想确认生效值用 --show-config。")
    ap.add_argument("--days", type=int, default=_rt.DIGEST["days"],
                    help="fresh 通道窗口天数（按 Crossref 创建日期；默认值来自 config/runtime.json）")
    ap.add_argument("--limit", type=int, default=_rt.DIGEST["limit"],
                    help="日报篇数，建议 10–20（默认值来自 config/runtime.json）")
    ap.add_argument("--min-year", type=int, default=_rt.DIGEST["min_year"],
                    help="query 通道年份下限，0（默认）= 不限年份。旧默认 2010 会把"
                         "1980 年的 LiCoO2 开山作、1996 年的 PBE 这类经典直接挡在闸门外，"
                         "与「经典不因年龄掉权重」冲突，故改为默认不开"
                         "（默认值来自 config/runtime.json）")
    ap.add_argument("--cooldown-days", type=int, default=_rt.DIGEST["cooldown_days"],
                    help="同一 DOI 的再推冷却期（天），超期后高分老文可再推一次"
                         "（默认值来自 config/runtime.json）")
    ap.add_argument("--rows", type=int, default=_rt.POOL["rows"],
                    help="Crossref 每页条数，直接决定每页拿到多少候选。它**几乎不影响耗时**："
                         "实测 rows=100 中位 4.0 s、rows=300 中位 4.1 s——三倍候选、耗时不变"
                         "（瓶颈是固定延迟，不是响应体大小），所以想多要候选先把这里拉满，"
                         "再考虑动 --pages（详见 帮助手册.md §6.2.3）。"
                         "默认值来自 config/runtime.json")
    ap.add_argument("--pages", type=int, default=_rt.POOL["pages"],
                    help="fresh 通道页数。fresh 每期必冷，请求数 = 检索式数 × 本值，是每日"
                         "耗时的主要来源（嫌日报慢就先降它）；默认值来自 config/runtime.json")
    ap.add_argument("--query-pages", type=int, default=_rt.POOL["query_pages"],
                    help="query 通道页数，独立于 --pages（默认 3；默认值来自 config/runtime.json）")
    ap.add_argument("--min-score", type=float, default=_rt.DIGEST["min_score"],
                    help="final 分数低于此值的候选不得入选（0 关闭该门槛）。"
                         "0.04 是按新权重重标定过的：R 权重 0.55→0.40、A 0.15→0.05 让"
                         "相关度主导的分数整体下移，X/J' 又把经典与好刊抬回去"
                         "（默认值来自 config/runtime.json）")
    ap.add_argument("--cat-floor", default=_rt.DIGEST["cat_floor"],
                    help="分类保底席位，「分类=整数」逗号分隔，空串关闭。保底在通道配额轮"
                         "之后、全局补位轮之前按分类权重从高到低发放，同样要过 --min-score"
                         "与每类上限 cap。给哪些分类留席位是**领域相关**的，按你自己库里"
                         "各方向的供给情况标定（分类名必须在 topic.json 的一级分类里，"
                         "否则启动即报错）；默认值来自 config/runtime.json 的 digest.cat_floor")
    ap.add_argument("--citer-years", type=float, default=_rt.SCORING["c_age_tau_y"],
                    help="C 里引用者年龄衰减的时间常数（**年**，默认 3）——只做有界调节，"
                         "见 --citer-floor。旧参数 --citer-tau（天）已随公式重写移除"
                         "（默认值来自 config/runtime.json 的 scoring.c_age_tau_y）")
    ap.add_argument("--citer-floor", type=float, default=_rt.SCORING["c_age_floor"],
                    help="C 里引用者年龄项的地板（默认 0.75）：年龄最多只影响 25%% 的权重，"
                         "调到 0.85–0.90 就更彻底地忽略引用者年代"
                         "（默认值来自 config/runtime.json 的 scoring.c_age_floor）")
    ap.add_argument("--oa-top", type=int, default=_rt.DIGEST["oa_top"],
                    help="除各通道短名单外，再按 pre 分（不含 A）取全局前 N 名查 Unpaywall，"
                         "让 A 在有资格入选的范围内是完整、可比的（默认 48；0 = 只查短名单；"
                         "默认值来自 config/runtime.json）")
    ap.add_argument("--diag-pagination", action="store_true",
                    help="只做一次分页对比诊断并打印，不影响正常流程")
    ap.add_argument("--diag-cats", action="store_true",
                    help="打印各分类候选的数量、命中来源与最高分（调检索权重时用）")
    ap.add_argument("--no-llm", action="store_true", help="完全不调用 LLM")
    ap.add_argument("--model", default=_rt.DIGEST["model"],
                    help="翻译用的模型（默认值来自 config/runtime.json）")
    ap.add_argument("--date", default="", help="指定日期（默认今天）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件，且不调用 LLM")
    ap.add_argument("--force", action="store_true",
                    help="忽略 schedule 的频率限制，强制执行一次")
    ap.add_argument("--show-config", action="store_true",
                    help="打印生效的运行配置后退出")
    a = ap.parse_args()
    if a.dry_run:
        a.no_llm = True  # dry run 不该产生任何花费

    if a.show_config:
        # 生效值 = runtime.json 的值再被 CLI 覆盖；只拼内存里的配置，不碰网络也不写文件
        cfg = {"runtime": _rt.PATH, "loaded": _rt.LOADED}
        cfg.update(_rt.snapshot())
        cfg["warnings"] = list(_rt.WARNINGS)
        for k in ("days", "limit", "min_year", "cooldown_days", "min_score",
                  "cat_floor", "oa_top", "model"):
            cfg["digest"][k] = getattr(a, k)
        for k in ("rows", "pages", "query_pages"):
            cfg["pool"][k] = getattr(a, k)
        cfg["scoring"]["c_age_tau_y"] = a.citer_years
        cfg["scoring"]["c_age_floor"] = a.citer_floor
        print(json.dumps(cfg, ensure_ascii=False, indent=1))
        return

    try:
        a.cat_floor = parse_cat_floors(a.cat_floor)
    except ValueError as e:
        ap.error(str(e))

    LOGS.mkdir(exist_ok=True)
    logf = LOGS / "daily_digest.log"
    lines = []

    def log(msg):
        print(msg)
        lines.append(msg)

    def flush_log():
        # 多条退出路径共用：每次 return 前都要把这一轮日志落盘，别写两遍
        with logf.open("a", encoding="utf-8", errors="replace") as f:
            f.write("\n".join(lines) + "\n")

    today = a.date or date.today().isoformat()
    today_d = date.fromisoformat(today)
    since = (today_d - timedelta(days=a.days)).isoformat()
    log(f"=== {time.strftime('%F %T')} 日报 {today}，fresh 窗口 {since} 起"
        f"（近 {a.days} 天）===")
    if not _rt.LOADED:
        # 读不到 runtime.json 时整轮退回内置默认值（篇数 / 门槛 / 页数全变），不喊一声的话
        # 只能从结果里去猜；LOADED 这个信号此前没有任何读取方看它，等于静默降级
        log(f"  ! 读不到运行配置 {_rt.PATH}：本轮**全部参数用的是内置默认值**，"
            f"config/runtime.json 里的工作点没有生效（用 --show-config 对比生效值）")

    # 频率闸门：systemd 每天触发一次，跑不跑由 config/runtime.json 的 schedule 段决定。
    # --dry-run 要能看到真实结果、显式 --date 是回填历史、--force 是手动强制，三者都放行。
    skip = schedule_skip(today_d, last_push_date(),
                         manual=bool(a.dry_run or a.date or a.force))
    if skip:
        log(f"  按 schedule 跳过本期：{skip}（要强制跑一期加 --force）")
        flush_log()
        return

    profile = {}
    pf = STATE / "profile.json"
    if pf.exists():
        profile = json.loads(pf.read_text(encoding="utf-8"))
    queries = profile.get("queries") or {}
    weights = profile.get("weights") or {}
    if not queries:
        log("  未找到 state/profile.json，先跑 build_profile.py")
        # 兜底不能写成 getattr(fc, "QUERIES", {...})：默认参数在调用前就求值，
        # fc 没有 QUERIES 时会先抛 AttributeError，兜底永远轮不到。退回 topic.json
        # 的检索式（也正是 find_new.QUERIES 的来源），连它都取不到才用一组空检索式。
        queries = getattr(fc, "QUERIES", None) or {
            c: list(_topic.QUERIES.get(c) or []) for c in _topic.CATEGORY_ORDER}
    else:
        queries = {c: qs for c, qs in queries.items()}
    w_max = max(weights.values()) if weights else 1.0

    lib_dois, lib_titles, doi2key, doi_year, key2primary, key2meta = load_library(log)
    known = make_known(lib_titles)
    recs, migrated = load_recs()
    log(f"  推送状态 {len(recs)} 条"
        + (f"（自 state/digest_seen.json 迁移 {migrated} 条，旧文件保留不删）"
           if migrated else "")
        + f"，冷却期 {a.cooldown_days} 天")

    if a.diag_pagination:
        diag_pagination(queries, a, since, lib_dois, recs, today, known, log)

    # 检索 + 过闸：三个通道进同一个 pool，DOI 唯一
    pool, pool_titles = {}, {}

    n_raw = 0
    for cat, qs in queries.items():
        for q in qs:
            try:
                res = search_crossref(q, since, rows=a.rows, pages=a.pages)
            except Exception as e:
                log(f"  ! fresh {cat} / {q}: {e}")
                continue
            n_raw += len(res)
            gate_candidates(res, cat, pool, pool_titles, lib_dois, recs, today,
                            a.cooldown_days, known, "fresh")
    log(f"  通道 fresh：近 {a.days} 天新文检索 {n_raw} 条 → 通过筛选 "
        f"{sum(1 for r in pool.values() if r['channel'] == 'fresh')} 篇")

    n_raw = 0
    # query / refs 的缓存键带刷新周期桶，跨周期自动刷新一次排行榜
    mon = period_tag(today, _rt.POOL["refresh_days"])
    for cat, qs in queries.items():
        for q in qs:
            try:
                res = search_crossref(q, None, rows=a.rows, pages=a.query_pages,
                                      pub_from=(f"{a.min_year}-01-01" if a.min_year else None),
                                      tag=mon)
            except Exception as e:
                log(f"  ! query {cat} / {q}: {e}")
                continue
            n_raw += len(res)
            gate_candidates(res, cat, pool, pool_titles, lib_dois, recs, today,
                            a.cooldown_days, known, "query", min_year=a.min_year)
    log(f"  通道 query：不限日期检索 {n_raw} 条"
        f"（{str(a.min_year) + ' 年起' if a.min_year else '不限年份'}，{a.query_pages} 页，"
        f"缓存键周期桶 {mon}）→ 通过筛选 "
        f"{sum(1 for r in pool.values() if r['channel'] == 'query')} 篇")

    refs_pool, n_citers, co_map = {}, 0, {}
    if CITE_GRAPH.exists():
        cg = json.loads(CITE_GRAPH.read_text(encoding="utf-8"))
        n_citers = cg.get("n_citers", 0)
        co_map = cg.get("co_citation") or {}
        refs_pool = build_refs_pool(co_map, cg.get("ref_meta") or {},
                                    lib_dois, recs, today, a.cooldown_days, log,
                                    tag=mon)
        n_ref_dup = 0
        for d, r in refs_pool.items():
            if d in pool:
                continue
            # 标题也要查，不能只看 DOI：同一篇的德文版/国际版是不同 DOI，而
            # gate_candidates 就是按标题去重的，只查 DOI 会让同题记录重复进池
            tk = fc.norm(r["title"])
            if tk and tk in pool_titles:
                n_ref_dup += 1
                continue
            pool[d] = r
            if tk:
                pool_titles[tk] = d
        if n_ref_dup:
            log(f"  引文通道按标题去重丢掉 {n_ref_dup} 篇"
                f"（与 fresh/query 的候选同题、不同 DOI）")
    else:
        log("  ! 缺 state/citation_graph.json（跑 scripts/citation_graph.py），引文通道跳过")

    by_ch = Counter(r["channel"] for r in pool.values())
    log(f"  入池合计 {len(pool)} 篇：fresh {by_ch['fresh']} / query {by_ch['query']}"
        f" / refs {by_ch['refs']}")

    if not pool:
        log("  三个通道都没有候选，日报不生成")
        flush_log()
        return

    # S 项的可用性必须在打分前定下来：它决定 R 的权重是 0.25（S 顶上 0.15）还是 0.40
    # （S 不可用，权重回补）。打到一半再改权重，同一批候选的分数就不可比了。
    sim_ok, sim_why = sim_probe()
    sc = scoring_weights(sim_ok)
    log(f"  S 项：{'可用' if sim_ok else '不可用'}（{sim_why}）；"
        f"权重 R={sc['R']:.2f} S={sc['S']:.2f} C={sc['C']:.2f} X={sc['X']:.2f} "
        f"J'={sc['J']:.2f} A={sc['A']:.2f}")

    # 打分：R 检索排名 / S 库内语义相似度 / C 库内共被引 / X 经典度 / J' 期刊层级
    #       / A 开放获取 / F published 新鲜度
    # R 只用检索排名，不再乘候选的内容分类权重：rank 是画像生成的检索式给的，w_cat 是
    # 按关键词数出来的，实测与命中通道 hit_cat 只有 30% 一致（1362 篇里 413 篇），
    # 两者相乘等于三重加权（检索式条数 × rank × w_cat）再叠一层噪声。分类权重仍在
    # C 的 q̄ 里生效（库内权重高的方向引用了它），那说的是另一件事。
    n_nodate, n_jif = 0, 0
    for r in pool.values():
        r.setdefault("cls", classify(r["title"], r["abstract"]))
        if r["channel"] == "refs":
            r["rank"] = None
            r["R"] = 0.0                       # refs 通道没有检索排名
            r["C"], r["_w"], r["_y"] = citation_term(
                r["cited_by"], weights, w_max, doi2key, key2primary, doi_year, today_d,
                tau_years=a.citer_years, age_floor=a.citer_floor)
        else:
            r["R"] = 1.0 / (1.0 + r["rank"] / 5.0)
            r["C"] = 0.0
        r["S"] = profile_similarity(r)          # 库索引不可用时恒为 0
        r["X"] = classic_term(r["cited"])       # 只看绝对被引量，不看年份
        r["jif"] = if_lookup(r["journal"])      # OpenAlex 2yr_mean_citedness，可缺
        n_jif += 1 if r["jif"] else 0
        r["J"] = journal_term(r["jif"])
        r["F"], r["age_days"] = freshness(r["date"], today_d)
        if r["age_days"] is None:
            n_nodate += 1
        r["A"] = 0.0
    if sim_ok:
        ss = sorted(r["S"] for r in pool.values())
        n_err, n_empty = sim_stats()
        log(f"  S 项：{len(ss)} 篇候选的库内最大余弦，中位 {ss[len(ss) // 2]:.3f}、"
            f"最高 {ss[-1]:.3f}"
            + (f"；{n_err} 篇编码失败、{n_empty} 篇无标题也无摘要，均按 0 计"
               if n_err or n_empty else ""))
    # C 的联表覆盖率：引用者分类联不上 / 年份缺失各有多少
    n_ref_rows = sum(1 for r in pool.values() if r["channel"] == "refs")
    if n_ref_rows:
        all_citers = [c for r in pool.values() if r["channel"] == "refs" for c in r["cited_by"]]
        n_w = sum(r["_w"] for r in pool.values() if r["channel"] == "refs")
        n_y = sum(r["_y"] for r in pool.values() if r["channel"] == "refs")
        log(f"  引文加权：{len(all_citers)} 条引用边中 {n_w} 条能联到引用者一级分类，"
            f"{n_y} 条能取到引用者年份；联不上的按已知者的平均权重/平均年龄折算")
    log(f"  期刊层级：{n_jif}/{len(pool)} 篇在 state/journal_if.json 里查到 IF"
        f"（查不到的 J'=0，中性不奖不罚）")
    log(f"  新鲜度：{len(pool) - n_nodate} 篇能解析 published 日期，{n_nodate} 篇无日期"
        f"（F=0，不给奖励）")
    by_pool_cat = Counter(r["cls"]["primary"] for r in pool.values())
    log("  入池分类：" + "、".join(f"{c} {n}" for c, n in by_pool_cat.most_common())
        + f"（分类权重系数 w/w_max，现只进 C 的 q̄：{w_desc(weights, w_max)}）")
    by_hit = Counter(r.get("hit_cat") for r in pool.values())
    n_align = sum(1 for r in pool.values() if r.get("hit_cat") == r["cls"]["primary"])
    log("  检索命中通道：" + "、".join(f"{c} {n}" for c, n in by_hit.most_common())
        + f"；命中分类与内容分类一致 {n_align}/{len(pool)}")

    # 保底席位：cap 只依赖 a.limit，「通道配额」那条日志又要报出预留数（不报的话复核时
    # 看不出保底到底有没有生效），所以在这里一次算清。
    cap = max(2, int(a.limit * 0.4))          # 每个分类最多入选几篇
    floors, reserved = resolve_floors(a.cat_floor, cap, a.limit, log)
    budget = max(0, a.limit - reserved)       # 通道配额轮只发这么多席位，其余留给保底
    # 通道配额按「扣掉保底预留后的 budget」折算比例，而不是按 limit：否则三通道配额
    # 之和仍是 limit，第一轮就成了先到先得——播放顺序里排最后的 refs 可能一席都拿不到，
    # 只能靠补位轮找回，配额的「通道多样性」作用等于没了（预留越大偏差越明显）。
    quota = {"refs": max(2, round(budget * 0.25)),
             "fresh": max(4, round(budget * 0.4))}
    quota["query"] = max(0, budget - quota["refs"] - quota["fresh"])
    # 短名单按 pre 分（不含 A）排：A 要花一次网络查询才知道，不能让它决定谁进短名单
    base = {ch: sorted((r for r in pool.values() if r["channel"] == ch),
                       key=lambda r: -pre_score(r, sc))
            for ch in CHANNELS}
    shortlist = [r for ch in CHANNELS for r in base[ch][:quota[ch] + 2]]
    for r in shortlist:
        r["abstract"] = strip_abs(fill_abstract(r["doi"], r["abstract"]))
        r["cls"] = classify(r["title"], r["abstract"])   # 有摘要后重算分类标签
    # 补位轮会从全局池取人，所以按 pre 分再补一批进 OA 查询集，让 A 在「有资格入选」
    # 的范围内是完整的、可比的。旧代码只给短名单查 OA，却拿含 A 的分做全局排序和
    # --min-score 判定：进短名单的白拿 0.15，没被查过的 1300 篇候选静默扣分。
    oa_set, oa_seen = list(shortlist), {r["doi"] for r in shortlist}
    if a.oa_top > 0:
        for r in sorted(pool.values(), key=lambda r: -pre_score(r, sc))[:a.oa_top]:
            if r["doi"] not in oa_seen:
                oa_seen.add(r["doi"])
                oa_set.append(r)
    for r in oa_set:
        r["pdf"], r["page"], r["is_oa"] = oa_info(r["doi"])
        r["A"] = 1.0 if r["is_oa"] else 0.0
    log(f"  通道配额 fresh {quota['fresh']} / query {quota['query']} / refs {quota['refs']}"
        f"；保底预留 {reserved} 篇（{budget} 篇走通道配额轮）"
        f"；补摘要的短名单 {len(shortlist)} 篇，查 OA（定 A）的 {len(oa_set)} 篇"
        f"（短名单 + pre 分全局前 {a.oa_top}）")

    for r in pool.values():
        r["score"] = final_score(r, sc)

    ordered = sorted(pool.values(), key=lambda r: -r["score"])
    if a.diag_cats:
        diag_categories(ordered, log)
    ordered_ch = {ch: [r for r in ordered if r["channel"] == ch] for ch in CHANNELS}
    picked, taken, by_cat, by_ch_pick = [], set(), Counter(), Counter()
    n_below = 0

    def take(r, enforce_cap=True):
        # --min-score 是硬门槛：分数不够的候选一律不入选（含补位轮），
        # 靠配额把弱相关的新文献硬塞进版面是本末倒置
        if r["score"] < a.min_score:
            return False
        if r["doi"] in taken or (enforce_cap and by_cat[r["cls"]["primary"]] >= cap):
            return False
        picked.append(r)
        taken.add(r["doi"])
        by_cat[r["cls"]["primary"]] += 1
        by_ch_pick[r["channel"]] += 1
        return True

    for ch in CHANNELS:                       # 第一轮：各通道按配额取（只到 budget）
        n = 0
        for r in ordered_ch[ch]:
            if n >= quota[ch] or len(picked) >= budget:
                break
            if take(r):
                n += 1
    # 第二轮：分类保底。按分类权重从高到低发（权重高的方向先占位），从全局分数序里
    # 取该分类尚未入选的候选。复用 take()，所以 --min-score 与每类 cap 照旧管着保底——
    # 保底给的是「版面名额」而不是「免检入场券」，跨不过门槛就空着并写明原因。
    floor_note = []
    for cat in sorted(floors, key=lambda c: (-weights.get(c, 1.0), CAT_ORDER.index(c))):
        want = floors[cat]
        while by_cat[cat] < want and len(picked) < a.limit:
            cand = next((r for r in ordered
                         if r["cls"]["primary"] == cat and r["doi"] not in taken), None)
            if cand is None or not take(cand):
                break
        got = by_cat[cat]
        if got >= want:
            floor_note.append(f"{cat} {got}/{want}")
            continue
        cands = [r for r in ordered if r["cls"]["primary"] == cat]
        why = []
        if len(cands) <= got:
            why.append("该分类入池候选不足")
        n_below_cat = sum(1 for r in cands if r["score"] < a.min_score)
        if n_below_cat:
            why.append(f"被 --min-score 挡下 {n_below_cat} 篇")
        if got >= cap:
            why.append(f"撞到每类上限 cap={cap}")
        if len(picked) >= a.limit:
            why.append(f"--limit {a.limit} 已发完")
        floor_note.append(f"{cat} {got}/{want}（{'、'.join(why) or '候选被更早的轮次取尽'}）")
    for enforce in (True, False):             # 第三轮：空出的名额让给全局分数最高的候选
        for r in ordered:
            if len(picked) >= a.limit:
                break
            take(r, enforce_cap=enforce)
    picked.sort(key=lambda r: (CAT_ORDER.index(r["cls"]["primary"])
                               if r["cls"]["primary"] in CAT_ORDER else 99, -r["score"]))
    n_below = sum(1 for r in ordered if r["score"] < a.min_score)
    log(f"  入选 {len(picked)} 篇（fresh {by_ch_pick['fresh']} / query {by_ch_pick['query']}"
        f" / refs {by_ch_pick['refs']}）："
        + "、".join(f"{c} {n}" for c, n in by_cat.most_common()))
    # 保底达成情况单独一行：复核时先看「有没有生效」，再看没生效的原因。这里的计数是
    # 保底轮结束时点的（之后的全局补位轮可能再给同一分类加人，那不是保底的功劳）。
    log("  保底席位：" + ("、".join(floor_note) if floor_note
                          else "未设（--cat-floor 为空或名额被 --limit 压成 0）"))
    if a.min_score > 0:
        log(f"  分数门槛 {a.min_score:.2f}：入池 {n_below}/{len(ordered)} 篇低于门槛，一律不入选")
        for ch in CHANNELS:
            bl = [r for r in ordered_ch[ch] if r["score"] < a.min_score]
            if bl:
                log(f"    门槛挡下 {ch} {len(bl)} 篇，该通道最高分的被挡者 "
                    f"{bl[0]['score']:.3f}（R={bl[0]['R']:.2f} S={bl[0]['S']:.2f} "
                    f"C={bl[0]['C']:.2f} X={bl[0]['X']:.2f} J'={bl[0]['J']:+.2f} "
                    f"A={bl[0]['A']:.0f} F={bl[0]['F']:.2f}）{bl[0]['title'][:46]}")

    # 补齐入选论文的链接（进过 OA 查询集的已有结果）
    for r in picked:
        if "pdf" not in r:
            r["pdf"], r["page"], r["is_oa"] = oa_info(r["doi"])
            r["A"] = 1.0 if r["is_oa"] else 0.0
            r["score"] = final_score(r, sc)
        if not r["abstract"]:
            r["abstract"] = strip_abs(fill_abstract(r["doi"], r["abstract"]))
    n_lt, n_lg = landing_stats()
    if n_lt:
        log(f"  摘要兜底：出版社 landing page 试了 {n_lt} 篇，补到 {n_lg} 篇"
            + ("（只覆盖 Nature 系；Elsevier 付费刊页面直连 403，"
               "只能等收编后从 PDF 拿）" if n_lg < n_lt else ""))
    n_pdf = sum(1 for r in picked if r["is_oa"])
    n_pub = sum(1 for r in picked if r["pdf"] and not r["is_oa"])
    log(f"  开放获取 PDF {n_pdf} 篇，出版方 PDF（可能需权限）{n_pub} 篇")
    for r in picked:
        log(f"    [{r['channel']:5s}] R={r['R']:.2f} S={r['S']:.2f} C={r['C']:.2f}"
            f" X={r['X']:.2f} J'={r['J']:+.2f} A={r['A']:.0f} F={r['F']:.2f}"
            f" 被引={r['cited']} -> {r['score']:.3f}  {r['title'][:64]}")

    # 翻译（唯一花钱的一步）
    zh, n_zh, n_zh_fail, zh_cost = {}, 0, 0, 0.0
    if not a.no_llm:
        zh, n_zh, n_zh_fail, zh_cost = translate(picked, a.model, log)
    else:
        log("  --no-llm：跳过翻译，零花销")

    # 渲染：开头直接进文献。检索口径/收录命中/概览这些都是复核时才翻的，攒到 stats
    # 里最后再附——每天真正要看的是下面这十几条。
    ch_name = {"fresh": f"新文献（近 {a.days} 天入库）",
               "query": (f"相关旧文（{a.min_year} 年起）" if a.min_year
                         else "相关旧文（不限年份）"),
               "refs": "引自库内文献参考文献"}
    n_nozh = sum(1 for r in picked if not zh.get(r["doi"], {}).get("zh_title"))
    md = [f"# 文献日报 {today}\n"]
    if n_nozh:
        md.append(f"\n> ⚠️ {n_nozh}/{len(picked)} 篇没有中文译文（翻译失败或原文无摘要），"
                  f"这些条目保留英文；原因见 logs/daily_digest.log 的「翻译」行\n")

    stats = [f"> 入池 {len(pool)} 篇（新文献 {by_ch['fresh']}、相关旧文 {by_ch['query']}、"
             f"引文推荐 {by_ch['refs']}），取 {len(picked)} 篇"
             f"（新文献 {by_ch_pick['fresh']}、相关旧文 {by_ch_pick['query']}、"
             f"引文推荐 {by_ch_pick['refs']}）　|　生成于 {time.strftime('%F %H:%M')}\n",
             f"> 新文献窗口：{since} 起近 {a.days} 天（按 Crossref 创建日期，未自动放宽）；"
             f"相关旧文不加日期过滤、按相关度分页取\n",
             f"> 分类依据《文献画像》（库内 {len(lib_titles)} 条标题 / {len(lib_dois)} 个 DOI）"
             f"自动判定；引文推荐来自库内 {n_citers} 篇的参考文献\n"]
    if not a.no_llm:
        stats.append(f"> 翻译：本次新译 {n_zh} 篇、命中缓存 "
                     f"{len(picked) - n_zh - n_zh_fail} 篇、失败 {n_zh_fail} 篇，"
                     f"约 {zh_cost:.4f} 元"
                     f"（分块 {TRANS_CHUNK} 篇/请求，重试至多 {TRANS_RETRY} 次；"
                     f"空闲时段价 命中 {PRICE_IN_HIT} / 未命中 {PRICE_IN_MISS} / "
                     f"输出 {PRICE_OUT} 元每百万 token）\n")
    stats.append("\n### 概览\n\n| 一级分类 | 篇数 |\n|---|---:|\n")
    for c in CAT_ORDER:
        if by_cat.get(c):
            stats.append(f"| {c} | {by_cat[c]} |\n")
    stats.append("\n| 通道 | 入池 | 入选 |\n|---|---:|---:|\n")
    for ch in CHANNELS:
        stats.append(f"| {ch_name[ch]} | {by_ch[ch]} | {by_ch_pick[ch]} |\n")
    stats.append(f"\n开放获取 PDF：{n_pdf} 篇可直接下载；出版方 PDF：{n_pub} 篇"
                 f"（可能需要机构权限）；其余仅有原文页链接。\n")

    for i, r in enumerate(picked, 1):
        t = zh.get(r["doi"], {})
        md.append(f"\n---\n\n## {i}. {t.get('zh_title') or r['title']}\n\n")
        if t.get("zh_title"):
            md.append(f"**{r['title']}**\n\n")
        tags = "、".join(r["cls"]["sub"]) or "—"
        mat = "、".join(r["cls"]["materials"]) or "—"
        meth = "、".join(r["cls"]["method"]) or "—"
        form = "、".join(r["cls"].get("form") or [])
        system = "、".join(r["cls"].get("system") or [])
        kind = "综述" if r["cls"]["is_review"] else "研究论文"
        if r["channel"] == "fresh":
            src = f"新文献（近 {a.days} 天入库）"
        elif r["channel"] == "refs":
            src = f"引文推荐（被库内 {len(r['cited_by'])} 篇引用）"
        else:
            d0 = parse_pub_date(r["date"])
            src = f"相关旧文（{d0.year} 年）" if d0 else "相关旧文（年份未给出）"
        md.append(f"- **来源**：{src}\n")
        md.append(f"- **推荐性分数**：{r['score']:.3f}\n")
        md.append(f"- **期刊**：{r['journal'] or '未给出'}　|　**出版**：{r['date'] or '未给出'}"
                  f"　|　**被引**：{r['cited']}　|　**类型**：{kind}\n")
        if r["authors"]:
            md.append(f"- **作者**：{', '.join(r['authors'])}"
                      f"{' 等' if len(r['authors']) >= 5 else ''}\n")
        affil = clean_affil(r.get("affils"))
        if affil:
            md.append(f"- **单位**：{affil}\n")
        md.append(f"- **分类**：{r['cls']['primary']}　|　**子类标签**：{tags}\n")
        md.append(f"- **材料体系**：{mat}　|　**方法**：{meth}"
                  + (f"　|　**形态**：{form}" if form else "")
                  + (f"　|　**体系**：{system}" if system else "") + "\n")
        link = f"https://doi.org/{r['doi']}" if r["doi"] else ""
        parts = [f"[DOI]({link})" if link else ""]
        if r["page"]:
            parts.append(f"[原文页]({r['page']})")
        if r["is_oa"] and r["pdf"]:
            parts.append(f"**[开放获取 PDF]({r['pdf']})**")
        elif r["pdf"]:
            parts.append(f"[出版方 PDF（可能需权限）]({r['pdf']})")
        else:
            parts.append("PDF：未找到")
        md.append(f"- **链接**：{'　|　'.join(p for p in parts if p)}\n")
        if t.get("zh_summary"):
            md.append(f"- **概述**：{t['zh_summary']}\n")
        ab = t.get("zh_abstract") or ""
        if ab:
            # 早期缓存里可能残留「摘要」前缀，渲染时统一清掉
            ab = re.sub(r"^\s*(摘要|abstract)\b[:：\s\-–]*", "", ab, flags=re.I).strip()
            md.append(f"- **摘要**：{ab}\n")
        elif r["abstract"]:
            md.append(f"- **摘要**：{r['abstract'][:600]}…（未取到中文翻译）\n")
        else:
            md.append("- **摘要**：未取到（Crossref / Semantic Scholar 均无开放摘要）\n")
        lib = lib_links(r["doi"], r["cls"], co_map, doi2key, key2meta)
        if lib:
            md.append(f"- **库内关联**：{lib}\n")
        if r["hit_cat"] and r["hit_cat"] != r["cls"]["primary"]:
            md.append(f"- **备注**：由「{r['hit_cat']}」检索式命中\n")

    md.append("\n---\n\n## 下载清单\n\n")
    md.append("需要入库的，把序号交给我即可批量下载；带 ⚠️ 的需机构权限，我能拿到就下、拿不到会说明。\n\n")
    for i, r in enumerate(picked, 1):
        label = zh.get(r["doi"], {}).get("zh_title") or r["title"]
        mark = "✅" if r["is_oa"] else ("⚠️" if r["pdf"] else "⛔")
        md.append(f"{i}. {mark} {label} — "
                  f"{r['pdf'] or ('https://doi.org/' + r['doi'])}\n")

    # 检索口径与收录统计一律殿后：它们是复核时才翻的，不是每天要读的
    md.append("\n---\n\n## 统计口径\n\n")
    md.extend(stats)

    text = "".join(md)
    urls = "".join(f"{r['pdf'] or ('https://doi.org/' + r['doi'])}\t"
                   f"{zh.get(r['doi'], {}).get('zh_title') or r['title']}\n" for r in picked)

    if a.dry_run:
        log("  --dry-run：不写任何文件（文献日报与 state/recommendations.json 都不动）")
    else:
        NEWS.mkdir(exist_ok=True)
        (NEWS / f"{today}.md").write_text(text, encoding="utf-8")
        (NEWS / f"{today}.urls.txt").write_text(urls, encoding="utf-8")
        n_again, n_same_day = 0, 0
        for r in picked:
            rec = recs.get(r["doi"]) or {}
            n_prev = int(rec.get("times_pushed") or 0)
            # 同一天重复跑（手动 --date <今天> 补跑）不再 +1：这一期已经记过账，
            # 叠加会把「再推」次数与冷却期的语义一起虚增，同一个 DOI 补跑几次就能
            # 刷出任意大的计数
            same_day = rec.get("last_pushed") == today
            if same_day:
                n_prev = max(n_prev, 1)   # 记录里没有 times_pushed 也当作记过 1 次
                n_same_day += 1
            elif n_prev:
                n_again += 1
            recs[r["doi"]] = {
                "first_pushed": rec.get("first_pushed") or today,
                "last_pushed": today,
                "times_pushed": n_prev if same_day else n_prev + 1,
                "channel": r["channel"],
                "score": round(r["score"], 4),
                "status": "pushed",
            }
        tmp = RECS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(recs, ensure_ascii=False, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, RECS)
        # 频率闸门只认这个日期，所以跟 RECS 一样走「先写 .tmp 再 os.replace」
        tmp = LAST_RUN.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"last_push": today}, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, LAST_RUN)
        log(f"  已写 文献日报/{today}.md 与 {today}.urls.txt；"
            f"recommendations.json 更新 {len(picked)} 条"
            f"（其中 {n_again} 条是冷却期外的再推"
            + (f"、{n_same_day} 条是今天已记过账的补跑（times_pushed 不叠加）"
               if n_same_day else "")
            + f"，现共 {len(recs)} 条）；state/last_push.json 记为 {today}")

    flush_log()


if __name__ == "__main__":
    main()
