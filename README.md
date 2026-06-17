# 📹 Techtalk Short Creator

Automated YouTube Shorts pipeline: fetches news photos from Telegram channels, compiles them into a **structured 59-second 9:16 vertical video**, and schedules it to YouTube — all via GitHub Actions.

```
0–11s   MAIN     │ Hero image + Ken Burns + title badge + CC
11–26s  CLIP A   │ Jump-cut video + PiP wiggle overlay (beat-synced)
26–32s  FLASH    │ Flash image with wiggle + white burst
32–47s  CLIP B   │ Hype jump-cut + zoom-pulse sync beat
47–53s  SUMMARY  │ Summary image + centred CC caption
53–59s  OUTRO    │ CTA text + source credit
```

**Features:** continuous TTS narration · word-level CC captions · beat-synced wiggle (2–3 Hz, 8–20 px) · TTS ducking music (12 %) · motion blur transitions · brand logo watermark · duplicate-post guard · auto-scheduled YouTube publish.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Repository Setup](#repository-setup)
3. [Required Files](#required-files)
4. [GitHub Secrets](#github-secrets)
5. [Environment Variables Reference](#environment-variables-reference)
6. [GitHub Actions Workflow](#github-actions-workflow)
7. [Local Development](#local-development)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.10 + | GitHub-hosted runners ship 3.12 |
| FFmpeg | 4.4 + | `sudo apt install ffmpeg` |
| Telegram Bot | any | Must be admin of the source channel |
| Google Cloud project | — | YouTube Data API v3 enabled |

---

## Repository Setup

```bash
git clone https://github.com/<your-org>/<your-repo>.git
cd <your-repo>
```

### Directory layout

```
.
├── short_creator.py        # main script (this repo)
├── requirements.txt        # Python dependencies
├── clips/                  # ≥1 video file (.mp4/.mov/.mkv/.avi/.webm)
│   └── background_01.mp4   # used for Clip A (11–26s) and Clip B (32–47s)
├── music.mp3               # background music track
├── brand_logo.png          # brand watermark (PNG with transparency)
└── .github/
    └── workflows/
        └── create_short.yml
```

> **clips/** and **music.mp3** are required at runtime. Store them via Git LFS or download them in the workflow (see [workflow example](#github-actions-workflow)).

---

## Required Files

### `requirements.txt`

```text
requests
moviepy==1.0.3
Pillow
numpy
scipy
edge-tts
google-auth
google-auth-oauthlib
google-api-python-client
```

> `moviepy` v1 and v2 are both supported — the script auto-detects. Pin to `1.0.3` for stability on runners.

---

## GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** for each of the following.

### `TELEGRAM_TOKEN`

Your Telegram Bot token from [@BotFather](https://t.me/BotFather).

```
1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
```

### `TELEGRAM_CHANNELS`

JSON array of channel usernames (with `@`).

```json
["@xeonbitchannel", "@Techtalk66"]
```

### `YOUTUBE_CLIENT_SECRETS`

OAuth 2.0 credentials JSON from Google Cloud Console.

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services → Credentials**
2. Create an **OAuth 2.0 Client ID** (Desktop app)
3. Download the JSON, then run the one-time local auth flow:

```bash
pip install google-auth-oauthlib google-api-python-client
python3 - <<'EOF'
from google_auth_oauthlib.flow import InstalledAppFlow
import json

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

print(json.dumps({
    "token":         creds.token,
    "refresh_token": creds.refresh_token,
    "token_uri":     creds.token_uri,
    "client_id":     creds.client_id,
    "client_secret": creds.client_secret,
    "scopes":        list(creds.scopes),
}))
EOF
```

Paste the printed JSON as the secret value.

---

## Environment Variables Reference

All variables can be set as GitHub Actions `env:` entries or repository/environment secrets. Every variable has a sensible default.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_TOKEN` | **required** | Telegram bot token |
| `TELEGRAM_CHANNELS` | `["@xeonbitchannel"]` | JSON array of channels to scrape |
| `YOUTUBE_CLIENT_SECRETS` | **required** | OAuth credentials JSON |
| `MAX_TELEGRAM_POSTS` | `3` | Max photos fetched per channel per run |

### Video layout

| Variable | Default | Description |
|----------|---------|-------------|
| `CLIPS_DIR` | `clips` | Folder of background video clips |
| `MUSIC_OPTION` | `music.mp3` | Path or HTTP URL to background music |
| `LOGO_PATH` | `brand_logo.png` | Watermark PNG (transparent background) |
| `LOGO_POSITION` | `top-left` | `top-left` / `top-right` / `bottom-left` / `bottom-right` |
| `LOGO_WIDTH_RATIO` | `0.20` | Logo width as fraction of frame width |
| `LOGO_OPACITY` | `0.92` | Logo opacity (0–1) |
| `TG_CHANNEL_NAME` | `xeonbitchannel` | Shown in credits (`t.me/<name>`) |

### Timeline (seconds, must sum to `MAX_DURATION`)

| Variable | Default | Segment |
|----------|---------|---------|
| `DUR_MAIN` | `11.0` | 0–11s hero image |
| `DUR_CLIPA` | `15.0` | 11–26s jump-cut A |
| `DUR_FLASH` | `6.0` | 26–32s flash image |
| `DUR_CLIPB` | `15.0` | 32–47s jump-cut B |
| `DUR_SUMMARY` | `6.0` | 47–53s summary |
| `DUR_OUTRO` | `6.0` | 53–59s CTA/outro |
| `MAX_DURATION` | `59` | Total video length (seconds) |

### Audio

| Variable | Default | Description |
|----------|---------|-------------|
| `BG_MUSIC_VOL` | `0.12` | Background music volume (ducked under TTS) |
| `TTS_VOL` | `1.0` | TTS narration volume |
| `OUTRO_CTA` | `Follow the channel for latest news. Like & Subscribe!` | CTA spoken at end |
| `INTRO_LABEL` | `BREAKING` | Badge text on hero image (0–11s) |

### YouTube upload

| Variable | Default | Description |
|----------|---------|-------------|
| `TITLE_TEMPLATE` | `Video Short - {date}` | YouTube title template |
| `DESCRIPTION` | `Automated YouTube Short` | Video description prefix |
| `TAGS` | `["Shorts","Auto-generated"]` | JSON array of base tags |
| `PRIVACY_STATUS` | `private` | `private` / `public` / `unlisted` |
| `PLAYLIST_ID` | `PLKfhqWP2rL8LS6mS4eJk0sx43sD4x8TeV` | YouTube playlist to add video |
| `PUBLISH_DELAY_HOURS` | `1` | Hours until scheduled publish |
| `BRAND_HASHTAGS` | `["xeonbitchannel","techtalk66","cryptohieu.com","xeonbit24.com"]` | JSON array added to description |

---

## GitHub Actions Workflow

Create `.github/workflows/create_short.yml`:

```yaml
name: Create & Upload YouTube Short

on:
  schedule:
    - cron: "0 */6 * * *"   # every 6 hours
  workflow_dispatch:          # manual trigger from Actions tab

jobs:
  build-and-upload:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      # ── 1. Checkout ──────────────────────────────────────────────────
      - name: Checkout repository
        uses: actions/checkout@v4

      # ── 2. System dependencies ────────────────────────────────────────
      - name: Install FFmpeg and fonts
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y ffmpeg fonts-dejavu-core fonts-freefont-ttf

      # ── 3. Python ─────────────────────────────────────────────────────
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install Python dependencies
        run: pip install -r requirements.txt

      # ── 4. Restore published-IDs cache ────────────────────────────────
      - name: Restore published IDs cache
        uses: actions/cache@v4
        with:
          path: .published_ids.json
          key: published-ids-${{ github.ref_name }}
          restore-keys: published-ids-

      # ── 5. Download large assets from release (optional) ──────────────
      # If you store clips and music as GitHub release assets, uncomment:
      #
      # - name: Download background clips
      #   run: |
      #     mkdir -p clips
      #     curl -L -o clips/bg1.mp4 \
      #       "https://github.com/${{ github.repository }}/releases/download/assets/bg1.mp4"
      #
      # - name: Download background music
      #   run: |
      #     curl -L -o music.mp3 \
      #       "https://github.com/${{ github.repository }}/releases/download/assets/music.mp3"

      # ── 6. Run the script ─────────────────────────────────────────────
      - name: Generate and upload short
        env:
          TELEGRAM_TOKEN:          ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHANNELS:       ${{ secrets.TELEGRAM_CHANNELS }}
          YOUTUBE_CLIENT_SECRETS:  ${{ secrets.YOUTUBE_CLIENT_SECRETS }}
          # --- optional overrides ---
          MAX_TELEGRAM_POSTS:      "3"
          PRIVACY_STATUS:          "private"
          PUBLISH_DELAY_HOURS:     "1"
          PLAYLIST_ID:             ${{ vars.PLAYLIST_ID }}
          TG_CHANNEL_NAME:         "xeonbitchannel"
          INTRO_LABEL:             "BREAKING"
          OUTRO_CTA:               "Follow Techtalk for the latest crypto news!"
          LOGO_POSITION:           "top-left"
          BG_MUSIC_VOL:            "0.12"
        run: python short_creator.py

      # ── 7. Save published-IDs cache ───────────────────────────────────
      - name: Save published IDs cache
        if: always()
        uses: actions/cache@v4
        with:
          path: .published_ids.json
          key: published-ids-${{ github.ref_name }}-${{ github.run_id }}
```

### Caching `.published_ids.json`

The workflow caches `.published_ids.json` between runs so the same Telegram post is never compiled twice. The cache key includes the run ID on save so each successful run creates a new snapshot while still restoring the most recent one on the next run.

### Storing large assets

GitHub repositories have a 100 MB file-size limit. For clips and music:

**Option A — GitHub Releases (recommended)**
Upload files as release assets once, then download them in the workflow with `curl` (see commented step above).

**Option B — Git LFS**
```bash
git lfs install
git lfs track "clips/*.mp4" "music.mp3"
git add .gitattributes clips/ music.mp3
git commit -m "Add LFS assets"
git push
```
Add `lfs: true` to the `actions/checkout` step:
```yaml
- uses: actions/checkout@v4
  with:
    lfs: true
```

**Option C — External storage (S3 / GCS / R2)**
Store assets in object storage and download at workflow start using `aws s3 cp` or `gsutil cp`.

---

## Local Development

```bash
# 1. Clone and enter the repo
git clone https://github.com/<your-org>/<your-repo>.git
cd <your-repo>

# 2. Install system deps (Debian/Ubuntu)
sudo apt-get install ffmpeg fonts-dejavu-core

# 3. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 4. Install Python packages
pip install -r requirements.txt

# 5. Set environment variables
export TELEGRAM_TOKEN="your_bot_token"
export TELEGRAM_CHANNELS='["@yourchannel"]'
export YOUTUBE_CLIENT_SECRETS='{ ...oauth json... }'

# 6. Add required assets
mkdir -p clips
cp /path/to/your/clip.mp4 clips/
cp /path/to/your/music.mp3 music.mp3
cp /path/to/your/logo.png  brand_logo.png   # optional

# 7. Run
python short_creator.py
```

The output video is written to `output_short.mp4` and deleted after a successful upload.

---

## Troubleshooting

### `No new suitable content found`
- The bot must be a member (or admin) of the Telegram channel.
- `getUpdates` only returns the last ~100 updates. For high-traffic channels, increase `MAX_TELEGRAM_POSTS` or switch to a webhook/polling setup.
- Check `.published_ids.json` — if all recent posts are already listed, the run exits early by design.

### `No video clips found in 'clips/'`
The script will fall back to static images for Clip A and Clip B. Add `.mp4` files to the `clips/` directory (or set `CLIPS_DIR`) for the full jump-cut effect.

### YouTube quota exceeded
The YouTube Data API v3 grants **10,000 units/day** by default. One video upload costs ~1,600 units. If you exceed the daily limit, the script exits with code 1 and logs a message. Quota resets at **midnight Pacific Time**. To request a higher quota: [Google Cloud Console → APIs & Services → YouTube Data API v3 → Quotas](https://console.cloud.google.com).

### `atempo` out of range
FFmpeg's `atempo` filter only accepts values between **0.5 and 2.0** per pass. The script chains two passes for speeds above 2.0 (e.g. `atempo=2.0,atempo=1.3`). If your caption text is extremely short relative to 59 seconds, the TTS may play at minimum speed (`TTS_SPEED_MIN=1.10`) and leave silence at the end — add more caption content or lower `TTS_SPEED_MIN`.

### Fonts not rendering
Install DejaVu or FreeSans fonts:
```bash
# Debian/Ubuntu
sudo apt-get install fonts-dejavu-core fonts-freefont-ttf

# macOS — Helvetica is the auto-detected fallback (already present)

# Windows — Arial is the auto-detected fallback (already present)
```

### `scipy` not found (wiggle disabled)
`scipy` is needed to extract the beat amplitude array. Install it:
```bash
pip install scipy
```
Without it the script logs a warning and disables wiggle/zoom-pulse effects but still produces a valid video.
