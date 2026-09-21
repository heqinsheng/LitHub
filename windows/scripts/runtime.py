#!/usr/bin/env python3
"""运行配置的加载器：把「多久跑一期、一期几篇、检索池多大、怎么打分」从代码里挪进
config/runtime.json。

**领域内容不在这里**——分类法、子类与四个标签轴的词表、检索式、关键词、闸门正则、
期刊白名单都在 config/topic.json（装载器 scripts/topic.py）。本模块只管运行参数，
且**不含任何领域名**：cat_floor 的默认值是空串（= 关闭分类保底），要开保底必须在
runtime.json 里写明分类名。

五段：
    schedule  跑多勤：enabled / every_n_days（daily_digest.py 的频率闸门读它）
    digest    版面与门槛：篇数 / fresh 窗口 / 年份下限 / 冷却期 / 分数门槛 / 分类保底 / 模型
    pool      检索池：缓存刷新周期 / Crossref 每页条数 / fresh 与 query 的页数
    scoring   打分权重与各分量常数（daily_digest.py 的 W_R…C_AGE_TAU_Y）
    email     日报发信（scripts/send_digest.py）：收件人 / SMTP 主机端口 / 主题模板。
              **密码不在这个文件里**——读 state/smtp_password（chmod 600），
              因为这个 json 是要进复现包给别人的。

用法：
    import runtime as R
    R.SCHEDULE["every_n_days"]    R.DIGEST["limit"]
    R.POOL["refresh_days"]        R.SCORING["w_r"]
    R.EMAIL["to"]                 R.EMAIL["smtp_host"]
    R.WARNINGS                    # 校验通过、但值得提醒的问题（权重不变量之类）

文件缺失**不算错误**：用内置 DEFAULTS 并把 LOADED 置 False（存在且解析成功才 True），
调用方据此提示「用的是内置默认值」。**解析失败与校验失败一律 raise ValueError**：
未知键、类型不对、越界值绝不静默忽略——静默忽略认不出的名字，会让人以为调过了、
其实没生效（同一条纪律见 scripts/daily_digest.py 的 parse_cat_floors）。

配置文件路径可用环境变量 LITHUB_RUNTIME 覆盖（便于多套工作点并存）。
"""
import json
import os

# 仓库根从本文件位置推出，不写死 ~/LitHub：把包解压到别的目录也能读到 config/runtime.json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("LITHUB_RUNTIME") or os.path.join(ROOT, "config", "runtime.json")

# 内置默认值：与原硬编码值一一对应，所以配置文件缺失时行为不变。
# cat_floor 只能是空串——分类名是领域内容，要开保底得写在 config/runtime.json 里。
DEFAULTS = {
    "schedule": {
        "enabled": True,
        "every_n_days": 1,
    },
    "digest": {
        "limit": 16,
        "days": 14,
        "min_year": 0,
        "cooldown_days": 60,
        "min_score": 0.04,
        "cat_floor": "",
        "oa_top": 48,
        "model": "deepseek-v4-flash",
    },
    "pool": {
        "refresh_days": 30,
        "rows": 100,
        "pages": 1,
        "query_pages": 3,
    },
    "scoring": {
        "w_r": 0.40,
        "w_s": 0.15,
        "w_c": 0.22,
        "w_x": 0.20,
        "w_j": 0.13,
        "w_a": 0.05,
        "f_gain": 0.35,
        "f_half_life": 180.0,
        "x_sat": 5001.0,
        "j_if_zero": 4.0,
        "j_if_top": 32.0,
        "c_n_sat": 31.0,
        "c_w_floor": 0.65,
        "c_age_floor": 0.75,
        "c_age_tau_y": 3.0,
    },
    # 发信（scripts/send_digest.py）。密码**不在这里**——见 state/smtp_password。
    "email": {
        "enabled": False,
        "to": "",
        "smtp_host": "",
        "smtp_port": 465,
        "smtp_user": "",
        "use_ssl": True,
        "subject": "文献日报 {date}（{n} 篇）",
    },
}

# 字段表 {段: {键: (类型, 下限, 上限)}}，None = 该侧不限。键名即白名单：不在这里的键一律报错。
# int 只收 int——bool 是 int 的子类，必须显式挡掉，否则 true 会被当 1 收下；
# float 收 int 与 float（让 w_r: 1 这种写法也能读，读进来统一转成 float）。
_SCHEMA = {
    "schedule": {
        "enabled": ("bool", None, None),
        "every_n_days": ("int", 1, None),
    },
    "digest": {
        "limit": ("int", 1, None),
        "days": ("int", 1, None),
        "min_year": ("int", 0, None),
        "cooldown_days": ("int", 0, None),
        "min_score": ("float", 0.0, None),
        "cat_floor": ("str", None, None),
        "oa_top": ("int", 0, None),
        "model": ("str", None, None),
    },
    "pool": {
        "refresh_days": ("int", 1, None),
        "rows": ("int", 1, None),
        "pages": ("int", 1, None),
        "query_pages": ("int", 1, None),
    },
    "scoring": {
        "w_r": ("float", 0.0, None),
        "w_s": ("float", 0.0, None),
        "w_c": ("float", 0.0, None),
        "w_x": ("float", 0.0, None),
        "w_j": ("float", 0.0, None),
        "w_a": ("float", 0.0, None),
        "f_gain": ("float", 0.0, None),
        "f_half_life": ("float", 0.0, None),
        "x_sat": ("float", 0.0, None),
        "j_if_zero": ("float", 0.0, None),
        "j_if_top": ("float", 0.0, None),
        "c_n_sat": ("float", 0.0, None),
        "c_w_floor": ("float", 0.0, None),
        "c_age_floor": ("float", 0.0, None),
        "c_age_tau_y": ("float", 0.0, None),
    },
    "email": {
        "enabled": ("bool", None, None),
        "to": ("str", None, None),
        "smtp_host": ("str", None, None),
        "smtp_port": ("int", 1, 65535),
        "smtp_user": ("str", None, None),
        "use_ssl": ("bool", None, None),
        "subject": ("str", None, None),
    },
}


