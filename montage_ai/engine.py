"""Core editing engine built on ffmpeg: probe, scene detection, color, render."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

RESOLUTIONS = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
    "8k": (7680, 4320),
    "vertical": (1080, 1920),
    "vertical4k": (2160, 3840),
    "square": (1080, 1080),
    "portrait45": (1080, 1350),
}

# Export presets. "pix_fmt" is the final pixel format of the graph.
PRESETS = {
    "h264_high": {
        "ext": ".mp4", "pix_fmt": "yuv420p",
        "video": ["-c:v", "libx264", "-preset", "medium", "-crf", "14", "-profile:v", "high",
                  "-movflags", "+faststart"],
        "audio": ["-c:a", "aac", "-b:a", "320k"],
    },
    "h265_10bit": {
        "ext": ".mp4", "pix_fmt": "yuv420p10le",
        "video": ["-c:v", "libx265", "-preset", "slow", "-crf", "16", "-tag:v", "hvc1",
                  "-movflags", "+faststart"],
        "audio": ["-c:a", "aac", "-b:a", "320k"],
    },
    "prores_hq": {
        "ext": ".mov", "pix_fmt": "yuv422p10le",
        "video": ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0"],
        "audio": ["-c:a", "pcm_s24le"],
    },
    "gpu": {  # filled in at runtime by gpu_preset(): NVIDIA, Intel Quick Sync or AMD
        "ext": ".mp4", "pix_fmt": "nv12", "video": [], "audio": ["-c:a", "aac", "-b:a", "320k"],
    },
    "draft": {
        "ext": ".mp4", "pix_fmt": "yuv420p",
        "video": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-movflags", "+faststart"],
        "audio": ["-c:a", "aac", "-b:a", "160k"],
    },
}

XFADE_TRANSITIONS = [
    "fade", "fadeblack", "fadewhite", "dissolve", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "circleopen",
    "circleclose", "radial", "zoomin", "pixelize", "hblur", "distance", "squeezeh", "squeezev",
    "diagtl", "diagbr", "hlslice", "hrslice",
]

CURVES_PRESETS = [
    "color_negative", "cross_process", "darker", "increase_contrast", "lighter",
    "linear_contrast", "medium_contrast", "negative", "strong_contrast", "vintage",
]

AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


GPU_ENCODERS = [
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p6", "-rc", "vbr", "-cq", "18", "-b:v", "0"]),
    ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "slower", "-global_quality", "18"]),
    ("h264_amf", ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp",
                  "-qp_i", "18", "-qp_p", "20", "-qp_b", "22"]),
]


class RenderError(RuntimeError):
    pass


_GPU = None


def gpu_preset() -> dict:
    """Pick the first hardware encoder that actually works on this machine (tested once)."""
    global _GPU
    if _GPU is None:
        _GPU = {}
        for name, args in GPU_ENCODERS:
            r = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                                "color=black:s=1280x720:d=0.2", "-vf", "format=nv12", *args,
                                "-f", "null", "-"], capture_output=True)
            if r.returncode == 0:
                _GPU = {**PRESETS["gpu"], "video": args + ["-movflags", "+faststart"],
                        "encoder": name}
                break
    if not _GPU:
        raise RenderError("No working GPU encoder (NVIDIA/Intel/AMD) found. Use h264_high.")
    return _GPU


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- probe

def probe(path) -> dict:
    r = _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
              "-show_streams", str(path)])
    if r.returncode:
        raise RenderError(f"ffprobe failed for {path}: {r.stderr.strip()}")
    d = json.loads(r.stdout)
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = _num(d.get("format", {}).get("duration"), 0.0)
    info = {"path": str(Path(path).resolve()), "duration": round(dur, 3),
            "has_video": v is not None, "has_audio": a is not None}
    if v:
        num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").partition("/")
        fps = _num(num, 0) / _num(den, 1) if _num(den, 1) else 0
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(_num(sd["rotation"], 0))
        if abs(rot) in (90, 270):
            w, h = h, w
        info.update(width=w, height=h, fps=round(fps, 3), codec=v.get("codec_name"),
                    pix_fmt=v.get("pix_fmt"))
    return info


_PROBE_CACHE: dict = {}


def probe_cached(path) -> dict:
    p = str(Path(str(path).strip().strip('"')).resolve())
    if not os.path.isfile(p):
        raise RenderError(f"File not found / الملف غير موجود: {p}")
    key = (p, os.path.getmtime(p))
    if key not in _PROBE_CACHE:
        _PROBE_CACHE[key] = probe(p)
    return _PROBE_CACHE[key]


def detect_scenes(path, threshold: float = 0.3) -> list[float]:
    """Return timestamps (seconds) where the picture changes sharply."""
    r = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-an", "-sn",
              "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    times = {round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)}
    return sorted(times)


# --------------------------------------------------------------------------- helpers

class Workdir:
    """Temp folder ffmpeg runs in, so filter args only use plain file names (no escaping)."""

    def __init__(self, path):
        self.path = Path(path)
        self.n = 0
        self._copied: dict = {}

    def add_file(self, src, prefix: str) -> str:
        src = str(Path(src).resolve())
        if src in self._copied:
            return self._copied[src]
        if not os.path.isfile(src):
            raise RenderError(f"File not found: {src}")
        self.n += 1
        name = f"{prefix}{self.n}{Path(src).suffix.lower() or '.bin'}"
        shutil.copyfile(src, self.path / name)
        self._copied[src] = name
        return name

    def add_text(self, text: str, prefix: str = "text") -> str:
        self.n += 1
        name = f"{prefix}{self.n}.txt"
        (self.path / name).write_text(text, encoding="utf-8")
        return name


FONT_CANDIDATES = {
    True: ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
           "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    False: ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
}


def find_font(bold: bool = True):
    for f in FONT_CANDIDATES[bool(bold)]:
        if os.path.isfile(f):
            return f
    return None


def color_filters(c: dict | None, work: Workdir | None = None, spatial: bool = True) -> list[str]:
    """Turn a color-grade dict into ffmpeg filters."""
    if not c:
        return []
    f = []
    b = _num(c.get("brightness"), 0)
    ct = _num(c.get("contrast"), 1)
    s = _num(c.get("saturation"), 1)
    g = _num(c.get("gamma"), 1)
    if (b, ct, s, g) != (0, 1, 1, 1):
        f.append(f"eq=brightness={b:.4f}:contrast={ct:.4f}:saturation={s:.4f}:gamma={g:.4f}")
    hue = _num(c.get("hue"), 0)
    if hue:
        f.append(f"hue=h={hue:.2f}")
    vib = _num(c.get("vibrance"), 0)
    if vib:
        f.append(f"vibrance=intensity={_clamp(vib, -2, 2):.3f}")
    t = _num(c.get("temperature"), 6500)
    if abs(t - 6500) >= 50:
        f.append(f"colortemperature=temperature={int(_clamp(t, 1000, 40000))}")
    bal = c.get("balance") or {}
    keys = []
    for zone, ch in (("shadows", "s"), ("midtones", "m"), ("highlights", "h")):
        v = bal.get(zone)
        if isinstance(v, (list, tuple)) and len(v) == 3:
            for comp, val in zip("rgb", v):
                val = _clamp(_num(val, 0), -1, 1)
                if val:
                    keys.append(f"{comp}{ch}={val:.3f}")
    if keys:
        f.append("colorbalance=" + ":".join(keys))
    cv = c.get("curves")
    if cv in CURVES_PRESETS:
        f.append(f"curves=preset={cv}")
    if c.get("lut"):
        if work is None:
            raise RenderError("A LUT needs a work folder")
        f.append(f"lut3d=file={work.add_file(c['lut'], 'lut')}")
    if spatial:
        sh = _num(c.get("sharpen"), 0)
        if sh > 0:
            f.append(f"unsharp=5:5:{min(sh, 3):.2f}")
        if c.get("vignette"):
            f.append("vignette=angle=PI/5")
        gr = _num(c.get("grain"), 0)
        if gr > 0:
            f.append(f"noise=alls={int(min(gr, 60))}:allf=t+u")
    return f


_SLOW_KEYS = ("balance", "temperature", "curves", "lut", "hue", "vibrance")


def grade_filters(c: dict | None, work: Workdir) -> list[str]:
    """Like color_filters, but bakes the per-pixel part into a single LUT when that is faster."""
    if not c:
        return []
    pixel = color_filters(c, work, spatial=False)
    slow = [k for k in _SLOW_KEYS if c.get(k) not in (None, "", 0, 6500, {}, [])]
    if len(pixel) < 2 or not slow:
        return color_filters(c, work)
    from .export import make_cube_lut
    name = f"grade{work.n + 1}.cube"
    work.n += 1
    make_cube_lut(c, str(work.path / name))
    spatial = color_filters({k: c[k] for k in ("sharpen", "vignette", "grain") if k in c}, work)
    return [f"lut3d=file={name}"] + spatial


def _atempo(speed: float) -> list[str]:
    out = []
    while speed > 2.0:
        out.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        out.append("atempo=0.5")
        speed /= 0.5
    if abs(speed - 1.0) > 1e-3:
        out.append(f"atempo={speed:.4f}")
    return out


def _fit(i, inl, outl, W, H, fit, pre, post) -> list[str]:
    sc = "flags=lanczos"
    cover = [f"scale={W}:{H}:force_original_aspect_ratio=increase:{sc}", f"crop={W}:{H}"]
    contain = f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2:{sc}"
    if fit == "contain":
        chain = pre + [contain, f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"] + post
        return [f"[{inl}]{','.join(chain)}[{outl}]"]
    if fit == "blur":
        return [
            f"[{inl}]{','.join(pre + ['split=2'])}[bg{i}][fg{i}]",
            f"[bg{i}]{','.join(cover)},boxblur=luma_radius=30:luma_power=2[bgb{i}]",
            f"[fg{i}]{contain}[fgs{i}]",
            f"[bgb{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2,{','.join(post)}[{outl}]",
        ]
    return [f"[{inl}]{','.join(pre + cover + post)}[{outl}]"]


def _color_hex(c: str) -> str:
    c = str(c or "white").strip()
    return "0x" + c[1:] if c.startswith("#") else c


def _drawtext(t: dict, work: Workdir, W: int, H: int, total: float, cache: dict) -> str:
    name = work.add_text(str(t.get("text", "")))
    font = t.get("font") or find_font(bool(t.get("bold", True)))
    size = int(_num(t.get("size"), 0) or round(H * _num(t.get("size_ratio"), 0.06)))
    st = _clamp(_num(t.get("start"), 0), 0, total)
    en = _clamp(_num(t.get("end"), total), st, total)
    pos = t.get("position", "center")
    y = {"top": "h*0.08", "bottom": "h-text_h-h*0.10", "lower_third": "h-text_h-h*0.20"}.get(
        pos, "(h-text_h)/2")
    fd = min(0.3, (en - st) / 3)
    parts = [f"textfile={name}"]
    if font:
        parts.append(f"fontfile={cache.setdefault(font, work.add_file(font, 'font'))}")
    parts += [
        f"fontsize={size}", f"fontcolor={_color_hex(t.get('color'))}",
        "x=(w-text_w)/2", f"y={y}",
        f"borderw={max(1, size // 30)}", "bordercolor=black@0.55",
        f"enable='between(t,{st:.3f},{en:.3f})'",
    ]
    if fd > 0.01:
        parts.append(f"alpha='if(lt(t,{st + fd:.3f}),(t-{st:.3f})/{fd:.3f},"
                     f"if(gt(t,{en - fd:.3f}),({en:.3f}-t)/{fd:.3f},1))'")
    if t.get("box"):
        parts += ["box=1", f"boxcolor={_color_hex(t.get('box_color', 'black@0.5'))}",
                  f"boxborderw={max(8, size // 3)}"]
    return "drawtext=" + ":".join(parts)


# --------------------------------------------------------------------------- render

def clip_duration(c: dict) -> float:
    return (c["end"] - c["start"]) / c.get("speed", 1.0)


def build_command(plan: dict, output: str, preset: str, work: Workdir):
    if preset not in PRESETS:
        raise RenderError(f"Unknown preset '{preset}'. Choose: {', '.join(PRESETS)}")
    P = gpu_preset() if preset == "gpu" else PRESETS[preset]
    s = plan["settings"]
    W, H, FPS = int(s["width"]), int(s["height"]), s["fps"]
    fit = s.get("fit", "cover")
    clips = plan["clips"]

    # Per-pixel grading is applied to each clip *before* scaling (far fewer pixels when
    # upscaling to 4K/8K). Only spatial effects run on the final frame.
    gcolor = plan.get("color") or {}
    spatial_keys = ("sharpen", "vignette", "grain")
    g_pixel = grade_filters({k: v for k, v in gcolor.items() if k not in spatial_keys}, work)
    g_spatial = color_filters({k: gcolor[k] for k in spatial_keys if k in gcolor}, work)

    args = [FFMPEG, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1"]
    graph: list[str] = []
    durs = []
    for i, c in enumerate(clips):
        st, en, sp = c["start"], c["end"], c.get("speed", 1.0)
        args += ["-ss", f"{st:.3f}", "-t", f"{en - st:.3f}", "-i", c["source"]]
        d = clip_duration(c)
        durs.append(d)
        pre = [f"setpts=(PTS-STARTPTS)/{sp:.4f}"] + g_pixel + color_filters(c.get("color"), work)
        post = [f"fps={FPS}", f"trim=duration={d:.3f}", "setpts=PTS-STARTPTS", "setsar=1",
                "format=yuv420p"]
        graph += _fit(i, f"{i}:v", f"v{i}", W, H, fit, pre, post)
        if probe_cached(c["source"])["has_audio"] and not c.get("mute"):
            af = ["asetpts=PTS-STARTPTS"] + _atempo(sp) + \
                [f"volume={c.get('volume', 1.0):.3f}", AFMT, "apad", f"atrim=duration={d:.3f}"]
            graph.append(f"[{i}:a]{','.join(af)}[a{i}]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo,{AFMT},atrim=duration={d:.3f}[a{i}]")

    # Join clips: xfade/acrossfade for transitions, concat for hard cuts.
    cur_v, cur_a, cur_d = "v0", "a0", durs[0]
    for i in range(1, len(clips)):
        tr = clips[i].get("transition") or {}
        ttype = tr.get("type")
        td = min(_num(tr.get("duration"), 0.5), cur_d - 0.05, durs[i] - 0.05)
        nv, na = f"jv{i}", f"ja{i}"
        if ttype in XFADE_TRANSITIONS and td > 0.04:
            graph.append(f"[{cur_v}][v{i}]xfade=transition={ttype}:duration={td:.3f}:"
                         f"offset={cur_d - td:.3f}[{nv}]")
            graph.append(f"[{cur_a}][a{i}]acrossfade=d={td:.3f}[{na}]")
            cur_d += durs[i] - td
        else:
            graph.append(f"[{cur_v}][v{i}]concat=n=2:v=1:a=0[{nv}]")
            graph.append(f"[{cur_a}][a{i}]concat=n=2:v=0:a=1[{na}]")
            cur_d += durs[i]
        cur_v, cur_a = nv, na
    total = cur_d

    # Titles, spatial look (sharpen, vignette, grain) and fades on the final frame.
    vg = list(g_spatial)
    font_cache: dict = {}
    for t in plan.get("texts", []):
        vg.append(_drawtext(t, work, W, H, total, font_cache))
    fi = _clamp(_num(plan.get("fade_in"), 0), 0, total / 2)
    fo = _clamp(_num(plan.get("fade_out"), 0), 0, total / 2)
    if fi:
        vg.append(f"fade=t=in:st=0:d={fi:.3f}")
    if fo:
        vg.append(f"fade=t=out:st={total - fo:.3f}:d={fo:.3f}")
    vg.append(f"format={P['pix_fmt']}")
    graph.append(f"[{cur_v}]{','.join(vg)}[vout]")

    a_label = cur_a
    music = plan.get("music") or {}
    if music.get("path"):
        midx = len(clips)
        args += ["-stream_loop", "-1", "-i", music["path"]]
        mfo = _clamp(_num(music.get("fade_out"), 2), 0, total)
        mf = [f"atrim=duration={total:.3f}", "asetpts=PTS-STARTPTS",
              f"volume={_num(music.get('volume'), 0.3):.3f}", AFMT]
        if mfo:
            mf.append(f"afade=t=out:st={total - mfo:.3f}:d={mfo:.3f}")
        graph.append(f"[{midx}:a]{','.join(mf)}[mus]")
        graph.append(f"[{a_label}][mus]amix=inputs=2:duration=first:normalize=0[amx]")
        a_label = "amx"
    ag = []
    if fi:
        ag.append(f"afade=t=in:st=0:d={fi:.3f}")
    if fo:
        ag.append(f"afade=t=out:st={total - fo:.3f}:d={fo:.3f}")
    if (plan.get("audio") or {}).get("normalize", True):
        ag += ["loudnorm=I=-14:TP=-1.5:LRA=11", "aresample=48000"]
    graph.append(f"[{a_label}]{','.join(ag or ['anull'])}[aout]")

    (work.path / "graph.txt").write_text(";\n".join(graph), encoding="utf-8")
    args += ["-/filter_complex", "graph.txt", "-map", "[vout]", "-map", "[aout]"]
    args += P["video"] + P["audio"] + ["-t", f"{total:.3f}", str(output)]
    return args, total


def render(plan: dict, output: str, preset: str = "h264_high", progress=None,
           keep_workdir: bool = False) -> str:
    """Render a normalized plan. progress(fraction) is called while encoding."""
    from .plan import normalize_plan
    plan = normalize_plan(plan)
    output = str(Path(output).resolve())
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    wd = tempfile.mkdtemp(prefix="montage_")
    try:
        cmd, total = build_command(plan, output, preset, Workdir(wd))
        log_path = os.path.join(wd, "ffmpeg.log")
        with open(log_path, "w", encoding="utf-8") as log:
            p = subprocess.Popen(cmd, cwd=wd, stdout=subprocess.PIPE, stderr=log, text=True,
                                 encoding="utf-8", errors="replace")
            # Count encoded video frames: out_time follows the slowest stream, and
            # loudnorm holds audio back, so it stalls long before the video is done.
            frames_total = max(1.0, total * plan["settings"]["fps"])
            for line in p.stdout:
                if line.startswith("frame=") and progress:
                    progress(_clamp(_num(line.split("=", 1)[1], 0) / frames_total, 0, 0.99))
            rc = p.wait()
        if rc:
            tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-3000:]
            raise RenderError(f"ffmpeg failed (code {rc}):\n{tail}")
        if progress:
            progress(1.0)
        return output
    finally:
        if not keep_workdir:
            shutil.rmtree(wd, ignore_errors=True)
