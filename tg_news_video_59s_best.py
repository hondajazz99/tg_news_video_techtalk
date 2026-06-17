"""
TG News Video Generator — 59s Structured Short Edition (continuous narration)
================================================================================
Bố cục cố định 9:16 (1080x1920), tổng đúng 59 giây, gồm 7 đoạn:

    0–3s   INTRO   : ảnh bài 1 làm nền (zoom Ken Burns) + overlay logo/tiêu đề
    3–11s  MAIN    : ảnh bài 1 + CC caption lớn
    11–26s CLIP A  : video "nhảy" (jump-cut nhiều đoạn ngắn, motion-blur ở mối nối)
                     + PiP ảnh bài 1 wiggle theo beat (freq 2–3Hz, amp 8–20px)
                     + CC caption lớn
    26–32s FLASH   : flash ảnh bài 2 (wiggle + flash trắng) + CC caption lớn
    32–47s CLIP B  : video cao trào — jump-cut + zoom-pulse sync theo amplitude
                     nhạc nền + CC caption lớn
    47–53s SUMMARY : ảnh bài 3 làm nền + CC caption lớn
    53–59s OUTRO   : CC caption lớn (CTA) + credit nguồn (t.me/<channel>)

── TTS XUYÊN SUỐT, KHÔNG NGẮT QUÃNG ────────────────────────────────────
Toàn bộ caption của NUM_TG_POSTS bài (mặc định 3) được GHÉP THÀNH 1 SCRIPT
duy nhất + câu CTA outro, rồi tạo MỘT file TTS duy nhất, auto-fit (tăng/giảm
tốc đọc) để khớp ~59s. Word-timings tính trên timeline toàn cục (0–59s).
CC caption ở TẤT CẢ 7 đoạn (kể cả Clip A/B) đều hiển thị đúng phần script
đang được đọc tại thời điểm đó — không có đoạn nào "câm" giữa video.

Nhạc nền giữ ở MỘT mức âm lượng thấp xuyên suốt (BG_MUSIC_VOL) vì lúc nào
cũng có giọng đọc đè lên.

Wiggle áp dụng cho ảnh overlay (PiP, ảnh flash) — freq 2–3Hz, amp 8–20px,
biên độ/tốc độ tỉ lệ theo amplitude nhạc nền (RMS đã chuẩn hoá 0–1).
Font sans-serif (DejaVu Sans / FreeSans). Transitions nhanh + motion blur
ở các mối nối jump-cut.

────────────────────────────────────────────────────────────────────────
Input cần có:
  clips/*.mp4|*.mov|*.mkv|*.avi|*.webm
                      : thư mục chứa NHIỀU clip ngắn (không cần nhạc).
                        Script tự CHỌN NGẪU NHIÊN file + CẮT đoạn ngẫu
                        nhiên trong file để dựng Clip A (11–26s) và
                        Clip B (32–47s).
  bg_music.mp3        : nhạc nền cho toàn video.
  Telegram channel    : kênh public, lấy NUM_TG_POSTS bài gần nhất:
                          - bài 1 -> ảnh nền chính (INTRO, MAIN, PiP, OUTRO)
                          - bài 2 -> ảnh cho đoạn FLASH (26–32s)
                          - bài 3 -> ảnh cho đoạn SUMMARY (47–53s)
                        Nếu thiếu bài, dùng biến thể (lật ảnh) của ảnh có sẵn.
                        Caption của TẤT CẢ các bài được ghép lại làm script
                        đọc liên tục.

Output:
  output_video.mp4    : video 1080x1920, 59s
  yt_description.txt  : caption + hashtag để paste YouTube

Dependencies:
  pip install requests beautifulsoup4 gtts moviepy Pillow langdetect numpy scipy

FFmpeg:
  Ubuntu/Debian : sudo apt install ffmpeg
  macOS         : brew install ffmpeg
  Windows       : https://ffmpeg.org/download.html
"""

import os, sys, re, json, random, subprocess, requests, tempfile
import numpy as np
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from gtts import gTTS
from langdetect import detect
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── moviepy v1 / v2 compat ──────────────────
try:
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, ImageClip, ColorClip,
        CompositeVideoClip, concatenate_videoclips, CompositeAudioClip
    )
    from moviepy.audio.AudioClip import AudioClip
    MOVIEPY_V2 = False
except ModuleNotFoundError:
    from moviepy import (
        VideoFileClip, AudioFileClip, ImageClip, ColorClip,
        CompositeVideoClip, concatenate_videoclips, CompositeAudioClip,
        AudioClip
    )
    MOVIEPY_V2 = True


# ─────────────────────────────────────────────
# CONFIG  (tất cả có thể override bằng biến môi trường — dùng cho
#          GitHub Actions / CI, xem README.md)
# ─────────────────────────────────────────────
TG_CHANNEL   = os.environ.get("TG_CHANNEL", "Techtalk66")
CLIPS_DIR    = os.environ.get("CLIPS_DIR", "clips")          # thư mục chứa clip nền cho đoạn A & B
BG_MUSIC     = os.environ.get("BG_MUSIC", "bg_music.mp3")
OUTPUT_VIDEO = os.environ.get("OUTPUT_VIDEO", "output_video.mp4")
YT_DESC_FILE = os.environ.get("YT_DESC_FILE", "yt_description.txt")

OUTPUT_SIZE  = (1080, 1920)     # 9:16
FPS          = 30

# Số bài TG lấy để ghép caption thành script đọc liên tục + lấy ảnh.
# Mặc định 3: bài 1 -> ảnh chính (INTRO/MAIN/PiP/OUTRO),
#             bài 2 -> ảnh FLASH, bài 3 -> ảnh SUMMARY.
# Có thể chỉnh 1-5; nếu thiếu bài, ảnh còn thiếu sẽ dùng biến thể (lật) ảnh có sẵn.
NUM_TG_POSTS = int(os.environ.get("NUM_TG_POSTS", "3"))

# Số bài gần nhất sẽ quét trên kênh TG để chọn ra bài CHƯA từng dùng
# (đối chiếu với .published_ids.json). Nên >= NUM_TG_POSTS.
TG_FETCH_LIMIT = int(os.environ.get("TG_FETCH_LIMIT", "20"))

# ── Chống trùng lặp bài đã publish ───────────
PUBLISHED_IDS_FILE = os.environ.get("PUBLISHED_IDS_FILE", ".published_ids.json")
PUBLISHED_IDS_MAX_KEEP = int(os.environ.get("PUBLISHED_IDS_MAX_KEEP", "1000"))

# ── Metadata cho bước upload YouTube (xem youtube_upload.py) ─
VIDEO_META_FILE   = os.environ.get("VIDEO_META_FILE", "video_meta.json")
YT_PLAYLIST_ID    = os.environ.get("YT_PLAYLIST_ID", "PLL7BH4dMy6VxNRcIWVpKYlC-9AJR3Tytn")
YT_CATEGORY_ID    = os.environ.get("YT_CATEGORY_ID", "25")       # 25 = News & Politics
YT_PRIVACY_STATUS = os.environ.get("YT_PRIVACY_STATUS", "public")

