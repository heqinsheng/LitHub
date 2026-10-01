#!/usr/bin/env python3
"""LLM 后端路由：把「用哪个模型」与「主模型失败怎么办」从三个批处理脚本里抽出来。

三个脚本（`summarize_batch.py` / `translate_batch.py` / `embed_figures.py`）都是同一种
调用形态——`subprocess` 跑 `~/.kimi-code/bin/kimi -p`，模型由 kimi CLI 自己的
`default_model` 决定。本项目想让它们优先走 Kimi 会员月付额度、失败时自动退回按量计费的
DeepSeek，于是统一到这里：

    proc, model, note = run_kimi_cli([...args...], cwd=..., timeout=...)

`model` 是本次实际用的 `-m` 别名（空串 = 没发 `-m`，用的是 CLI 的 default_model），
`note` 非空时说明发生了回退。**验收仍然由各脚本自己负责**（有的解析 stream-json 拿正文、
有的只认模型写出的文件）——这个模块只负责「跑」与「要不要换模型再跑一次」。

判据（2026-09-30 实测）：kimi CLI 在模型不可用 / 认证失败 / 额度用尽等**进程级失败**时
退出码非 0，stderr 打 `error: failed to run prompt: …`（额度用尽长这样：
`provider.auth_error: 403 You've reached your 5-hour usage limit`）；正常调用退出码 0。
所以「returncode != 0 或 stderr 含该字面量」判为 provider 失败——stderr 那条是防御性判据，
实测那次失败本身退出码就非 0。**业务层面的失败**（模型没写出文件、正文不合规）不在这里判，
仍由各脚本自己的验收逻辑处理。

主模型连续失败 `HARD_FAIL_LIMIT` 次后，本进程后续调用**直接走兜底模型**（不再每次先白试
一轮）——批处理一次几十篇，每篇都先撞一次墙的代价很实在。这个记忆是进程级的，
不落盘：下次运行仍会先试主模型（额度可能已经恢复）。

模型名与兜底来自 `config/runtime.json` 的 `llm` 段（装载器 `scripts/runtime.py`）。
两个都是空串 = 保持换后端之前的行为（不发 `-m`、不兜底）。
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as _rt  # noqa: E402

KIMI = os.environ.get("LITHUB_KIMI") or os.path.expanduser("~/.kimi-code/bin/kimi")

# provider 级失败的 stderr 特征（实测形态见模块开头）
FAIL_RE = re.compile(r"error:\s*failed to run prompt", re.I)
HARD_FAIL_LIMIT = 2

_state = {"fails": 0, "noted": False}


def models():
    """(主模型, 兜底模型)，都可能是空串（= 不指定）。"""
    return _rt.LLM["model"].strip(), _rt.LLM["fallback_model"].strip()


def fail_line(err):
    """从 stderr 里挑出说明问题的那一行。

    kimi 的错误块是「错误行 + See log: …」，取末行只会打印一句没用的 See log；
    所以优先取含 `error` 的行，没有才退回最后一个非空行。
    """
    lines = [l.strip() for l in (err or "").splitlines() if l.strip()]
    for l in lines:
        if re.search(r"\berror\b", l, re.I):
            return l[:160]
    return lines[-1][:160] if lines else "无输出"


def provider_failed(proc):
    """这次 kimi 调用算不算 provider 级失败（模型/额度/认证/网络）。"""
    err = (getattr(proc, "stderr", "") or "")
    return getattr(proc, "returncode", 0) != 0 or bool(FAIL_RE.search(err))


def _run(cmd, cwd, timeout, env):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              env=env)
    except subprocess.TimeoutExpired:
        return None                      # 超时不换模型重试：那是任务本身跑太久


def run_kimi_cli(args, cwd, timeout, env=None, log=print):
    """跑一次 kimi CLI，主模型失败时用兜底模型重跑。

    `args` 是**除 kimi 可执行文件与 `-m` 之外**的全部参数（`--agent-file=…`、`-p …` 等），
    顺序原样保留——各脚本对参数顺序有实测约束（`--agent-file` 必须在 `-p` 之前）。
    返回 `(CompletedProcess|None, 实际用的模型别名, 说明)`；`None` = 超时，由调用方决定
    是报 TIMEOUT 还是别的。
    """
    primary, fallback = models()
    use_primary = bool(primary) and _state["fails"] < HARD_FAIL_LIMIT
    order = []
    if use_primary:
        order.append(primary)
        if fallback:
            order.append(fallback)
    elif fallback:
        order.append(fallback)
    else:
        order.append("")                 # 既不试主模型也没有兜底：照旧跑，用 CLI 默认

    proc = None
    for i, model in enumerate(order):
        cmd = [KIMI] + (["-m", model] if model else []) + list(args)
        proc = _run(cmd, cwd, timeout, env)
        if proc is None:
            return None, model, "TIMEOUT"
        if not provider_failed(proc):
            _state["fails"] = 0
            return proc, model, ""
        why = fail_line(getattr(proc, "stderr", ""))
        if i + 1 < len(order):
            log(f"    ! {model or 'CLI 默认模型'} 调用失败（{why[:100]}），"
                f"改用 {order[i + 1]}")
            continue
        _state["fails"] += 1
        if _state["fails"] >= HARD_FAIL_LIMIT and not _state["noted"]:
            _state["noted"] = True
            tail = (fallback or "CLI 默认模型")
            log(f"  ! LLM 主模型连续失败 {_state['fails']} 次，本进程余下任务直接走 {tail}"
                f"（下次运行仍会先试 {primary or 'CLI 默认模型'}）")
        return proc, model, why
    return proc, order[-1], ""


if __name__ == "__main__":
    print(f"主模型：{models()[0] or '(空，用 CLI default_model)'}")
    print(f"兜底：  {models()[1] or '(空，不兜底)'}")
