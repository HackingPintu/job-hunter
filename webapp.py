"""Local web UI. Binds to 127.0.0.1 by default. CVs are parsed in memory and never written to disk."""
import csv
import io
import json
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from . import __version__, engine
from . import profile as prof

TEMPLATES = Path(__file__).parent / "templates"
MAX_JOBS = 40
JOB_TTL = 3600
_jobs = {}
_lock = threading.Lock()


def _gc():
    cutoff = time.time() - JOB_TTL
    for k in [k for k, v in _jobs.items() if v["created"] < cutoff]:
        _jobs.pop(k, None)


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

    @app.get("/")
    def index():
        return send_from_directory(TEMPLATES, "app.html")

    @app.get("/api/meta")
    def meta():
        reg, voc = prof.regions(), prof.vocab()
        co = engine.load_companies()
        return jsonify({
            "version": __version__,
            "countries": {n: {"cities": list(c["cities"])} for n, c in reg["countries"].items()},
            "roles": [{"id": k, "label": v.get("label", k)} for k, v in voc["roles"].items()],
            "skills": sorted(voc["skills"]),
            "tiers": [{"id": t, "label": engine.TIER_LABEL[t]} for t in prof.TIERS],
            "companies_total": len(co["companies"]) + len(co["manual_boards"]),
        })

    @app.post("/api/analyze-cv")
    def analyze():
        f = request.files.get("cv")
        if not f or not f.filename:
            return jsonify(error="No file uploaded"), 400
        try:
            text = prof.extract_text(f.read(), f.filename)
        except Exception as e:
            return jsonify(error=f"Could not read this file: {e}"), 400
        if len(text.strip()) < 50:
            return jsonify(error="No readable text found. Scanned PDFs are not supported, try a text-based PDF or DOCX."), 400
        return jsonify(prof.analyze_cv(text))

    @app.post("/api/search")
    def search():
        raw = request.get_json(silent=True) or {}
        try:
            prof.normalize_profile(raw)
        except Exception as e:
            return jsonify(error=str(e)), 400
        with _lock:
            _gc()
            if sum(1 for v in _jobs.values() if v["state"] == "running") >= 3:
                return jsonify(error="Server is busy, please retry in a minute"), 429
            if len(_jobs) >= MAX_JOBS:
                return jsonify(error="Too many stored searches, retry later"), 429
            jid = uuid.uuid4().hex[:12]
            _jobs[jid] = {"state": "running", "done": 0, "total": 0, "message": "Starting...", "result": None, "created": time.time()}

        def progress(done, total, msg):
            _jobs[jid].update(done=done, total=total, message=msg)

        def work():
            try:
                res = engine.run_hunt(raw, progress=progress)
                res["id"] = jid
                _jobs[jid].update(state="done", result=res, message="Done")
            except Exception as e:
                _jobs[jid].update(state="error", message=str(e))

        threading.Thread(target=work, daemon=True).start()
        return jsonify(id=jid)

    @app.get("/api/status/<jid>")
    def status(jid):
        j = _jobs.get(jid)
        if not j:
            return jsonify(error="Unknown search"), 404
        return jsonify({k: j[k] for k in ("state", "done", "total", "message")})

    @app.get("/api/results/<jid>")
    def results(jid):
        j = _jobs.get(jid)
        if not j or j["state"] != "done":
            return jsonify(error="Not ready"), 404
        return jsonify(j["result"])

    @app.get("/api/export/<jid>.csv")
    def export(jid):
        j = _jobs.get(jid)
        if not j or j["state"] != "done":
            return jsonify(error="Not ready"), 404
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["score", "company", "tier", "title", "location", "min_years", "skills", "posted", "url"])
        for x in j["result"]["jobs"]:
            w.writerow([x["score"], x["company"], x["tier"], x["title"], x["location"], x["min_years"] or "",
                        "; ".join(x["skills"]), x["posted"] or "", x["url"]])
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=jobhunter-results.csv"})

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    return app


def serve(host="127.0.0.1", port=8000, open_browser=True):
    app = create_app()
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"
    if open_browser:
        threading.Timer(1.0, lambda: __import__("webbrowser").open(url)).start()
    print(f"JobHunter running at {url}  (Ctrl+C to stop)")
    app.run(host=host, port=port, threaded=True)
