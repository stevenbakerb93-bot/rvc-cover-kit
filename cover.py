#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键翻唱：人声分离 -> RVC 换声 -> 混音导出

用法（在 RVC 根目录下执行）：
    .venv\\Scripts\\python.exe cover.py "某首歌.mp3"
    .venv\\Scripts\\python.exe cover.py "某首歌.mp3" --transpose -2
    .venv\\Scripts\\python.exe cover.py "某首歌.mp3" --model 你的模型.pth --index-rate 0.5

常用参数：
    --model       音色模型文件名（assets\\weights 下的 .pth），不填取目录里第一个
    --transpose   整体移调半音数（推荐）：人声与伴奏一起移，不会脱调
    --pitch       只动人声的变调（高级用法，伴奏不动，会脱调）
    --index-rate  特征检索比例；有 .index 才有意义，没有就保持 0
    --protect     清辅音保护 0.2~0.5，越高气声/齿音越明显
    --vocal-gain  混音时人声音量 dB，默认 -3
    --inst-gain   混音时伴奏音量 dB，默认 0
    --reuse-stems 复用上次分轨，跳过分离（换模型/改音调时省 78 秒）
    --sep-model   分离模型，默认「去伴奏」
    --no-sep      跳过分离，直接对输入音频换声（输入已是干声时用）
    --out-dir     输出目录，默认 RVC根目录\\output
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

os.environ.setdefault("weight_root", str(ROOT / "assets" / "weights"))
os.environ.setdefault("index_root", str(ROOT / "logs"))
os.environ.setdefault("outside_index_root", str(ROOT / "assets" / "indices"))
os.environ.setdefault("rmvpe_root", str(ROOT / "assets" / "rmvpe"))
os.environ.setdefault("weight_pymss_root", str(ROOT / "assets" / "pymss_weights"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

WEIGHT_DIR = ROOT / "assets" / "weights"
INDEX_DIR = ROOT / "assets" / "indices"


def list_models():
    """assets/weights 下所有 .pth，按文件名排序"""
    if not WEIGHT_DIR.is_dir():
        return []
    return sorted(p.name for p in WEIGHT_DIR.glob("*.pth"))


def pick_default_model():
    """没指定 --model 时取第一个音色模型；一个都没有返回 None"""
    ms = list_models()
    return ms[0] if ms else None



def log(msg):
    print(msg, flush=True)


def run(cmd, what):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("%s 失败:\n%s\n%s" % (what, p.stdout[-2000:], p.stderr[-2000:]))
    return p


def to_wav(src, dst, sr=44100):
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-ar", str(sr), "-ac", "2",
         "-c:a", "pcm_s16le", str(dst)], "音频解码")
    return dst


def _atempo_chain(x):
    """atempo 单级只支持 0.5~2.0，超出范围时拆成多级串联"""
    parts = []
    while x > 2.0:
        parts.append(2.0)
        x /= 2.0
    while x < 0.5:
        parts.append(0.5)
        x /= 0.5
    parts.append(x)
    return ",".join("atempo=%.6f" % p for p in parts)


def shift_pitch(src, dst, semitones):
    """整轨移调但不改时长。优先 rubberband（音质好），失败回退 asetrate+atempo。"""
    if semitones == 0:
        return src
    r = 2.0 ** (semitones / 12.0)
    p = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(src),
         "-af", "rubberband=pitch=%.6f" % r,
         "-ar", "44100", "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode == 0 and Path(dst).is_file():
        return dst
    log("     rubberband 不可用，回退 asetrate（音质略降）")
    af = "asetrate=44100*%.6f,aresample=44100,%s" % (r, _atempo_chain(1.0 / r))
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-af", af,
         "-ar", "44100", "-c:a", "pcm_s16le", str(dst)], "伴奏移调")
    return dst


def duration_of(path):
    p = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)], "读取时长")
    return float(p.stdout.strip())


