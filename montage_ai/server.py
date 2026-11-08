"""Local web interface (stdlib only). Listens on 127.0.0.1 so only this computer can reach it."""
from __future__ import annotations

import json
import os
import re
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ai import PROVIDERS, available_providers
from .engine import PRESETS, RESOLUTIONS, XFADE_TRANSITIONS, render
from .plan import normalize_plan
from .templates import TEMPLATE_DIR, list_templates, load_template

STATIC = Path(__file__).parent / "static"
JOBS: dict = {}
DEFAULT_OUT = Path.home() / "Videos" / "MontageAI"


def _run_job(kind: str, fn, *args) -> str:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "status": "running", "progress": 0.0, "result": None,
           "error": None}
    JOBS[jid] = job

    def prog(f):
        job["progress"] = round(f, 4)

    def work():
        try:
            job["result"] = fn(*args, progress=prog)
            job["status"] = "done"
        except Exception as e:  # report any failure to the page
            job["status"] = "error"
            job["error"] = str(e) or traceback.format_exc()
    threading.Thread(target=work, daemon=True).start()
    return jid


def _out_path(name: str | None, ext: str) -> str:
    if name and os.path.isabs(name):
        return name
    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-]+", "_", name or "montage", flags=re.UNICODE) or "montage"
    return str(DEFAULT_OUT / (stem if stem.endswith(ext) or not ext else stem + ext))


class Handler(BaseHTTPRequestHandler):
    server_version = "MontageAI"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str):
        size = path.stat().st_size
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else end
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                return self._file(STATIC / "index.html", "text/html; charset=utf-8")
            if path == "/api/status":
                return self._json({
                    "providers": list(PROVIDERS), "available": available_providers(),
                    "templates": list_templates(), "presets": list(PRESETS),
                    "resolutions": list(RESOLUTIONS), "transitions": XFADE_TRANSITIONS,
                    "output_dir": str(DEFAULT_OUT)})
            m = re.fullmatch(r"/api/templates/([\w\-]+)", path)
            if m:
                return self._json(load_template(m.group(1)))
            m = re.fullmatch(r"/api/templates/([\w\-]+)/image", path)
            if m:
                for ext, ct in ((".jpg", "image/jpeg"), (".png", "image/png"),
                                (".webp", "image/webp")):
                    p = TEMPLATE_DIR / f"{m.group(1)}{ext}"
                    if p.is_file():
                        return self._file(p, ct)
                return self._json({"error": "no image"}, 404)
            m = re.fullmatch(r"/api/jobs/(\w+)", path)
            if m and m.group(1) in JOBS:
                return self._json(JOBS[m.group(1)])
            m = re.fullmatch(r"/api/jobs/(\w+)/video", path)
            if m and JOBS.get(m.group(1), {}).get("kind") == "render":
                res = JOBS[m.group(1)].get("result")
                if res and os.path.isfile(res):
                    ct = "video/quicktime" if res.endswith(".mov") else "video/mp4"
                    return self._file(Path(res), ct)
            self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        try:
            b = self._body()
            path = self.path
            if path == "/api/keys":
                for prov, key in (b or {}).items():
                    if prov in PROVIDERS and isinstance(key, str):
                        os.environ[PROVIDERS[prov]["env"][0]] = key.strip()
                return self._json({"available": available_providers()})
            if path == "/api/plan":
                return self._json(self._plan(b))
            if path == "/api/template":
                from .reference import template_from_reference
                t = template_from_reference(b["source"], b.get("name") or None,
                                            b.get("ai") or None, b.get("hint", ""))
                return self._json(t)
            if path == "/api/render":
                preset = b.get("preset", "h264_high")
                ext = PRESETS.get(preset, PRESETS["h264_high"])["ext"]
                out = _out_path(b.get("output"), ext)
                return self._json({"job": _run_job("render", render, b["plan"], out, preset)})
            if path == "/api/export":
                from .export import export_capcut_pack, export_premiere_xml, make_cube_lut
                kind, plan = b.get("kind"), b["plan"]
                if kind == "capcut":
                    out = _out_path((b.get("output") or "montage") + "_capcut", "")
                    return self._json({"job": _run_job("export", export_capcut_pack, plan, out)})
                if kind == "xml":
                    return self._json({"result": export_premiere_xml(
                        plan, _out_path(b.get("output"), ".xml"))})
                if kind == "lut":
                    color = normalize_plan(plan).get("color") or {}
                    return self._json({"result": make_cube_lut(
                        color, _out_path(b.get("output"), ".cube"))})
            self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 400)

    def _plan(self, b: dict) -> dict:
        videos = [v.strip().strip('"') for v in b.get("videos", []) if v.strip()]
        if not videos:
            raise ValueError("Add at least one video path")
        tpl = load_template(b["template"]) if b.get("template") else {}
        settings = {k: b[k] for k in ("resolution", "fit") if b.get(k)}
        if b.get("ai"):
            from .ai import generate_plan
            plan = generate_plan(b.get("prompt") or "Make a polished montage.", videos, b["ai"],
                                 tpl, settings)
        else:
            from .templates import auto_plan
            dur = float(b["duration"]) if b.get("duration") else None
            plan = auto_plan(videos, tpl, settings, dur, b.get("title") or None)
        if b.get("music"):
            plan["music"] = {"path": b["music"].strip().strip('"'),
                             "volume": tpl.get("music_volume", 0.3), "fade_out": 2}
            plan = normalize_plan(plan)
        return plan


def serve(port: int = 8765, open_browser: bool = True):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Montage AI is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
