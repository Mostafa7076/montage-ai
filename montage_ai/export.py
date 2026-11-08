"""Hand the edit to other editors: .cube LUT, Premiere/DaVinci XML, CapCut project pack."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from array import array
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from .engine import FFMPEG, RenderError, Workdir, clip_duration, color_filters, probe_cached
from .plan import normalize_plan


# --------------------------------------------------------------------------- LUT

def make_cube_lut(color: dict, path: str, level: int = 6) -> str:
    """Bake the plan's color grade into a .cube 3D LUT (exact: rendered through ffmpeg).

    Spatial effects (vignette, grain, sharpen) cannot live in a LUT and are left out.
    """
    n = level * level
    with tempfile.TemporaryDirectory() as wd:
        filters = color_filters(color, Workdir(wd), spatial=False)
        chain = ",".join(["format=rgb24"] + filters + ["format=rgb48le"])
        r = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                            f"haldclutsrc=level={level}", "-frames:v", "1", "-vf", chain,
                            "-f", "rawvideo", "-"], capture_output=True, cwd=wd)
    if r.returncode or len(r.stdout) != n ** 3 * 6:
        raise RenderError("LUT bake failed: " + r.stderr.decode("utf-8", "replace")[:400])
    px = array("H")
    px.frombytes(r.stdout)
    lines = ["TITLE \"Montage AI look\"", f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0",
             "DOMAIN_MAX 1.0 1.0 1.0"]
    # Hald order and .cube order are both red-fastest, then green, then blue.
    for i in range(0, len(px), 3):
        lines.append(f"{px[i] / 65535:.6f} {px[i + 1] / 65535:.6f} {px[i + 2] / 65535:.6f}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")
    return str(path)


# --------------------------------------------------------------------------- XML

def _rate(fps: float) -> tuple[int, str]:
    tb = round(fps)
    return tb, "TRUE" if abs(fps - tb) > 0.01 else "FALSE"


def export_premiere_xml(plan: dict, path: str, name: str = "Montage AI") -> str:
    """FCP7 XML (xmeml v4): imports into Premiere Pro and DaVinci Resolve.

    Cuts and order are kept; transitions, speed and grading are not (use the .cube LUT).
    """
    plan = normalize_plan(plan)
    s = plan["settings"]
    tb, ntsc = _rate(s["fps"])
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    files_done: set = set()
    file_ids: dict = {}
    v_items, a_items = [], []
    t = 0
    for i, c in enumerate(plan["clips"], 1):
        info = probe_cached(c["source"])
        fid = file_ids.setdefault(c["source"], f"file-{len(file_ids) + 1}")
        fin, fout = round(c["start"] * tb), round(c["end"] * tb)
        start, end = t, t + (fout - fin)
        t = end
        src_frames = round(info["duration"] * tb)
        fname = escape(os.path.basename(c["source"]))
        if fid in files_done:
            file_xml = f'<file id="{fid}"/>'
        else:
            files_done.add(fid)
            url = "file://localhost/" + quote(c["source"].replace("\\", "/").lstrip("/"), safe="/:")
            audio = ("<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>"
                     "</samplecharacteristics><channelcount>2</channelcount></audio>"
                     if info["has_audio"] else "")
            file_xml = (f'<file id="{fid}"><name>{fname}</name><pathurl>{escape(url)}</pathurl>'
                        f"{rate}<duration>{src_frames}</duration><media><video>"
                        f"<samplecharacteristics>{rate}<width>{info.get('width')}</width>"
                        f"<height>{info.get('height')}</height></samplecharacteristics></video>"
                        f"{audio}</media></file>")
        common = (f"<name>{fname}</name><enabled>TRUE</enabled><duration>{src_frames}</duration>"
                  f"{rate}<start>{start}</start><end>{end}</end><in>{fin}</in><out>{fout}</out>")
        v_items.append(f'<clipitem id="clipitem-v{i}">{common}{file_xml}</clipitem>')
        if info["has_audio"] and not c.get("mute"):
            a_items.append(f'<clipitem id="clipitem-a{i}">{common}<file id="{fid}"/>'
                           "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex>"
                           "</sourcetrack></clipitem>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
           f'<sequence id="sequence-1"><name>{escape(name)}</name><duration>{t}</duration>{rate}'
           "<media><video><format><samplecharacteristics>"
           f"{rate}<width>{s['width']}</width><height>{s['height']}</height>"
           "<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>"
           f"<track>{''.join(v_items)}</track></video>"
           f"<audio><track>{''.join(a_items)}</track></audio></media></sequence></xmeml>\n")
    Path(path).write_text(xml, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- CapCut

def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _timeline_starts(plan: dict) -> list[float]:
    starts, t = [], 0.0
    for i, c in enumerate(plan["clips"]):
        tr = c.get("transition") if i else None
        if tr:
            t -= min(tr["duration"], clip_duration(c) - 0.05, clip_duration(plan["clips"][i - 1]) - 0.05)
        starts.append(round(max(0.0, t), 3))
        t += clip_duration(c)
    return starts


def export_capcut_pack(plan: dict, outdir: str, progress=None) -> str:
    """A folder CapCut can use directly: numbered clips, look.cube, captions.srt, guide.

    CapCut has no official project-import format, so the pack is built from what CapCut
    does import: video files (drag in order), LUTs (Adjust > LUT > Import) and .srt captions.
    """
    plan = normalize_plan(plan)
    out = Path(outdir)
    (out / "clips").mkdir(parents=True, exist_ok=True)
    clips = plan["clips"]
    for i, c in enumerate(clips, 1):
        sp = c.get("speed", 1.0)
        vf = [f"setpts=PTS/{sp:.4f}"] if sp != 1 else []
        cmd = [FFMPEG, "-v", "error", "-y", "-ss", f"{c['start']:.3f}", "-t",
               f"{c['end'] - c['start']:.3f}", "-i", c["source"]]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        if probe_cached(c["source"])["has_audio"] and not c.get("mute"):
            from .engine import _atempo
            at = _atempo(sp)
            if at:
                cmd += ["-af", ",".join(at)]
            cmd += ["-c:a", "aac", "-b:a", "320k"]
        else:
            cmd += ["-an"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "12", "-pix_fmt", "yuv420p",
                str(out / "clips" / f"{i:03d}_{Path(c['source']).stem}.mp4")]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise RenderError(f"Clip {i} export failed: {r.stderr[-800:]}")
        if progress:
            progress(i / (len(clips) + 1))

    has_look = bool(color_filters(plan.get("color"), Workdir(out), spatial=False))
    if has_look:
        make_cube_lut(plan["color"], str(out / "look.cube"))
    for f in out.glob("lut*"):  # Workdir copies of LUT files used while checking the grade
        f.unlink()
    if plan.get("texts"):
        srt = []
        for n, t in enumerate(sorted(plan["texts"], key=lambda x: x.get("start", 0)), 1):
            srt.append(f"{n}\n{_srt_time(float(t.get('start', 0)))} --> "
                       f"{_srt_time(float(t.get('end', 3)))}\n{t['text']}\n")
        (out / "captions.srt").write_text("\n".join(srt), encoding="utf-8")
    if (plan.get("music") or {}).get("path"):
        shutil.copyfile(plan["music"]["path"], out / ("music" + Path(plan["music"]["path"]).suffix))

    starts = _timeline_starts(plan)
    timeline = [{"order": i + 1, "file": f"clips/{i + 1:03d}_{Path(c['source']).stem}.mp4",
                 "timeline_start": starts[i], "duration": round(clip_duration(c), 3),
                 "transition_in": c.get("transition")} for i, c in enumerate(clips)]
    (out / "timeline.json").write_text(json.dumps({"settings": plan["settings"],
                                                   "clips": timeline}, ensure_ascii=False,
                                                  indent=2), encoding="utf-8")
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    trans = ", ".join(f"#{x['order']}: {x['transition_in']['type']}" for x in timeline
                      if x["transition_in"]) or "لا يوجد / none"
    guide = f"""CapCut — طريقة الاستيراد / How to import
========================================
1) افتح CapCut > New project. اسحب كل الملفات من مجلد clips إلى الخط الزمني بالترتيب (الأرقام 001، 002...).
   Drag every file from clips/ onto the timeline in numeric order.
2) الألوان: اختر كل المقاطع > Adjust > LUT > Import ثم اختر look.cube.
   Color: select all clips > Adjust > LUT > Import > look.cube.{'' if has_look else '  (لا يوجد تعديل ألوان في هذه الخطة)'}
3) النصوص: Text > Local captions / Import captions ثم اختر captions.srt (إن وجد).
   Captions: import captions.srt if present.
4) الموسيقى: اسحب ملف music إلى مسار الصوت (إن وجد).
5) الانتقالات المقترحة / Suggested transitions: {trans}
الدقة / Resolution: {plan['settings']['width']}x{plan['settings']['height']} @ {plan['settings']['fps']} fps
"""
    (out / "README_CapCut.txt").write_text(guide, encoding="utf-8")
    if progress:
        progress(1.0)
    return str(out)