# ── Brand logo overlay (watermark xuyên suốt video) ──────────
BRAND_LOGO_PATH  = os.environ.get("BRAND_LOGO_PATH", "brand_logo.png")
LOGO_POSITION    = os.environ.get("LOGO_POSITION", "top-left")   # top-left/top-right/bottom-left/bottom-right
LOGO_WIDTH_RATIO = float(os.environ.get("LOGO_WIDTH_RATIO", "0.20"))
LOGO_MARGIN_PX   = int(os.environ.get("LOGO_MARGIN_PX", "28"))
LOGO_OPACITY     = float(os.environ.get("LOGO_OPACITY", "0.92"))

# ── Timeline 59s (tổng phải = 59) ────────────
DUR_INTRO   = 3.0
DUR_MAIN    = 8.0
DUR_CLIPA   = 15.0
DUR_FLASH   = 6.0
DUR_CLIPB   = 15.0
DUR_SUMMARY = 6.0
DUR_OUTRO   = 6.0
TOTAL_DUR   = (DUR_INTRO + DUR_MAIN + DUR_CLIPA + DUR_FLASH +
               DUR_CLIPB + DUR_SUMMARY + DUR_OUTRO)   # = 59.0

# ── Âm thanh ──────────────────────────────────
# TTS đọc liên tục xuyên suốt video -> nhạc nền giữ 1 mức âm lượng thấp cố định.
BG_MUSIC_VOL  = 0.12
TTS_VOL       = 1.0
TTS_SPEED_MIN = 1.25     # tốc độ đọc tối thiểu (atempo) — không làm chậm quá mức
TTS_SPEED_MAX = 1.85    # tốc độ đọc tối đa — auto-fit để TTS vừa ~59s
TTS_PAD       = 0.1     # khoảng lặng cuối TTS (giây)

# ── Wiggle (ảnh overlay: PiP, flash) — theo bố cục: freq 2–3Hz, amp 8–20px
WIGGLE_AMP_MIN_PX  = 8
WIGGLE_AMP_MAX_PX  = 20
WIGGLE_FREQ_MIN_HZ = 2.0
WIGGLE_FREQ_MAX_HZ = 3.0
WIGGLE_SMOOTH      = 5      # smoothing window cho amplitude (frames)

# ── PiP (đoạn 11–26s) ─────────────────────────
PIP_SCALE  = 0.42
PIP_MARGIN = 24

# ── Caption CC (lớn, dưới màn hình) ──────────
CC_FONT_SIZE      = 56
CC_BOX_ALPHA      = 215
CC_Y_RATIO        = 0.78    # vị trí box CC ~ 78% chiều cao khung hình
CC_MAX_LINE_WORDS = 4
COLOR_NORMAL      = (235, 235, 235, 255)
COLOR_HIGHLIGHT   = (255, 221, 0,   255)
COLOR_DONE        = (130, 130, 130, 255)

# ── Intro / Summary text style ───────────────
TITLE_FONT_SIZE   = 70
SUMMARY_FONT_SIZE = 50

# ── Jump-cut & motion blur (đoạn A/B) ────────
JUMPCUT_SEGMENTS_A = 3       # số đoạn nhỏ ghép thành Clip A
JUMPCUT_SEGMENTS_B = 2       # số đoạn nhỏ ghép thành Clip B (cao trào, ít cắt hơn)
EDGE_BLUR_SEC      = 0.12    # thời gian blur ở đầu/cuối mỗi sub-clip (giây)
EDGE_BLUR_RADIUS   = 7       # bán kính Gaussian blur tối đa
BEAT_ZOOM_MAX      = 0.07    # zoom-pulse tối đa cho Clip B (7% theo amplitude)

KEN_BURNS_ZOOM = 1.18        # zoom tối đa cho hiệu ứng Ken Burns trên ảnh tĩnh

# ── Text mặc định theo ngôn ngữ ───────────────
INTRO_LABEL = {
    "vi": "TIN MỚI",
    "en": "BREAKING",
}
OUTRO_TEXT = {
    "vi": "Hãy theo dõi kênh để nhận tin mới nhất",
    "en": "Follow the channel for latest news",
}
CREDIT_PREFIX = {
    "vi": "Nguồn: t.me/",
    "en": "Source: t.me/",
}
CLIP_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm")
# ─────────────────────────────────────────────


def log(msg): print(f"[•] {msg}")


# ── moviepy compat helpers ───────────────────
def c_duration(clip, d):
    return clip.with_duration(d)       if MOVIEPY_V2 else clip.set_duration(d)
def c_audio(clip, a):
    return clip.with_audio(a)          if MOVIEPY_V2 else clip.set_audio(a)
def c_vol(clip, v):
    return clip.with_volume_scaled(v)  if MOVIEPY_V2 else clip.volumex(v)
def c_loop(clip, dur):
    if MOVIEPY_V2:
        from moviepy import vfx
        return clip.with_effects([vfx.Loop()]).subclipped(0, dur)
    return clip.loop(duration=dur).subclip(0, dur)
def c_audio_loop(clip, dur):
    if MOVIEPY_V2:
        from moviepy import afx
        return clip.with_effects([afx.AudioLoop(duration=dur)])
    return clip.audio_loop(duration=dur)
def c_subclip(clip, t1, t2):
    return clip.subclipped(t1, t2) if MOVIEPY_V2 else clip.subclip(t1, t2)
def c_transform(clip, func):
    """func(get_frame, t) -> frame.  Dùng cho cả video & audio clip."""
    return clip.transform(func) if MOVIEPY_V2 else clip.fl(func)


# ── CI / GitHub Actions helpers ──────────────

def set_github_output(name, value):
    """Ghi key=value vào $GITHUB_OUTPUT (no-op nếu không chạy trong GH Actions)."""
    out_file = os.environ.get("GITHUB_OUTPUT")
    if not out_file:
        return
    try:
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    except Exception as e:
        log(f"  ⚠ Không ghi được GITHUB_OUTPUT: {e}")


# ── Chống trùng lặp bài Telegram (.published_ids.json) ───────

def _post_sort_key(post_id):
    """Sắp xếp id dạng 'channel/12345' theo số message id."""
    try:
        return int(str(post_id).rsplit("/", 1)[-1])
    except Exception:
        return 0


def load_published_ids(path=PUBLISHED_IDS_FILE):
    """Đọc danh sách id bài TG đã publish trước đó -> set()."""
    p = Path(path)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("published", []))
    except Exception as e:
        log(f"  ⚠ Không đọc được {path}: {e} — coi như chưa có bài nào publish")
        return set()


