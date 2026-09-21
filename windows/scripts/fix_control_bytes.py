#!/usr/bin/env python3
"""修掉 MinerU 输出里残留的控制字节（U+0000 等）。

这些字节是 PDF 字体 ToUnicode 映射损坏时留下的：连字、度符号、上标负号、
希腊字母等被写成 NUL。转成精准模式后绝大多数已消失，剩下的是符号类。

做法：按上下文规则判定该位置的字符，**判定不出的不猜**，替换成 `【?】` 并单列出来，
保证文件变成合法 UTF-8 文本（否则会被当二进制文件）。

⚠️ 占位符不要用 `□`（U+25A1）—— 本库里 `□` 是**晶格空位**的合法记号
（如 `Li4/7[□1/7Mn6/7]O2`），用它当占位符会分不清哪个是原有内容。

用法:
  python3 scripts/fix_control_bytes.py            # 只报告将要怎么改
  python3 scripts/fix_control_bytes.py --apply    # 实际写入（原件备份到 state/trash/）
"""
import glob
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.expanduser("~/LitHub")
APPLY = "--apply" in sys.argv

ELEMENT_DASH = [("Li", "Ni"), ("Li", "O"), ("Al", "O"), ("Ni", "O"), ("Mn", "O"),
                ("Co", "O"), ("Al", "F"), ("Ni", "F"), ("Ni", "Mn"), ("Ni", "Co")]


def decide(l, r):
    """l/r 是控制字节左右的原文片段（r 未去首部空白）。返回 (替换字符, 规则名)。"""
    lt = l.rstrip()
    rt = r.lstrip()

    # R◉3m —— 空间群 R3̄m 的横杠
    if re.search(r"R\s*$", lt) and re.match(r"^3\s*m", rt):
        return "-", "R-3m 空间群"
    if re.search(r"R\s*3\s*<sup>\s*$", lt) and re.match(r"^</sup>\s*m", rt):
        return "-", "R-3m 空间群"

    # 温度：940◉C / 50◉C
    if re.match(r"^C\b", rt) and re.search(r"\d\s*$", lt):
        return "°", "温度符号"

    # 微米：1.5 ◉m
    if re.match(r"^m\b", rt) and re.search(r"\d\s*$", lt):
        return "μ", "微米 μm"

    # 上标负号：g<sup>◉</sup> <sup>1</sup> / dm◉<sup>3</sup>
    if re.search(r"<sup>\s*$", lt) and re.match(r"^</sup>", rt):
        return "−", "上标负号"
    if re.search(r"[a-zA-Z]\s*$", lt) and re.match(r"^<sup>\s*\d", rt):
        return "−", "上标负号"

    # 乘号：1.55 ◉ $10^{-12}$
    if re.match(r"^\$\s*1\s*0\s*\^", rt) and re.search(r"\d\s*$", lt):
        return "×", "乘号"

    # 断裂韧性 Γ（含 dΓ/da）、偏摩尔体积 Ω
    if re.search(r"(fracture toughness|crack resistance|expression)\s*$", lt):
        return "Γ", "断裂韧性符号 Γ"
    if re.search(r"\bd\s*$", lt) and re.match(r"^/\s*d\s*a", rt):
        return "Γ", "断裂韧性符号 Γ"
    if re.search(r"\bas\s*$", lt) and rt.startswith("("):
        return "Γ", "断裂韧性符号 Γ"
    if re.search(r"where\s*$", lt) and "partial molar volume" in r:
        return "Ω", "偏摩尔体积 Ω"

    # 负离子 / 负号：……]◉; ……(◉3.04 V、F◉)、58NiO◉
    if re.search(r"[\]\)]\s*$", lt) and re.match(r"^[;,)\s]", r):
        return "−", "负离子/负号"
    if re.search(r"\(\s*$", lt) and re.match(r"^\s*\d", r):
        return "−", "负号"
    if re.search(r"[A-Z][a-z]?O\s*$", lt) and re.match(r"^\s*(fragment|ion)", rt):
        return "−", "负离子"
    if re.search(r"\b[A-Z]\s*$", lt) and re.match(r"^\)", rt):
        return "−", "负离子"

    # 箭头：O^{2-}$ ◉ to oxidized
    if re.search(r"\$\s*$", lt) and re.match(r"^to\b", rt):
        return "→", "箭头"

    # 元素之间的连接号：Li◉Ni、Al◉O、Ni◉O
    for a, b in ELEMENT_DASH:
        if re.search(rf"{a}$", lt) and re.match(rf"^{b}\b", rt):
            return "–", "元素连接号"

    # 角度（放最后，避免抢掉上面的规则）：
    #   180◉ Ni-O-Ni 键角、7◉ and 32、0.02◉、0.029◉ (2q)、10◉ to 80
    if re.search(r"\d\s*$", lt):
        return "°", "角度符号"

    return None, "未定"


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "papers", "*", "paper.md")))
    total = 0
    hits = {}
    uncertain = []
    plan = {}

    for p in files:
        data = open(p, "rb").read()
        if not any(b < 9 or (13 < b < 32) for b in data):
            continue
        out = bytearray()
        i = 0
        for i, b in enumerate(data):
            if b < 9 or (13 < b < 32):
                l = data[max(0, i - 120):i].decode("utf8", "replace")
                r = data[i + 1:i + 121].decode("utf8", "replace")
                ch, rule = decide(l, r)
                total += 1
                hits[rule] = hits.get(rule, 0) + 1
                if ch is None:
                    uncertain.append((p, l[-60:].replace("\n", " "), r[:60].replace("\n", " ")))
                    ch = "【?】"
                out += ch.encode("utf8")
            else:
                out.append(b)
        plan[p] = bytes(out)

    print(f"控制字节共 {total} 处，涉及 {len(plan)} 个文件\n")
    print("按规则分类：")
    for k, v in sorted(hits.items(), key=lambda x: -x[1]):
        print(f"  {v:4d}  {k}")
    if uncertain:
        print(f"\n判定不出的 {len(uncertain)} 处（将替换为 【?】，需人工核对）：")
        for p, l, r in uncertain:
            print(f"  {os.path.basename(p).split('_')[0]}: …{l}◉{r}…")

    if not APPLY:
        print("\n未写入任何文件。加 --apply 执行。")
        return

    dest = os.path.join(ROOT, "state", "trash", time.strftime("%Y-%m-%d") + "_control_bytes")
    os.makedirs(dest, exist_ok=True)
    for p, newdata in plan.items():
        shutil.copy2(p, os.path.join(dest, os.path.basename(os.path.dirname(p)) + ".paper.md"))
        open(p, "wb").write(newdata)
    json.dump({"replaced": total, "rules": hits,
               "uncertain": [{"file": p, "left": l, "right": r} for p, l, r in uncertain],
               "backup_dir": dest},
              open(os.path.join(ROOT, "state", "control_bytes_fix.json"), "w",
                   encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n已修复 {len(plan)} 个文件、{total} 处；原件备份到 {dest}")
    print("清单写入 state/control_bytes_fix.json")


if __name__ == "__main__":
    main()
