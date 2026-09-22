#!/usr/bin/env python3
"""把工作区打成一个可以直接推到 GitHub 的发布目录 `release/`。

为什么需要它
------------
`release/` 会被**整体重建**，所以不能手工往里放东西——放进去的下次同步就没了。
凡是「只该出现在发布里、不该出现在工作区」的素材，都放在 `docs/release/`
（见下），由本脚本搬到位。

产出布局
--------
    release/
    ├── README.md          ← 仓库首页，用 docs/release/root-README.md
    ├── LICENSE
    ├── .gitignore
    ├── .github/workflows/ci.yml
    ├── linux/             ← 完整一份，可以直接改名成 ~/LitHub
    └── windows/           ← 同上 + WINDOWS_先读这个.txt

两份的**代码完全相同**（Windows 适配是合进主代码的，不是分叉版本）；分成两个目录只是
为了让用户一眼找到该看哪份文档。CI 会把两份都编译一遍，能抓出复制时的缺漏。

会带进发布 / 会被排除的
-----------------------
- **带**：工作区里**所有没被 `.gitignore` 排除**的文件（与 `git add -A` 的口径一致）
- **排除**：`state/` `papers/` `logs/` `文献日报/` `待整理/` `.obsidian/`（都在 .gitignore 里）、
  `.git/`、`__pycache__/`、`release/` 自身、以及 `docs/release/`（它只供给发布用）

安全约定
--------
- **保留 `release/.git`**：如果你习惯在 `release/` 里 `git init` 并推送，重建时不会把它删掉
  （2026-09-20 踩过这个坑——原先用 rmtree 重建，把仓库一起删了）。
- 先建临时目录、拷贝完整后再原子替换；中途失败不会留下半个 `release/`。

用法：
  python3 scripts/make_release.py            # 只报告将要写入什么
  python3 scripts/make_release.py --apply    # 真的重建
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── 控制台编码兜底 ────────────────────────────────────────────────────
# Windows 中文控制台的 Python 默认编码是 cp936：print 路径里的非 GBK 字符会抛
# UnicodeEncodeError，输出断在半路。只放宽错误策略、不改 encoding——编不出来时
# 退化成 "?"，UTF-8 环境下的输出字节一个都不变。本脚本不 import 任何本地模块，
# 所以自带一份（与 zapi.py 同款）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "state" / "work" / ".dist_build"
DEST = ROOT / "release"
ASSETS = ROOT / "docs" / "release"

# 首页 README 的抬头：让拿到发布包的人知道「这个目录本身不能直接跑」
BANNER = (
    "> **📦 你正在看的是发布包里的一份。** 这个目录必须放到 `~/LitHub` 才能跑——\n"
    "> 把**本目录整个改名/移动**成 `~/LitHub`（不是在里面直接跑，见仓库根目录的 README）。\n"
    "> `linux/` 与 `windows/` 两份的**代码完全相同**，区别只在文档。\n\n"
)


# 只该留在本地、绝不进发布包的工作稿。.gitignore 里也列了它们，但那要依赖 git 在场；
# 这里再钉一遍，是因为其中几个含作者的绝对路径与机构名（2026-09-21 审查发现：
# 当时的过滤是 `git check-ignore` 单点依赖，目录不是仓库时会静默全部放行）。
DROP_NAMES = {"README 1.md", "脚本说明.md", "分类与标签总表.md", "文献画像.md", "报告.md"}
DROP_PREFIX = ("新文献候选", "综述_")


def git_ready():
    """过滤依赖 `git check-ignore`，所以先确认它真的能用。

    目录不是 git 工作树时 `check-ignore` 对所有文件都返回非 0（= 没被忽略），
    于是上面那些本地工作稿会被**静默**打进发布包。宁可不跑，也不出这种包。
    """
    if shutil.which("git") is None:
        print("❌ 找不到 git —— 发布包的过滤靠 `git check-ignore`，装好再跑。")
        return False
    r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 or r.stdout.strip() != "true":
        print("❌ 工作区不是一个 git 工作树（`git rev-parse --is-inside-work-tree` 失败）。\n"
              "   先 `git init && git add -A`，否则无法判断哪些本地文件不该发布。")
        return False
    return True


def collect():
    """工作区里所有「会被 git 提交」的文件，返回相对路径列表；git 不可用时返回 None。"""
    if not git_ready():
        return None
    out = []
    for f in ROOT.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(ROOT)
        # 顶层目录白名单式的排除。"home" 是防线：脚本里一旦有人把绝对路径当相对路径用
        # （写成 `home/<用户名>/...`），那个目录就会出现在这里，而它绝不是项目内容——
        # 2026-09-21 发布自检时真撞到过一个 `home/<用户名>/<别的项目>/…` 的空文件。
        if rel.parts[0] in (".git", "release", "state", "papers", "logs", "home",
                            "文献日报", "待整理", ".obsidian", ".github"):
            continue
        if "__pycache__" in rel.parts or rel.parts[:2] == ("docs", "release"):
            continue
        if rel.name in DROP_NAMES or rel.name.startswith(DROP_PREFIX):
            continue
        if subprocess.run(["git", "check-ignore", "-q", str(rel)],
                          cwd=ROOT).returncode == 0:
            continue
        out.append(rel)
    return sorted(out)


# 发布包里 email 段一律换成这些值。工作区的 config/runtime.json 保留作者的真值
# （send_digest.py 每天要读它），所以脱敏只能发生在拷贝这一侧，不能靠手改源文件——
# 2026-09-21 那次脱敏就是改在源文件上，结果作者的日报邮件静默停发了两天。
EMAIL_PLACEHOLDER = {
    "enabled": False,
    "to": "you@example.com",
    "smtp_host": "smtp.example.com",
    "smtp_user": "you@example.com",
}


def sanitize_runtime(path):
    """把发布包里 config/runtime.json 的 email 段换成占位符。

    只动那几个身份字段，其余原样保留：运行参数（篇数 / 权重 / 池子深度）是作者的
    实测工作点，本来就该随包发布；发件账号是个人隐私，不该出去。`_help` 的说明也
    留着——它正是写给拿到包的人看的。
    """
    if not path.exists():
        return
    cfg = json.loads(path.read_text(encoding="utf-8"))
    em = cfg.get("email")
    if isinstance(em, dict):
        em.update(EMAIL_PLACEHOLDER)
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"  🔒 已脱敏 {path.relative_to(STAGE)} 的 email 段")


def main():
    apply = "--apply" in sys.argv
    files = collect()
    if files is None:
        return 1
    win_txt = ASSETS / "WINDOWS_先读这个.txt"
    root_readme = ASSETS / "root-README.md"
    missing = [p for p in (win_txt, root_readme) if not p.exists()]

    print(f"将写入 release/：每版 {len(files)} 个项目文件 × 2 个版本"
          f"（共 {len(files) * 2 + 4} 个）")
    print(f"  linux/   {len(files)} 个")
    print(f"  windows/ {len(files) + 1} 个（多一份 {win_txt.name}）")
    print(f"  根目录   README.md / LICENSE / .gitignore / .github/workflows/ci.yml")
    print(f"  docs/release/ 里的素材：{win_txt.name}、{root_readme.name}")
    if missing:
        print(f"  ❌ 缺素材：{'、'.join(str(p) for p in missing)}")
        return 1
    if (DEST / ".git").exists():
        print("  注意：release/.git 存在，重建时会原样保留")
    if not apply:
        print("\n（只报告。要真的重建加 --apply）")
        return 0

    # 仓库级素材先确认齐了再动 release/：建完暂存区才发现缺文件，会留下半成品。
    need = [ROOT / n for n in ("LICENSE", ".gitignore", ".github/workflows/ci.yml")]
    lack = [p for p in need if not p.exists()]
    if lack:
        print("  ❌ 缺文件：" + "、".join(str(p.relative_to(ROOT)) for p in lack))
        return 1

    # ── 建暂存区
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for variant in ("linux", "windows"):
        for rel in files:
            dst = STAGE / variant / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dst)
        # 每个版本里的 README 是同一份：一律加抬头，保证两份逐字节相同
        rp = STAGE / variant / "README.md"
        rp.write_text(BANNER + rp.read_text(encoding="utf-8"), encoding="utf-8")
        # 发件账号只在发布包这一份里脱敏，工作区那份照旧可用
        sanitize_runtime(STAGE / variant / "config" / "runtime.json")

    # Windows 入口说明只放 windows/ 根目录——不进 linux/，也不进任何 docs/
    shutil.copy2(win_txt, STAGE / "windows" / win_txt.name)

    # 仓库级文件
    for name in ("LICENSE", ".gitignore"):
        shutil.copy2(ROOT / name, STAGE / name)
    shutil.copy2(root_readme, STAGE / "README.md")
    (STAGE / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / ".github" / "workflows" / "ci.yml",
                 STAGE / ".github" / "workflows" / "ci.yml")

    # ── 替换（保留 release/.git）
    keep_git = None
    if (DEST / ".git").exists():
        keep_git = STAGE.parent / ".release_git"
        if keep_git.exists():
            shutil.rmtree(keep_git)
        shutil.move(str(DEST / ".git"), str(keep_git))
    if DEST.exists():
        shutil.rmtree(DEST)
    STAGE.rename(DEST)
    if keep_git is not None:
        shutil.move(str(keep_git), str(DEST / ".git"))
        print("  ✅ release/.git 已还原")

    n = sum(1 for _ in DEST.rglob("*") if _.is_file())
    print(f"✅ release/ 重建完成：{n} 个文件")
    print("   下一步：cd release && git add -A && git commit && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