def separate_vocals(song_wav, work, sep_model):
    """返回 (vocals_path, instrumental_path)"""
    from tools.pymss_webui import MSSTBatchSeparator, resolve_model

    spec = resolve_model(sep_model)
    vdir = work / "vocals"
    idir = work / "instrumental"
    vdir.mkdir(parents=True, exist_ok=True)
    idir.mkdir(parents=True, exist_ok=True)

    with MSSTBatchSeparator(spec, "wav", str(vdir), str(idir)) as sep:
        info = sep.separate_file(str(song_wav))

    outs = [Path(p) for p in info["outputs"]]
    vocals = next((p for p in outs if spec.desired_suffix in p.stem), None)
    inst = next((p for p in outs if p != vocals), None)
    if vocals is None or inst is None:
        raise RuntimeError("分离输出异常: %s" % outs)
    return vocals, inst


def find_index(model_file, explicit=None):
    """按 精确同名 -> 前缀 -> 包含 的顺序找 index，避免多个模型互相串用"""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute():
            p = ROOT / p
        return str(p) if p.is_file() else ""
    stem = Path(model_file).stem.lower()
    pool = []
    for d in (ROOT / "assets" / "indices", ROOT / "logs"):
        if d.is_dir():
            pool.extend(d.rglob("*.index"))
    if not pool:
        return ""
    for test in (lambda c: c.stem.lower() == stem,
                 lambda c: c.stem.lower().startswith(stem),
                 lambda c: stem in c.stem.lower()):
        hit = [c for c in pool if test(c)]
        if hit:
            return str(hit[0])
    return ""


def convert_vocal(vocal_wav, out_path, model, pitch, speaker_id, index_rate, protect,
                  chunk_sec=30.0, overlap_sec=0.3, work=None, index_path=None,
                  f0_method="rmvpe", rms_mix=0.25, resample_sr=0):
    from configs.config import Config
    from infer.vc.modules import VC
    import numpy as np
    import soundfile as sf

    cfg = Config()
    vc = VC(cfg)
    ck = os.path.join(os.environ["weight_root"], model)
    if not os.path.isfile(ck):
        raise FileNotFoundError("模型不存在: %s" % ck)
    vc.get_vc(model)

    # 自动找同名 index
    idx = find_index(model, index_path)
    if not idx:
        index_rate = 0.0
        log("     未找到匹配的 .index，特征检索已关闭（index_rate=0）")
    else:
        log("     使用索引: %s（index_rate=%.2f）" % (Path(idx).name, index_rate))

    tmp_dir = Path(work) if work else Path(os.environ.get("TEMP", "."))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_seg = tmp_dir / "_seg.wav"

    def infer(seg):
        sf.write(str(tmp_seg), seg, sr, subtype="PCM_16")
        status, result = vc.vc_single(
            sid=speaker_id,
            input_audio_path=str(tmp_seg),
            f0_up_key=pitch,
            f0_method=f0_method,
            file_index=idx,
            index_rate=index_rate,
            resample_sr=resample_sr,
            rms_mix_rate=rms_mix,
            protect=protect,
        )
        if result is None or result[1] is None:
            msg = status.get("value", "") if isinstance(status, dict) else str(status)
            raise RuntimeError("换声失败:\n%s" % msg)
        osr, audio = result
        # vc_single 返回的是 int16（±32768），必须归一化到 [-1,1]。
        # 若直接 astype(float32) 不除 32768，写出的换声输入会被削成方波。
        a = np.asarray(audio)
        if a.dtype == np.int16:
            a = a.astype(np.float32) / 32768.0
        else:
            a = a.astype(np.float32)
        return a, int(osr)

    y, sr = sf.read(str(vocal_wav), dtype="float32")
    if y.ndim > 1:
        y = y.mean(1)

    # 必须先对齐到模型采样率：输出是 tgt_sr，如果拿 44.1k 的样本索引去写 40k 文件，
    # 成品时长会被拉长 44100/40000 = 10%（实测 181s 变 199.6s）
    tgt_sr = int(getattr(vc, "tgt_sr", 0) or 0)
    if tgt_sr and sr != tgt_sr:
        import librosa
        y = librosa.resample(y, orig_sr=sr, target_sr=tgt_sr).astype("float32")
        sr = tgt_sr
    n = len(y)

    # 短音频整段推理；长音频切片 + 交叉淡化，避免显存随时长增长而 OOM
    if n <= int((chunk_sec + 2) * sr):
        audio, osr = infer(y)
        sf.write(str(out_path), audio, osr, subtype="PCM_16")
        return out_path, osr

    chunk = int(chunk_sec * sr)
    ov = int(overlap_sec * sr)
    out = None
    wsum = None
    pos = 0
    k = 0
    total_seg = max(1, int(np.ceil(n / chunk)))
    while pos < n:
        s = max(0, pos - ov)
        e = min(n, pos + chunk + ov)
        audio, osr = infer(y[s:e])
        if out is None:
            out = np.zeros(n, dtype="float32")
            wsum = np.zeros(n, dtype="float32")
        seg_len = min(len(audio), n - s)
        w = np.ones(seg_len, dtype="float32")
        if s > 0 and seg_len > 1:
            L = min(ov, seg_len)
            w[:L] = np.linspace(0.0, 1.0, L, dtype="float32")
        if s + seg_len < n and seg_len > 1:
            L = min(ov, seg_len)
            w[-L:] = np.minimum(w[-L:], np.linspace(1.0, 0.0, L, dtype="float32"))
        out[s:s + seg_len] += audio[:seg_len] * w
        wsum[s:s + seg_len] += w
        pos += chunk
        k += 1
        log("      分段 %d/%d 完成" % (k, total_seg))

    out /= np.maximum(wsum, 1e-6)
    peak = float(np.abs(out).max())
    if peak > 0.98:
        out *= 0.98 / peak
    try:
        if tmp_seg.exists():
            tmp_seg.unlink()
    except Exception:
        pass
    sf.write(str(out_path), out, osr, subtype="PCM_16")
    return out_path, osr


