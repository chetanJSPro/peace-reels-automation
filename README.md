# Peace Reels Automation — free + legitimate YouTube Shorts/meditation video system

This project creates videos similar in **format** to the channel you showed: Indian spiritual/nature views, a top location/pin label, burned-in subtitles, a human-type AI voice, and YouTube-ready metadata.

It is designed to be legitimate:

- It does **not** download or reuse another channel's videos.
- It does **not** clone a creator's voice.
- It uses original scripts from your own idea bank or a local LLM.
- It uses your own footage or free/licensed stock footage with a rights log.
- Upload automation uses the official YouTube Data API only.

> No tool can guarantee income. The goal is to build a monetizable, policy-safe content pipeline that avoids reused/mass-produced content problems.

---

## 1) What the research found

See: [`research/channel_analysis.md`](research/channel_analysis.md)

Quick summary:

- The reference channel is **SHANTIMARG 20M**.
- Captured from public tabs:
  - 56 long videos in `research/shantimarg_videos.csv`
  - 53 Shorts in `research/shantimarg_shorts.csv`
- Shorts are the strongest format. The biggest winners use:
  - Rishikesh/Ganga/Kashi/nature visuals
  - calm emotional Hinglish text
  - themes: ekant, silence, shanti, detachment, sukoon
  - top location label + big subtitles
  - short, shareable thoughts

Do not copy the channel. Use this as a **format and market research reference** only.

---

## 2) Free tool stack

| Need | Free legitimate option used here | Notes |
|---|---|---|
| Footage | Your own clips, Pexels API, Pixabay video | Keep source URLs. Do not mislabel locations. |
| Voiceover | Kokoro TTS local model | Runs locally; Hindi voices available; check license before monetization. |
| Captions/subtitles | ASS/SRT generated from script timing | Burned in + uploadable SRT. |
| Top pin/location text | ASS overlay style | Template: `📍 RISHIKESH | UTTARAKHAND`. |
| Editing/rendering | FFmpeg | Free/open-source command-line video engine. |
| Thumbnail | Python Pillow | Generates a simple vertical thumbnail. |
| Upload | Official YouTube Data API | Free, but public automation needs API audit. See upload section. |

---

## 3) Install

### Linux / Ubuntu

```bash
sudo apt update
sudo apt install -y ffmpeg espeak-ng fonts-noto-core fonts-noto-color-emoji
cd peace_reels_automation
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
cp config.example.yaml config.yaml
```

### Windows

1. Install Python 3.11+.
2. Install FFmpeg and add it to PATH.
   - `winget install Gyan.FFmpeg`
3. Install eSpeak NG.
   - `winget install eSpeak-NG.eSpeak-NG`
4. Install a Devanagari font such as Noto Sans Devanagari or use Windows `Nirmala UI`.
5. In PowerShell:

```powershell
cd peace_reels_automation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
copy config.example.yaml config.yaml
```

### macOS

```bash
brew install ffmpeg espeak-ng
cd peace_reels_automation
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
cp config.example.yaml config.yaml
```

---

## 4) Get free footage legally

### Option A — Pixabay API (recommended for India-specific footage)

Pexels' video library is thin on Indian locations (Rishikesh, Varanasi ghats, Himalayas). Pixabay's is noticeably deeper for this niche and carries the same free-to-use/modify license, so it's tried first when both keys are set.

1. Create a free API key: <https://pixabay.com/api/docs/>
2. Put it in `.env`:

```env
PIXABAY_API_KEY=your_key_here
```

The project auto-adds Pixabay creator/page credits to `metadata.json` and `description.txt`, same as Pexels.

### Option B — Pexels API

1. Create a free API key: <https://www.pexels.com/api/>
2. Put it in `.env`:

```env
PEXELS_API_KEY=your_key_here
```

Pexels videos are free to use and modification is allowed, but still keep credits/source URLs in your description and rights log. This project automatically adds Pexels creator/page credits to `metadata.json` and `description.txt`. Used to top up footage when Pixabay results run short.

### Option C — local/own footage

Put videos here:

```text
assets/videos/
```

The script will use these first, before either stock API.

Note: Instagram (or any social platform) is **not** a legal footage source here — Reels/videos posted by other creators are their copyright, and scraping/downloading them for reuse violates the platform's Terms of Use regardless of automation method. Only use IG content you personally own/posted.

### Option C — background music

Put one legitimate music track here:

```text
assets/music/background.mp3
```

Free sources to consider:

- YouTube Audio Library
- Pixabay Music
- Mixkit Music
- Your own music

Always keep the exact music source/artist/license in your rights log. The generated metadata reminds you to add the exact music credit before publishing.

---

## 5) Configure the style

Edit `config.yaml`.

Most important settings:

```yaml
style:
  location_label: "RISHIKESH | UTTARAKHAND"
  caption_font: "Noto Sans Devanagari"
  caption_font_size: 76
  use_location_pin: true

voice:
  engine: "kokoro"
  lang_code: "h"
  voice_id: "hm_omega"
  speed: 0.94
```

