#!/usr/bin/env python3
"""把 Zotero 里的 PDF 批量转成 Markdown。

默认走 `extract`（精准模式，需 token）：上限 **200 MB / 600 页**，产出 md + images/
等全部资源，不用预压缩、不用分块。

`--flash` 是免令牌的兜底模式：只有 Markdown，图表变成 `<!-- image-->` 占位符，而且
**单次上限 10 MB 且 20 页**——文件大小限制先于页数触发，`--pages` 绕不过 10 MB。走它时：
  - >8MB 的文件先用 ghostscript 重压（/ebook 150dpi；不够再 /screen 72dpi）
  - 页数 >20 再用 --pages 按 20 页分块
  - pdfseparate 在损坏 PDF 上不可用（每页会变成整份文档），故不使用

跳过判据是 paper.md 头部的 `engine:` 与本次引擎一致——flash 转的没有图片，
不能当成 extract 的已完成。

用法:
  python3 scripts/mineru_batch.py [<8位Zotero key>|all] [并发数] [--flash] [--force]
"""
import json, os, re, subprocess, sys, shutil, time, glob
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.expanduser("~/LitHub")
PAPERS = os.path.join(ROOT, "papers")
MANIFEST = os.path.join(ROOT, "state", "manifest.json")
WORK = os.path.join(ROOT, "state", "work")

MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 20
BIN = "mineru-open-api"

# flash = 免令牌、仅 Markdown，图表变 <!-- image--> 占位符，限 10MB/20页
# extract = 精准模式（需 token），产出 images/ 等全部资源，限 200MB/600页（默认）
ENGINE = "flash" if ("--engine=flash" in sys.argv or "--flash" in sys.argv) else "extract"
FORCE = "--force" in sys.argv


def paper_dir(rec):
    safe = re.sub(r"[^\w\- ]", "", rec["title"])[:60].strip().replace(" ", "_")
    return os.path.join(PAPERS, f"{rec['key']}_{safe}")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def gs_compress(src, dst, preset):
    cmd = ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
           f"-dPDFSETTINGS=/{preset}", "-dCompatibilityLevel=1.5",
           f"-sOutputFile={dst}", src]
    try:
        subprocess.run(cmd, capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return False
    return os.path.exists(dst) and os.path.getsize(dst) > 1000


def prepare(pdf, tmp):
    """把过大的 PDF 压到 8MB 以内，返回可用路径。"""
    if os.path.getsize(pdf) <= MAX_BYTES:
        return pdf
    for preset in ("ebook", "screen"):
        out = os.path.join(tmp, f"comp_{preset}.pdf")
        if gs_compress(pdf, out, preset):
            sz = os.path.getsize(out)
            log(f"    gs/{preset}: {os.path.getsize(pdf)/1e6:.1f}MB -> {sz/1e6:.1f}MB")
            if sz <= MAX_BYTES:
                return out
    return pdf


def call_flash(pdf, outdir, pages=None, retries=3):
    os.makedirs(outdir, exist_ok=True)
    err = "unknown"
    for attempt in range(retries):
        cmd = [BIN, "flash-extract", pdf]
        if pages:
            cmd += ["--pages", pages]
        cmd += ["-o", outdir]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1500)
            mds = glob.glob(os.path.join(outdir, "*.md"))
            if mds:
                return open(mds[0], encoding="utf-8").read(), None
            lines = (r.stdout + r.stderr).strip().split("\n")
            err = next((l for l in lines if "Error" in l or "Hint" in l),
                       lines[-1] if lines else "unknown")
        except subprocess.TimeoutExpired:
            err = "timeout"
        if attempt < retries - 1:
            time.sleep(6 * (attempt + 1))
    return None, err


def call_extract(pdf, outdir, retries=3):
    """精准模式（需 token）：产出 md + images/ 等全部资源，返回 md 所在目录。

    限制 200MB/600 页，不需要预压缩，也不需要分块。
    目录布局不做假设 —— 交给调用方把「md 所在的整个目录」搬走。
    """
    os.makedirs(outdir, exist_ok=True)
    err = "unknown"
    for attempt in range(retries):
        cmd = [BIN, "extract", pdf, "-o", outdir]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=3600)
            mds = glob.glob(os.path.join(outdir, "**", "*.md"), recursive=True)
            if mds:
                return os.path.dirname(mds[0]), None
            lines = (r.stdout + r.stderr).strip().split("\n")
            err = next((l for l in lines if "Error" in l or "msg" in l.lower()),
                       lines[-1] if lines else "unknown")
        except subprocess.TimeoutExpired:
            err = "timeout"
        if attempt < retries - 1:
            time.sleep(6 * (attempt + 1))
    return None, err


def front_matter(path, limit=65536):
    """读回 paper.md 头部那段 front-matter（含首尾 `---`）。

    不能只读固定字符数的窗口：`engine:` 行在标题、source 文件名之后，位置随两者
    长度浮动（约 68 + len(标题) + len(文件名) 字符），长标题会落到窗口外，于是
    每次运行都当成「没转过」重转一遍。
    """
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        first = f.readline()
        if first.strip() != "---":
            return first
        out.append(first)
        for line in f:
            out.append(line)
            if line.strip() == "---" or sum(len(x) for x in out) > limit:
                break
    return "".join(out)


