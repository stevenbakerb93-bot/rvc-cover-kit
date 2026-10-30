#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
秒级重新混音：只改人声 / 伴奏音量，不重跑分离和换声

前提：cover.py 跑过一次并保留了分轨（现在默认就保留）。
单次耗时约 0.3~3 秒，取决于歌曲长度。

用法（在 RVC 根目录下执行）：

    # 改成「人声大一点、伴奏小一点」
    .venv\\Scripts\\python.exe mix.py "某首歌.mp3" --vocal-gain -1 --inst-gain -5

    # 直接指定分轨目录
    .venv\\Scripts\\python.exe mix.py "output\\_work_某首歌" --vocal-gain -3 --inst-gain 2

    # 不带参数 = 用上次的参数重混一份（等于复制一份，可用来对比）
    .venv\\Scripts\\python.exe mix.py "某首歌.mp3"

    # 拖成品进来也行，会自动反推原曲名
    .venv\\Scripts\\python.exe mix.py "output\\cover_某首歌_pitch+0_tr-2.mp3" --vocal-gain 0

参数说明：
    --vocal-gain  翻唱人声音量（dB），默认沿用上次的值，首次默认 -3
    --inst-gain   伴奏音量（dB），默认沿用上次的值，首次默认 0
    --out-dir     输出目录，默认 RVC根目录\\output

原理：两轨各自加 volume 后混合，末尾只做 0.95 峰值限制，不做响度归一化。
所以这两个数字调的是「人声与伴奏的相对比例」，不是整体音量。
想让整首歌更响，用播放器音量或后期做，不在这里调。
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

_CWD = Path.cwd()
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cover import ROOT as RVC_ROOT  # noqa: E402  （import 时会把 cwd 切到项目目录）
from cover import duration_of, log, mix  # noqa: E402

OUT_DEFAULT = RVC_ROOT / "output"
# 兼容旧版本把产物放在 RVC 上一级目录的情况
SEARCH_DIRS = list(dict.fromkeys([OUT_DEFAULT, RVC_ROOT.parent / "output"]))


def _name_candidates(stem):
    """从成品文件名反推原曲名。

    cover_Ref rain_pitch+0     -> Ref rain
    cover_Ref rain_pitch+0_tr-2 -> Ref rain
    cover_Ref rain_mix_v-3_i-6 -> Ref rain
    """
    tail = re.compile(r"(_mix_v[+-]?\d+(?:\.\d+)?_i[+-]?\d+(?:\.\d+)?|_tr[+-]\d+|_pitch[+-]\d+)$")
    names = [stem]
    s = stem[len("cover_"):] if stem.startswith("cover_") else stem
    for _ in range(3):
        m = tail.search(s)
        if not m:
            break
        s = s[:m.start()]
    if s and s != stem:
        names.append(s)
    return names


def resolve_work(target):
    """给歌曲路径或分轨目录，返回 (分轨目录, 歌名)"""
    p = Path(target).expanduser()
    if not p.is_absolute():
        p = (_CWD / p)
    if p.is_dir():
        name = p.name
        for pre in ("_work_", "_stem_"):
            if name.startswith(pre):
                name = name[len(pre):]
                break
        return p, name

    bases = SEARCH_DIRS
    for stem in _name_candidates(p.stem):
        for base in bases:
            if not base.is_dir():
                continue
            for pre in ("_work_", "_stem_"):
                d = base / (pre + stem)
                if d.is_dir():
                    return d, stem

    have = []
    for base in bases:
        if base.is_dir():
            have += sorted(d.name[len("_work_"):] for d in base.glob("_work_*"))
    listing = "\n".join("     " + h for h in have) if have else "     （一个都没有）"
    raise SystemExit(
        "找不到《%s》的分轨目录。\n"
        "mix.py 认的是「原曲」，不是成品；拖成品时我会自动剥掉 cover_ / _pitch+N / _tr+N / _mix_ 后缀。\n"
        "现在 %s 下已有分轨的歌：\n%s\n"
        "如果这首不在上面，说明还没跑过 cover.py（或跑时用了 --clean）。"
        % (p.stem, OUT_DEFAULT, listing))


def find_vocal(work):
    p = work / "vocal_converted.wav"
    if p.is_file():
        return p
    hits = sorted(work.glob("**/vocal_converted.wav"))
    if hits:
        return hits[0]
    raise SystemExit(
        "分轨目录里没有 vocal_converted.wav：\n  %s\n"
        "说明这一份还没换过声（或分轨不完整），请先跑 cover.py。" % work)


def find_inst(work):
    """优先用移调后的伴奏，保证与已换声的人声同调"""
    t = work / "instrumental_transposed.wav"
    if t.is_file():
        return t, True
    d = work / "instrumental"
    if d.is_dir():
        ws = sorted(d.glob("*.wav"))
        if ws:
            return ws[0], False
    raise SystemExit(
        "分轨目录里没有伴奏文件：\n  %s\n"
        "请先跑 cover.py（不要加 --clean）。" % work)


def read_meta(work):
    p = work / "mix_meta.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser(description="秒级重混：只调人声/伴奏音量")
    ap.add_argument("target", help="歌曲路径，或 _work_ 分轨目录")
    ap.add_argument("--vocal-gain", type=float, default=None, help="人声音量 dB")
    ap.add_argument("--inst-gain", type=float, default=None, help="伴奏音量 dB")
    ap.add_argument("--out-dir", default=str(OUT_DEFAULT), help="输出目录")
    ap.add_argument("--loudnorm", action="store_true",
                    help="额外做 -14 LUFS 响度归一化（会压缩你刚调的比例，一般别加）")
    args = ap.parse_args()
    sys.argv = [sys.argv[0]]

    work, stem = resolve_work(args.target)
    vocal = find_vocal(work)
    inst, transposed = find_inst(work)
    meta = read_meta(work)

    vg = args.vocal_gain if args.vocal_gain is not None else meta.get("vocal_gain", -3.0)
    ig = args.inst_gain if args.inst_gain is not None else meta.get("inst_gain", 0.0)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "%s_mix_v%+g_i%+g" % (stem, vg, ig)
    out_wav = out_dir / ("cover_%s.wav" % tag)
    out_mp3 = out_dir / ("cover_%s.mp3" % tag)

    log("=" * 60)
    log("歌名   : %s" % stem)
    log("分轨   : %s" % work)
    if meta:
        log("上次   : 人声 %+.1f dB / 伴奏 %+.1f dB / 模型 %s"
            % (meta.get("vocal_gain", 0), meta.get("inst_gain", 0), meta.get("model", "?")))
    log("本次   : 人声 %+.1f dB / 伴奏 %+.1f dB（相对比例）" % (vg, ig))
    log("伴奏源 : %s%s" % (inst.name, "（已移调）" if transposed else ""))
    log("=" * 60)

    t = time.time()
    mix(vocal, inst, out_wav, vg, out_mp3, ig, args.loudnorm)
    dur = duration_of(out_wav)
    log("完成：%.1f 秒音频，耗时 %.2f 秒" % (dur, time.time() - t))
    for p in (out_wav, out_mp3):
        log("   %s  (%.1f MB)" % (p, p.stat().st_size / 1048576))
    log("")
    log("再调一次只要换数字重跑，比如 --vocal-gain -1 --inst-gain -6")
    return 0


if __name__ == "__main__":
    sys.exit(main())
