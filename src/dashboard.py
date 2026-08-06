from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from flask import Flask, Response, jsonify, send_file

from utils import project_root

ROOT = project_root()
PYTHON = sys.executable  # dashboard.py is launched by the same venv python, so reuse it
LOG_TAIL_LINES = 300

app = Flask(__name__)

state_lock = threading.Lock()
state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_result": None,  # "success" | "error" | None
    "last_error": None,
}
log_lines: deque[str] = deque(maxlen=LOG_TAIL_LINES)


def _run_job() -> None:
    with state_lock:
        state["running"] = True
        state["started_at"] = time.time()
        state["finished_at"] = None
        state["last_result"] = None
        state["last_error"] = None
    log_lines.append(f"--- run started {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    try:
        proc = subprocess.Popen(
            [PYTHON, str(ROOT / "src" / "generate.py"), "--config", "config.yaml"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            log_lines.append(line.rstrip("\n"))
        code = proc.wait()
        with state_lock:
            state["last_result"] = "success" if code == 0 else "error"
            if code != 0:
                state["last_error"] = f"generate.py exited with code {code}"
    except Exception as e:
        with state_lock:
            state["last_result"] = "error"
            state["last_error"] = str(e)
        log_lines.append(f"--- run failed: {e} ---")
    finally:
        with state_lock:
            state["running"] = False
            state["finished_at"] = time.time()
        log_lines.append(f"--- run finished {time.strftime('%Y-%m-%d %H:%M:%S')} ---")


def _recent_jobs(limit: int = 12) -> list[dict]:
    out_dir = ROOT / "output"
    if not out_dir.exists():
        return []
    jobs = []
    for job_dir in sorted(out_dir.iterdir(), reverse=True):
        if not job_dir.is_dir():
            continue
        meta_path = job_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        jobs.append(
            {
                "job_dir": job_dir.name,
                "title": meta.get("title", job_dir.name),
                "location_label": meta.get("location_label", ""),
                "duration_seconds": meta.get("duration_seconds"),
                "youtube_video_id": meta.get("youtube_video_id"),
                "has_thumbnail": (job_dir / "thumbnail.jpg").exists(),
            }
        )
        if len(jobs) >= limit:
            break
    return jobs


@app.get("/")
def index() -> Response:
    return Response(PAGE_HTML, mimetype="text/html")


@app.get("/api/status")
def api_status():
    with state_lock:
        s = dict(state)
    s["log_tail"] = list(log_lines)
    s["jobs"] = _recent_jobs()
    return jsonify(s)


@app.post("/api/run")
def api_run():
    with state_lock:
        already_running = state["running"]
    if already_running:
        return jsonify({"ok": False, "message": "A run is already in progress."}), 409
    threading.Thread(target=_run_job, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/media/<job_dir>/thumbnail.jpg")
def media_thumbnail(job_dir: str):
    path = ROOT / "output" / job_dir / "thumbnail.jpg"
    if not path.exists() or ".." in job_dir:
        return "", 404
    return send_file(path, mimetype="image/jpeg")


PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Peace Reels — Dashboard</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 24px auto; padding: 0 16px; }
  h1 { font-size: 1.4rem; }
  #status-badge { display: inline-block; padding: 4px 12px; border-radius: 999px; font-weight: 600; font-size: 0.85rem; }
  .idle { background: #4444; }
  .running { background: #f5a62344; color: #b57300; }
  .success { background: #2ecc7133; color: #1e8449; }
  .error { background: #e74c3c33; color: #c0392b; }
  button { padding: 10px 20px; font-size: 1rem; border-radius: 8px; border: 1px solid #8884; cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  #log { background: #1118; color: #ccc; font-family: monospace; font-size: 0.8rem; padding: 12px;
         height: 260px; overflow-y: auto; border-radius: 8px; white-space: pre-wrap; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; margin-top: 14px; }
  .card { border: 1px solid #8883; border-radius: 10px; overflow: hidden; }
  .card img { width: 100%; aspect-ratio: 9/16; object-fit: cover; display: block; background: #8882; }
  .card .body { padding: 8px 10px; font-size: 0.85rem; }
  .card a { color: inherit; }
</style>
</head>
<body>
  <h1>Peace Reels automation</h1>
  <p>
    <span id="status-badge" class="idle">Idle</span>
    &nbsp;
    <button id="run-btn" onclick="runNow()">Run now</button>
  </p>
  <h3>Live log</h3>
  <div id="log"></div>
  <h3>Recent videos</h3>
  <div class="grid" id="jobs"></div>

<script>
async function runNow() {
  const r = await fetch('/api/run', { method: 'POST' });
  poll();
}

function fmtBadge(s) {
  const badge = document.getElementById('status-badge');
  if (s.running) { badge.textContent = 'Running…'; badge.className = 'running'; }
  else if (s.last_result === 'success') { badge.textContent = 'Idle — last run OK'; badge.className = 'success'; }
  else if (s.last_result === 'error') { badge.textContent = 'Idle — last run failed'; badge.className = 'error'; }
  else { badge.textContent = 'Idle'; badge.className = 'idle'; }
  document.getElementById('run-btn').disabled = s.running;
}

function renderJobs(jobs) {
  const el = document.getElementById('jobs');
  el.innerHTML = jobs.map(j => {
    const thumb = j.has_thumbnail ? `/media/${j.job_dir}/thumbnail.jpg` : '';
    const link = j.youtube_video_id ? `https://youtube.com/watch?v=${j.youtube_video_id}` : null;
    const titleHtml = link ? `<a href="${link}" target="_blank">${j.title}</a>` : j.title;
    return `<div class="card">
      ${thumb ? `<img src="${thumb}">` : ''}
      <div class="body"><b>${titleHtml}</b><br>${j.location_label || ''}</div>
    </div>`;
  }).join('');
}

async function poll() {
  const r = await fetch('/api/status');
  const s = await r.json();
  fmtBadge(s);
  document.getElementById('log').textContent = s.log_tail.join('\\n');
  const log = document.getElementById('log');
  log.scrollTop = log.scrollHeight;
  renderJobs(s.jobs);
}

poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8787, debug=False)
