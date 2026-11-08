"""Command line: python -m montage_ai <command> ..."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .engine import PRESETS, RESOLUTIONS, RenderError, detect_scenes, probe, render
from .plan import PlanError, normalize_plan


def _bar(label):
    def cb(f):
        n = int(f * 30)
        sys.stdout.write(f"\r{label} [{'#' * n}{'.' * (30 - n)}] {f * 100:5.1f}%")
        sys.stdout.flush()
        if f >= 1:
            sys.stdout.write("\n")
    return cb


def _settings(a) -> dict:
    s = {}
    if getattr(a, "resolution", None):
        s["resolution"] = a.resolution
    if getattr(a, "fps", None):
        s["fps"] = a.fps
    if getattr(a, "fit", None):
        s["fit"] = a.fit
    return s


def _load_plan(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _save(obj, path):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {path}")


def cmd_make(a):
    from .templates import auto_plan, load_template
    tpl = load_template(a.template) if a.template else {}
    if a.ai:
        from .ai import generate_plan
        prompt = a.prompt or "Make a polished, well-paced montage of these clips."
        print(f"Asking {a.ai} to edit...")
        plan = generate_plan(prompt, a.videos, a.ai, tpl, _settings(a))
        if plan.get("notes"):
            print("AI:", plan["notes"])
    else:
        plan = auto_plan(a.videos, tpl, _settings(a), a.duration, a.title)
    if a.music:
        plan["music"] = {"path": a.music, "volume": tpl.get("music_volume", 0.3), "fade_out": 2}
    plan = normalize_plan(plan)
    if a.save_plan:
        _save(plan, a.save_plan)
    out = render(plan, a.output, "draft" if a.draft else a.preset, _bar("Rendering"))
    print(f"Done: {out}")


def cmd_plan(a):
    from .ai import generate_plan
    from .templates import auto_plan, load_template
    tpl = load_template(a.template) if a.template else {}
    plan = (generate_plan(a.prompt, a.videos, a.ai, tpl, _settings(a)) if a.ai
            else auto_plan(a.videos, tpl, _settings(a), a.duration, a.title))
    _save(plan, a.output)


def cmd_render(a):
    out = render(_load_plan(a.plan), a.output, "draft" if a.draft else a.preset, _bar("Rendering"))
    print(f"Done: {out}")


def cmd_template(a):
    from .templates import list_templates
    if a.action == "list":
        for t in list_templates():
            print(f"{t['id']:<22} {t['name']}  —  {t['description']}")
        return
    from .reference import template_from_reference
    t = template_from_reference(a.source, a.name, a.ai, a.hint or "")
    if t.get("ai_error"):
        print("AI analysis failed, kept the measured look:", t["ai_error"])
    print(f"Saved template '{t['id']}':")
    print(json.dumps({k: t[k] for k in ("color", "transition", "pacing", "text_style")},
                     ensure_ascii=False, indent=2))


def cmd_export(a):
    from .export import export_capcut_pack, export_premiere_xml, make_cube_lut
    plan = _load_plan(a.plan)
    if a.kind == "xml":
        print("Saved", export_premiere_xml(plan, a.output))
    elif a.kind == "lut":
        print("Saved", make_cube_lut(normalize_plan(plan).get("color") or {}, a.output))
    else:
        print("Saved", export_capcut_pack(plan, a.output, _bar("Exporting")))


def cmd_info(a):
    for v in a.videos:
        info = probe(v)
        if a.scenes:
            info["scene_changes"] = detect_scenes(v)
        print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_serve(a):
    from .server import serve
    serve(a.port, open_browser=not a.no_browser)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="montage_ai", description="AI video editing on ffmpeg")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def out_opts(p):
        p.add_argument("--resolution", choices=list(RESOLUTIONS))
        p.add_argument("--fps", type=float)
        p.add_argument("--fit", choices=["cover", "contain", "blur"])

    def edit_opts(p):
        p.add_argument("videos", nargs="+")
        p.add_argument("-t", "--template", help="template id or .json path")
        p.add_argument("--ai", choices=["claude", "gemini", "grok"], help="let an AI do the edit")
        p.add_argument("-p", "--prompt", default="", help="what you want (any language)")
        p.add_argument("--duration", type=float, help="target length in seconds (auto mode)")
        p.add_argument("--title", help="title text over the opening (auto mode)")
        out_opts(p)

    p = sub.add_parser("make", help="edit + render in one go")
    edit_opts(p)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--music")
    p.add_argument("--preset", choices=list(PRESETS), default="h264_high")
    p.add_argument("--draft", action="store_true", help="fast low-quality preview")
    p.add_argument("--save-plan")
    p.set_defaults(fn=cmd_make)

    p = sub.add_parser("plan", help="create an editable plan.json (no render)")
    edit_opts(p)
    p.add_argument("-o", "--output", default="plan.json")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("render", help="render a plan.json")
    p.add_argument("plan")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--preset", choices=list(PRESETS), default="h264_high")
    p.add_argument("--draft", action="store_true")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("template", help="list templates or build one from Pinterest/an image")
    p.add_argument("action", choices=["list", "from"])
    p.add_argument("source", nargs="?", help="Pinterest pin URL, image URL or image file")
    p.add_argument("-n", "--name")
    p.add_argument("--ai", choices=["claude", "gemini", "grok"])
    p.add_argument("--hint", help="extra wishes, e.g. 'fast cuts for a travel reel'")
    p.set_defaults(fn=cmd_template)

    p = sub.add_parser("export", help="export a plan to Premiere/DaVinci XML, CapCut or LUT")
    p.add_argument("kind", choices=["xml", "capcut", "lut"])
    p.add_argument("plan")
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("info", help="show video details")
    p.add_argument("videos", nargs="+")
    p.add_argument("--scenes", action="store_true")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("serve", help="open the web interface")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_serve)

    a = ap.parse_args(argv)
    if a.cmd == "template" and a.action == "from" and not a.source:
        ap.error("template from needs a source (Pinterest URL or image)")
    try:
        a.fn(a)
    except (RenderError, PlanError, FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
