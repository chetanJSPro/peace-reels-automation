#!/usr/bin/env python3
"""Local web UI for pin2shorts: paste Pinterest links, get YouTube-Shorts-ready files."""
from __future__ import annotations

import io
import threading
import uuid
import zipfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

import pin2shorts as P

app = Flask(__name__)
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


def run_job(job_id: str, urls, **kw):
    def log(msg=""):
        with LOCK:
            JOBS[job_id]["log"].append(str(msg))
    JOBS[job_id]["status"] = "running"
    try:
        rows = P.process(urls, log=log, **kw)
        JOBS[job_id]["rows"] = rows
        JOBS[job_id]["done"] = [r for r in rows if r.get("status") == "rendered"]
        JOBS[job_id]["status"] = "done"
    except Exception as exc:  # keep the UI alive
        JOBS[job_id]["status"] = "error"
        log(f"ERROR: {exc}")
    log("__END__")


@app.route("/")
def index():
    shorts = sorted(P.OUT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return render_template("index.html", shorts=shorts, rows=P.load_manifest()[::-1][:60],
                           ffmpeg=Path(P.FFMPEG).name)


@app.route("/start", methods=["POST"])
def start():
    urls = [u.strip() for u in (request.form.get("urls") or "").splitlines() if u.strip()]
    if request.form.get("board", "").strip():
        urls.append(request.form["board"].strip())
    if not urls:
        return jsonify({"error": "paste at least one Pinterest link"}), 400
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "queued", "log": [], "rows": [], "done": []}
    threading.Thread(target=run_job, args=(job_id, urls), kwargs=dict(
        mode=request.form.get("mode", "blur"),
        max_duration=float(request.form.get("max_duration") or P.MAX_DURATION),
        watermark=(request.form.get("watermark") or "").strip() or None,
        fade=request.form.get("fade") == "on",
        cookies=(request.form.get("cookies") or "").strip() or None,
        limit=int(request.form["limit"]) if request.form.get("limit") else None,
        skip_seen=request.form.get("skip_seen") == "on",
    ), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify({"status": job["status"],
                    "log": job["log"][-120:],
                    "done": [{"file": Path(r["shorts_file"]).name,
                              "title": r.get("title", ""),
                              "url": r.get("url", ""),
                              "note": r.get("note", "")} for r in job["done"]]})


@app.route("/shorts/<name>")
def serve(name):
    return send_from_directory(P.OUT_DIR, name, as_attachment=False)


@app.route("/zip")
def zip_all():
    files = sorted(P.OUT_DIR.glob("*.mp4"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.name)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name="shorts.zip")


@app.route("/csv")
def csv_all():
    return send_from_directory(P.BASE, P.MANIFEST.name, as_attachment=True)


if __name__ == "__main__":
    P.OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"ffmpeg: {P.FFMPEG}")
    app.run(host="0.0.0.0", port=int(__import__("os").environ.get("PORT", 8000)), debug=False)