def save_published_ids(ids, path=PUBLISHED_IDS_FILE, max_keep=PUBLISHED_IDS_MAX_KEEP):
    """Lưu lại danh sách id đã publish (giữ tối đa `max_keep` id gần nhất)."""
    ids_list = sorted(set(ids), key=_post_sort_key)
    if len(ids_list) > max_keep:
        ids_list = ids_list[-max_keep:]
    data = {
        "published": ids_list,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"✓ Đã cập nhật {path} ({len(ids_list)} id)")


def make_video_clip(frame_func, duration):
    if MOVIEPY_V2:
        from moviepy import VideoClip
        return VideoClip(frame_func, duration=duration)
    else:
        from moviepy.video.VideoClip import VideoClip
        return VideoClip(frame_func, duration=duration, ismask=False)


def make_silence(duration, fps=44100):
    """Audio im lặng (stereo) — dùng cho audio riêng của từng đoạn video
    (giọng đọc thật được mix riêng ở cuối, xem main())."""
    def frame(t):
        if np.isscalar(t):
            return np.array([0.0, 0.0])
        return np.zeros((len(t), 2))
    return AudioClip(frame, duration=duration, fps=fps)


# ── 1. Caption helpers ───────────────────────

def split_caption_hashtags(raw_text):
    """
    Tách caption thành 2 phần:
      - display_text : không có #hashtag, không có emoji/icon
      - hashtags     : list các #tag giữ nguyên để dùng cho YT
    """
    import unicodedata
    tags = re.findall(r"#\S+", raw_text)
    text = re.sub(r"#\S+", "", raw_text)
    text = "".join(
        ch for ch in text
        if not unicodedata.category(ch).startswith(("So", "Sm", "Sk", "Sc"))
        and ord(ch) < 0x1F000
    )
    text = re.sub(r" +", " ", text).strip()
    return text, tags


def make_title_text(caption, max_words=6):
    """Headline ngắn cho overlay INTRO (0–3s), lấy từ vài từ đầu caption bài 1."""
    caption = caption.strip() or "Tin tiếp theo"
    words = caption.split()
    title = " ".join(words[:max_words])
    if len(words) > max_words:
        title += "…"
    return title


def build_script_text(posts, outro_text):
    """Ghép caption của tất cả các bài + câu CTA outro thành 1 script đọc liên tục."""
    parts = [p["caption"].strip() for p in posts if p["caption"].strip()]
    if not parts:
        parts = ["Tin tiếp theo"]
    combined = " ".join(parts)
    return f"{combined} {outro_text}".strip()


# ── 2. Scrape Telegram ───────────────────────

def scrape_tg_channel(channel, num=NUM_TG_POSTS, exclude_ids=None, fetch_limit=TG_FETCH_LIMIT):
    """
    Lấy các bài mới nhất từ kênh TG có ảnh, BỎ QUA các bài có id nằm trong
    `exclude_ids` (đã publish trước đó — xem .published_ids.json).

    Quét tối đa `fetch_limit` message gần nhất trên trang preview
    (t.me/s/<channel>), trả về tối đa `num` bài CHƯA publish, mới nhất
    trước. Nếu kênh chưa có bài mới nào, trả về [].
    """
    exclude_ids = exclude_ids or set()
    url = f"https://t.me/s/{channel}"
    log(f"Scraping {url} ...")
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    posts, skipped = [], 0
    messages = list(reversed(soup.select(".tgme_widget_message")))[:fetch_limit]
    for msg in messages:
        post_id = msg.get("data-post") or msg.get("data-view") or None

        photo_el = msg.select_one(".tgme_widget_message_photo_wrap")
        if not photo_el:
            continue
        m = re.search(r"url\(['\"]?(https?://[^'\")\s]+)['\"]?\)",
                      photo_el.get("style", ""))
        if not m:
            continue

        if post_id and post_id in exclude_ids:
            skipped += 1
            continue

        text_el  = msg.select_one(".tgme_widget_message_text")
        raw_text = text_el.get_text(separator=" ", strip=True) if text_el else ""
        display_text, hashtags = split_caption_hashtags(raw_text)
        posts.append({
            "id"          : post_id,
            "img_url"     : m.group(1),
            "caption"     : display_text,
            "hashtags"    : hashtags,
            "raw_caption" : raw_text,
        })
        if len(posts) >= num:
            break

    log(f"Tìm được {len(posts)} bài mới (đã bỏ {skipped} bài đã publish trước đó, "
        f"quét {len(messages)} bài gần nhất)")
    return posts



def download_image(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")

def get_image_variant(img, idx):
    """Tạo biến thể khác từ 1 ảnh khi không đủ bài TG (idx=1 -> lật ngang, idx=2 -> lật dọc)."""
    if idx % 2 == 1:
        return img.transpose(Image.FLIP_LEFT_RIGHT)
    return img.transpose(Image.FLIP_TOP_BOTTOM)


# ── 2b. Resize video to 9:16 ────────────────

def resize_to_shorts(clip):
    """
    Resize + crop video/clip về 1080x1920 (9:16).
    - Nếu video ngang: zoom + crop giữa
    - Nếu video dọc:   scale vừa chiều rộng
    """
    target_w, target_h = OUTPUT_SIZE
    src_w, src_h = clip.w, clip.h
    src_ratio    = src_w / src_h
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        scale = target_h / src_h
    else:
        scale = target_w / src_w

    new_w = int(src_w * scale)
    new_h = int(src_h * scale)

    if MOVIEPY_V2:
        from moviepy import vfx
        resized = clip.with_effects([vfx.Resize((new_w, new_h))])
    else:
        resized = clip.resize((new_w, new_h))

    x1 = (new_w - target_w) // 2
    y1 = (new_h - target_h) // 2
    if MOVIEPY_V2:
        from moviepy import vfx
        return resized.with_effects([vfx.Crop(x1=x1, y1=y1, x2=x1+target_w, y2=y1+target_h)])
    else:
        return resized.crop(x1=x1, y1=y1, x2=x1+target_w, y2=y1+target_h)


# ── 3. TTS auto-fit thời lượng ───────────────

def make_tts_fit(text, out_path, target_duration, pad=TTS_PAD):
    """
    Tạo TTS rồi tự chỉnh tốc độ đọc (atempo) sao cho khớp `target_duration`
    (trừ `pad` giây lặng cuối). Trả về lang phát hiện được.
    """
    if not text.strip():
        text = "Không có mô tả"
    try:
        lang = detect(text)
    except Exception:
        lang = "vi"

    raw_path = out_path + ".raw.mp3"
    gTTS(text=text, lang=lang).save(raw_path)

    raw_clip = AudioFileClip(raw_path)
    raw_dur  = raw_clip.duration
    raw_clip.close()

    avail = max(target_duration - pad, 0.5)
    speed = raw_dur / avail
    speed = min(max(speed, TTS_SPEED_MIN), TTS_SPEED_MAX)

    log(f"  TTS lang={lang} raw={raw_dur:.2f}s target={target_duration:.1f}s "
        f"speed={speed:.2f}x ({len(text.split())} từ)")

    if abs(speed - 1.0) < 0.02:
        os.rename(raw_path, out_path)
    else:
        atempo = f"atempo={speed:.4f}" if speed <= 2.0 else f"atempo=2.0,atempo={speed/2.0:.4f}"
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path, "-filter:a", atempo, "-vn", out_path],
            check=True, capture_output=True
        )
        os.remove(raw_path)
        final_clip = AudioFileClip(out_path)
        final_dur  = final_clip.duration
        final_clip.close()
    if final_dur < target_duration - 0.5:
        log(f"  ⚠ TTS ({final_dur:.2f}s) ngắn hơn target ({target_duration:.1f}s) — "
            f"phần cuối video sẽ chỉ còn nhạc nền. Tăng NUM_TG_POSTS để có thêm caption.")
    elif final_dur > target_duration:
        log(f"  ⚠ TTS dài hơn video ({final_dur:.2f}s > {target_duration:.1f}s) — sẽ bị cắt ở cuối")

    return lang


