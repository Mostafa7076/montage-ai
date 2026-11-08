"""AI providers (Claude, Gemini, Grok) that turn a request into an edit plan.

Keys come from environment variables:
  ANTHROPIC_API_KEY   -> claude   (model: MONTAGE_CLAUDE_MODEL, default claude-sonnet-5-5)
  GEMINI_API_KEY      -> gemini   (model: MONTAGE_GEMINI_MODEL, default gemini-2.5-flash)
  XAI_API_KEY         -> grok     (model: MONTAGE_GROK_MODEL,   default grok-4)
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request

from .engine import CURVES_PRESETS, XFADE_TRANSITIONS, detect_scenes, probe_cached
from .plan import normalize_plan
from .templates import apply_template

PROVIDERS = {
    "claude": {"env": ["ANTHROPIC_API_KEY"], "model_env": "MONTAGE_CLAUDE_MODEL",
               "default_model": "claude-sonnet-5-5"},
    "gemini": {"env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"], "model_env": "MONTAGE_GEMINI_MODEL",
               "default_model": "gemini-2.5-flash"},
    "grok": {"env": ["XAI_API_KEY"], "model_env": "MONTAGE_GROK_MODEL",
             "default_model": "grok-4"},
}


class AIError(RuntimeError):
    pass


def _key(provider: str) -> str:
    if provider not in PROVIDERS:
        raise AIError(f"Unknown provider '{provider}'. Choose: {', '.join(PROVIDERS)}")
    for env in PROVIDERS[provider]["env"]:
        if os.environ.get(env):
            return os.environ[env]
    raise AIError(f"No API key for {provider}. Set {PROVIDERS[provider]['env'][0]}.")


def _model(provider: str) -> str:
    cfg = PROVIDERS[provider]
    return os.environ.get(cfg["model_env"]) or cfg["default_model"]


def available_providers() -> list[str]:
    return [p for p, c in PROVIDERS.items() if any(os.environ.get(e) for e in c["env"])]


def _post(url: str, headers: dict, body: dict, timeout: int = 240) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise AIError(f"HTTP {e.code} from {url.split('/')[2]}: "
                      f"{e.read().decode('utf-8', 'replace')[:600]}") from None
    except urllib.error.URLError as e:
        raise AIError(f"Network error: {e.reason}") from None


def ask(provider: str, system: str, user: str, image: tuple[bytes, str] | None = None) -> str:
    """Send one prompt (optionally with one image) and return the model's text."""
    key, model = _key(provider), _model(provider)
    b64 = base64.b64encode(image[0]).decode() if image else None
    mime = image[1] if image else None

    if provider == "claude":
        content = []
        if image:
            content.append({"type": "image",
                            "source": {"type": "base64", "media_type": mime, "data": b64}})
        content.append({"type": "text", "text": user})
        r = _post("https://api.anthropic.com/v1/messages",
                  {"x-api-key": key, "anthropic-version": "2023-06-01"},
                  {"model": model, "max_tokens": 8000, "system": system,
                   "messages": [{"role": "user", "content": content}]})
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")

    if provider == "gemini":
        parts = []
        if image:
            parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        parts.append({"text": user})
        r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                  {"x-goog-api-key": key},
                  {"systemInstruction": {"parts": [{"text": system}]},
                   "contents": [{"role": "user", "parts": parts}],
                   "generationConfig": {"responseMimeType": "application/json"}})
        try:
            return "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError):
            raise AIError(f"Unexpected Gemini response: {json.dumps(r)[:400]}") from None

    # grok (xAI, OpenAI-compatible)
    content = []
    if image:
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    content.append({"type": "text", "text": user})
    r = _post("https://api.x.ai/v1/chat/completions", {"Authorization": f"Bearer {key}"},
              {"model": model, "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": content}]})
    try:
        return r["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise AIError(f"Unexpected Grok response: {json.dumps(r)[:400]}") from None


def extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text or "")
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise AIError(f"The model did not return JSON:\n{text[:500]}")
    try:
        return json.loads(text[a:b + 1])
    except json.JSONDecodeError as e:
        raise AIError(f"Invalid JSON from the model: {e}") from None


PLAN_SYSTEM = f"""You are a senior professional video editor. You receive a list of source
videos (index, duration, resolution, detected scene-change times) plus the user's request and
an optional style template. Build an edit decision list.

Return ONLY a JSON object with this shape:
{{
  "settings": {{"width": int, "height": int, "fps": number, "fit": "cover"|"contain"|"blur"}},
  "clips": [{{"source": <source index int>, "start": sec, "end": sec, "speed": 0.25-4,
             "transition": {{"type": <transition>, "duration": sec}} }}],
  "color": {{"brightness": -0.3..0.3, "contrast": 0.5..2, "saturation": 0..3, "gamma": 0.5..2,
            "temperature": 2000..12000 (6500 neutral, lower = warmer), "vibrance": -1..1,
            "balance": {{"shadows": [r,g,b], "midtones": [r,g,b], "highlights": [r,g,b]}} (-0.5..0.5),
            "curves": <curve preset or omit>, "vignette": bool, "grain": 0..30, "sharpen": 0..2}},
  "texts": [{{"text": str, "start": sec, "end": sec,
             "position": "top"|"center"|"bottom"|"lower_third", "color": "#RRGGBB", "box": bool}}],
  "fade_in": sec, "fade_out": sec,
  "notes": "one short sentence explaining the edit"
}}
Transitions: {", ".join(XFADE_TRANSITIONS)} (omit "transition" for a hard cut).
Curve presets: {", ".join(CURVES_PRESETS)}.
Rules: only use the given source indexes; keep start/end inside each source's duration;
prefer cutting on scene changes; match the requested length, aspect ratio and mood;
write on-screen text in the same language as the user's request; text times are on the
final timeline. Omit "settings" fields you have no reason to change."""


def describe_sources(sources: list[str]) -> list[dict]:
    out = []
    for i, src in enumerate(sources):
        info = probe_cached(src)
        out.append({"index": i, "file": os.path.basename(src), "duration": info["duration"],
                    "width": info.get("width"), "height": info.get("height"),
                    "fps": info.get("fps"), "has_audio": info["has_audio"],
                    "scene_changes": detect_scenes(src)[:120]})
    return out


def generate_plan(prompt: str, sources: list[str], provider: str, template: dict | None = None,
                  settings: dict | None = None) -> dict:
    """Ask the chosen AI to edit the sources; returns a validated plan."""
    payload = {"request": prompt, "sources": describe_sources(sources),
               "template": template or None, "required_settings": settings or None}
    plan = extract_json(ask(provider, PLAN_SYSTEM, json.dumps(payload, ensure_ascii=False)))

    clips = []
    for c in plan.get("clips") or []:
        idx = c.get("source")
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
        if not isinstance(idx, int) or not 0 <= idx < len(sources):
            continue  # never let the model point at files it was not given
        clips.append({**c, "source": sources[idx]})
    plan["clips"] = clips
    if settings:
        plan["settings"] = {**(plan.get("settings") or {}), **settings}
    plan.pop("music", None)
    return normalize_plan(apply_template(plan, template or {}))
