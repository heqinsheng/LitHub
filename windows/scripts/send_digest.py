#!/usr/bin/env python3
"""把当天的文献日报作为**纯文本**邮件发出去。

正文就是 文献日报/<日期>.md 的原文，不转 HTML——邮件客户端本来也不渲染 Markdown，
保持原文最忠实，同时避免为一个渲染功能引入第三方库（本项目只用标准库）。

**本脚本零 token 花费**：日报在 daily_digest.py 里已经生成好了，这里只读文件 + 发 SMTP。
主题行用配置里的模板填 {date} / {n}，不调 LLM。

配置在 config/runtime.json 的 email 段（收件人 / SMTP 主机端口 / 主题模板）。
**密码不在配置文件里**——那个 json 是要进复现包给别人的，密码读
state/smtp_password（chmod 600），与 state/zotero_local_key 同一套路。

**当日计数**：一天成功发出几次，记在 state/email_sent.json 的 count 字段里。
正常流程是「出报后发一次」→ count = 1；只要 count ≥ 1 就不再自动发（--force 才会再加），
所以本脚本可以随便多跑几次都不会重复打扰。缺 count 字段的老记录按 1 次算。
该文件读不出来时**直接报错退出，不当作 0 次**——那会让 06:00 的兜底 timer 重复发一封，
而且不留任何痕迹。

这套计数是给两个定时钩子用的（见 AGENTS.md 的「发信」一节）：
  1. `lithub-daily.service` 的 `ExecStartPost` —— 05:00 出完报立刻发，正常路径；
  2. `lithub-mailcheck.timer` —— 06:00 兜底：当日有日报但 count < 1 就补发一次。
     （出报失败/超时导致第 1 条没跑时，靠它补。）

两种情况**不算失败、退出码 0**（这样挂在 systemd 的 ExecStartPost 上不会把整次运行
标成 failed）：`email.enabled = false`，以及「当天没有日报可发」（多半是频率闸门跳过了
本期）。日志里都会写明原因。`--check` / `--dry-run` 不受 `enabled` 影响——它们本来就不
发信，被这道闸门挡住的话，「先自检、再打开」的顺序会让自检只打印一句「已跳过」。

用法:
  python3 scripts/send_digest.py --dry-run           # 只打印将发送的内容概要，不连服务器
  python3 scripts/send_digest.py --check             # SMTP 连通性/登录自检，不发送
  python3 scripts/send_digest.py                     # 发当天（已发过则跳过）
  python3 scripts/send_digest.py --date 2026-09-19   # 补发某天
  python3 scripts/send_digest.py --force             # 强制重发（计数 +1）
  python3 scripts/send_digest.py --to a@b.c          # 临时换收件人（测试用）
"""
import argparse
import json
import re
import smtplib
import socket
import ssl
import sys
import time
from datetime import date
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

# ── 强制 IPv4 ──────────────────────────────────────────────────────────
# smtplib 也走 socket.getaddrinfo。本机曾出现「有全局 IPv6 地址与默认路由但实际 100%
# 丢包」，Python 没有 curl 那样的 happy-eyeballs 回退，会一直卡到超时（实测同一请求
# IPv6 181s vs 强制 IPv4 0.72s）。与 zapi.py / daily_digest.py 里那段一致，逐脚本各带一份。
_orig = socket.getaddrinfo


def _v4(host, port, family=0, *a, **k):
    return [r for r in _orig(host, port, family, *a, **k) if r[0] == socket.AF_INET]


socket.getaddrinfo = _v4

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
NEWS = ROOT / "文献日报"
LOGS = ROOT / "logs"
SENT = STATE / "email_sent.json"      # 已发记录（可写）
PASSFILE = STATE / "smtp_password"    # SMTP 密码/授权码（chmod 600，不进仓库）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime as _rt  # noqa: E402


def load_sent():
    """已发记录 {日期: {count, ...}}。

    读不出来时**不吞成空字典**——那等于告诉调用方「今天一次都没发过」，06:00 的兜底
    timer 于是重复发一封，而且不留任何痕迹。改成抛出，由调用方记日志并以非零退出码结束。
    """
    if not SENT.exists():
        return {}
    try:
        return json.loads(SENT.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"{SENT} 读不出来（{type(e).__name__}: {e}）——它记着今天已经发过"
                         f"几次，先打开看一眼再决定删改，别直接当没发过") from e


def save_sent(d):
    STATE.mkdir(exist_ok=True, parents=True)
    tmp = SENT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(SENT)


