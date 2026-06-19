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

# 📱 Bilingual YouTube Shorts Creator

Automatically fetch images and captions from Telegram channels, render them into vertical YouTube Shorts (1080×1920) with TTS narration, background music, and Ken Burns / jump-cut effects — then upload to YouTube on a schedule.

**Two fully independent pipelines run in parallel:**

| Pipeline | Telegram source | YouTube target | TTS voice |
|---|---|---|---|
| 🇬🇧 English | `@xeonbitchannel` | EN channel | `en-SG-LunaNeural` |
| 🇻🇳 Vietnamese | `@CryptoHieuQua` | VI channel | `vi-VN-HoaiMyNeural` |

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Fork & Clone the Repository](#2-fork--clone-the-repository)
3. [Prepare Your Repository Files](#3-prepare-your-repository-files)
4. [Get a Telegram Bot Token](#4-get-a-telegram-bot-token)
5. [Set Up YouTube OAuth Credentials](#5-set-up-youtube-oauth-credentials)
6. [Configure GitHub Secrets](#6-configure-github-secrets)
7. [Configure GitHub Variables (Optional)](#7-configure-github-variables-optional)
8. [Add the Workflow File](#8-add-the-workflow-file)
9. [Run the Workflow](#9-run-the-workflow)
10. [Schedule (Automatic Runs)](#10-schedule-automatic-runs)
11. [Customisation Reference](#11-customisation-reference)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

Before you start, make sure you have:

- A **GitHub account** (free tier is fine)
- A **Telegram Bot** with access to both channels
- Two **Google / YouTube accounts** — one per language channel
- `brand_logo.png` — your brand logo (PNG with transparency recommended)
- `music.mp3` — background music file (royalty-free)
- Optionally, a `clips/` folder containing `.mp4` / `.mov` B-roll video files

---

## 2. Fork & Clone the Repository

### 2a. Create a new GitHub repository

1. Go to [github.com/new](https://github.com/new)
2. Name it, e.g. `bilingual-shorts-creator`
3. Set visibility to **Private** (recommended — your secrets stay safe)
4. Click **Create repository**

### 2b. Clone it locally

```bash
git clone https://github.com/YOUR_USERNAME/bilingual-shorts-creator.git
cd bilingual-shorts-creator
```

---

## 3. Prepare Your Repository Files

Your repository must contain the following files:

```
bilingual-shorts-creator/
├── short_creator_bilingual.py   ← main Python script
├── .github/
│   └── workflows/
│       └── bilingual_short.yml  ← GitHub Actions workflow
├── brand_logo.png               ← your brand logo
├── music.mp3                    ← background music
└── clips/                       ← (optional) B-roll video clips
    ├── clip1.mp4
    └── clip2.mp4
```

### 3a. Add the Python script

Copy `short_creator_bilingual.py` into the root of your repository.

### 3b. Create the workflow directory and file

```bash
mkdir -p .github/workflows
```

Copy `bilingual_short.yml` into `.github/workflows/`.

### 3c. Add your brand assets

Place `brand_logo.png` and `music.mp3` in the repository root.

> **Logo tip:** Use a PNG with a transparent background at roughly 500×200 px. The script will scale it to 20% of the video width automatically.

### 3d. Commit and push everything

```bash
git add .
git commit -m "Initial setup"
git push origin main
```

---

## 4. Get a Telegram Bot Token

### 4a. Create a bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Follow the prompts — choose a name and username
4. Copy the **token** (looks like `123456789:ABCdef...`)

### 4b. Add the bot to your channels

For **each** of the two Telegram channels (`@xeonbitchannel` and `@CryptoHieuQua` or your own):

1. Open the channel settings → **Administrators**
2. Add your bot as an administrator
3. Grant at minimum: **Read Messages** permission

### 4c. Verify the bot can see updates

Open this URL in a browser (replace `YOUR_TOKEN`):

```
https://api.telegram.org/botYOUR_TOKEN/getUpdates
```

You should see recent channel posts in the JSON response.

---

## 5. Set Up YouTube OAuth Credentials

You need **separate credentials** for each YouTube channel (EN and VI). Repeat these steps twice.

### 5a. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown → **New Project**
3. Name it, e.g. `shorts-creator-en`, then click **Create**

### 5b. Enable the YouTube Data API

1. In your project, go to **APIs & Services → Library**
2. Search for **YouTube Data API v3**
3. Click **Enable**

### 5c. Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** → **Create**
3. Fill in the required fields (App name, support email)
4. Under **Scopes**, click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube`
5. Add your Google account email under **Test users**
6. Save and continue through all steps

### 5d. Create OAuth 2.0 credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth 2.0 Client IDs**
3. Application type: **Desktop app**
4. Name it, e.g. `shorts-creator-en-desktop`
5. Click **Create**
6. Click **Download JSON** — this is your client secrets file

### 5e. Generate a refresh token locally

The workflow needs a refresh token so it can upload without interactive login. Run this on your local machine:

```bash
pip install google-auth-oauthlib google-api-python-client

python - <<'EOF'
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

flow = InstalledAppFlow.from_client_secrets_file("client_secrets_en.json", SCOPES)
creds = flow.run_local_server(port=0)

output = {
    "client_id":     creds.client_id,
    "client_secret": creds.client_secret,
    "refresh_token": creds.refresh_token,
    "token_uri":     "https://oauth2.googleapis.com/token",
    "scopes":        list(creds.scopes),
}
print(json.dumps(output, indent=2))
EOF
```

A browser window will open — log in with the YouTube account you want to upload to. Copy the printed JSON — you will paste it as a GitHub Secret in the next step.

Repeat this process for the **VI channel** using `client_secrets_vi.json`.

---

## 6. Configure GitHub Secrets

Go to your repository on GitHub → **Settings → Secrets and variables → Actions → Secrets**.

Click **New repository secret** for each of the following:

| Secret name | Value |
|---|---|
| `TELEGRAM_TOKEN` | Your Telegram bot token from Step 4 |
| `YOUTUBE_CLIENT_SECRETS_EN` | Full JSON from Step 5e for the EN channel |
| `YOUTUBE_CLIENT_SECRETS_VI` | Full JSON from Step 5e for the VI channel |

> **Important:** Paste the entire JSON object as the secret value — including the curly braces `{ }`.

Example value for `YOUTUBE_CLIENT_SECRETS_EN`:
```json
{
  "client_id": "123456789-abc.apps.googleusercontent.com",
  "client_secret": "GOCSPX-...",
  "refresh_token": "1//0g...",
  "token_uri": "https://oauth2.googleapis.com/token",
  "scopes": ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
}
```

---

## 7. Configure GitHub Variables (Optional)

These are non-sensitive values you can customise. Go to **Settings → Secrets and variables → Actions → Variables**.

### English pipeline

| Variable | Default | Description |
|---|---|---|
| `TTS_VOICE_EN` | `en-SG-LunaNeural` | Edge TTS voice for EN |
| `DESCRIPTION_EN` | `Latest crypto & tech news in English` | YouTube video description |
| `TAGS_EN` | `["Shorts","Crypto","Bitcoin","Tech","News"]` | YouTube tags (JSON array) |
| `BRAND_HASHTAGS_EN` | `["xeonbit24","xeonbit24.com"]` | Hashtags appended to title |
| `OUTRO_CTA_EN` | `Follow for latest crypto news. Like & Subscribe!` | Outro text overlay |
| `INTRO_LABEL_EN` | `BREAKING` | Badge label on intro screen |
| `PLAYLIST_ID_EN` | _(empty)_ | YouTube playlist ID to add videos to |

### Vietnamese pipeline

| Variable | Default | Description |
|---|---|---|
| `TTS_VOICE_VI` | `vi-VN-HoaiMyNeural` | Edge TTS voice for VI |
| `DESCRIPTION_VI` | `Tin tức công nghệ và crypto bằng tiếng Việt` | YouTube video description |
| `TAGS_VI` | `["Shorts","CryptoViet","TinTuc","CongNghe"]` | YouTube tags (JSON array) |
| `BRAND_HASHTAGS_VI` | `["techtalk66","techtalk"]` | Hashtags appended to title |
| `OUTRO_CTA_VI` | `Theo dõi để cập nhật tin tức mới nhất. Like & Đăng ký!` | Outro text overlay |
| `INTRO_LABEL_VI` | `TIN MỚI` | Badge label on intro screen |
| `PLAYLIST_ID_VI` | _(empty)_ | YouTube playlist ID to add videos to |

---

## 8. Add the Workflow File

Confirm your workflow file is at `.github/workflows/bilingual_short.yml` and has been pushed to `main`. GitHub will automatically detect it.

To verify: go to your repository → **Actions** tab. You should see **"Bilingual YouTube Shorts Creator"** listed.

---

## 9. Run the Workflow

### Manual run (recommended for first test)

1. Go to **Actions → Bilingual YouTube Shorts Creator**
2. Click **Run workflow**
3. Fill in the inputs:

| Input | Options | Recommended for first run |
|---|---|---|
| `pipeline` | `both` / `en_only` / `vi_only` | `en_only` (test one at a time) |
| `max_posts` | 1–10 | `1` |
| `privacy` | `private` / `public` / `unlisted` | `private` |
| `publish_delay_hours` | Any number | `0` |

4. Click **Run workflow** (green button)
5. Click the running job to watch the live logs

A successful run will:
- Fetch the latest image post from the Telegram channel
- Generate a TTS audio file
- Render a 1080×1920 Short with music, overlays, and captions
- Upload the video to YouTube (as private by default)
- Save the processed post ID to the `data/published-ids` branch so it isn't reused

---

## 10. Schedule (Automatic Runs)

The workflow runs automatically three times per day (ICT timezone):

| UTC cron | ICT time |
|---|---|
| `0 2 * * *` | 09:00 |
| `0 8 * * *` | 15:00 |
| `0 14 * * *` | 21:00 |

To change the schedule, edit the `cron` entries in `bilingual_short.yml`:

```yaml
on:
  schedule:
    - cron: '0 2 * * *'   # 09:00 ICT
    - cron: '0 8 * * *'   # 15:00 ICT
    - cron: '0 14 * * *'  # 21:00 ICT
```

> **Note:** GitHub Actions schedules can be delayed by up to 15 minutes during peak times.

---

## 11. Customisation Reference

### Change the Telegram source channel

Edit the workflow's `Run EN pipeline` / `Run VI pipeline` steps and update:

```yaml
TG_CHANNEL_EN: "@your_channel_username"
TG_CHANNEL_NAME_EN: "your_channel_username"
```

### Adjust video timeline durations

Add these as GitHub Variables or set them directly in the workflow env:

| Variable | Default | Meaning |
|---|---|---|
| `DUR_MAIN` | `11.0` | Intro card duration (seconds) |
| `DUR_CLIPA` | `15.0` | First B-roll segment |
| `DUR_FLASH` | `6.0` | Flash transition segment |
| `DUR_CLIPB` | `15.0` | Second B-roll segment |
| `DUR_SUMMARY` | `6.0` | Summary card |
| `DUR_OUTRO` | `6.0` | Outro / CTA card |

### Adjust audio levels

| Variable | Default | Meaning |
|---|---|---|
| `BG_MUSIC_VOL` | `0.12` | Background music volume (0.0–1.0) |
| `TTS_VOL` | `1.0` | TTS narration volume |

### Use a different logo per language

Place two logo files in the repo root and set:

```yaml
LOGO_PATH_EN: "brand_logo_en.png"
LOGO_PATH_VI: "brand_logo_vi.png"
```

### Use separate B-roll clips per language

```yaml
CLIPS_DIR_EN: "clips/en"
CLIPS_DIR_VI: "clips/vi"
```

---

## 12. Troubleshooting

**No posts fetched from Telegram**
- Confirm the bot is an administrator of both channels
- Check `getUpdates` manually (Step 4c) — if the result array is empty, send a test message to the channel
- The bot only sees posts sent *after* it was added as admin

**YouTube upload fails with `invalid_grant`**
- Your refresh token has expired. Repeat Step 5e to generate a new one and update the GitHub Secret

**`YOUTUBE_CLIENT_SECRETS_EN must be configured` error**
- The secret value is empty or malformed JSON. Paste the full JSON object (Step 6)

**Video is generated but has no B-roll clips (black background segments)**
- Add `.mp4` or `.mov` files to the `clips/` directory in your repository
- Or set `CLIPS_DIR_EN` / `CLIPS_DIR_VI` to a folder that exists

**Font rendering issues (Vietnamese characters appear as boxes)**
- The VI pipeline installs `fonts-noto` automatically. If running locally, install it:
  ```bash
  sudo apt-get install fonts-noto
  fc-cache -f
  ```

**Concurrent push conflict on `data/published-ids` branch**
- This is handled automatically with a 5-attempt retry loop. If it fails after 5 attempts, re-run the workflow manually — no data is lost

**Workflow never triggers on schedule**
- GitHub disables scheduled workflows on repositories with no activity for 60 days. Make any commit to re-enable, or use **Run workflow** manually to keep the repo active

---

## Data Branch

Published post IDs are stored in a separate Git branch called `data/published-ids` to avoid cluttering your `main` branch history. Two files are maintained:

- `.published_ids_en.json` — IDs of EN posts already turned into videos
- `.published_ids_vi.json` — IDs of VI posts already turned into videos

Each file holds the last **30** processed IDs. Older IDs are pruned automatically.

---

## License

MIT — do whatever you like, just don't hold the author liable.
