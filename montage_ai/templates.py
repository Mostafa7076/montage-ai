"""Montage templates (look + pacing + transitions + text style) and the no-AI auto editor."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .engine import _num, detect_scenes, probe_cached
from .plan import normalize_plan, normalize_settings

TEMPLATE_DIR = Path(__file__).parent / "templates"


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", str(name).strip().lower(), flags=re.UNICODE).strip("_")
    return s or "template"


def list_templates() -> list[dict]:
    out = []
    for f in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": f.stem, "name": t.get("name", f.stem),
                    "description": t.get("description", ""), "source": t.get("source")})
    return out


def load_template(name_or_path) -> dict:
    if not name_or_path:
        return {}
    p = Path(str(name_or_path))
    if not p.is_file():
        p = TEMPLATE_DIR / f"{slugify(name_or_path)}.json"
    if not p.is_file():
        raise FileNotFoundError(f"Template not found: {name_or_path}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_template(t: dict, name: str | None = None) -> Path:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMPLATE_DIR / f"{slugify(name or t.get('name'))}.json"
    path.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def apply_template(plan: dict, t: dict) -> dict:
    """Use the template as defaults; anything already in the plan wins."""
    if not t:
        return plan
    p = copy.deepcopy(plan)
    p["color"] = {**(t.get("color") or {}), **(p.get("color") or {})}
    if t.get("settings"):
        p["settings"] = {**t["settings"], **(p.get("settings") or {})}
    tr = t.get("transition")
    if tr and tr.get("type") not in (None, "none", "cut"):
        for i, c in enumerate(p.get("clips", [])):
            if i and "transition" not in c:
                c["transition"] = dict(tr)
    style = t.get("text_style") or {}
    p["texts"] = [{**style, **x} for x in p.get("texts", [])]
    if t.get("music_volume") is not None and p.get("music"):
        p["music"].setdefault("volume", t["music_volume"])
    for k in ("fade_in", "fade_out"):
        if k in t:
            p.setdefault(k, t[k])
    return p


def auto_plan(sources: list[str], template: dict | None = None, settings: dict | None = None,
              target_duration: float | None = None, title: str | None = None) -> dict:
    """Edit without AI: cut at scene changes, keep the template's pace, alternate sources."""
    t = template or {}
    L = max(0.5, _num((t.get("pacing") or {}).get("clip_length"), 3.0))
    per_source = []
    for src in sources:
        dur = probe_cached(src)["duration"]
        cuts = [0.0] + [x for x in detect_scenes(src) if 0.3 < x < dur - 0.3] + [dur]
        segs = []
        for a, b in zip(cuts, cuts[1:]):
            x = a
            while x < b - 0.3:
                e = min(x + L, b)
                if e - x >= 0.5:
                    segs.append((round(x, 3), round(e, 3)))
                x = e
        per_source.append([(src, s, e) for s, e in segs])

    clips, total = [], 0.0
    while any(per_source):
        for lst in per_source:
            if lst and (target_duration is None or total < target_duration):
                src, s, e = lst.pop(0)
                clips.append({"source": src, "start": s, "end": e})
                total += e - s
        if target_duration is not None and total >= target_duration:
            break
    plan = {"settings": normalize_settings({**(t.get("settings") or {}), **(settings or {})}),
            "clips": clips, "texts": []}
    if title:
        plan["texts"].append({"text": title, "start": 0.2, "end": min(3.5, max(1.0, total)),
                              "position": "center"})
    plan.setdefault("fade_in", 0.4)
    plan.setdefault("fade_out", 0.8)
    return normalize_plan(apply_template(plan, t))