def mix(vocal, inst, out_wav, vocal_gain_db, out_mp3=None, inst_gain_db=0.0,
        loudnorm=False):
    dur_v = duration_of(vocal)
    dur_i = duration_of(inst)
    # 以人声长度为准截断/补齐伴奏；apad 必须显式给 whole_dur，
    # 否则 duration=longest 会无限补静音（曾产出 7 小时的巨型文件）
    #
    # 默认只限幅、不做 loudnorm：loudnorm 是动态归一化，会按段落给不同增益，
    # 把人声/伴奏的比例一起压扁（实测设定差 9 dB 的两版量出来只差 2.7 dB）。
    # 需要流媒体标准响度时显式加 --loudnorm。
    tail = ("alimiter=limit=0.95,loudnorm=I=-14:TP=-1.5:LRA=11"
            if loudnorm else "alimiter=limit=0.95")
    filt = (
        "[0:a]volume=%.2fdB,aresample=44100[v];"
        "[1:a]volume=%.2fdB,aresample=44100,atrim=0:%.3f,apad=whole_dur=%.3f[i];"
        "[v][i]amix=inputs=2:duration=first:normalize=0,"
        "%s[a]"
    ) % (vocal_gain_db, inst_gain_db, dur_v, dur_v, tail)
    run([FFMPEG, "-y", "-v", "error", "-i", str(vocal), "-i", str(inst),
         "-filter_complex", filt, "-map", "[a]", "-ar", "44100", "-ac", "2",
         "-c:a", "pcm_s16le", str(out_wav)], "混音")
    got = duration_of(out_wav)
    if got > dur_v * 1.2 + 2:
        raise RuntimeError("混音输出时长异常: %.1fs（输入 %.1fs）" % (got, dur_v))
    result = [out_wav]
    if out_mp3:
        run([FFMPEG, "-y", "-v", "error", "-i", str(out_wav), "-c:a", "libmp3lame",
             "-b:a", "320k", str(out_mp3)], "导出 mp3")
        result.append(out_mp3)
    return result