def split_addrs(s):
    """收件人串 → 列表。中英文逗号与分号都认，空项丢掉。"""
    for ch in ("；", ";", "，"):
        s = s.replace(ch, ",")
    return [x.strip() for x in s.split(",") if x.strip()]


def build_message(sender, recipients, subject, body):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.partition("@")[2] or "lithub.local")
    # utf-8 正文：EmailMessage 会自动选 base64 传输编码，长行（摘要段落）因此不会
    # 撞上 RFC 5322 的 998 字符限制。
    msg.set_content(body, subtype="plain", charset="utf-8")
    return msg


def connect(cfg, log):
    """按配置连上 SMTP（465 直连 SSL / 其它端口明文 + STARTTLS）。返回连接对象。"""
    host, port, use_ssl = cfg["smtp_host"], cfg["smtp_port"], cfg["use_ssl"]
    ctx = ssl.create_default_context()
    t0 = time.time()
    if use_ssl:
        s = smtplib.SMTP_SSL(host, port, timeout=60, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=60)
        s.ehlo()
        s.starttls(context=ctx)
    s.ehlo()
    log(f"  已连接 {host}:{port}（{'SSL' if use_ssl else 'STARTTLS'}，"
        f"{time.time() - t0:.1f}s）：{s.esmtp_features.get('auth', '（未声明 AUTH）')}")
    return s


