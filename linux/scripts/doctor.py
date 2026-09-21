#!/usr/bin/env python3
"""一条命令告诉你这台机器还缺什么——装完环境先跑它。

```bash
python3 scripts/doctor.py          # Linux / macOS
python scripts\\doctor.py           # Windows（PowerShell / cmd）
```

九项检查逐条打印 `✅ / ⚠️ / ❌`，最后一行是汇总。退出码 **0 = 全好**、
**1 = 有硬性缺失**（❌），便于脚本或 CI 判断。

**只做只读检查**：不问 LLM、不调 MinerU、不碰 Zotero 的写接口。
唯一的写动作是中文编码自检——在 `state/work/` 建一个临时文件、读写比完立刻删掉
（`state/work/` 不存在时会建出来，它本来就是缓存目录）。

为什么要有这项编码自检：Windows 中文环境的 Python 默认编码是 **cp936 (GBK)**，
文件读写不显式指定 `encoding=` 时按它解码。仓库里的脚本已全部显式写 `utf-8`，
但**乱码是静默的**——写出乱码再拿去 Zotero 建目录，比报错难查得多，所以这里每次都验一遍。
"""
import argparse
import json
import locale
import os
import shutil
import sys
import urllib.error
import urllib.request

ROOT = os.path.expanduser("~/LitHub")
WORK = os.path.join(ROOT, "state", "work")
KEYFILE = os.path.join(ROOT, "state", "zotero_local_key")
# 用 127.0.0.1 而不是 localhost：本机曾出现「有 IPv6 地址但 100% 丢包」，
# 而 Python 没有 happy-eyeballs 回退，会一路卡到超时（详见帮助手册 §3.8）。
ZOTERO_PING = "http://127.0.0.1:23119/api/users/0/items?limit=1"
PY_MIN = (3, 12)
CHINESE = "结构退化 O2 释放 电量"

# 每项外部命令：用途 + 各平台的安装提示
CLIS = [
    ("mineru-open-api", "PDF → Markdown（mineru_batch.py 用裸命令名调它）",
     {"Linux": "pip install mineru   # 或按官方文档装，装完 mineru-open-api auth 配 token",
      "Darwin": "pip install mineru    # 装完 mineru-open-api auth 配 token",
      "Windows": "pip install mineru    # 装完 mineru-open-api auth 配 token；\n"
                 "        pip 脚本目录（…\\Python312\\Scripts）必须在 PATH 上"}),
    ("pdfinfo", "读 PDF 页数（intake_pdfs.py 用，poppler 提供）",
     {"Linux": "sudo apt install poppler-utils",
      "Darwin": "brew install poppler",
      "Windows": "poppler for Windows：到 github.com/oschwartz10612/poppler-windows/releases\n"
                 "        下载 Release-*.zip 解压，把解压目录下的 Library\\bin 加进 PATH，\n"
                 "        然后**重开终端**（PATH 改动对已开的终端不生效）"}),
    ("pdftotext", "抠首页 DOI（intake_pdfs.py 用，poppler 提供）",
     {"Linux": "sudo apt install poppler-utils",
      "Darwin": "brew install poppler",
      "Windows": "同 pdfinfo，poppler 包里的 Library\\bin"}),
    ("gs", "Ghostscript：把 >8 MB 的 PDF 预压缩（只 --flash 兜底模式需要）",
     {"Linux": "sudo apt install ghostscript",
      "Darwin": "brew install ghostscript",
      "Windows": "Ghostscript 官网安装包：ghostscript.com/releases/gsdnld.html\n"
                 "        装完确认 gswin64c 所在目录在 PATH 上，并**重开终端**"}),
]

TIER = {
    "Linux": "✅ 作者主力环境，已实测",
    "Darwin": "⚠️ 应该可用（纯标准库 + 同样的外部 CLI），**未实测**",
    "Windows": "⚠️ 代码已适配（文件/子进程编码、命令行长度、路径分隔符），"
               "但**作者没有 Windows 机器、未实测**；也可用 WSL2 原样跑 Linux",
}


