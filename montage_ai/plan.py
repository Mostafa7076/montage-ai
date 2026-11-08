"""Edit plan: the JSON timeline every part of the tool reads and writes.

{
  "settings": {"width": 3840, "height": 2160, "fps": 30, "fit": "cover|contain|blur"},
  "clips": [{"source": "a.mp4", "start": 0, "end": 4.2, "speed": 1,
             "transition": {"type": "fade", "duration": 0.5}, "color": {...}}],
  "color": {"brightness": 0, "contrast": 1.1, "saturation": 1.2, "gamma": 1,
            "temperature": 6000, "balance": {"shadows": [r,g,b], ...},
            "curves": "vintage", "lut": "look.cube", "vignette": true, "grain": 8},
  "texts": [{"text": "Hello", "start": 0, "end": 3, "position": "center"}],
  "music": {"path": "song.mp3", "volume": 0.3, "fade_out": 2},
  "fade_in": 0.5, "fade_out": 1
}
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

from .engine import (RESOLUTIONS, XFADE_TRANSITIONS, _clamp, _num, clip_duration,
                     probe_cached)

DEFAULT_SETTINGS = {"width": 1920, "height": 1080, "fps": 30, "fit": "cover"}


class PlanError(ValueError):
    pass


def _resolve(path, base_dir):
    p = Path(str(path)).expanduser()
    if not p.is_absolute() and base_dir:
        p = Path(base_dir) / p
    return str(p.resolve())


def normalize_settings(s: dict | None) -> dict:
    s = {**DEFAULT_SETTINGS, **(s or {})}
    res = s.pop("resolution", None)
    if res in RESOLUTIONS:
        s["width"], s["height"] = RESOLUTIONS[res]
    s["width"] = int(_clamp(_num(s["width"], 1920), 16, 8192)) // 2 * 2
    s["height"] = int(_clamp(_num(s["height"], 1080), 16, 8192)) // 2 * 2
    s["fps"] = round(_clamp(_num(s["fps"], 30), 1, 120), 3)
    if s.get("fit") not in ("cover", "contain", "blur"):
        s["fit"] = "cover"
    return s


def normalize_plan(plan: dict, base_dir=None) -> dict:
    """Validate a plan, fill defaults, clamp times to real media lengths."""
    p = copy.deepcopy(plan or {})
    p["settings"] = normalize_settings(p.get("settings"))
    clips = []
    for n, c in enumerate(p.get("clips") or []):
        src = c.get("source")
        if not src:
            raise PlanError(f"Clip {n + 1} has no source")
        src = _resolve(src, base_dir)
        if not os.path.isfile(src):
            raise PlanError(f"Clip {n + 1}: file not found: {src}")
        info = probe_cached(src)
        if not info["has_video"]:
            raise PlanError(f"Clip {n + 1}: no video stream in {src}")
        dur = info["duration"]
        st = max(0.0, _num(c.get("start"), 0))
        en = _num(c.get("end"), dur)
        if dur > 0:
            en = min(en, dur)
        if en - st < 0.1:
            continue
        nc = {"source": src, "start": round(st, 3), "end": round(en, 3),
              "speed": round(_clamp(_num(c.get("speed"), 1), 0.25, 4), 4),
              "volume": _clamp(_num(c.get("volume"), 1), 0, 4), "mute": bool(c.get("mute"))}
        if c.get("color"):
            nc["color"] = c["color"]
        tr = c.get("transition") or {}
        if tr.get("type") in XFADE_TRANSITIONS:
            nc["transition"] = {"type": tr["type"],
                                "duration": round(_clamp(_num(tr.get("duration"), 0.5), 0.1, 3), 3)}
        clips.append(nc)
    if not clips:
        raise PlanError("The plan has no usable clips")
    p["clips"] = clips
    p["texts"] = [t for t in (p.get("texts") or []) if str(t.get("text", "")).strip()]
    color = p.get("color") or {}
    if color.get("lut"):
        color["lut"] = _resolve(color["lut"], base_dir)
    p["color"] = color
    music = p.get("music") or {}
    if music.get("path"):
        music["path"] = _resolve(music["path"], base_dir)
        if not os.path.isfile(music["path"]):
            raise PlanError(f"Music file not found: {music['path']}")
    p["music"] = music
    return p


def plan_duration(plan: dict) -> float:
    total = 0.0
    for i, c in enumerate(plan["clips"]):
        d = clip_duration(c)
        tr = c.get("transition") if i else None
        total += d - (min(tr["duration"], d - 0.05) if tr else 0)
    return round(total, 3)