Hindi Kokoro voices commonly used:

- `hf_alpha`
- `hf_beta`
- `hm_omega`
- `hm_psi`

For the top pin text, only use a specific place if the footage is really that place. If unsure, use generic labels:

- `GANGA GHAT | INDIA`
- `NATURE | INNER PEACE`
- `HIMALAYAN MORNING`

---

## 6) Generate a video

```bash
python src/generate.py --config config.yaml --topic-index 0
```

Output appears in:

```text
output/YYYYMMDD_HHMMSS_topic_name/
  final_video.mp4
  thumbnail.jpg
  narration.wav
  captions_burnin.ass
  captions_upload.srt
  script.txt
  metadata.json
  description.txt
```

Try different topics:

```bash
python src/generate.py --config config.yaml --topic-index 1
python src/generate.py --config config.yaml --topic-index 2
python src/generate.py --config config.yaml --topic-index 3
```

Dry run without rendering:

```bash
python src/generate.py --config config.yaml --topic-index 0 --dry-run
```

---

## 7) Add more original content ideas

Edit:

```text
data/ideas.csv
```

Each row has:

- `topic`
- `hook`
- `line1` to `line5`
- `cta`
- `title_base`

Keep lines short. Shorts captions should be readable in 1–3 seconds.

Good formula:

```text
Hook: Ekant me hi shanti hai
Line 1: Jab duniya bahut tez lage, thoda ruk jao.
Line 2: Ganga ki tarah behna seekho, har baat ko pakadna nahi.
Line 3: Jo cheez tumhari nahi, usey jaane do.
Line 4: Shanti kisi jagah nahi, tumhare andar ka faisla hai.
Line 5: Aaj bas ek saans dheere lo, aur khud ko maaf karo.
CTA: Save this for a peaceful moment.
```

Avoid:

- guaranteed miracles
- medical claims
- fake guru quotes
- copying Osho/Premanand/Buddha quotes without verifying source/license
- “third eye open in 5 minutes” type misleading promises

---

## 8) Optional: use a local LLM for scripts

This is still free if you run it locally with Ollama.

1. Install Ollama: <https://ollama.com/>
2. Pull a model:

```bash
ollama pull llama3.1:8b
```

3. In `.env`:

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```

4. In `config.yaml`:

```yaml
content:
  use_ollama: true
```

The prompt tells the local LLM to generate original Hinglish/Hindi and avoid medical/miracle claims. Still review manually before publishing.

---

## 9) Upload options

### Safest fully free method at the beginning: manual upload

1. Open YouTube Studio.
2. Upload `final_video.mp4`.
3. Add `thumbnail.jpg`.
4. Paste `description.txt`.
5. Upload `captions_upload.srt` as subtitles.
6. Publish or schedule.

This is safest while your API project is not audited.

### Fully automated upload: official YouTube Data API

This project includes `src/uploader.py` using the official YouTube Data API.

Setup outline:

1. Create a Google Cloud project.
2. Enable YouTube Data API v3.
3. Configure OAuth consent screen.
4. Create OAuth Desktop credentials.
5. Download as `client_secret.json` into the project root.
6. In `config.yaml`:

```yaml
upload:
  enabled: true
  privacy_status: "private"