def platform_key():
    if sys.platform.startswith("win"):
        return "Windows"
    if sys.platform == "darwin":
        return "Darwin"
    return "Linux"


def check_python(results):
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v[:2] >= PY_MIN:
        results.append(("ok", f"Python 版本 {ver}", [f"解释器：{sys.executable}"]))
    else:
        results.append(("bad", f"Python 版本 {ver}（需要 ≥ {PY_MIN[0]}.{PY_MIN[1]}）",
                        [f"解释器：{sys.executable}",
                         "项目只依赖标准库，但用到了 3.12 的语法/接口，低版本会直接语法错"]))


def check_platform(results, plat):
    results.append(("ok", f"平台 {plat}", [TIER[plat]]))


def check_root(results, plat):
    p = os.path.abspath(ROOT)
    if not os.path.isdir(ROOT):
        results.append(("bad", f"项目根目录不存在：{p}", [
            "脚本里写死了 ~/LitHub（各脚本顶部的 ROOT 常量）——放到别处的副本跑不起来。",
            "Windows 上展开成 C:\\Users\\<你>\\LitHub。"]))
        return False
    try:
        os.makedirs(WORK, exist_ok=True)
        probe = os.path.join(WORK, ".doctor_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        results.append(("bad", f"根目录存在但不可写：{p}", [str(e)]))
        return False
    results.append(("ok", f"根目录存在且可写：{p}", []))
    return True


def check_encoding(results, plat):
    """中文编码自检——本文件存在的主要理由。

    两种写法都验：① 显式 utf-8（脚本现在的做法，必须过）；② 不带 encoding= 的
    默认编码（就是 Windows 上 cp936 那条老路，只报告、不判死）。
    """
    default = locale.getpreferredencoding(False)
    det = [f"locale.getpreferredencoding(False) = {default}",
           f"sys.stdout.encoding = {getattr(sys.stdout, 'encoding', None)}"
           f" / 文件系统 = {sys.getfilesystemencoding()}"]
    tmp = os.path.join(WORK, ".doctor_encoding.tmp")
    payload = {"primary": CHINESE, "note": "≥ ≈ → 全角：℃，单位 mAh/g"}

    # ① 显式 utf-8：与仓库脚本完全一致的路径
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        with open(tmp, encoding="utf-8") as f:
            back = json.load(f)
    except (OSError, ValueError, UnicodeError) as e:
        results.append(("bad", "中文编码自检：显式 utf-8 读写失败", det + [repr(e)]))
        return
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    det.insert(0, f"显式 utf-8 往返：写出再读回含中文的 JSON，"
                  + ("逐字段一致" if back == payload else "内容不一致！"))

    if back != payload:
        results.append(("bad", "中文编码自检：utf-8 往返内容不一致", det))
        return

    if default.lower().replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
        results.append(("ok", "中文编码自检：通过（本机默认编码就是 UTF-8）", det))
        return

    # ② 默认编码（Windows 上通常是 cp936）：能编码就免报，不能编码或读回不一致就提醒
    extra = f"默认编码 {default} 不是 UTF-8（Windows 中文环境通常是 cp936/GBK）。"
    try:
        with open(tmp, "w") as f:                       # 故意不写 encoding=
            f.write(CHINESE)
        with open(tmp) as f:
            raw = f.read()
        os.remove(tmp)
    except (OSError, UnicodeError) as e:
        results.append(("warn", f"中文编码自检：显式 utf-8 通过，但默认编码 {default} 不可用", det + [
            extra, f"不带 encoding= 时失败：{e!r}",
            "保险做法：设环境变量 PYTHONUTF8=1（PowerShell：$env:PYTHONUTF8=1，"
            "要永久生效就写进系统环境变量）。"]))
        return
    if raw != CHINESE:
        results.append(("warn", f"中文编码自检：显式 utf-8 通过，但默认编码 {default} 读回不一致", det + [
            extra, "本仓库的脚本已全部显式写 utf-8，不受影响；但**混用**两种编码的文件会乱码。",
            "保险做法：设 PYTHONUTF8=1。"]))
        return
    results.append(("warn", f"中文编码自检：显式 utf-8 通过（默认编码 {default} 也能往返中文）", det + [
        extra + " 仓库脚本不受影响；手写 JSON 或第三方工具建议一并显式 utf-8。",
        "想彻底避开：设 PYTHONUTF8=1。",
        "本项只提醒、不算失败——脚本的读写都带 encoding='utf-8'。"]))
    return


def check_clis(results, plat):
    for cmd, why, hints in CLIS:
        path = shutil.which(cmd)
        if path:
            results.append(("ok", f"外部命令 {cmd} 在 PATH 上", [f"{path}（{why}）"]))
        else:
            results.append(("warn", f"外部命令 {cmd} 不在 PATH 上", [
                f"用途：{why}", "安装方式：", "        " + hints[plat]]))


def check_llm(result_sink, plat):
    env = os.environ.get("LITHUB_KIMI")
    path = env or os.path.expanduser("~/.kimi-code/bin/kimi")
    if os.path.exists(path):
        result_sink.append(("ok", "LLM CLI 存在", [f"{path}"
                            + ("（来自 LITHUB_KIMI）" if env else "（默认路径）")]))
        return
    det = [f"试过：{path}"]
    if plat == "Windows":
        det += ["Windows 上默认路径 `~/.kimi-code/bin/kimi` 是 Unix 布局，通常不存在——",
                "把 LITHUB_KIMI 指到可执行文件（kimi.exe / kimi.cmd）：",
                '        PowerShell: $env:LITHUB_KIMI = "C:\\Users\\<你>\\...\\kimi.exe"',
                '        永久生效:   setx LITHUB_KIMI "C:\\Users\\<你>\\...\\kimi.exe"']
    else:
        det += ["装好 CLI 后确认路径，或用 LITHUB_KIMI 覆盖。"]
    det += ["只影响写中文总结 / 挑配图（summarize_batch.py、embed_figures.py），"
            "缺了不会让别的脚本崩。"]
    result_sink.append(("warn", "LLM CLI 不存在", det))


def check_zotero(results):
    req = urllib.request.Request(ZOTERO_PING, headers={"Zotero-API-Version": "3"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            code = r.status
        if code == 200:
            results.append(("ok", "Zotero 本地 API 可用（200）", [ZOTERO_PING]))
            return
        results.append(("bad", f"Zotero 本地 API 返回 {code}", [ZOTERO_PING]))
    except urllib.error.HTTPError as e:
        det = [ZOTERO_PING, f"HTTP {e.code}"]
        if e.code == 403:
            det.append("403 = 开关没开：Zotero → 设置 → 高级 → 勾选"
                       "「允许其他应用与本机 Zotero 通信」，然后重试")
        results.append(("bad", f"Zotero 本地 API 拒绝访问（{e.code}）", det))
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        results.append(("bad", "连不上 Zotero 本地 API（23119 端口）", [
            ZOTERO_PING, f"{e!r}",
            "先确认 Zotero 桌面版**正在运行**；它没跑时这个端口没人监听。",
            "再确认那个开关已勾（Zotero → 设置 → 高级）。"]))


def check_keyfile(results):
    if os.path.exists(KEYFILE):
        size = os.path.getsize(KEYFILE)
        results.append(("ok", "写入授权密钥存在", [
            f"{KEYFILE}（{size} 字节）"
            + ("" if size else "  ← 0 字节，多半授权没写成")]))
        if not size:
            results[-1] = ("bad", "写入授权密钥是空文件", [KEYFILE,
                                                        "重新做一次授权（帮助手册 §1.1 步骤 1）"])
    else:
        results.append(("bad", "缺少写入授权密钥", [
            f"{KEYFILE}",
            "scripts/zapi.py 的 _key() 在**第一次真发请求时**才读它——只 import 不受影响，",
            "但任何要连 Zotero 的脚本都会在这里失败。",
            "授权命令见 帮助手册 §1.1 步骤 1。Windows 上同样可用（Zotero 会弹对话框）：",
            '        curl -H "Zotero-API-Version: 3" -H "Content-Type: application/json" \\',
            '             -d \'{"appName":"LitHub"}\' "http://127.0.0.1:23119/api/users/0/keys"',
            "        （PowerShell 把反斜杠续行换成反引号 `，或整条写在一行里；",
            "         把返回的 key 存进 state\\zotero_local_key）"]))


def check_longpath(results, plat):
    if plat != "Windows":
        return
    base = os.path.abspath(ROOT)
    # papers\<8位key>_<60字slug>\images\<64位hash>.jpg ≈ 177 字符
    typical = len(base) + len("\\papers\\XXXXXXXX_") + 60 + len("\\images\\") + 64 + len(".jpg")
    det = [f"根目录绝对路径 {len(base)} 字符：{base}",
           f"典型最深路径 papers\\<8位key>_<60字slug>\\images\\<64位hash>.jpg"
           f" ≈ {typical} 字符（上限 260）"]
    if typical > 260:
        results.append(("bad", f"长路径风险：典型路径约 {typical} 字符，超 260 上限", det + [
            "开「启用长路径支持」：gpedit.msc → 计算机配置 → 管理模板 → 系统 →"
            " 文件系统 → 启用 Win32 长路径；",
            "或注册表 HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem 的"
            " LongPathsEnabled = 1（DWORD），改完重启。"]))
    else:
        results.append(("warn", f"长路径：典型最深路径约 {typical} 字符（上限 260）", det + [
            "余量不大——用户名或标题特别长时会逼近上限，届时开「启用长路径支持」：",
            "注册表 HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem 的"
            " LongPathsEnabled = 1（DWORD），或 gpedit.msc 里对应策略。",
            "（未实测：作者没有 Windows 机器。）"]))


MARK = {"ok": "✅", "warn": "⚠️ ", "bad": "❌"}


def main():
    ap = argparse.ArgumentParser(
        description="检查 LitHub 运行环境缺什么（只读检查，不问 LLM、不调 MinerU）。")
    ap.parse_args()

    # cp936 控制台下 ✅/⚠️ 不属于 GBK：重定向到文件时 print 会 UnicodeEncodeError。
    # 这里把错误策略放宽成 replace——控制台输出不受影响，重定向时降级成 "?" 而不炸。
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    plat = platform_key()
    results = []
    check_python(results)
    check_platform(results, plat)
    ready = check_root(results, plat)
    if ready:
        check_encoding(results, plat)
    check_clis(results, plat)
    check_llm(results, plat)
    if ready:
        check_zotero(results)
        check_keyfile(results)
    else:
        results.append(("warn", "Zotero 检查跳过（根目录不可用）", []))
    check_longpath(results, plat)

    print(f"LitHub 环境自检（{plat} / Python {sys.version.split()[0]}）\n")
    for level, title, det in results:
        print(f"{MARK[level]} {title}")
        for line in det:
            print(f"    {line}")
    print()

    n_ok = sum(1 for lv, _, _ in results if lv == "ok")
    n_warn = sum(1 for lv, _, _ in results if lv == "warn")
    n_bad = sum(1 for lv, _, _ in results if lv == "bad")
    tail = f"{n_ok} 项通过 / {n_warn + n_bad} 项需要注意"
    if n_bad:
        tail += f"（其中 {n_bad} 项缺失会直接卡住流水线，标 ❌ 的那些）"
    else:
        tail += "——没有硬性缺失，可以开始跑 帮助手册.md §1 的最短路径"
    print("总结：" + tail)
    if plat == "Windows":
        print("本机平台未被作者实测过，装完请把这份输出的全文贴回 issue。")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
