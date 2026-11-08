"""Turn a Pinterest pin (or any reference image) into a reusable montage template.

1. Fetch the pin's public image (og:image), or read a direct image URL / local file.
2. Measure it locally with ffmpeg: brightness, contrast, saturation, warmth, palette.
3. Optionally ask Claude / Gemini / Grok to read the mood and refine the look.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path

from .ai import AIError, ask, extract_json
from .engine import CURVES_PRESETS, FFMPEG, XFADE_TRANSITIONS, _clamp, _num
from .templates import TEMPLATE_DIR, save_template, slugify

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MontageAI/0.1"
IMG_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
           ".webp": "image/webp", ".gif": "image/gif"}


def _get(url: str, limit: int = 25_000_000) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(limit), (r.headers.get("Content-Type") or "").split(";")[0].strip()


def fetch_reference_image(src: str) -> tuple[bytes, str]:
    """Return (image bytes, mime) from a local file, an image URL, or a Pinterest pin URL."""
    if os.path.isfile(src):
        return Path(src).read_bytes(), IMG_EXT.get(Path(src).suffix.lower(), "image/jpeg")
    if not re.match(r"https?://", src):
        raise ValueError(f"Not a file or URL: {src}")
    data, ctype = _get(src)
    if ctype.startswith("image/"):
        return data, ctype
    page = data.decode("utf-8", "replace")
    m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
         or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page)
         or re.search(r'(https://i\.pinimg\.com/(?:originals|736x)/[^"\'\s\\]+\.(?:jpg|png|webp))',
                      page))
    if not m:
        raise ValueError("No image found on that page. Open the pin, right-click the image, "
                         "copy the image address and use that instead.")
    img_url = html.unescape(m.group(1))
    img_url = re.sub(r"pinimg\.com/\d+x/", "pinimg.com/originals/", img_url)
    try:
        data, ctype = _get(img_url)
    except Exception:  # originals may not exist for every pin
        data, ctype = _get(html.unescape(m.group(1)))
    return data, ctype if ctype.startswith("image/") else "image/jpeg"


def _pixels(img: bytes, size: int = 48) -> list[tuple[int, int, int]]:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ref.img")
        Path(p).write_bytes(img)
        r = subprocess.run([FFMPEG, "-v", "error", "-i", p, "-frames:v", "1",
                            "-vf", f"scale={size}:{size}:flags=area,format=rgb24",
                            "-f", "rawvideo", "-"], capture_output=True)
    if r.returncode or not r.stdout:
        raise ValueError("Could not decode the reference image: "
                         + r.stderr.decode("utf-8", "replace")[:300])
    b = r.stdout
    return [(b[i], b[i + 1], b[i + 2]) for i in range(0, len(b) - 2, 3)]


def analyze_image_local(img: bytes) -> dict:
    px = _pixels(img)
    n = len(px)
    lum = [(0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 for r, g, b in px]
    mean = sum(lum) / n
    std = (sum((x - mean) ** 2 for x in lum) / n) ** 0.5
    sat = sum((max(p) - min(p)) / max(1, max(p)) for p in px) / n
    warmth = sum(r - b for r, g, b in px) / n / 255
    palette = Counter((r // 32 * 32 + 16, g // 32 * 32 + 16, b // 32 * 32 + 16) for r, g, b in px)
    hexes = ["#%02X%02X%02X" % c for c, _ in palette.most_common(5)]

    color = {
        "brightness": round(_clamp((mean - 0.5) * 0.15, -0.08, 0.08), 3),
        "contrast": round(_clamp(1 + (std - 0.22) * 1.6, 0.85, 1.35), 3),
        "saturation": 0.0 if sat < 0.06 else round(_clamp(0.45 + sat * 1.5, 0.4, 1.6), 3),
        "temperature": int(_clamp(6500 - warmth * 7000, 4200, 9000)),
        "vignette": mean < 0.42,
    }
    stats = {"brightness": round(mean, 3), "contrast": round(std, 3),
             "saturation": round(sat, 3), "warmth": round(warmth, 3), "palette": hexes}
    light = mean > 0.6
    return {
        "color": color,
        "transition": {"type": "fade" if std < 0.2 else "dissolve", "duration": 0.6},
        "pacing": {"clip_length": 3.0},
        "text_style": {"color": "#111111" if light else "#FFFFFF", "position": "center",
                       "size_ratio": 0.06, "bold": True},
        "stats": stats,
    }


TEMPLATE_SYSTEM = f"""You are a film colorist and editor. Look at the reference image (usually a
Pinterest pin showing an aesthetic) and describe a video montage style that recreates its look
and mood. Return ONLY JSON:
{{"name": short name, "description": one sentence in the user's language, "mood": str,
  "color": {{"brightness": -0.3..0.3, "contrast": 0.5..2, "saturation": 0..3, "gamma": 0.5..2,
            "temperature": 2000..12000 (6500 neutral, lower = warmer), "vibrance": -1..1,
            "balance": {{"shadows": [r,g,b], "midtones": [r,g,b], "highlights": [r,g,b]}} (-0.5..0.5),
            "curves": one of {CURVES_PRESETS} or null, "vignette": bool, "grain": 0..30,
            "sharpen": 0..2}},
  "transition": {{"type": one of {XFADE_TRANSITIONS} or "cut", "duration": sec}},
  "pacing": {{"clip_length": seconds per shot}},
  "text_style": {{"color": "#RRGGBB", "position": "top"|"center"|"bottom"|"lower_third",
                 "size_ratio": 0.03..0.1, "bold": bool, "box": bool}},
  "settings": {{"fit": "cover"|"blur"}}, "fade_in": sec, "fade_out": sec}}
