#!/usr/bin/env python3
"""把库内文献离线编码成句子向量，供 daily_digest.py 的 S 项（库内语义相似度）用。

语料用现成数据：state/digest.json 每条含 key/title/concl/method，拼成
`title + " " + concl + " " + method` 直接编码，**不发任何网络请求**（模型权重首次
从 HuggingFace 下载除外）。库外候选由 daily_digest.py 用同一个模型在线编码。

产出：
  state/work/lib_emb.npz
    mat     float32 [n, d]，已 L2 归一化（余弦 = 点积）
    keys    与 mat 行一一对应的 Zotero key
    model   模型名。daily_digest.py 必须用它编码候选——两侧模型不同，余弦就没有意义
    built   构建时间
    n       条数

幂等：npz 已存在且模型名、条数都一致时跳过并提示可加 --force 重跑。条数一致不代表
内容一致（同一篇的 concl/method 可能更新过），所以 --force 永远保留。

依赖（本项目唯一需要它的脚本；装不装由你定，不装日报照常出，只是 S 项记 0）：
  pip install torch --index-url https://download.pytorch.org/whl/cpu
  pip install sentence-transformers
国内 HuggingFace 不可达，首次跑要设镜像后再编码：
  export HF_ENDPOINT=https://hf-mirror.com

用法：
  python3 scripts/build_embeddings.py                  # 默认 all-MiniLM-L6-v2
  python3 scripts/build_embeddings.py --force          # 忽略已有 npz 重跑
  python3 scripts/build_embeddings.py --dry-run        # 只报语料与已有 npz，不加载模型
  python3 scripts/build_embeddings.py --model BAAI/bge-small-zh-v1.5
"""
import argparse
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
CACHE = STATE / "work"
LOGS = ROOT / "logs"
DIGEST = STATE / "digest.json"
OUT = CACHE / "lib_emb.npz"
DEFAULT_MODEL = "all-MiniLM-L6-v2"

INSTALL_HINT = """\
缺依赖：本脚本需要 sentence-transformers 才能编码（numpy 已在用）。
CPU 版安装，约 200 MB，不需要 GPU / CUDA：
    pip install torch --index-url https://download.pytorch.org/whl/cpu
    pip install sentence-transformers
模型权重首次从 HuggingFace 下载，本机不可达，必须走镜像：
    export HF_ENDPOINT=https://hf-mirror.com
不装也不影响日报：daily_digest.py 的 S 项会记 0，并把它的权重回补给 R。"""


def load_corpus():
    """state/digest.json -> ([key], [文本], 跳过条数)。

    文本是 title + concl + method：concl 是一句话结论、method 是工艺/方法路线，
    两者合起来就是「这篇在做什么」的最短描述，比只编码标题分辨力高得多。
    """
    items = json.loads(DIGEST.read_text())
    keys, texts, skipped = [], [], 0
    for x in items:
        key = str(x.get("key") or "").strip()
        title = str(x.get("title") or "").strip()
        if not key or not title:
            skipped += 1
            continue
        # concl/method 有长有短、也可能整段缺失，缺的那个不占位（避免拼出多余空格）
        parts = [title] + [str(x.get(f) or "").strip() for f in ("concl", "method")]
        keys.append(key)
        texts.append(" ".join(p for p in parts if p))
    return keys, texts, skipped


def existing():
    """已存在的 npz 的 (模型名, 条数)；读不出返回 None。"""
    if not OUT.exists():
        return None
    try:
        import numpy as np
        with np.load(OUT) as z:
            return str(z["model"]), int(z["n"])
    except Exception as e:
        print(f"! 已有 {OUT.relative_to(ROOT)} 读不出来（{type(e).__name__} {e}），按需重建")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"sentence-transformers 模型名（默认 {DEFAULT_MODEL}）")
    ap.add_argument("--force", action="store_true", help="忽略已有 npz，强制重建")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--dry-run", action="store_true", help="只报语料与已有 npz，不加载模型")
    a = ap.parse_args()

    if not DIGEST.exists():
        print(f"! 缺 {DIGEST.relative_to(ROOT)}（跑 scripts/digest.py json 生成），无从取语料")
        raise SystemExit(2)
    keys, texts, skipped = load_corpus()
    print(f"语料 {len(keys)} 条（源 {DIGEST.relative_to(ROOT)}，跳过无 key/标题 {skipped} 条），"
          f"模型 {a.model}")
    prev = existing()
    if prev:
        print(f"已有 {OUT.relative_to(ROOT)}：模型 {prev[0]}、{prev[1]} 条")
    if a.dry_run:
        print("--dry-run：不加载模型、不写文件")
        return
    if prev and prev == (a.model, len(keys)) and not a.force:
        print("模型名与条数都没变，跳过；语料内容可能已更新，要重建加 --force")
        return

    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(f"! 依赖缺失：{type(e).__name__} {e}")
        print(INSTALL_HINT)
        raise SystemExit(2)

    t0 = time.time()
    try:
        model = SentenceTransformer(a.model)
        mat = model.encode(texts, batch_size=a.batch_size,
                           normalize_embeddings=True, convert_to_numpy=True,
                           show_progress_bar=False)
    except Exception as e:
        # 加载失败（下载不到权重、torch 坏了）与编码失败都从这里出去：不吞异常，
        # 否则会写出一个维度不明或半截的 npz，日报那边只能记 S=0 还查不出原因
        print(f"! 编码失败：{type(e).__name__} {e}")
        print(INSTALL_HINT)
        raise SystemExit(3)
    mat = np.asarray(mat, dtype="float32")
    if len(mat) != len(keys):
        print(f"! 编码条数对不上：{len(mat)} vs 语料 {len(keys)}")
        raise SystemExit(3)

    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.name + ".tmp")
    with tmp.open("wb") as f:
        # keys 存成 unicode 数组而不是 object（object 数组要 allow_pickle 才能读出来，
        # 读方一旦忘了这个参数就报错）；daily_digest.py 目前不用它，留作对账
        np.savez(f, mat=mat, keys=np.array(keys),
                 model=a.model, built=time.strftime("%F %T"), n=len(keys))
    os.replace(tmp, OUT)
    msg = (f"已写 {OUT.relative_to(ROOT)}：{mat.shape[0]} 条 × {mat.shape[1]} 维，"
           f"模型 {a.model}，{time.time() - t0:.1f}s")
    print(msg)
    print(f"向量 L2 范数 min/max：{np.linalg.norm(mat, axis=1).min():.4f}"
          f" / {np.linalg.norm(mat, axis=1).max():.4f}（归一化后应均为 1.0）")

    LOGS.mkdir(exist_ok=True)
    with (LOGS / "build_embeddings.log").open("a") as f:
        f.write(f"=== {time.strftime('%F %T')} ===\n{msg}\n")
    print("下次跑 daily_digest.py 就会用上 S 项（日志里会打印「S 项：可用」）")


if __name__ == "__main__":
    main()
