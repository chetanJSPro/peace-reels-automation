# pin2shorts — Pinterest → YouTube Shorts toolkit

Downloads public Pinterest video pins and renders them into Shorts-ready MP4s:
**1080×1920, 30 fps, ≤59 s (configurable), audio track guaranteed, faststart, optional handle watermark.**

```
pin2shorts/
├── pin2shorts.py        # CLI + library (download, dedupe, render, manifest)
├── app.py               # local web UI  →  python3 app.py  →  http://localhost:8000
├── templates/index.html
├── links.txt            # batch file: one URL per line
├── manifest.csv         # every pin you've touched (auto-written)
├── downloads/           # raw pins + .jpg thumbnail + .description
└── shorts/              # ready-to-upload MP4s
```

## Install

```bash
pip install -r requirements.txt     # flask, yt-dlp, static-ffmpeg
```

If `static-ffmpeg` can't fetch its binary, install ffmpeg yourself (`sudo apt install ffmpeg`)
or `pip install imageio-ffmpeg` (lighter build, no text watermark — falls back to a PNG overlay
via Pillow).

## CLI

```bash
# one pin
python3 pin2shorts.py "https://www.pinterest.com/pin/123456789012345/"

# with your handle burned in, trimmed to 45s, hard crop instead of blur fill
python3 pin2shorts.py "https://pin.it/abc123" --watermark "@bhagtivideotop" --mode crop --max-duration 45

# batch of links
python3 pin2shorts.py --batch links.txt

# whole board / collection (first 20 video pins)
python3 pin2shorts.py --board "https://www.pinterest.com/username/board-name/" --limit 20

# see everything you've downloaded
python3 pin2shorts.py --list
```

| flag | meaning |
|---|---|
| `--mode blur\|crop\|stretch` | how a non-vertical video is fitted. **blur** (default) keeps the whole frame on a blurred backdrop; **crop** fills 9:16 and cuts the sides; **stretch** distorts. |
| `--max-duration N` | trim to N seconds. Shorts accept up to 180 s; 25–60 s is the engagement sweet spot. |
| `--watermark "@handle"` | bottom-right watermark. |
| `--no-fade` | disable fade in/out. |
| `--limit N` | cap clips taken from a board. |
| `--cookies cookies.txt` | only for boards you need to be logged in to see (your own account). |
| `--redo` | re-download even if the pin id is already in `manifest.csv`. |

## AI-Bhagwan pipeline (keyword discovery + auto-publish)

Beyond feeding it explicit links, this checkout also has keyword-based discovery and an
automatic YouTube publish step wired up for the "AI Hanuman / AI Shiva / AI Krishna ..."
devotional-AI-art niche:

```bash
python3 pinterest_search.py "ai hanuman" "ai shiva" --limit 10   # test the search, no side effects
python3 automate.py --discover                                  # search config.json's "keywords", queue new pins
python3 run_pipeline.py --render-limit 6 --upload-limit 6        # discover -> render -> publish, one command
python3 publish.py --dry-run                                     # see what would upload without calling the API
```

- `config.json`'s `keywords` list drives `--discover`; edit it to add/remove deities or niches.
- `pinterest_search.py` calls Pinterest's own search endpoint (no login) — it's an
  undocumented internal API, so it can break if Pinterest changes their frontend; it
  fails soft (returns 0 results for that keyword) rather than crashing the run.
- `publish.py` does **not** reimplement OAuth — it shells out to the main repo's
  `../src/uploader.py` using `../config.ai_bhagwan.yaml`, so this pipeline publishes to
  a **separate YouTube channel/token** from the repo's original-content pipeline
  (`config.yaml`). See `config.ai_bhagwan.yaml` for the one-time OAuth login step this
  requires before it can upload anything, and why it's a separate channel on purpose.
- `.github/workflows/publish_ai_bhagwan.yml` runs this on a schedule via GitHub Actions,
  independent of the local machine — needs the `YOUTUBE_CLIENT_SECRET_JSON` and
  `YOUTUBE_TOKEN_JSON_AI_BHAGWAN` repo secrets (see that workflow file).
- **Read `PLAYBOOK.md` before turning this on for real** — it lays out the copyright and
  "reused content" risk of raw Pinterest reposts in detail. AI-generated pins carry lower
  *copyright* risk than footage of real people/places, but YouTube's reused-content
  channel review is a separate, stricter bar than copyright, and it isn't AI-content-aware.

## Automation (headless / cron / Claude Code)

```bash
python3 pin2shorts.py --selftest                    # verify install end to end
python3 automate.py --add "https://pin.it/abc123"   # queue links (repeat anytime)
python3 automate.py --once                          # drain queue → render → write metadata
python3 automate.py --watch                         # poll every poll_seconds (config.json)
python3 automate.py --status                        # queued / rendered / failed / pending
```

`automate.py` reads `config.json` (fit mode, trim, handle, hashtags, title/description
templates), skips pins already in `manifest.csv`, moves processed links to `queue.done.txt`,
and writes ready-to-paste titles + descriptions + tags into `uploads.csv`. It's idempotent, so
it's safe to run from cron every few minutes:

```cron
*/15 * * * * cd /path/to/pin2shorts && /usr/bin/python3 automate.py --once >> cron.log 2>&1
```

### Optional: upload to YouTube

Upload is deliberately **not** part of the render loop — you should look at things before they
go public.

```bash
pip install google-auth-oauthlib google-api-python-client
# Google Cloud console → enable YouTube Data API v3 → OAuth client (Desktop) → save as client_secret.json
python3 upload.py --auth                                   # one-time browser sign-in
python3 upload.py --from-csv uploads.csv --dry-run         # preview
python3 upload.py --from-csv uploads.csv --privacy private # upload; rows marked uploaded=yes
```

Default privacy is `private` so you can review and schedule in Studio. Use
`--privacy public` only when you're sure about the rights.

## Web UI

```bash
python3 app.py          # then open http://localhost:8000
```

Paste links → pick fit mode / duration / handle → **Download & render**. Live progress log,
inline video previews, **Download all (.zip)**, and `manifest.csv` export.

## Accepted links

- `https://www.pinterest.com/pin/<id>/` (also `.co.uk`, `.ca`, `.de`, `in.pinterest.com`, …)
- `https://pin.it/<short>` — the mobile share link
- `https://www.pinterest.com/<user>/<board>/` — every video pin on that board

## What it does *not* do

- No private/secret boards, no login-wall bypass, no scraping behind auth (except your own
  cookies if you supply them).
- Image-only pins and GIFs are skipped — they have no video stream.
- It doesn't upload to YouTube. Upload from Studio (desktop or phone).

## House rules

Only download content you have the right to use. Most Pinterest videos are themselves reposts.
If an owner asks you to take something down, delete it. Read `PLAYBOOK.md` before you build a
channel on other people's clips.