Measured image statistics are provided; stay consistent with them."""

_LIMITS = {"brightness": (-0.3, 0.3), "contrast": (0.5, 2), "saturation": (0, 3),
           "gamma": (0.5, 2), "temperature": (2000, 12000), "vibrance": (-1, 1),
           "grain": (0, 30), "sharpen": (0, 2)}


def _sanitize_color(c: dict) -> dict:
    out = {}
    for k, (lo, hi) in _LIMITS.items():
        if c.get(k) is not None:
            out[k] = round(_clamp(_num(c[k], (lo + hi) / 2), lo, hi), 3)
    if c.get("curves") in CURVES_PRESETS:
        out["curves"] = c["curves"]
    if isinstance(c.get("vignette"), bool):
        out["vignette"] = c["vignette"]
    bal = {}
    for z in ("shadows", "midtones", "highlights"):
        v = (c.get("balance") or {}).get(z)
        if isinstance(v, list) and len(v) == 3:
            bal[z] = [round(_clamp(_num(x, 0), -0.5, 0.5), 3) for x in v]
    if bal:
        out["balance"] = bal
    return out


def template_from_reference(src: str, name: str | None = None, provider: str | None = None,
                            hint: str = "", save: bool = True) -> dict:
    img, mime = fetch_reference_image(src)
    tpl = analyze_image_local(img)
    tpl["name"] = name or "Reference look"
    tpl["description"] = "Look measured from a reference image"
    tpl["source"] = src
    if provider:
        try:
            user = f"Measured stats: {tpl['stats']}. Extra wishes from the user: {hint or 'none'}"
            ai = extract_json(ask(provider, TEMPLATE_SYSTEM, user, (img, mime)))
            tpl["color"] = {**tpl["color"], **_sanitize_color(ai.get("color") or {})}
            tr = ai.get("transition") or {}
            if tr.get("type") in XFADE_TRANSITIONS + ["cut"]:
                tpl["transition"] = {"type": tr["type"],
                                     "duration": _clamp(_num(tr.get("duration"), 0.5), 0.1, 2)}
            cl = _num((ai.get("pacing") or {}).get("clip_length"), 0)
            if cl:
                tpl["pacing"] = {"clip_length": round(_clamp(cl, 0.5, 10), 2)}
            if isinstance(ai.get("text_style"), dict):
                tpl["text_style"] = {**tpl["text_style"], **ai["text_style"]}
            if (ai.get("settings") or {}).get("fit") in ("cover", "blur"):
                tpl["settings"] = {"fit": ai["settings"]["fit"]}
            for k in ("fade_in", "fade_out"):
                if ai.get(k) is not None:
                    tpl[k] = round(_clamp(_num(ai[k], 0.5), 0, 3), 2)
            tpl["name"] = name or ai.get("name") or tpl["name"]
            tpl["description"] = ai.get("description") or tpl["description"]
            tpl["mood"] = ai.get("mood")
            tpl["analyzed_by"] = provider
        except AIError as e:
            tpl["ai_error"] = str(e)
    if save:
        path = save_template(tpl, tpl["name"])
        ext = {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
        (TEMPLATE_DIR / f"{slugify(tpl['name'])}{ext}").write_bytes(img)
        tpl["id"] = path.stem
    return tpl