# ── 4. Waveform amplitude array ──────────────

def extract_amplitude(audio_path, fps, duration):
    """
    Đọc audio → RMS amplitude theo từng frame video.
    Trả về numpy array shape (n_frames,) giá trị [0, 1].
    """
    from scipy.io import wavfile
    tmp_wav = audio_path + ".amp.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path,
         "-ac", "1", "-ar", "22050", tmp_wav],
        check=True, capture_output=True
    )
    sr, data = wavfile.read(tmp_wav)
    os.remove(tmp_wav)

    if data.dtype != np.float32:
        data = data.astype(np.float32) / np.iinfo(data.dtype).max

    n_frames = int(duration * fps)
    samples_per_frame = max(1, int(sr / fps))
    amps = []
    for i in range(n_frames):
        s = i * samples_per_frame
        e = s + samples_per_frame
        chunk = data[s:e] if s < len(data) else np.array([0.0])
        amps.append(float(np.sqrt(np.mean(chunk ** 2))))

    amps = np.array(amps)
    mx = amps.max()
    if mx > 0:
        amps /= mx
    if WIGGLE_SMOOTH > 1:
        kernel = np.ones(WIGGLE_SMOOTH) / WIGGLE_SMOOTH
        amps   = np.convolve(amps, kernel, mode="same")
        amps   = np.clip(amps / (amps.max() + 1e-9), 0, 1)
    return amps


# ── 5. Image helpers (cover-crop, Ken Burns, wiggle) ─────────────────

def cover_crop(pil_img, target_w, target_h):
    """Scale + center-crop ảnh để lấp đầy (cover) khung target_w x target_h."""
    src_w, src_h = pil_img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale) + 1, int(src_h * scale) + 1
    img = pil_img.resize((new_w, new_h), Image.LANCZOS)
    x = (new_w - target_w) // 2
    y = (new_h - target_h) // 2
    return img.crop((x, y, x + target_w, y + target_h))


def ken_burns_clip(pil_img, duration, fps, canvas_w, canvas_h,
                   zoom_in=True, max_zoom=KEN_BURNS_ZOOM):
    """Ảnh tĩnh -> clip có hiệu ứng zoom chậm (Ken Burns)."""
    big_w, big_h = int(canvas_w * max_zoom), int(canvas_h * max_zoom)
    big = cover_crop(pil_img.convert("RGB"), big_w, big_h)
    big_arr = np.array(big)

    def make_frame(t):
        progress = min(max(t / duration, 0.0), 1.0)
        if not zoom_in:
            progress = 1.0 - progress
        win_w = int(big_w - (big_w - canvas_w) * progress)
        win_h = int(big_h - (big_h - canvas_h) * progress)
        win_w = max(win_w, canvas_w)
        win_h = max(win_h, canvas_h)
        x = (big_w - win_w) // 2
        y = (big_h - win_h) // 2
        crop = Image.fromarray(big_arr).crop((x, y, x + win_w, y + win_h))
        if (win_w, win_h) != (canvas_w, canvas_h):
            crop = crop.resize((canvas_w, canvas_h), Image.LANCZOS)
        return np.array(crop)

    return make_video_clip(make_frame, duration)


def make_wiggle_image_clip(pil_img_rgba, duration, fps, amp_array, t_offset,
                            base_x, base_y, canvas_w, canvas_h):
    """
    Ảnh RGBA overlay (PiP / flash) wiggle theo amplitude nhạc nền:
      - freq dao động trong [WIGGLE_FREQ_MIN_HZ, WIGGLE_FREQ_MAX_HZ]
      - biên độ trong [WIGGLE_AMP_MIN_PX, WIGGLE_AMP_MAX_PX] px
    `t_offset` = thời điểm bắt đầu đoạn này trong timeline 59s (để tra
    amplitude đúng vị trí trong bài nhạc).
    """
    img_arr = np.array(pil_img_rgba.convert("RGBA"))

    def make_frame(t):
        gi  = min(max(int((t + t_offset) * fps), 0), len(amp_array) - 1)
        amp = float(amp_array[gi])

        freq    = WIGGLE_FREQ_MIN_HZ + (WIGGLE_FREQ_MAX_HZ - WIGGLE_FREQ_MIN_HZ) * amp
        amp_px  = WIGGLE_AMP_MIN_PX  + (WIGGLE_AMP_MAX_PX  - WIGGLE_AMP_MIN_PX)  * amp
        phase   = t * freq * 2 * np.pi

        dx    = int(amp_px * np.sin(phase))
        dy    = int(amp_px * 0.6 * np.sin(phase * 1.3 + 1.0))
        angle = 2.0 + amp * 5.0 * np.sin(phase * 0.8 + 0.5)

        pil = Image.fromarray(img_arr, "RGBA").rotate(angle, resample=Image.BICUBIC, expand=False)
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        canvas.paste(pil, (base_x + dx, base_y + dy), pil)
        return np.array(canvas)

    return make_video_clip(make_frame, duration)


# ── 6. Word timing (toàn cục, 0–59s) ─────────

def build_word_timings(words, duration, pad=0.3):
    """Chia đều `duration` cho tất cả các từ trong script (xấp xỉ tuyến tính)."""
    speak_dur = max(duration - pad, duration * 0.9)
    if not words:
        return []
    per_word = speak_dur / len(words)
    return [
        {"word": w, "start": i * per_word, "end": (i + 1) * per_word}
        for i, w in enumerate(words)
    ]


# ── 7. Fonts & CC caption renderer ───────────

def load_font(size):
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]:
        if Path(p).exists():
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return ImageFont.load_default()


def wrap_words(words, n):
    return [words[i:i+n] for i in range(0, len(words), n)]


