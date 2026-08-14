from __future__ import annotations

import json
import os
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
VIDEOS_PER_RUN = 3  # click the desktop icon once -> 3 uploads (matches the cloud cadence), then this process exits itself
# The 3 videos fire back-to-back within a couple of minutes, so picking the content slot from
# the real clock (like the scheduled cloud runs do) would land all 3 in whichever single slot
# the click happened to fall in. Force one video per slot instead, so a manual batch always
# spans the full topic variety instead of hammering one 7-8 topic pool three times.
SLOT_CYCLE = ["morning", "midday", "evening"]

app = Flask(__name__)

state_lock = threading.Lock()
state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_result": None,  # "success" | "partial" | "error" | None
    "last_error": None,
    "progress": f"0/{VIDEOS_PER_RUN}",
}
log_lines: deque[str] = deque(maxlen=LOG_TAIL_LINES)


def _notify(title: str, message: str) -> None:
    """Best-effort native Windows toast; falls back to a blocking popup if that fails so the
    user always gets *something* telling them the batch is done."""
    ps_script = f"""
$xml = @"
<toast><visual><binding template='ToastGeneric'>
<text>{title}</text><text>{message}</text>
</binding></visual></toast>
"@
try {{
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
  $doc = [Windows.Data.Xml.Dom.XmlDocument]::new()
  $doc.LoadXml($xml)
  $toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Peace Reels").Show($toast)
}} catch {{
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.MessageBox]::Show("{message}", "{title}") > $null
}}
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            timeout=20,
        )
    except Exception as e:
        log_lines.append(f"(notification failed: {e})")


def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(ROOT), text=True, capture_output=True)


def _pull_latest_rotation_state() -> None:
    """Sync data/state.json from GitHub before generating. The cloud scheduler
    (.github/workflows/publish.yml) advances and pushes its own copy of this file after every
    run; without this, a desktop batch continues from whatever stale rotation position this
    machine last saw and can walk the same early slot positions the cloud side already used,
    reposting the same script (this was confirmed as the cause of same-story repeats across
    cloud + desktop uploads)."""
    log_lines.append("--- syncing rotation state from GitHub ---")
    r = _git(["fetch", "origin"])
    if r.returncode != 0:
        log_lines.append(f"(git fetch failed, continuing with local rotation state: {r.stderr.strip()})")
        return
    r = _git(["checkout", "origin/main", "--", "data/state.json"])
    if r.returncode != 0:
        log_lines.append(f"(could not sync data/state.json from origin: {r.stderr.strip()})")


def _push_rotation_state() -> None:
    """Commit + push data/state.json after generating, mirroring the cloud workflow's own
    'Commit topic-rotation state' step, so the next run anywhere sees this batch's advance."""
    _git(["add", "data/state.json"])
    commit = _git(["commit", "-m", "Auto: advance topic rotation (desktop)"])
    if commit.returncode != 0:
        return  # nothing changed
    r = _git(["pull", "--rebase", "origin", "main"])
    if r.returncode != 0:
        log_lines.append(f"(git pull --rebase failed, rotation state not synced to GitHub: {r.stderr.strip()})")
        return
    r = _git(["push"])
    if r.returncode != 0:
        log_lines.append(f"(git push failed, rotation state not synced to GitHub: {r.stderr.strip()})")
    else:
        log_lines.append("--- rotation state synced to GitHub ---")


def _run_one(video_num: int, time_slot: str) -> bool:
    log_lines.append(f"--- video {video_num}/{VIDEOS_PER_RUN} started (slot={time_slot}) ---")
    try:
        proc = subprocess.Popen(
            [PYTHON, str(ROOT / "src" / "generate.py"), "--config", "config.yaml", "--time-slot", time_slot],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            log_lines.append(line.rstrip("\n"))
        code = proc.wait()
        if code != 0:
            log_lines.append(f"--- video {video_num} FAILED (exit {code}) ---")
        return code == 0
    except Exception as e:
        log_lines.append(f"--- video {video_num} FAILED: {e} ---")
        return False


def _run_job() -> None:
    with state_lock:
        state["running"] = True
        state["started_at"] = time.time()
        state["finished_at"] = None
        state["last_result"] = None
        state["last_error"] = None
        state["progress"] = f"0/{VIDEOS_PER_RUN}"
    log_lines.append(f"--- batch started {time.strftime('%Y-%m-%d %H:%M:%S')}: {VIDEOS_PER_RUN} videos ---")
    _pull_latest_rotation_state()

    succeeded = 0
    for i in range(1, VIDEOS_PER_RUN + 1):
        ok = _run_one(i, SLOT_CYCLE[(i - 1) % len(SLOT_CYCLE)])
        succeeded += 1 if ok else 0
        with state_lock:
            state["progress"] = f"{succeeded}/{VIDEOS_PER_RUN}"
        if ok:
            _push_rotation_state()
    failed = VIDEOS_PER_RUN - succeeded

    with state_lock:
        state["running"] = False
        state["finished_at"] = time.time()
        state["last_result"] = "success" if failed == 0 else ("error" if succeeded == 0 else "partial")
        state["last_error"] = None if failed == 0 else f"{failed}/{VIDEOS_PER_RUN} video(s) failed — see log"
    log_lines.append(f"--- batch finished: {succeeded}/{VIDEOS_PER_RUN} uploaded ---")

    summary = f"{succeeded}/{VIDEOS_PER_RUN} videos uploaded." + (f" {failed} failed." if failed else "")
    _notify("Peace Reels", summary)
    log_lines.append("--- shutting down ---")
    time.sleep(2)  # give the notification a moment to actually reach the OS before we die
    os._exit(0)


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
  .partial { background: #f5a62344; color: #b57300; }
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
  if (s.running) { badge.textContent = `Running… (${s.progress})`; badge.className = 'running'; }
  else if (s.last_result === 'success') { badge.textContent = `Done — ${s.progress} uploaded, shutting down`; badge.className = 'success'; }
  else if (s.last_result === 'partial') { badge.textContent = `Done — ${s.progress} uploaded (some failed), shutting down`; badge.className = 'partial'; }
  else if (s.last_result === 'error') { badge.textContent = 'Failed — shutting down'; badge.className = 'error'; }
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