def warn_webui():
    import socket
    s = socket.socket()
    s.settimeout(0.3)
    try:
        if s.connect_ex(("127.0.0.1", 7897)) == 0:
            log("提示: 检测到 WebUI 正在运行（7897 端口），会一起占用显存。")
            log("      8GB 卡同时跑容易触顶，建议先关掉 WebUI 再跑本脚本。")
            log("")
    except Exception:
        pass
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="一键翻唱：分离 + 换声 + 混音")
    ap.add_argument("input", help="输入歌曲路径 (mp3/wav/flac/m4a)")
    ap.add_argument("--model", default=None,
                    help="音色模型文件名，默认取 assets/weights 下第一个 .pth")
    ap.add_argument("--pitch", type=int, default=0, help="只动人声的变调半音数（高级用法）")
    ap.add_argument("--transpose", type=int, default=None,
                    help="整体移调半音数：人声与伴奏一起移，不会脱调（推荐用它）")
    ap.add_argument("--speaker-id", type=int, default=0, help="说话人 ID")
    ap.add_argument("--index-rate", type=float, default=0.75, help="特征检索比例")
    ap.add_argument("--index", default=None, help="显式指定 .index 路径（默认按模型名自动匹配）")
    ap.add_argument("--protect", type=float, default=0.33, help="清辅音保护 0.2~0.5")
    ap.add_argument("--f0-method", default="rmvpe",
                    choices=["rmvpe", "fcpe", "pm", "harvest", "crepe"],
                    help="音高提取算法：rmvpe 最准；fcpe 最快；pm 老式；harvest 适合低音/说话；crepe 最慢")
    ap.add_argument("--rms-mix", type=float, default=0.25,
                    help="音量包络混合 0~1：0=完全用模型音量，1=完全跟原唱音量")
    ap.add_argument("--resample", type=int, default=0,
                    help="输出重采样率，0=保持模型原生（40k/48k）")
    ap.add_argument("--chunk-sec", type=float, default=30.0, help="切片长度（秒），显存紧张就调小")
    ap.add_argument("--overlap-sec", type=float, default=0.3, help="切片交叉淡化长度（秒）")
    ap.add_argument("--vocal-gain", type=float, default=-3.0, help="人声音量 dB（相对混音基准）")
    ap.add_argument("--inst-gain", type=float, default=0.0, help="伴奏音量 dB（相对混音基准）")
    ap.add_argument("--loudnorm", action="store_true",
                    help="额外做 -14 LUFS 响度归一化。会让音量调整的比例被压缩，一般别加")
    ap.add_argument("--sep-model", default="去伴奏", help="分离模型名")
    ap.add_argument("--no-sep", action="store_true", help="跳过分离（输入已是干声）")
    ap.add_argument("--reuse-stems", action="store_true",
                    help="复用上次的分轨，跳过分离。换模型/改音调时能省 78 秒")
    ap.add_argument("--out-dir", default=str(ROOT / "output"), help="输出目录")
    ap.add_argument("--keep-stems", action="store_true",
                    help="保留分轨（现在默认就保留，此参数仅为兼容旧命令）")
    ap.add_argument("--clean", action="store_true",
                    help="跑完删掉分轨。想以后用 mix.py 秒级调音量就别加")
    args = ap.parse_args()
    # 项目内的 Config() 会在导入时解析 sys.argv，这里切断以免吞掉本脚本的参数
    sys.argv = [sys.argv[0]]

    if not args.model:
        args.model = pick_default_model()
        if not args.model:
            log("[错误] %s 下没有任何 .pth 音色模型。" % WEIGHT_DIR)
            log("       把音色模型放进去，或用 WebUI 的训练页训练一个。")
            return 2
        log("未指定 --model，自动使用目录里第一个：%s" % args.model)

    # --transpose: 人声 + 伴奏一起移调（安全，不会脱调）
    # --pitch   : 只动人声（高级用法，伴奏不动，容易脱调）
    if args.transpose is not None:
        if args.pitch != 0 and args.pitch != args.transpose:
            log("注意: 同时给了 --pitch %+d 与 --transpose %+d，人声按 %+d、伴奏按 %+d 处理"
                % (args.pitch, args.transpose, args.pitch, args.transpose))
            pitch = args.pitch
        else:
            pitch = args.transpose
    else:
        pitch = args.pitch

    src = Path(args.input).expanduser().resolve()
    if not src.is_file():
        log("找不到输入文件: %s" % src)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ("_work_" + src.stem)
    work.mkdir(parents=True, exist_ok=True)

    warn_webui()

    total_t0 = time.time()
    log("=" * 60)
    log("输入   : %s" % src.name)
    log("模型   : %s   pitch=%+d   speaker=%d" % (args.model, pitch, args.speaker_id))
    log("混音   : 人声 %+.1f dB   伴奏 %+.1f dB" % (args.vocal_gain, args.inst_gain))
    if args.transpose:
        log("整体移调: %+d 半音（人声与伴奏同步）" % args.transpose)
    log("输出   : %s" % out_dir)
    log("=" * 60)

    song_wav = to_wav(src, work / "source.wav")
    log("[1/3] 音频解码完成  %.2fs" % duration_of(song_wav))

    if args.no_sep:
        vocals = song_wav
        inst = None
        log("[2/3] 跳过分离")
    else:
        reused = False
        if args.reuse_stems:
            vdir = work / "vocals"
            idir = work / "instrumental"
            vs = sorted(vdir.glob("*.wav")) if vdir.is_dir() else []
            ins = sorted(idir.glob("*.wav")) if idir.is_dir() else []
            if vs and ins:
                vocals, base_inst = vs[0], ins[0]
                reused = True
                log("[2/3] 复用已有分轨（跳过分离）-> %s" % vocals.name)
                # 换成本次要求的移调；0 表示用原始伴奏
                inst = (shift_pitch(base_inst, work / "instrumental_transposed.wav",
                                    args.transpose) if args.transpose else base_inst)
            else:
                log("      没有可复用的分轨，改为重新分离")

        if not reused:
            log("[2/3] 人声分离中（模型：%s）..." % args.sep_model)
            t = time.time()
            vocals, inst = separate_vocals(song_wav, work, args.sep_model)
            log("      完成 %.1fs -> %s" % (time.time() - t, vocals.name))

            # 伴奏跟随整体移调（人声由 RVC 在推理时移，音质更好）
            if args.transpose:
                t = time.time()
                inst = shift_pitch(inst, work / "instrumental_transposed.wav", args.transpose)
                log("      伴奏移调 %+d 半音 完成 %.1fs" % (args.transpose, time.time() - t))

            # 释放分离模型显存，给换声腾空间
            try:
                import gc
                import torch
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    log("[3/3] 换声中 ...")
    t = time.time()
    conv = work / "vocal_converted.wav"
    conv, sr = convert_vocal(vocals, conv, args.model, pitch,
                             args.speaker_id, args.index_rate, args.protect,
                             work=work, index_path=args.index,
                             chunk_sec=args.chunk_sec, overlap_sec=args.overlap_sec,
                             f0_method=args.f0_method, rms_mix=args.rms_mix,
                             resample_sr=args.resample)
    log("      完成 %.1fs  %d Hz" % (time.time() - t, sr))

    tag = "%s_pitch%+d" % (src.stem, pitch)
    if args.transpose:
        tag += "_tr%+d" % args.transpose
    out_wav = out_dir / ("cover_%s.wav" % tag)
    out_mp3 = out_dir / ("cover_%s.mp3" % tag)

    # 记下本次参数，mix.py 重混时能显示基准值，避免"调了却不知道原来是多少"
    meta = {
        "source": str(src),
        "model": args.model,
        "pitch": pitch,
        "transpose": args.transpose,
        "vocal_gain": args.vocal_gain,
        "inst_gain": args.inst_gain,
        "index_rate": args.index_rate,
        "protect": args.protect,
        "f0_method": args.f0_method,
        "rms_mix": args.rms_mix,
    }
    try:
        (work / "mix_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    if inst is None:
        run([FFMPEG, "-y", "-v", "error", "-i", str(conv), "-ar", "44100", "-ac", "2",
             "-c:a", "pcm_s16le", str(out_wav)], "导出")
        run([FFMPEG, "-y", "-v", "error", "-i", str(out_wav), "-c:a", "libmp3lame",
             "-b:a", "320k", str(out_mp3)], "导出 mp3")
        outs = [out_wav, out_mp3]
    else:
        outs = mix(conv, inst, out_wav, args.vocal_gain, out_mp3, args.inst_gain,
                   args.loudnorm)

    dur = duration_of(out_wav)
    log("-" * 60)
    log("成品（%.1f 秒，总耗时 %.1f 秒）：" % (dur, time.time() - total_t0))
    for p in outs:
        log("   %s  (%.1f MB)" % (p, p.stat().st_size / 1048576))
    # 分轨默认保留：改音量/换模型重跑换声时能直接复用，省掉 78 秒分离
    if args.clean:
        shutil.rmtree(work, ignore_errors=True)
        log("   （分轨已按 --clean 删除）")
    else:
        log("   （分轨保留在 %s）" % work)
        log("    改音量不用重跑整首：python mix.py \"%s\" --vocal-gain -2 --inst-gain 3" % src.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