def make_cc_frame(t_global, words, timings, canvas_w, canvas_h,
                  font_size=CC_FONT_SIZE, y_ratio=CC_Y_RATIO,
                  max_line_words=CC_MAX_LINE_WORDS, visible_lines=1):
    """
    CC caption lớn, highlight theo từ đang đọc tại thời điểm `t_global`
    (mốc thời gian trên timeline toàn cục 0–59s, KHÔNG phải thời gian
    local của từng đoạn).
    """
    img  = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if not words:
        return img

    fn = load_font(font_size)
    fh = load_font(int(font_size * 1.18))

    active_idx = len(timings) - 1
    for i, tm in enumerate(timings):
        if tm["start"] <= t_global < tm["end"]:
            active_idx = i
            break
    if t_global < timings[0]["start"]:
        active_idx = 0

    all_lines  = wrap_words(words, max_line_words)
    word_line  = {}
    wi = 0
    for li, line in enumerate(all_lines):
        for ci in range(len(line)):
            word_line[wi] = (li, ci)
            wi += 1

    active_line = word_line.get(active_idx, (0, 0))[0]
    half = visible_lines // 2
    sl = max(0, active_line - half)
    el = min(len(all_lines), sl + visible_lines)
    sl = max(0, el - visible_lines)
    visible = all_lines[sl:el]

    line_h = font_size + 16
    box_h  = line_h * len(visible) + 36
    box_w  = int(canvas_w * 0.92)
    box_x  = (canvas_w - box_w) // 2
    box_y  = int(canvas_h * y_ratio)

    draw.rounded_rectangle(
        [box_x - 20, box_y - 18, box_x + box_w + 20, box_y + box_h + 18],
        radius=20, fill=(0, 0, 0, CC_BOX_ALPHA)
    )

    gwi = sl * max_line_words
    for li, line_words in enumerate(visible):
        widths = []
        for ci, w in enumerate(line_words):
            f  = fh if (gwi + ci) == active_idx else fn
            bb = draw.textbbox((0, 0), w + " ", font=f)
            widths.append(bb[2] - bb[0])
        x = (canvas_w - sum(widths)) // 2
        y = box_y + li * line_h + 14
        for ci, w in enumerate(line_words):
            wi2 = gwi + ci
            f   = fh if wi2 == active_idx else fn
            col = COLOR_HIGHLIGHT if wi2 == active_idx else (COLOR_DONE if wi2 < active_idx else COLOR_NORMAL)
            draw.text((x, y), w, font=f, fill=col)
            bb = draw.textbbox((x, y), w + " ", font=f)
            x += bb[2] - bb[0]
        gwi += len(line_words)

    return img


def make_cc_overlay(words, timings, t_offset, duration, w, h,
                     font_size=CC_FONT_SIZE, y_ratio=CC_Y_RATIO,
                     max_line_words=CC_MAX_LINE_WORDS):
    """
    Lớp CC caption cho 1 đoạn — `t_offset` là mốc bắt đầu (giây) của đoạn
    này trên timeline toàn cục 0–59s. Tại local time `t`, hiển thị đúng
    phần script đang được đọc ở thời điểm global (t + t_offset).
    """
    return make_video_clip(
        lambda t: np.array(make_cc_frame(t + t_offset, words, timings, w, h,
                                          font_size=font_size, y_ratio=y_ratio,
                                          max_line_words=max_line_words)),
        duration
    )


def dark_overlay_frame(w, h, alpha=120):
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, 3] = alpha
    return arr


