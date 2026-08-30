# CLAUDE.md — pin2shorts

Guidance for Claude Code (or any coding agent) working in this repo.

## What this is

Pipeline that takes **public Pinterest video pins** and outputs **YouTube-Shorts-ready MP4s**
(1080×1920, 30 fps, trimmed, audio track always present, optional handle watermark), plus
optional upload to YouTube via the Data API.

```
queue.txt  →  automate.py  →  pin2shorts.py  →  downloads/ (raw)  →  shorts/ (ready)
                                                    ↓
                              manifest.csv (every pin) + uploads.csv (title/desc/tags)
                                                    ↓
                                        upload.py  →  YouTube
```

## Commands

```bash
pip install -r requirements.txt          # flask, yt-dlp, static-ffmpeg, Pillow
python3 pin2shorts.py --selftest         # verify network + ffmpeg end to end (~30s)
python3 automate.py --add "<pin url>"    # queue links
python3 automate.py --once               # drain queue, render, write metadata
python3 automate.py --status             # queued / rendered / failed / pending uploads
python3 app.py                           # web UI on :8000
python3 upload.py --auth                 # one-time OAuth (needs client_secret.json)
python3 upload.py --from-csv uploads.csv --privacy private
```

## Files

| File | Role |
|---|---|
| `pin2shorts.py` | Core library + CLI. yt-dlp download, ffmpeg render, `manifest.csv` dedupe. |
| `automate.py` | Queue runner (`--once` / `--watch`), generates titles/descriptions into `uploads.csv`. |
| `app.py`, `templates/index.html` | Flask UI with live progress log, previews, ZIP export. |
| `upload.py` | YouTube Data API v3 resumable upload; marks rows `uploaded=yes`. |
| `config.json` | Render defaults, hashtags, title/description templates. |
| `queue.txt` / `queue.done.txt` | Inbox / processed links. |
| `manifest.csv` / `uploads.csv` | State. Both are append-only truth; don't rewrite by hand. |

## Conventions when editing

- Python 3.10+, stdlib + yt-dlp/flask/Pillow only. No new heavy deps without asking.
- ffmpeg is discovered at runtime (`find_ffmpeg()`): static-ffmpeg → system ffmpeg →
  imageio-ffmpeg. Never hardcode a path. `drawtext` may be missing — watermark falls back to a
  Pillow-rendered PNG overlay; keep that fallback working.
- `process()` is the single entry point for downloading + rendering; CLI, web UI and queue runner
  all call it. Add features there, not in three places.
- Dedupe is by Pinterest pin id in `manifest.csv`. Keep it idempotent — the runner is meant to be
  safe to call every minute.
- Every new row must populate the manifest fields in `FIELDS`.
- Run `python3 pin2shorts.py --selftest` after touching the render path; run
  `python3 automate.py --once` on a real pin after touching the queue path.

## Guardrails (do not change without asking)

- Public pins only. No private/secret boards, no login-wall bypass, no scraping behind auth
  (user-supplied `--cookies` for their own account is the one exception, already implemented).
- No watermark removal, no DRM circumvention.
- The tool never auto-publishes publicly: upload default privacy is `private`.

## Useful prompts

- "Add a `--min-duration` filter so clips under N seconds are skipped."
- "Add a face-cam / intro clip option to the render filter."
- "Generate Hindi titles with an LLM from the pin title and write them into uploads.csv."
- "Add Speechify/Hindi TTS voiceover over the clip (this is what makes content non-reused)."
- "Add a cron/launchd/systemd unit that runs `automate.py --once` every 15 minutes."