def _check(where, spec, v):
    """按字段表校验一个标量，返回归一化后的值；不合规 raise ValueError。"""
    kind, lo, hi = spec
    if kind == "bool":
        if type(v) is not bool:
            raise ValueError(f"{PATH} 的 {where} 必须是 true / false，"
                             f"实际是 {type(v).__name__}：{v!r}")
        return v
    if kind == "int":
        if type(v) is not int:
            raise ValueError(f"{PATH} 的 {where} 必须是整数（true/false 与小数都不收），"
                             f"实际是 {type(v).__name__}：{v!r}")
        num = v
    elif kind == "float":
        if type(v) is bool or not isinstance(v, (int, float)):
            raise ValueError(f"{PATH} 的 {where} 必须是数字，"
                             f"实际是 {type(v).__name__}：{v!r}")
        num = float(v)
    else:
        if type(v) is not str:
            raise ValueError(f"{PATH} 的 {where} 必须是字符串，"
                             f"实际是 {type(v).__name__}：{v!r}")
        return v
    if lo is not None and num < lo:
        raise ValueError(f"{PATH} 的 {where} 必须 ≥ {lo}，实际是 {num!r}")
    if hi is not None and num > hi:
        raise ValueError(f"{PATH} 的 {where} 必须 ≤ {hi}，实际是 {num!r}")
    return num


def _load():
    """读 runtime.json 并逐字段校验，返回 (四段配置, 警告, 是否读到了文件)。

    文件不存在 → 内置默认值。JSON 语法错误、顶层不是对象、未知键、类型/范围不对
    一律 raise ValueError。
    """
    raw, loaded = {}, False
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8-sig") as f:
            try:
                raw = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"{PATH} 不是合法 JSON：{e}") from e
        if not isinstance(raw, dict):
            raise ValueError(f"{PATH} 的顶层必须是对象（{{...}}），"
                             f"实际是 {type(raw).__name__}")
        loaded = True

    unknown = sorted(k for k in raw if k not in _SCHEMA and k != "_comment")
    if unknown:
        raise ValueError(f"{PATH} 顶层有认不出的键：{'、'.join(unknown)}；只允许 _comment 与 "
                         f"{'、'.join(_SCHEMA)}（分类法/检索式/闸门这些领域内容属于 "
                         f"config/topic.json，不要写进这里）")

    cfg = {}
    for sec, fields in _SCHEMA.items():
        block = raw.get(sec, {})
        if not isinstance(block, dict):
            raise ValueError(f"{PATH} 的 {sec} 必须是对象（{{...}}），"
                             f"实际是 {type(block).__name__}")
        # 段内的 `_help` 是写给人的字段说明（见 config/runtime.json），不参与校验、不进配置
        unknown = sorted(k for k in block if k not in fields and k != "_help")
        if unknown:
            raise ValueError(f"{PATH} 的 {sec} 有认不出的键：{'、'.join(unknown)}；"
                             f"只允许 _help 与 {'、'.join(fields)}")
        # 段内没写的键回落到 DEFAULTS：只改一项工作点不必抄全整段
        cfg[sec] = {k: _check(f"{sec}.{k}", spec, block.get(k, DEFAULTS[sec][k]))
                    for k, spec in fields.items()}

    # 权重不变量只警告、不报错：这两条都还有救，但要让人看见。
    s = cfg["scoring"]
    total = s["w_r"] + s["w_c"] + s["w_x"] + s["w_j"] + s["w_a"]
    warn = []
    if abs(total - 1.0) > 0.01:
        warn.append(f"scoring 的 w_r + w_c + w_x + w_j + w_a = {total:.3f}，偏离 1.0 超过 0.01："
                    f"不含 S 与 F 的基线分不再落在 [0,1]，--min-score 的门槛刻度跟着变")
    if s["w_s"] > s["w_r"]:
        warn.append(f"scoring 的 w_s={s['w_s']:.3f} > w_r={s['w_r']:.3f}：有 S 时 R 的份额是 "
                    f"w_r−w_s={s['w_r'] - s['w_s']:.3f}，已经归零或为负，请保证 w_s ≤ w_r")
    return cfg, warn, loaded


_cfg, WARNINGS, LOADED = _load()

SCHEDULE = _cfg["schedule"]
DIGEST = _cfg["digest"]
POOL = _cfg["pool"]
SCORING = _cfg["scoring"]
EMAIL = _cfg["email"]


def snapshot():
    """四段生效配置的浅拷贝，供命令行工具打印（改返回值不影响本模块）。"""
    return {sec: dict(v) for sec, v in _cfg.items()}


if __name__ == "__main__":
    print(f"运行配置：{PATH}（{'已加载' if LOADED else '文件不存在，用内置默认值'}）")
    for w in WARNINGS:
        print(f"  ! {w}")
    print(json.dumps(snapshot(), ensure_ascii=False, indent=1))