def make_title_frame(t, title_text, label_text, canvas_w, canvas_h):
    """Overlay logo/tiêu đề cho đoạn INTRO (0–3s) — fade-in nhanh."""
    img  = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    f  = load_font(TITLE_FONT_SIZE)
    lf = load_font(int(TITLE_FONT_SIZE * 0.42))

    progress = min(t / 0.4, 1.0)   # fade-in 0.4s đầu
    alpha = int(255 * progress)

    # dải gradient tối phía trên để chữ nổi rõ
    grad_h = int(canvas_h * 0.42)
    for gy in range(grad_h):
        a = int(180 * (1 - gy / grad_h))
        draw.line([(0, gy), (canvas_w, gy)], fill=(0, 0, 0, a))

    words   = title_text.split()
    lines   = wrap_words(words, 3)
    line_h  = TITLE_FONT_SIZE + 14
    total_h = line_h * len(lines)
    y0 = int(canvas_h * 0.16) - total_h // 2

    # nhãn nhỏ (TIN MỚI / BREAKING)
    bb = draw.textbbox((0, 0), label_text, font=lf)
    lw, lh = bb[2] - bb[0], bb[3] - bb[1]
    label_y = y0 - line_h
    draw.rounded_rectangle(
        [(canvas_w - lw) // 2 - 22, label_y - 8,
         (canvas_w + lw) // 2 + 22, label_y + lh + 16],
        radius=14, fill=(230, 30, 30, alpha)
    )
    draw.text(((canvas_w - lw) // 2, label_y), label_text, font=lf, fill=(255, 255, 255, alpha))

    for li, line in enumerate(lines):
        line_str = " ".join(line)
        bb = draw.textbbox((0, 0), line_str, font=f)
        w = bb[2] - bb[0]
        x = (canvas_w - w) // 2
        y = y0 + li * line_h
        draw.text((x, y), line_str, font=f, fill=(255, 255, 255, alpha))

    return img


def make_credit_frame(text, canvas_w, canvas_h):
    img  = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    f = load_font(30)
    bb = draw.textbbox((0, 0), text, font=f)
    tw = bb[2] - bb[0]
    x = (canvas_w - tw) // 2
    y = int(canvas_h * 0.88)
    draw.text((x, y), text, font=f, fill=(200, 200, 200, 220))
    return img


def make_flash_overlay_clip(duration, flash_sec, w, h):
    """Lớp trắng fade-out nhanh ở đầu đoạn FLASH (hiệu ứng 'chớp')."""
    def frame(t):
        alpha = max(0.0, 1 - t / flash_sec) if t < flash_sec else 0.0
        arr = np.full((h, w, 4), 255, dtype=np.uint8)
        arr[:, :, 3] = int(alpha * 255)
        return arr
    return make_video_clip(frame, duration)


# ── 7b. Brand logo watermark (xuyên suốt video) ──────────────

def make_logo_overlay_clip(duration, w, h,
                            logo_path=BRAND_LOGO_PATH,
                            position=LOGO_POSITION,
                            width_ratio=LOGO_WIDTH_RATIO,
                            margin=LOGO_MARGIN_PX,
                            opacity=LOGO_OPACITY):
    """
    Overlay logo thương hiệu (PNG có alpha) ở 1 góc màn hình, hiển thị
    xuyên suốt toàn bộ video. Trả về None nếu không tìm thấy file logo
    (cho phép chạy bình thường mà không cần logo).
    """
    p = Path(logo_path)
    if not p.exists():
        log(f"  (không có logo '{logo_path}' — bỏ qua watermark)")
        return None

    logo = Image.open(p).convert("RGBA")
    target_w = max(1, int(w * width_ratio))
    target_h = max(1, int(target_w * logo.height / logo.width))
    logo = logo.resize((target_w, target_h), Image.LANCZOS)

    if opacity < 1.0:
        r, g, b, a = logo.split()
        a = a.point(lambda px: int(px * opacity))
        logo = Image.merge("RGBA", (r, g, b, a))

    if position == "top-right":
        x, y = w - target_w - margin, margin
    elif position == "bottom-left":
        x, y = margin, h - target_h - margin
    elif position == "bottom-right":
        x, y = w - target_w - margin, h - target_h - margin
    else:  # top-left (default)
        x, y = margin, margin

    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(logo, (x, y), logo)
    frame_arr = np.array(canvas)
    return make_video_clip(lambda t: frame_arr, duration)


# ── 8. Jump-cut clips (từ thư mục clips/) ────

def list_clip_files(clips_dir):
    p = Path(clips_dir)
    if not p.exists():
        return []
    return [str(f) for f in sorted(p.iterdir())
            if f.is_file() and f.suffix.lower() in CLIP_EXTS]


def pick_segment(path, seg_dur):
    """Mở 1 file clip, cắt 1 đoạn dài seg_dur (random start), resize 9:16."""
    clip = VideoFileClip(path)
    if clip.duration <= seg_dur:
        out = c_loop(clip, seg_dur)
    else:
        max_start = clip.duration - seg_dur
        start = random.uniform(0, max_start)
        out = c_subclip(clip, start, start + seg_dur)
    return resize_to_shorts(out).without_audio()


def apply_edge_blur(clip, blur_sec=EDGE_BLUR_SEC, max_radius=EDGE_BLUR_RADIUS):
    """Motion-blur ở đầu/cuối clip — tạo cảm giác 'nhảy' giữa các jump-cut."""
    dur = clip.duration

    def fx(get_frame, t):
        frame = get_frame(t)
        r = 0.0
        if t < blur_sec:
            r = max_radius * (1 - t / blur_sec)
        elif t > dur - blur_sec:
            r = max_radius * (1 - (dur - t) / blur_sec)
        if r > 0.4:
            pil = Image.fromarray(frame).filter(ImageFilter.GaussianBlur(radius=r))
            return np.array(pil)
        return frame

    return c_transform(clip, fx)


def build_jumpcut_sequence(clip_files, total_dur, n_segments):
    """Chọn ngẫu nhiên n_segments đoạn từ clip_files, ghép thành 1 clip dài total_dur."""
    seg_dur = total_dur / n_segments
    segs = []
    for _ in range(n_segments):
        path = random.choice(clip_files)
        seg  = pick_segment(path, seg_dur)
        seg  = apply_edge_blur(seg)
        segs.append(seg)
    return concatenate_videoclips(segs, method="compose")


def apply_beat_zoom_pulse(clip, amp_array, fps, t_offset, max_zoom=BEAT_ZOOM_MAX):
    """Clip B 'cao trào' — zoom-pulse nhẹ theo amplitude nhạc nền (sync beat)."""
    w, h = clip.w, clip.h

    def fx(get_frame, t):
        frame = get_frame(t)
        gi  = min(max(int((t + t_offset) * fps), 0), len(amp_array) - 1)
        amp = float(amp_array[gi])
        scale = 1.0 + amp * max_zoom
        new_w, new_h = int(w * scale), int(h * scale)
        pil = Image.fromarray(frame).resize((new_w, new_h), Image.BILINEAR)
        x = (new_w - w) // 2
        y = (new_h - h) // 2
        pil = pil.crop((x, y, x + w, y + h))
        return np.array(pil)

    return c_transform(clip, fx)


# ── 9. Build từng đoạn theo bố cục 59s ────────
# Mọi đoạn nhận `words`, `timings` (toàn cục) + `t_offset` (mốc bắt đầu
# đoạn trên timeline 0–59s) để vẽ CC caption đúng phần script đang đọc.
# Audio của từng đoạn để im lặng — giọng đọc thật được mix 1 lần ở main().

def _add_logo(layers, duration, w, h):
    """Thêm logo overlay vào danh sách layer nếu file logo tồn tại."""
    logo_clip = make_logo_overlay_clip(duration, w, h)
    if logo_clip is not None:
        layers.append(logo_clip)


def build_intro_clip(main_img, title_text, label_text, words, timings, t_offset,
                      duration, w, h, fps):
    """0–3s: ảnh bài 1 (Ken Burns nhẹ) + overlay logo/tiêu đề + CC + brand logo."""
    bg = ken_burns_clip(main_img, duration, fps, w, h, zoom_in=True, max_zoom=1.10)
    title_clip = make_video_clip(
        lambda t: np.array(make_title_frame(t, title_text, label_text, w, h)),
        duration
    )
    cap_clip = make_cc_overlay(words, timings, t_offset, duration, w, h)
    layers = [bg, title_clip, cap_clip]
    _add_logo(layers, duration, w, h)

    comp = CompositeVideoClip(layers, size=(w, h))
    comp = c_duration(comp, duration)
    return c_audio(comp, make_silence(duration))


def build_main_clip(main_img, words, timings, t_offset, duration, w, h, fps, amp_array):
    """3–11s: ảnh bài 1 (Ken Burns) + CC caption lớn + brand logo."""
    bg = ken_burns_clip(main_img, duration, fps, w, h, zoom_in=True, max_zoom=KEN_BURNS_ZOOM)
    cap_clip = make_cc_overlay(words, timings, t_offset, duration, w, h)
    layers = [bg, cap_clip]
    _add_logo(layers, duration, w, h)

    comp = CompositeVideoClip(layers, size=(w, h))
    comp = c_duration(comp, duration)
    return c_audio(comp, make_silence(duration))


def build_clip_a(clip_files, pip_img, words, timings, t_offset, duration, w, h, fps, amp_array):
    """11–26s: video jump-cut (nhiều đoạn ngắn, motion blur) + PiP ảnh bài 1 wiggle + CC + brand logo."""
    base = build_jumpcut_sequence(clip_files, duration, JUMPCUT_SEGMENTS_A)
    base = c_duration(base, duration)

    pip_w = int(w * PIP_SCALE)
    pip_h = int(pip_w * pip_img.height / pip_img.width)
    pip   = pip_img.resize((pip_w, pip_h), Image.LANCZOS)

    base_x = w - pip_w - PIP_MARGIN
    base_y = PIP_MARGIN

    pip_clip = make_wiggle_image_clip(pip, duration, fps, amp_array, t_offset,
                                       base_x, base_y, w, h)
    cap_clip = make_cc_overlay(words, timings, t_offset, duration, w, h)
    layers = [base, pip_clip, cap_clip]
    _add_logo(layers, duration, w, h)

    comp = CompositeVideoClip(layers, size=(w, h))
    comp = c_duration(comp, duration)
    return c_audio(comp, make_silence(duration))


def build_flash_clip(flash_img, words, timings, t_offset, duration, w, h, fps, amp_array):
    """26–32s: flash ảnh bài 2 (wiggle + flash trắng) + CC + brand logo."""
    black_bg = c_duration(ColorClip(size=(w, h), color=(10, 10, 10)), duration)

    flash_full  = cover_crop(flash_img.convert("RGBA"), w, h)
    wiggle_clip = make_wiggle_image_clip(flash_full, duration, fps, amp_array, t_offset, 0, 0, w, h)

    flash_overlay = make_flash_overlay_clip(duration, 0.18, w, h)
    cap_clip = make_cc_overlay(words, timings, t_offset, duration, w, h)
    layers = [black_bg, wiggle_clip, flash_overlay, cap_clip]
    _add_logo(layers, duration, w, h)

    comp = CompositeVideoClip(layers, size=(w, h))
    comp = c_duration(comp, duration)
    return c_audio(comp, make_silence(duration))


def build_clip_b(clip_files, words, timings, t_offset, duration, w, h, fps, amp_array):
    """32–47s: video cao trào — jump-cut + zoom-pulse sync beat + CC + brand logo."""
    base = build_jumpcut_sequence(clip_files, duration, JUMPCUT_SEGMENTS_B)
    base = c_duration(base, duration)
    base = apply_beat_zoom_pulse(base, amp_array, fps, t_offset)

    cap_clip = make_cc_overlay(words, timings, t_offset, duration, w, h)
    layers = [base, cap_clip]
    _add_logo(layers, duration, w, h)

    comp = CompositeVideoClip(layers, size=(w, h))
    comp = c_duration(comp, duration)
    return c_audio(comp, make_silence(duration))


def build_summary_clip(summary_img, words, timings, t_offset, duration, w, h, fps):
    """47–53s: ảnh bài 3 làm nền (tối) + CC caption lớn + brand logo."""
    bg   = ken_burns_clip(summary_img, duration, fps, w, h, zoom_in=False, max_zoom=1.10)
    dark = make_video_clip(lambda t: dark_overlay_frame(w, h, 120), duration)
    cap_clip = make_cc_overlay(words, timings, t_offset, duration, w, h,
                                font_size=SUMMARY_FONT_SIZE, y_ratio=0.42, max_line_words=4)
    layers = [bg, dark, cap_clip]
    _add_logo(layers, duration, w, h)

    comp = CompositeVideoClip(layers, size=(w, h))
    comp = c_duration(comp, duration)
    return c_audio(comp, make_silence(duration))


def build_outro_clip(main_img, channel, lang, words, timings, t_offset, duration, w, h, fps):
    """53–59s: CC caption (CTA) + credit nguồn (t.me/<channel>) + brand logo."""
    bg   = ken_burns_clip(main_img, duration, fps, w, h, zoom_in=True, max_zoom=1.12)
    dark = make_video_clip(lambda t: dark_overlay_frame(w, h, 150), duration)
    cap_clip = make_cc_overlay(words, timings, t_offset, duration, w, h,
                                y_ratio=0.55, max_line_words=4)

    credit_text = CREDIT_PREFIX.get(lang, CREDIT_PREFIX["en"]) + channel
    credit_clip = make_video_clip(
        lambda t: np.array(make_credit_frame(credit_text, w, h)),
        duration
    )

    layers = [bg, dark, cap_clip, credit_clip]
    _add_logo(layers, duration, w, h)

    comp = CompositeVideoClip(layers, size=(w, h))
    comp = c_duration(comp, duration)
    return c_audio(comp, make_silence(duration))


# ── 10. Save YT description + video metadata ──

def save_yt_description(posts, out_path):
    lines = []
    for i, post in enumerate(posts):
        lines.append(f"--- Bài {i+1} ---")
        lines.append(post["caption"])
        if post["hashtags"]:
            lines.append(" ".join(post["hashtags"]))
        lines.append("")
    all_tags, seen = [], set()
    for post in posts:
        for t in post["hashtags"]:
            if t.lower() not in seen:
                seen.add(t.lower())
                all_tags.append(t)
    if all_tags:
        lines.append("── Hashtags ──")
        lines.append(" ".join(all_tags))
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    log(f"✓ YouTube description saved → {out_path}")


def save_video_meta(posts, video_path, desc_path, out_path=VIDEO_META_FILE):
    """
    Lưu metadata để youtube_upload.py đọc & upload lên YouTube Shorts.
    Gồm: title, description, tags, playlist_id, privacy_status, video_path.
    """
    main_post  = posts[0]
    title_text = make_title_text(main_post["caption"], max_words=8)

    # Thu gọn title cho YT (≤100 ký tự)
    yt_title = f"{title_text} #Shorts"
    if len(yt_title) > 100:
        yt_title = yt_title[:97] + "…"

    all_tags = []
    seen = set()
    for post in posts:
        for t in post["hashtags"]:
            tag = t.lstrip("#")
            if tag.lower() not in seen:
                seen.add(tag.lower())
                all_tags.append(tag)
    # YouTube tag tối đa 500 ký tự tổng
    tags_joined = ", ".join(all_tags)
    if len(tags_joined) > 480:
        all_tags_trimmed = []
        used = 0
        for t in all_tags:
            if used + len(t) + 2 > 480:
                break
            all_tags_trimmed.append(t)
            used += len(t) + 2
        all_tags = all_tags_trimmed

    description_text = Path(desc_path).read_text(encoding="utf-8") if Path(desc_path).exists() else ""

    meta = {
        "title"          : yt_title,
        "description"    : description_text[:4900],   # YT giới hạn 5000 ký tự
        "tags"           : all_tags,
        "category_id"    : YT_CATEGORY_ID,
        "privacy_status" : YT_PRIVACY_STATUS,
        "playlist_id"    : YT_PLAYLIST_ID,
        "video_path"     : str(Path(video_path).resolve()),
        "created_at"     : datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tg_channel"     : TG_CHANNEL,
        "post_ids"       : [p.get("id") for p in posts if p.get("id")],
    }
    Path(out_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"✓ Video metadata saved → {out_path}")
    return meta


# ── 11. Main ─────────────────────────────────

def main():
    if not Path(BG_MUSIC).exists():
        print(f"[!] File không tìm thấy: {BG_MUSIC}")
        sys.exit(1)

    clip_files = list_clip_files(CLIPS_DIR)
    if not clip_files:
        print(f"[!] Không tìm thấy clip nào trong thư mục '{CLIPS_DIR}/'.")
        print(f"    Hãy bỏ vài file video (.mp4/.mov/...) vào đó — script sẽ tự")
        print(f"    chọn & cắt ngẫu nhiên để dựng Clip A (11–26s) và Clip B (32–47s).")
        sys.exit(1)
    log(f"Tìm thấy {len(clip_files)} clip trong '{CLIPS_DIR}/'")

    tmpdir = tempfile.mkdtemp()
    log(f"Temp: {tmpdir} | moviepy v2={MOVIEPY_V2}")

    # ── Chống trùng: load danh sách bài đã publish ──
    published_ids = load_published_ids()
    log(f"Đã có {len(published_ids)} bài đã publish trong {PUBLISHED_IDS_FILE}")

    posts = scrape_tg_channel(TG_CHANNEL, NUM_TG_POSTS, exclude_ids=published_ids)
    if not posts:
        print("[!] Không có bài mới nào (tất cả bài gần nhất đã được publish trước đó)")
        print("    Tăng TG_FETCH_LIMIT hoặc chờ kênh đăng bài mới.")
        # Xuất output rỗng cho GitHub Actions biết: không có gì để làm
        set_github_output("has_new_posts", "false")
        sys.exit(0)

    set_github_output("has_new_posts", "true")
    log(f"→ Sẽ tạo video từ {len(posts)} bài mới: "
        + ", ".join(p.get("id") or "?" for p in posts))

    save_yt_description(posts, YT_DESC_FILE)

    main_post    = posts[0]
    flash_post   = posts[1] if len(posts) > 1 else None
    summary_post = posts[2] if len(posts) > 2 else None

    log("Tải ảnh (bài 1 = nền chính, bài 2 = flash, bài 3 = summary)...")
    main_img    = download_image(main_post["img_url"])
    flash_img   = download_image(flash_post["img_url"])   if flash_post   else get_image_variant(main_img, 1)
    summary_img = download_image(summary_post["img_url"]) if summary_post else get_image_variant(flash_img, 2)

    title_text = make_title_text(main_post["caption"])

    # lang tạm (để chọn câu CTA outro) — dựa trên caption bài 1
    try:
        probe_lang = detect(main_post["caption"]) if main_post["caption"].strip() else "vi"
    except Exception:
        probe_lang = "vi"
    outro_text  = OUTRO_TEXT.get(probe_lang, OUTRO_TEXT["en"])
    script_text = build_script_text(posts, outro_text)
    log(f"Script ({len(script_text.split())} từ): {script_text[:140]}")

    log("Tạo TTS LIÊN TỤC cho toàn bộ video (auto-fit ~59s, không ngắt quãng)...")
    tts_path = os.path.join(tmpdir, "tts_full.mp3")
    lang = make_tts_fit(script_text, tts_path, TOTAL_DUR, pad=TTS_PAD)

    tts_clip    = AudioFileClip(tts_path)
    all_words   = script_text.split()
    all_timings = build_word_timings(all_words, tts_clip.duration, pad=0.3)

    w, h = OUTPUT_SIZE
    fps  = FPS

    log("Phân tích waveform nhạc nền (cho wiggle & zoom-pulse)...")
    amp_array = extract_amplitude(BG_MUSIC, fps, duration=TOTAL_DUR + 2)

    # mốc thời gian bắt đầu mỗi đoạn trong timeline 59s
    t_intro   = 0.0
    t_main    = t_intro + DUR_INTRO
    t_clipA   = t_main  + DUR_MAIN
    t_flash   = t_clipA + DUR_CLIPA
    t_clipB   = t_flash + DUR_FLASH
    t_summary = t_clipB + DUR_CLIPB
    t_outro   = t_summary + DUR_SUMMARY

    log("[0-3s]   INTRO — logo/tiêu đề + CC + brand watermark...")
    intro_clip = build_intro_clip(main_img, title_text, INTRO_LABEL.get(lang, INTRO_LABEL["en"]),
                                   all_words, all_timings, t_intro, DUR_INTRO, w, h, fps)

    log("[3-11s]  MAIN — ảnh bài 1 + CC + brand watermark...")
    main_clip = build_main_clip(main_img, all_words, all_timings, t_main, DUR_MAIN, w, h, fps, amp_array)

    log("[11-26s] CLIP A — jump-cut + PiP wiggle + CC + brand watermark...")
    clipA = build_clip_a(clip_files, main_img, all_words, all_timings, t_clipA, DUR_CLIPA, w, h, fps, amp_array)

    log("[26-32s] FLASH — ảnh bài 2 + CC + brand watermark...")
    flash_clip = build_flash_clip(flash_img, all_words, all_timings, t_flash, DUR_FLASH, w, h, fps, amp_array)

    log("[32-47s] CLIP B — jump-cut + zoom-pulse cao trào + CC + brand watermark...")
    clipB = build_clip_b(clip_files, all_words, all_timings, t_clipB, DUR_CLIPB, w, h, fps, amp_array)

    log("[47-53s] SUMMARY — ảnh bài 3 + CC + brand watermark...")
    summary_clip = build_summary_clip(summary_img, all_words, all_timings, t_summary, DUR_SUMMARY, w, h, fps)

    log("[53-59s] OUTRO — CC (CTA) + credit + brand watermark...")
    outro_clip = build_outro_clip(main_img, TG_CHANNEL, lang, all_words, all_timings, t_outro, DUR_OUTRO, w, h, fps)

    log("Nối 7 đoạn theo bố cục 59s...")
    segments  = [intro_clip, main_clip, clipA, flash_clip, clipB, summary_clip, outro_clip]
    final     = concatenate_videoclips(segments, method="compose")
    total_dur = final.duration
    log(f"Tổng thời lượng video: {total_dur:.2f}s (mục tiêu {TOTAL_DUR:.0f}s)")

    log("Mix âm thanh: TTS liên tục (1 track duy nhất) + nhạc nền...")
    tts_audio = c_vol(tts_clip, TTS_VOL)
    if tts_audio.duration > total_dur:
        tts_audio = c_subclip(tts_audio, 0, total_dur)

    bg_music = c_audio_loop(AudioFileClip(BG_MUSIC), total_dur)
    bg_music = c_vol(bg_music, BG_MUSIC_VOL)

    final = c_audio(final, CompositeAudioClip([tts_audio, bg_music]))

    log(f"Xuất {OUTPUT_VIDEO} ({total_dur:.1f}s)...")
    final.write_videofile(
        OUTPUT_VIDEO,
        codec="libx264",
        audio_codec="aac",
        fps=fps,
        logger="bar"
    )
    log(f"✓ Xong! → {OUTPUT_VIDEO}")

    # ── Lưu metadata cho bước upload YouTube ──
    meta = save_video_meta(posts, OUTPUT_VIDEO, YT_DESC_FILE)
    set_github_output("video_title", meta["title"])
    set_github_output("video_path",  OUTPUT_VIDEO)

    # ── Cập nhật .published_ids.json ──
    new_ids = {p["id"] for p in posts if p.get("id")}
    if new_ids:
        all_ids = published_ids | new_ids
        save_published_ids(all_ids)
        log(f"✓ Đánh dấu {len(new_ids)} bài đã publish: {', '.join(new_ids)}")
    else:
        log("  (bài không có id — bỏ qua cập nhật .published_ids.json)")


if __name__ == "__main__":
    main()
