#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
调参试听：只分离一次，然后对同一段 30 秒片段批量跑多组参数，快速 A/B。

用法（在 RVC 根目录下执行）：
    # 试 index_rate 三档
    .venv\\Scripts\\python.exe tune.py "歌.mp3" --vary index_rate=0.3,0.5,0.7

    # 同时试两个维度（笛卡尔积，最多 12 组）
    .venv\\Scripts\\python.exe tune.py "歌.mp3" --vary index_rate=0.3,0.5 --vary protect=0.25,0.4

    # 指定试听位置（默认从 60 秒处取 30 秒）
    .venv\\Scripts\\python.exe tune.py "歌.mp3" --start 95 --dur 25

可变的键：index_rate / protect / rms_mix / transpose / pitch / f0_method
输出：RVC根目录\\tune\\tune_01_xxx.mp3 …，文件名里带该组的参数

第一次会分离分轨（约 1 分半），同一首歌之后复用缓存，每组约 10 秒。
"""

import argparse
import itertools
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cover  # noqa: E402  复用 cover.py 的解码/分离/换声/混音

MAX_COMBOS = 12


def cut(src, dst, start, dur):
    cover.run([cover.FFMPEG, "-y", "-v", "error", "-ss", "%.3f" % start,
               "-t", "%.3f" % dur, "-i", str(src), "-ar", "44100", "-ac", "1",
               "-c:a", "pcm_s16le", str(dst)], "截取片段")
    return dst


def coerce(tpl, text):
    text = text.strip()
    if isinstance(tpl, bool):
        return text.lower() in ("1", "true", "yes")
    if isinstance(tpl, int):
        return int(float(text))
    if isinstance(tpl, float):
        return float(text)
    return text


def main():
    ap = argparse.ArgumentParser(description="批量试参数，输出短片段 A/B")
    ap.add_argument("input", help="歌曲路径")
    ap.add_argument("--model", default=None, help="音色模型文件名，默认取 assets/weights 下第一个")
    ap.add_argument("--start", type=float, default=60.0, help="试听起点（秒）")
    ap.add_argument("--dur", type=float, default=30.0, help="试听长度（秒）")
    ap.add_argument("--vary", action="append", default=[], metavar="KEY=v1,v2",
                    help="要扫的参数轴，可重复")
    ap.add_argument("--index", default=None, help="显式指定 .index")
    ap.add_argument("--index-rate", type=float, default=0.5)
    ap.add_argument("--protect", type=float, default=0.33)
    ap.add_argument("--rms-mix", type=float, default=0.25)
    ap.add_argument("--f0-method", default="rmvpe")
    ap.add_argument("--transpose", type=int, default=0)
    ap.add_argument("--pitch", type=int, default=None, help="只动人声（默认跟 transpose）")
    ap.add_argument("--vocal-gain", type=float, default=-3.0)
    ap.add_argument("--sep-model", default="去伴奏")
    ap.add_argument("--out-dir", default=str(HERE / "tune"))
    args = ap.parse_args()
    sys.argv = [sys.argv[0]]  # 切断 Config() 的 argparse

    src = Path(args.input).expanduser().resolve()
    if not src.is_file():
        print("找不到输入文件: %s" % src)
        return 2

    if not args.model:
        args.model = cover.pick_default_model()
        if not args.model:
            print("assets\\weights 下没有 .pth 音色模型，请先放入模型文件。")
            return 2
        print("未指定 --model，自动使用目录里第一个：%s" % args.model)

    base = {
        "index_rate": args.index_rate,
        "protect": args.protect,
        "rms_mix": args.rms_mix,
        "transpose": args.transpose,
        "pitch": args.transpose if args.pitch is None else args.pitch,
        "f0_method": args.f0_method,
    }

    axes = {}
    for spec in args.vary:
        k, _, v = spec.partition("=")
        k = k.strip()
        if k not in base:
            print("未知参数: %s（可用: %s）" % (k, ", ".join(base)))
            return 2
        axes[k] = [coerce(base[k], x) for x in v.split(",") if x.strip()]

    if axes:
        combos = []
        for prod in itertools.product(*axes.values()):
            d = dict(base)
            d.update(dict(zip(axes.keys(), prod)))
            # --pitch 未显式指定时，人声变调必须跟随本组 transpose：
            # 否则扫 transpose 轴只有伴奏被移调、人声停在原调，整首跑调。
            # tune.bat 给 transpose 轴的默认档（-2,-1,0）会直接踩中这个问题。
            # 显式传了 --pitch 说明用户有意让人声固定（伴奏单独移调），尊重之。
            if args.pitch is None and "transpose" in axes and "pitch" not in axes:
                d["pitch"] = d["transpose"]
            combos.append(d)
    else:
        combos = [dict(base)]

    if len(combos) > MAX_COMBOS:
        print("组合数 %d 超过上限 %d，请减少 --vary 的取值" % (len(combos), MAX_COMBOS))
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ("_stem_" + src.stem)
    work.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print("输入   : %s（%.1fs 处取 %.0fs）" % (src.name, args.start, args.dur))
    print("模型   : %s   组合数 %d" % (args.model, len(combos)))
    print("=" * 64)

    # 1. 解码 + 分离（只做一次，复用缓存）
    vocals = work / "vocals" / "source_vocals.wav"
    inst = work / "instrumental" / "source_instrumental.wav"
    if vocals.is_file() and inst.is_file():
        print("[1/2] 复用已缓存的分轨")
    else:
        song_wav = cover.to_wav(src, work / "source.wav")
        print("[1/2] 分离中 ...")
        vocals, inst = cover.separate_vocals(song_wav, work, args.sep_model)
        try:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        print("      完成")

    clip_v = cut(vocals, work / "clip_vocal.wav", args.start, args.dur)
    clip_i = cut(inst, work / "clip_inst.wav", args.start, args.dur)

    # 2. 逐组换声 + 混音
    made = []
    for i, c in enumerate(combos, 1):
        desc = "_".join("%s%s" % (k[:4], c[k]) for k in axes) if axes else "base"
        desc = desc.replace(".", "")
        print("[2/2] (%d/%d) %s" % (i, len(combos), desc))
        conv = work / ("_conv_%02d.wav" % i)
        cover.convert_vocal(
            clip_v, conv, args.model, c["pitch"], 0, c["index_rate"], c["protect"],
            work=work, index_path=args.index,
            f0_method=c["f0_method"], rms_mix=c["rms_mix"], resample_sr=0)
        use_inst = clip_i
        if c["transpose"]:
            use_inst = cover.shift_pitch(clip_i, work / ("_inst_%02d.wav" % i), c["transpose"])
        o_wav = out_dir / ("tune_%02d_%s.wav" % (i, desc))
        o_mp3 = out_dir / ("tune_%02d_%s.mp3" % (i, desc))
        cover.mix(conv, use_inst, o_wav, args.vocal_gain, o_mp3)
        made.append((i, desc, c, o_mp3))

    print("\n" + "=" * 64)
    print("%-4s %-34s %s" % ("#", "参数", "文件"))
    for i, desc, c, p in made:
        detail = " ".join("%s=%s" % (k, c[k]) for k in axes) if axes else "默认参数"
        print("%-4d %-34s %s" % (i, detail, p.name))
    print("=" * 64)
    print("试听挑一组，再用 cover.py 跑整首，例如：")
    if made:
        c = made[0][2]
        print('  .venv\\Scripts\\python.exe cover.py "%s" --model %s --transpose %d '
              '--index-rate %.2f --protect %.2f --rms-mix %.2f --f0-method %s'
              % (src.name, args.model, c["transpose"], c["index_rate"],
                 c["protect"], c["rms_mix"], c["f0_method"]))
    print("（中间分轨缓存在 %s，下次换参数不用重新分离）" % work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