```

7. Generate:

```bash
python src/generate.py --config config.yaml --topic-index 0
```

Or upload an existing generated metadata file:

```bash
python src/uploader.py output/your_job/metadata.json --config config.yaml
```

Important: Google states that videos uploaded through unverified API projects created after 2020-07-28 are restricted to private viewing mode until the API project passes a compliance audit. Do not use browser bots or unofficial upload bypasses; those can violate terms and risk the channel.

---

## 9a) Free 24x7 cloud automation (GitHub Actions)

`.github/workflows/publish.yml` runs the whole pipeline on GitHub's free hosted runners on a
schedule, so it keeps posting even when your PC is off. It generates a video, uploads it to
YouTube, and (if Instagram is turned on) publishes it as a Reel too.

Cadence: 2 videos/day at 07:30 and 20:30 IST — generally the two highest-engagement windows for
Indian Shorts/Reels audiences (morning phone-check, evening wind-down). Edit the `cron:` lines in
the workflow to change this (cron is UTC; IST = UTC + 5:30).

### One-time setup

1. Push this repo to GitHub. It should be **public** — Instagram's API can only fetch video from
   a public URL, and the free Actions minutes are unlimited on public repos. This is safe: `.env`
   and the `youtube/` folder (real credentials) are git-ignored and never enter the repo; every
   secret lives only in GitHub's encrypted Actions secrets below.

   ```bash
   git remote add origin https://github.com/<you>/<repo>.git
   git branch -M main
   git push -u origin main
   ```

2. In the repo, go to **Settings → Secrets and variables → Actions** and add these **Secrets**:

   | Secret | Value |
   |---|---|
   | `PIXABAY_API_KEY` | from `.env` |
   | `PEXELS_API_KEY` | from `.env` |
   | `YOUTUBE_CLIENT_SECRET_JSON` | full contents of `youtube/client_secret.json` |
   | `YOUTUBE_TOKEN_JSON` | full contents of `youtube/token.json` |
   | `IG_BUSINESS_ACCOUNT_ID` | only needed once Instagram is set up (below) |
   | `IG_ACCESS_TOKEN` | only needed once Instagram is set up (below) |

3. Test it: **Actions tab → Peace Reels 24x7 automation → Run workflow** (this is the
   `workflow_dispatch` trigger — no need to wait for the schedule). Watch the logs for errors
   before trusting the unattended schedule.

4. `data/state.json` (topic rotation position) is committed back to the repo automatically after
   every run, so the schedule never repeats the same idea twice in a row even across runs.

## 9b) Instagram automation (official Graph API, no bots)

Meta's Content Publishing API is the only ToS-compliant way to post Reels automatically. It
needs real setup in Meta's own dashboard that only you can do (it requires your Facebook login):

1. Convert (or create) an Instagram account to **Professional (Business or Creator)**, and link it
   to a **Facebook Page** you control — required by the API, done inside the Instagram app under
   Settings → Account type.
2. Create an app at <https://developers.facebook.com/apps/> → add the **Instagram Graph API**
   product.
3. In Graph API Explorer (or via the app's token tool), generate a User/Page token with the
   `instagram_basic`, `instagram_content_publish`, and `pages_show_list` permissions, then exchange
   it for a **long-lived token** (~60 days; the short-lived one from Explorer expires in ~1 hour).
   Meta's "Access Token Debugger" tool shows the expiry and lets you extend it.
4. Get your Instagram Business Account ID: `GET /me/accounts` → note the Page ID → 
   `GET /<page-id>?fields=instagram_business_account`.
5. Add `IG_BUSINESS_ACCOUNT_ID` and `IG_ACCESS_TOKEN` as repo secrets (table above).
6. Turn it on: **Settings → Secrets and variables → Actions → Variables tab** → add
   `INSTAGRAM_ENABLED` = `true`. (It's a repo *variable*, not a secret, because the workflow needs
   to check it before the job even starts.)

Long-lived tokens expire (~60 days) and need refreshing — Meta doesn't offer a silent
refresh-token flow like Google's for this token type, so you'll periodically need to regenerate
and update the `IG_ACCESS_TOKEN` secret by hand.

---

## 10) Monetization plan

### Phase 1 — first 30 days

Goal: find winning hooks.

- Publish 1–2 Shorts/day.
- Test 5 content buckets:
  1. Ekant/silence
  2. Rishikesh/Ganga sukoon
  3. Detachment/letting go
  4. Karma/patience
  5. Morning/Brahma Muhurta routine
- Track:
  - first 3 seconds retention
  - average view duration
  - replays
  - saves/shares
  - subscriber conversion

### Phase 2 — days 31–90

Goal: build a real channel, not AI spam.

- Double down on top 2 buckets.
- Add one weekly 8–15 minute guided meditation/breathing video.
- Add a pinned comment: free PDF/checklist/community link.
- Start collecting emails or community members.

### Phase 3 — income stack

Do not rely only on Shorts RPM. Add:

1. YouTube Partner Program after eligibility.
2. Affiliate links with disclosure:
   - meditation cushion
   - yoga mat
   - spiritual books
   - travel essentials for Rishikesh/Varanasi
3. Digital products:
   - 7-day calm journal PDF
   - guided breathing audio pack
   - Rishikesh peaceful itinerary
4. Services/community:
   - paid WhatsApp/Telegram meditation group
   - live guided meditation sessions

Always disclose affiliate links and paid products.

---

## 11) Policy-safe checklist before every upload

- [ ] Script is original and reviewed by you.
- [ ] No fake medical/spiritual guarantees.
- [ ] No copied voice from another creator.
- [ ] Footage source is allowed for YouTube/commercial use.
- [ ] Music source is allowed for YouTube/commercial use.
- [ ] Location label is accurate or generic.
- [ ] Description includes rights/source credits.
- [ ] If you used realistic synthetic/altered content, disclose it in YouTube Studio/API.
- [ ] Video is not just a low-effort stock compilation; it has an original message, edit, narration, and caption design.

---

## 12) Files in this project

```text
README.md
config.example.yaml
.env.example
requirements.txt
assets/
  README.md
  videos/
  music/
data/
  ideas.csv
  locations.csv
research/
  channel_analysis.md
  shantimarg_videos.csv
  shantimarg_shorts.csv
src/
  generate.py
  uploader.py
  pexels.py
  tts.py
  captions.py
  video_builder.py
  content.py
  utils.py
```

---

## 13) Next improvements

- Add Whisper/faster-whisper for word-level subtitles instead of approximate line timing.
- Add automatic A/B title generation.
- Add YouTube Analytics CSV ingestion to rank hooks.
- Add a rights database for every downloaded asset.
- Add a web UI for non-coders.