def convert(rec):
    key, title = rec["key"], rec["title"]
    d = paper_dir(rec)
    md_path = os.path.join(d, "paper.md")
    if not FORCE and os.path.exists(md_path) and os.path.getsize(md_path) > 800:
        # 引擎不同就得重转（flash 转的没有图片，不能当已完成）
        if f"engine: {ENGINE}" in front_matter(md_path):
            return key, "skip", 0

    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(WORK, key)
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    t0 = time.time()
    try:
        header = (f"---\ntitle: \"{title.replace(chr(34), '')}\"\n"
                  f"zotero_key: {key}\n"
                  f"source: {os.path.basename(rec['path'])}\n"
                  f"pages: {rec['pages']}\n")
        if ENGINE == "extract":
            # 精准模式：整棵产出树（md + images/ + json）一起落到论文目录，
            # 这样 md 里的相对图片路径原样可用；不压缩、不分块。
            outdir = os.path.join(tmp, "out")
            srcdir, err = call_extract(rec["path"], outdir)
            if srcdir is None:
                return key, f"FAIL({err[:75]})", time.time() - t0
            names = os.listdir(srcdir)
            if os.path.exists(md_path):
                os.remove(md_path)
            for n in names:
                if n.endswith(".md"):
                    continue
                s, t = os.path.join(srcdir, n), os.path.join(d, n)
                shutil.rmtree(t, ignore_errors=True) if os.path.isdir(t) else None
                if os.path.isdir(s):
                    shutil.copytree(s, t, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, t)
            md_name = next(n for n in names if n.endswith(".md"))
            body = open(os.path.join(srcdir, md_name), encoding="utf-8").read()
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(header + f"mineru_chunks: 1\nengine: extract\n---\n\n" + body)
            extra = sum(len(fs) for _, _, fs in os.walk(d)) - 1
            return key, f"ok(1块,{extra}个附属文件)", time.time() - t0

        src = prepare(rec["path"], tmp)
        pages = rec["pages"] or 999
        ranges = ([None] if pages <= MAX_PAGES
                  else [f"{a}-{min(a+MAX_PAGES-1, pages)}"
                        for a in range(1, pages + 1, MAX_PAGES)])
        parts = []
        for i, rng in enumerate(ranges):
            text, err = call_flash(src, os.path.join(tmp, f"out{i:02d}"), rng)
            if text is None:
                return key, f"FAIL({err[:75]})", time.time() - t0
            parts.append(text)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(header + f"mineru_chunks: {len(parts)}\nengine: flash\n---\n\n"
                    + "\n\n<!-- chunk break -->\n\n".join(parts))
        return key, f"ok({len(parts)}块)", time.time() - t0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


KEY_RE = re.compile(r"^[0-9A-Za-z]{8}$")


def parse_args(argv):
    """显式解析位置参数 [key] [并发数]。

    旧写法先滤掉所有 `--` 开头的参数再取 args[0]，于是 `mineru_batch.py --flash 3`
    会把 3 当成 key，静默「待转换 0 篇」；key 打错同样只打印 0 篇。
    """
    pos = [a for a in argv if not a.startswith("-")]
    if len(pos) > 2:
        sys.exit("用法: mineru_batch.py [<8位Zotero key>|all] [并发数] "
                 "[--flash] [--force]")
    key = pos[0] if pos else None
    if key and key != "all" and not KEY_RE.match(key):
        sys.exit(f"key 格式不对（应为 8 位字母数字的 Zotero key 或 all）: {key!r}\n"
                 "若第一个位置参数想给并发数，请写成 `all <并发数>`")
    workers = 3
    if len(pos) == 2:
        try:
            workers = int(pos[1])
        except ValueError:
            sys.exit(f"并发数不是整数: {pos[1]!r}")
        if workers < 1:
            sys.exit(f"并发数必须 ≥1: {workers}")
    return key, workers


def main():
    only, workers = parse_args(sys.argv[1:])
    man = json.load(open(MANIFEST, encoding="utf-8"))
    todo = [r for r in man if r["status"] == "pdf"]
    if only and only != "all":
        todo = [r for r in todo if r["key"] == only]
    if only and only != "all" and not todo:
        hit = [r for r in man if r["key"] == only]
        why = (f"manifest 里有它，但状态是 {hit[0]['status']}（不是 pdf）"
               if hit else "manifest 里没有这个 key")
        log(f"✗ 指定了 key {only}，却没有可转换的记录：{why}")
        sys.exit(1)
    if ENGINE == "extract" and len(todo) > 24:
        workers = min(workers, 4)
    log(f"待转换 {len(todo)} 篇，并发 {workers}，引擎 {ENGINE}"
        f"{'，强制重转' if FORCE else ''}")
    os.makedirs(WORK, exist_ok=True)

    ok = fail = skip = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(convert, r): r for r in todo}
        for f in as_completed(futs):
            r = futs[f]
            try:
                key, status, dt = f.result()
            except Exception as e:
                key, status, dt = r["key"], f"EXC({e})", 0
            if status.startswith("ok"):
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
            log(f"  {key} {status} {dt:.0f}s | {r['title'][:42]}")
    log(f"完成：成功 {ok}，跳过 {skip}，失败 {fail}")


if __name__ == "__main__":
    main()