def do_check(cfg, log, flush):
    """连通性 + 登录自检。不发送任何邮件、不写已发记录。"""
    if not cfg["smtp_host"]:
        log("  ! 没有 smtp_host，无法自检")
        flush()
        return 1
    if not cfg["smtp_user"]:
        log("  ! 没有 smtp_user，无法自检：登录账号也是发件地址，"
            "填 config/runtime.json 的 email.smtp_user")
        flush()
        return 1
    try:
        s = connect(cfg, log)
    except Exception as e:
        log(f"  ! 连接失败：{type(e).__name__} {e}")
        flush()
        return 1
    try:
        if PASSFILE.exists() and PASSFILE.read_text(encoding="utf-8").strip():
            s.login(cfg["smtp_user"], PASSFILE.read_text(encoding="utf-8").strip())
            log(f"  ✅ 登录成功（{cfg['smtp_user']}）")
        else:
            log(f"  （{PASSFILE} 不存在或为空，跳过登录自检）")
    except Exception as e:
        log(f"  ! 登录失败：{type(e).__name__} {e}")
        log("    常见原因：密码不对 / 邮箱需要在网页端开启 SMTP 并生成「客户端授权码」")
        flush()
        return 1
    finally:
        try:
            s.quit()
        except Exception:
            pass
    log("  ✅ 自检通过，可以真发")
    flush()
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="把文献日报作为纯文本邮件发出去。收件人/服务器在 config/runtime.json "
                    "的 email 段，密码在 state/smtp_password。")
    ap.add_argument("--date", default="", help="发哪一天（默认今天）")
    ap.add_argument("--to", default="", help="临时覆盖收件人（逗号分隔），测试用")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要发送的内容概要，不连服务器、不发送")
    ap.add_argument("--check", action="store_true",
                    help="只做 SMTP 连通性与登录自检，不发送")
    ap.add_argument("--force", action="store_true", help="忽略已发记录，重发")
    a = ap.parse_args()

    LOGS.mkdir(exist_ok=True, parents=True)
    lines = []

    def log(msg):
        print(msg)
        lines.append(msg)

    def flush():
        with (LOGS / "send_digest.log").open("a", encoding="utf-8",
                                            errors="replace") as f:
            f.write("\n".join(lines) + "\n")

    cfg = _rt.EMAIL
    today = a.date or date.today().isoformat()
    log(f"=== {time.strftime('%F %T')} 发日报 {today} ===")
    for w in _rt.WARNINGS:
        log(f"  ! 配置警告：{w}")

    # --check 是纯 SMTP 自检，既不发信也不看日报，所以放在 enabled 闸门与「今天有没有
    # 日报」检查之前。否则按手册的顺序「先 --check 自检、再打开 enabled」时，它只会打印
    # 一句「enabled = false，跳过」，让人以为 SMTP 有问题。
    if a.check:
        return do_check(cfg, log, flush)

    # --dry-run 同理绕过 enabled：它本来就不发信，只是给人看效果
    if not cfg["enabled"] and not a.dry_run:
        log("  config/runtime.json 的 email.enabled = false，跳过（不算失败）")
        flush()
        return 0

    md_path = NEWS / f"{today}.md"
    if not md_path.exists():
        if a.date:
            log(f"  ! 找不到 {md_path}——显式指定了日期但那天没有日报，算错误")
            flush()
            return 1
        log(f"  今天没有日报（{md_path.name} 不存在），跳过（不算失败）"
            f"——多半是频率闸门跳过了本期")
        flush()
        return 0

    body = md_path.read_text(encoding="utf-8")
    n = len(re.findall(r"^## \d+\.", body, flags=re.M))
    recipients = split_addrs(a.to or cfg["to"])
    if not recipients:
        log("  ! 没有收件人：填 config/runtime.json 的 email.to，或用 --to 临时指定")
        flush()
        return 1
    subject = (cfg["subject"] or "文献日报 {date}").format(date=today, n=n)
    # 不拿收件人顶替空的 smtp_user：那会把「账号根本没配」伪装成一次认证失败
    sender = cfg["smtp_user"]

    log(f"  收件人：{', '.join(recipients)}"
        + (f"　|　发件人：{sender}" if sender else "　|　发件人：未配置 smtp_user"))
    log(f"  主题：{subject}")
    log(f"  正文：{md_path.name}（{len(body)} 字符 / {body.count(chr(10)) + 1} 行 / {n} 篇），"
        f"纯文本 Markdown 原文")

    # 「当日计数」= 这一天成功发出去几次，存在 state/email_sent.json 里。
    # 出报后自动发一次 → 计数 1；06:00 的兜底检查只在计数 < 1 时才补发，
    # 所以正常日子多跑几次本脚本都不会重复打扰（--force 才会把计数加上去）。
    try:
        sent = load_sent()
    except ValueError as e:
        log(f"  ! {e}")
        flush()
        return 1
    prev = sent.get(today) or {}
    # 老记录没有 count 字段：有记录就当发过 1 次
    done = int(prev.get("count") or (1 if prev else 0))
    if done >= 1 and not a.force:
        log(f"  {today} 已发过 {done} 次（最近 {prev.get('sent_at')} → {prev.get('to')}），"
            f"当日计数 {done}，跳过；要强制重发加 --force")
        flush()
        return 0

    # dry-run 放在凭据检查之前：没配密码也该能先看效果
    if a.dry_run:
        log("  --dry-run：不连服务器、不发送。正文前 12 行预览 ——")
        for ln in body.splitlines()[:12]:
            log("    | " + ln)
        log("    | …（其余略）")
        flush()
        return 0

    if not cfg["smtp_host"]:
        log("  ! 没有 smtp_host：填 config/runtime.json 的 email.smtp_host")
        flush()
        return 1
    if not cfg["smtp_user"]:
        log("  ! 没有 smtp_user：填 config/runtime.json 的 email.smtp_user"
            "（登录账号，通常是完整邮箱地址）")
        log("    留空时服务器只会回一句含糊的认证失败，所以在这里先挡下")
        flush()
        return 1
    if not PASSFILE.exists() or not PASSFILE.read_text(encoding="utf-8").strip():
        log(f"  ! 缺密码：{PASSFILE} 不存在或为空")
        log("    密码不放在 config/runtime.json（那个文件要进复现包）。创建：")
        log(f"      printf '%s' '你的邮箱密码或授权码' > {PASSFILE} && chmod 600 {PASSFILE}")
        flush()
        return 1
    pw = PASSFILE.read_text(encoding="utf-8").strip()

    msg = build_message(sender, recipients, subject, body)
    try:
        s = connect(cfg, log)
    except Exception as e:
        log(f"  ! 连接失败：{type(e).__name__} {e}")
        flush()
        return 1
    try:
        s.login(cfg["smtp_user"], pw)
        s.send_message(msg)
    except Exception as e:
        log(f"  ! 发送失败：{type(e).__name__} {e}")
        flush()
        return 1
    finally:
        try:
            s.quit()
        except Exception:
            pass

    cnt = done + 1
    sent[today] = {"count": cnt, "sent_at": time.strftime("%F %T"), "to": recipients,
                   "subject": subject, "bytes": len(body.encode("utf-8")),
                   "papers": n, "source": md_path.name}
    save_sent(sent)
    log(f"  ✅ 已发送，{today} 当日计数 = {cnt}（state/email_sent.json 共 {len(sent)} 天记录）。"
        f"重发同一期要加 --force")
    flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
