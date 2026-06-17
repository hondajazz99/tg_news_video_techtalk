# short_creator.py
#
# Upgrade: 59s Structured Short — 9:16 vertical MP4 với bố cục 7 đoạn cố định:
#
#   0–11s   MAIN     : ảnh tin tức chính (Ken Burns) + TTS + CC caption lớn
#   11–26s  CLIP A   : video nhảy jump-cut + PiP ảnh nhỏ wiggle theo beat
#   26–32s  FLASH    : flash ảnh (wiggle + chớp trắng) + TTS + CC
#   32–47s  CLIP B   : video cao trào, jump-cut + zoom-pulse sync beat
#   47–53s  SUMMARY  : tóm tắt 1 dòng + TTS + CC
#   53–59s  CTA/OUTRO: CTA + credits (t.me/<channel>)
#
# TTS XUYÊN SUỐT — 1 file TTS duy nhất ghép toàn bộ caption, auto-fit ~59s.
# CC caption lớn dưới màn hình, highlight theo từ, KHÔNG có đoạn nào "câm".
# Nhạc nền ducked thấp (BG_MUSIC_VOL=0.12) vì TTS luôn đọc đè lên.
# Wiggle: freq 2–3Hz, amp 8–20px, tỉ lệ theo RMS amplitude nhạc nền.
# Transitions nhanh + motion blur giữa jump-cut segments.
# Font sans-serif (DejaVu Sans / FreeSans / system fallback).
#
import asyncio
import os
import json
import logging
import random
import requests
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# moviepy v1 / v2 compat
try:
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, ImageClip, ColorClip,
        CompositeVideoClip, concatenate_videoclips, CompositeAudioClip,
    )
    from moviepy.audio.AudioClip import AudioClip
    from moviepy.video.VideoClip import VideoClip
    MOVIEPY_V2 = False
except ModuleNotFoundError:
    from moviepy import (
        VideoFileClip, AudioFileClip, ImageClip, ColorClip,
        CompositeVideoClip, concatenate_videoclips, CompositeAudioClip,
        AudioClip, VideoClip,
    )
    MOVIEPY_V2 = True

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# moviepy compat helpers
# ---------------------------------------------------------------------------
def _c_duration(clip, d):
    return clip.with_duration(d)       if MOVIEPY_V2 else clip.set_duration(d)
def _c_audio(clip, a):
    return clip.with_audio(a)          if MOVIEPY_V2 else clip.set_audio(a)
def _c_vol(clip, v):
    return clip.with_volume_scaled(v)  if MOVIEPY_V2 else clip.volumex(v)
def _c_loop(clip, dur):
    if MOVIEPY_V2:
        from moviepy import vfx
        return clip.with_effects([vfx.Loop()]).subclipped(0, dur)
    return clip.loop(duration=dur).subclip(0, dur)
def _c_audio_loop(clip, dur):
    if MOVIEPY_V2:
        from moviepy import afx
        return clip.with_effects([afx.AudioLoop(duration=dur)])
    return clip.audio_loop(duration=dur)
def _c_subclip(clip, t1, t2):
    return clip.subclipped(t1, t2) if MOVIEPY_V2 else clip.subclip(t1, t2)
def _c_transform(clip, func):
    return clip.transform(func)    if MOVIEPY_V2 else clip.fl(func)
def _c_resize(clip, size):
    if MOVIEPY_V2:
        from moviepy import vfx
        return clip.with_effects([vfx.Resize(size)])
    return clip.resize(size)
def _c_crop(clip, x1, y1, x2, y2):
    if MOVIEPY_V2:
        from moviepy import vfx
        return clip.with_effects([vfx.Crop(x1=x1, y1=y1, x2=x2, y2=y2)])
    return clip.crop(x1=x1, y1=y1, x2=x2, y2=y2)


def _make_video_clip(frame_func, duration):
    if MOVIEPY_V2:
        return VideoClip(frame_func, duration=duration)
    return VideoClip(frame_func, duration=duration, ismask=False)


def _make_silence(duration, fps=44100):
    def frame(t):
        if np.isscalar(t):
            return np.array([0.0, 0.0])
        return np.zeros((len(t), 2))
    return AudioClip(frame, duration=duration, fps=fps)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_env_json(key: str, default: str = "[]") -> Union[list, dict]:
    try:
        value = os.getenv(key)
        if not value:
            logger.warning(f"Using default value for {key}")
            return json.loads(default)
        return json.loads(value)
    except Exception as e:
        logger.error(f"Error parsing {key}: {str(e)}")
        return json.loads(default)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class Config:
    # Telegram
    TELEGRAM_TOKEN: str
    TELEGRAM_CHANNELS: List[str]

    # YouTube
    YOUTUBE_CLIENT_SECRETS: dict
    TITLE_TEMPLATE: str = "Video Short - {date}"
    DESCRIPTION: str = "Automated YouTube Short created from Telegram content"
    TAGS: List[str] = field(default_factory=lambda: ["Shorts", "Auto-generated", "Telegram"])
    PRIVACY_STATUS: str = "private"
    PLAYLIST_ID: str = "PLL7BH4dMy6VxNRcIWVpKYlC-9AJR3Tytn"
    PUBLISH_DELAY_HOURS: int = 1
    BRAND_HASHTAGS: List[str] = field(default_factory=lambda: ["xeonbit24", "xeonbit24.com"])

    # Content / Layout
    MAX_DURATION: int = 59
    MUSIC_OPTION: str = "music.mp3"
    CLIPS_DIR: str = "clips"               # thư mục chứa clip .mp4/.mov/... cho đoạn A & B
    FONT_PATH: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_BOLD_PATH: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    OUTPUT_RESOLUTION: Tuple[int, int] = field(default_factory=lambda: (1080, 1920))
    LOGO_PATH: str = "brand_logo.png"
    LOGO_POSITION: str = "top-left"        # top-left / top-right / bottom-left / bottom-right
    LOGO_WIDTH_RATIO: float = 0.20
    LOGO_MARGIN_PX: int = 28
    LOGO_OPACITY: float = 0.92
    PUBLISHED_IDS_FILE: str = ".published_ids.json"
    TG_CHANNEL_NAME: str = "xeonbitchannel"  # hiển thị credit cuối video

    # Timeline (tổng phải = MAX_DURATION)
    DUR_MAIN: float = 11.0       # 0–11s
    DUR_CLIPA: float = 15.0      # 11–26s
    DUR_FLASH: float = 6.0       # 26–32s
    DUR_CLIPB: float = 15.0      # 32–47s
    DUR_SUMMARY: float = 6.0     # 47–53s
    DUR_OUTRO: float = 6.0       # 53–59s

    # Audio
    BG_MUSIC_VOL: float = 0.12   # nhạc nền thấp vì TTS luôn chạy
    TTS_VOL: float = 1.0
    TTS_SPEED_MIN: float = 1.10
    TTS_SPEED_MAX: float = 1.85
    TTS_PAD: float = 0.1

    # Wiggle
    WIGGLE_AMP_MIN_PX: int = 8
    WIGGLE_AMP_MAX_PX: int = 20
    WIGGLE_FREQ_MIN_HZ: float = 2.0
    WIGGLE_FREQ_MAX_HZ: float = 3.0
    WIGGLE_SMOOTH: int = 5

    # PiP (Clip A)
    PIP_SCALE: float = 0.42
    PIP_MARGIN: int = 24

    # CC Caption
    CC_FONT_SIZE: int = 62
    CC_BOX_ALPHA: int = 210
    CC_Y_RATIO: float = 0.78
    CC_MAX_LINE_WORDS: int = 4

    # Jump-cut
    JUMPCUT_SEGMENTS_A: int = 3
    JUMPCUT_SEGMENTS_B: int = 2
    EDGE_BLUR_SEC: float = 0.12
    EDGE_BLUR_RADIUS: int = 7
    BEAT_ZOOM_MAX: float = 0.07
    KEN_BURNS_ZOOM: float = 1.18

    # Intro label
    INTRO_LABEL: str = "BREAKING"
    OUTRO_CTA: str = "Follow the channel for latest news"


CLIP_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm")


# ---------------------------------------------------------------------------
# Telegram client
# ---------------------------------------------------------------------------
class TelegramClient:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.session = requests.Session()

    def get_latest_images(
        self,
        channel: str,
        published_ids: set,
        max_posts: int = 10,
    ) -> List[Tuple[str, str, str]]:
        results: List[Tuple[str, str, str]] = []
        try:
            url = f'{self.base_url}getUpdates?allowed_updates=["channel_post","message"]'
            updates = self.session.get(url).json()
            logger.info(f"Total updates received: {len(updates.get('result', []))}")
            if not updates["ok"]:
                logger.error(f"Failed to get updates: {updates}")
                return results

            for update in reversed(updates.get("result", [])):
                if len(results) >= max_posts:
                    break
                post = update.get("channel_post") or update.get("message", {})
                chat_username = "@" + (
                    post.get("sender_chat", {}).get("username")
                    or post.get("chat", {}).get("username", "")
                )
                if chat_username == channel and "photo" in post:
                    message_id = str(post.get("message_id", update.get("update_id", "")))
                    unique_key = f"{channel}:{message_id}"
                    if unique_key in published_ids:
                        continue
                    try:
                        photo = max(post["photo"], key=lambda x: x["file_size"])
                        file_resp = self.session.get(
                            f"{self.base_url}getFile?file_id={photo['file_id']}"
                        ).json()
                        file_path = file_resp["result"]["file_path"]
                        caption = post.get("caption", "No caption")
                        results.append((
                            f"https://api.telegram.org/file/bot{self.token}/{file_path}",
                            caption,
                            unique_key,
                        ))
                    except Exception as inner_e:
                        logger.error(f"Error resolving file for {unique_key}: {inner_e}")
        except Exception as e:
            logger.error(f"Error fetching telegram content: {str(e)}")
        return results


# ---------------------------------------------------------------------------
# Video creator — bố cục 59s có cấu trúc
# ---------------------------------------------------------------------------
class VideoCreator:
    def __init__(self, config: Config):
        self.config = config
        self.music_cache = Path(".music_cache")
        self.music_cache.mkdir(exist_ok=True)

        # Logo
        self._logo_clip_cache: dict = {}
        self._logo_arr: Optional[np.ndarray] = None
        self._logo_pos: Optional[Tuple[int, int]] = None
        if config.LOGO_PATH and Path(config.LOGO_PATH).exists():
            try:
                w, h = config.OUTPUT_RESOLUTION
                logo = Image.open(config.LOGO_PATH).convert("RGBA")
                lw = max(1, int(w * config.LOGO_WIDTH_RATIO))
                lh = max(1, int(lw * logo.height / logo.width))
                logo = logo.resize((lw, lh), Image.LANCZOS)
                if config.LOGO_OPACITY < 1.0:
                    r, g, b, a = logo.split()
                    a = a.point(lambda px: int(px * config.LOGO_OPACITY))
                    logo = Image.merge("RGBA", (r, g, b, a))
                m = config.LOGO_MARGIN_PX
                pos_map = {
                    "top-right":    (w - lw - m, m),
                    "bottom-left":  (m, h - lh - m),
                    "bottom-right": (w - lw - m, h - lh - m),
                }
                lx, ly = pos_map.get(config.LOGO_POSITION, (m, m))
                canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                canvas.paste(logo, (lx, ly), logo)
                self._logo_arr = np.array(canvas)
                logger.info(f"Logo loaded: {logo.size} at ({lx},{ly})")
            except Exception as e:
                logger.warning(f"Could not load logo: {e}")

    # ── Private helpers ────────────────────────────────────────────────

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        candidates = [
            self.config.FONT_BOLD_PATH,
            self.config.FONT_PATH,
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
        for p in candidates:
            if p and Path(p).exists():
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    def _download_music(self, url_or_path: str) -> Path:
        if url_or_path.startswith("http"):
            filename = self.music_cache / url_or_path.split("/")[-1]
            if not filename.exists():
                filename.write_bytes(requests.get(url_or_path).content)
            return filename
        return Path(url_or_path)

    def _cover_crop(self, img: Image.Image, tw: int, th: int) -> Image.Image:
        ow, oh = img.size
        scale = max(tw / ow, th / oh)
        nw, nh = int(ow * scale) + 1, int(oh * scale) + 1
        img = img.resize((nw, nh), Image.LANCZOS)
        x, y = (nw - tw) // 2, (nh - th) // 2
        return img.crop((x, y, x + tw, y + th))

    def _resize_clip_to_shorts(self, clip):
        tw, th = self.config.OUTPUT_RESOLUTION
        sw, sh = clip.w, clip.h
        scale = max(tw / sw, th / sh) if sw / sh > tw / th else tw / sw
        nw, nh = int(sw * scale), int(sh * scale)
        c = _c_resize(clip, (nw, nh))
        x1, y1 = (nw - tw) // 2, (nh - th) // 2
        return _c_crop(c, x1, y1, x1 + tw, y1 + th)

    def _ken_burns_clip(self, pil_img: Image.Image, duration: float,
                        zoom_in: bool = True, max_zoom: float = None) -> "VideoClip":
        if max_zoom is None:
            max_zoom = self.config.KEN_BURNS_ZOOM
        w, h = self.config.OUTPUT_RESOLUTION
        big_w, big_h = int(w * max_zoom), int(h * max_zoom)
        big = self._cover_crop(pil_img.convert("RGB"), big_w, big_h)
        big_arr = np.array(big)

        def make_frame(t):
            progress = min(max(t / duration, 0.0), 1.0)
            if not zoom_in:
                progress = 1.0 - progress
            win_w = max(int(big_w - (big_w - w) * progress), w)
            win_h = max(int(big_h - (big_h - h) * progress), h)
            x = (big_w - win_w) // 2
            y = (big_h - win_h) // 2
            crop = Image.fromarray(big_arr).crop((x, y, x + win_w, y + win_h))
            if (win_w, win_h) != (w, h):
                crop = crop.resize((w, h), Image.LANCZOS)
            return np.array(crop)

        return _make_video_clip(make_frame, duration)

    def _extract_amplitude(self, audio_path: str, fps: int, duration: float) -> np.ndarray:
        from scipy.io import wavfile
        tmp_wav = audio_path + ".amp.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-ac", "1", "-ar", "22050", tmp_wav],
            check=True, capture_output=True
        )
        sr, data = wavfile.read(tmp_wav)
        os.remove(tmp_wav)
        if data.dtype != np.float32:
            data = data.astype(np.float32) / np.iinfo(data.dtype).max
        n_frames = int(duration * fps)
        spf = max(1, int(sr / fps))
        amps = np.array([
            float(np.sqrt(np.mean((data[i*spf:(i+1)*spf] if i*spf < len(data) else np.array([0.0]))**2)))
            for i in range(n_frames)
        ])
        mx = amps.max()
        if mx > 0:
            amps /= mx
        k = self.config.WIGGLE_SMOOTH
        if k > 1:
            kernel = np.ones(k) / k
            amps = np.convolve(amps, kernel, mode="same")
            amps = np.clip(amps / (amps.max() + 1e-9), 0, 1)
        return amps

    def _make_wiggle_clip(self, pil_rgba: Image.Image, duration: float, fps: int,
                          amp_array: np.ndarray, t_offset: float,
                          base_x: int, base_y: int) -> "VideoClip":
        cfg = self.config
        w, h = cfg.OUTPUT_RESOLUTION
        img_arr = np.array(pil_rgba.convert("RGBA"))

        def make_frame(t):
            gi  = min(max(int((t + t_offset) * fps), 0), len(amp_array) - 1)
            amp = float(amp_array[gi])
            freq   = cfg.WIGGLE_FREQ_MIN_HZ + (cfg.WIGGLE_FREQ_MAX_HZ - cfg.WIGGLE_FREQ_MIN_HZ) * amp
            amp_px = cfg.WIGGLE_AMP_MIN_PX  + (cfg.WIGGLE_AMP_MAX_PX  - cfg.WIGGLE_AMP_MIN_PX)  * amp
            phase  = t * freq * 2 * np.pi
            dx     = int(amp_px * np.sin(phase))
            dy     = int(amp_px * 0.6 * np.sin(phase * 1.3 + 1.0))
            angle  = 2.0 + amp * 5.0 * np.sin(phase * 0.8 + 0.5)
            pil    = Image.fromarray(img_arr, "RGBA").rotate(angle, resample=Image.BICUBIC, expand=False)
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            canvas.paste(pil, (base_x + dx, base_y + dy), pil)
            return np.array(canvas)

        return _make_video_clip(make_frame, duration)

    def _apply_edge_blur(self, clip):
        dur = clip.duration
        blur_sec = self.config.EDGE_BLUR_SEC
        max_r = self.config.EDGE_BLUR_RADIUS

        def fx(get_frame, t):
            frame = get_frame(t)
            r = 0.0
            if t < blur_sec:
                r = max_r * (1 - t / blur_sec)
            elif t > dur - blur_sec:
                r = max_r * (1 - (dur - t) / blur_sec)
            if r > 0.4:
                return np.array(Image.fromarray(frame).filter(ImageFilter.GaussianBlur(radius=r)))
            return frame

        return _c_transform(clip, fx)

    def _pick_segment(self, path: str, seg_dur: float):
        clip = VideoFileClip(path)
        if clip.duration <= seg_dur:
            out = _c_loop(clip, seg_dur)
        else:
            start = random.uniform(0, clip.duration - seg_dur)
            out = _c_subclip(clip, start, start + seg_dur)
        return self._resize_clip_to_shorts(out).without_audio()

    def _build_jumpcut_sequence(self, clip_files: List[str], total_dur: float, n_seg: int):
        seg_dur = total_dur / n_seg
        segs = []
        for _ in range(n_seg):
            path = random.choice(clip_files)
            seg  = self._pick_segment(path, seg_dur)
            seg  = self._apply_edge_blur(seg)
            segs.append(seg)
        return concatenate_videoclips(segs, method="compose")

    def _apply_beat_zoom_pulse(self, clip, amp_array: np.ndarray, fps: int, t_offset: float):
        w, h = self.config.OUTPUT_RESOLUTION
        max_zoom = self.config.BEAT_ZOOM_MAX

        def fx(get_frame, t):
            frame = get_frame(t)
            gi = min(max(int((t + t_offset) * fps), 0), len(amp_array) - 1)
            amp = float(amp_array[gi])
            scale = 1.0 + amp * max_zoom
            nw, nh = int(w * scale), int(h * scale)
            pil = Image.fromarray(frame).resize((nw, nh), Image.BILINEAR)
            x = (nw - w) // 2; y = (nh - h) // 2
            return np.array(pil.crop((x, y, x + w, y + h)))

        return _c_transform(clip, fx)

    def _logo_overlay(self, duration: float) -> Optional["VideoClip"]:
        if self._logo_arr is None:
            return None
        arr = self._logo_arr
        return _make_video_clip(lambda t: arr, duration)

    def _add_logo(self, layers: list, duration: float):
        lc = self._logo_overlay(duration)
        if lc is not None:
            layers.append(lc)

    def _dark_overlay(self, duration: float, alpha: int = 120) -> "VideoClip":
        w, h = self.config.OUTPUT_RESOLUTION
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        arr[:, :, 3] = alpha
        return _make_video_clip(lambda t: arr, duration)

    def _flash_overlay(self, duration: float, flash_sec: float = 0.18) -> "VideoClip":
        w, h = self.config.OUTPUT_RESOLUTION

        def frame(t):
            alpha = max(0.0, 1 - t / flash_sec) if t < flash_sec else 0.0
            arr = np.full((h, w, 4), 255, dtype=np.uint8)
            arr[:, :, 3] = int(alpha * 255)
            return arr

        return _make_video_clip(frame, duration)

    # ── CC Caption renderer ────────────────────────────────────────────

    def _wrap_words(self, words: list, n: int) -> list:
        return [words[i:i+n] for i in range(0, len(words), n)]

    def _make_cc_frame(self, t_global: float, words: list, timings: list,
                       font_size: int = None, y_ratio: float = None,
                       max_line_words: int = None) -> np.ndarray:
        cfg = self.config
        if font_size is None:    font_size = cfg.CC_FONT_SIZE
        if y_ratio is None:      y_ratio   = cfg.CC_Y_RATIO
        if max_line_words is None: max_line_words = cfg.CC_MAX_LINE_WORDS

        w, h = cfg.OUTPUT_RESOLUTION
        img  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if not words or not timings:
            return np.array(img)

        fn  = self._load_font(font_size)
        fnh = self._load_font(int(font_size * 1.18))  # highlight font, slightly bigger

        COLOR_NORMAL    = (235, 235, 235, 255)
        COLOR_HIGHLIGHT = (255, 221, 0,   255)
        COLOR_DONE      = (130, 130, 130, 255)

        active_idx = len(timings) - 1
        for i, tm in enumerate(timings):
            if tm["start"] <= t_global < tm["end"]:
                active_idx = i; break
        if t_global < timings[0]["start"]:
            active_idx = 0

        all_lines = self._wrap_words(words, max_line_words)
        word_line: dict = {}
        wi = 0
        for li, line in enumerate(all_lines):
            for ci in range(len(line)):
                word_line[wi] = (li, ci); wi += 1

        active_line = word_line.get(active_idx, (0, 0))[0]
        # Show 2 lines at a time centred on active
        sl = max(0, active_line - 1)
        el = min(len(all_lines), sl + 2)
        sl = max(0, el - 2)
        visible = all_lines[sl:el]

        line_h = font_size + 16
        box_h  = line_h * len(visible) + 36
        box_w  = int(w * 0.92)
        box_x  = (w - box_w) // 2
        box_y  = int(h * y_ratio)

        draw.rounded_rectangle(
            [box_x - 20, box_y - 18, box_x + box_w + 20, box_y + box_h + 18],
            radius=20, fill=(0, 0, 0, cfg.CC_BOX_ALPHA)
        )

        gwi = sl * max_line_words
        for li, line_words in enumerate(visible):
            widths = []
            for ci, word in enumerate(line_words):
                f  = fnh if (gwi + ci) == active_idx else fn
                bb = draw.textbbox((0, 0), word + " ", font=f)
                widths.append(bb[2] - bb[0])
            x = (w - sum(widths)) // 2
            y = box_y + li * line_h + 14
            for ci, word in enumerate(line_words):
                wi2 = gwi + ci
                f   = fnh if wi2 == active_idx else fn
                col = COLOR_HIGHLIGHT if wi2 == active_idx else (COLOR_DONE if wi2 < active_idx else COLOR_NORMAL)
                draw.text((x, y), word, font=f, fill=col)
                bb = draw.textbbox((x, y), word + " ", font=f)
                x += bb[2] - bb[0]
            gwi += len(line_words)

        return np.array(img)

    def _make_cc_overlay(self, words: list, timings: list, t_offset: float,
                         duration: float, font_size: int = None,
                         y_ratio: float = None, max_line_words: int = None) -> "VideoClip":
        return _make_video_clip(
            lambda t: self._make_cc_frame(t + t_offset, words, timings,
                                          font_size=font_size, y_ratio=y_ratio,
                                          max_line_words=max_line_words),
            duration
        )

    def _make_title_frame(self, t: float, title_text: str) -> np.ndarray:
        w, h = self.config.OUTPUT_RESOLUTION
        img  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        f  = self._load_font(74)
        lf = self._load_font(32)

        progress = min(t / 0.4, 1.0)
        alpha = int(255 * progress)

        # Dark gradient top
        grad_h = int(h * 0.40)
        for gy in range(grad_h):
            a = int(180 * (1 - gy / grad_h))
            draw.line([(0, gy), (w, gy)], fill=(0, 0, 0, a))

        # Label badge
        label = self.config.INTRO_LABEL
        bb = draw.textbbox((0, 0), label, font=lf)
        lw, lh = bb[2] - bb[0], bb[3] - bb[1]
        label_y = int(h * 0.12)
        draw.rounded_rectangle(
            [(w - lw) // 2 - 22, label_y - 8, (w + lw) // 2 + 22, label_y + lh + 16],
            radius=14, fill=(230, 30, 30, alpha)
        )
        draw.text(((w - lw) // 2, label_y), label, font=lf, fill=(255, 255, 255, alpha))

        # Title text
        words = title_text.split()
        lines = self._wrap_words(words, 3)
        line_h = 84
        y0 = int(h * 0.20)
        for li, line in enumerate(lines):
            ls = " ".join(line)
            bb = draw.textbbox((0, 0), ls, font=f)
            tw = bb[2] - bb[0]
            x  = (w - tw) // 2
            y  = y0 + li * line_h
            # shadow
            for ox in range(-4, 5):
                for oy in range(-4, 5):
                    if ox != 0 or oy != 0:
                        draw.text((x + ox, y + oy), ls, font=f, fill=(0, 0, 0, min(alpha, 200)))
            draw.text((x, y), ls, font=f, fill=(255, 255, 255, alpha))

        return np.array(img)

    def _make_credit_frame(self, channel: str) -> np.ndarray:
        w, h = self.config.OUTPUT_RESOLUTION
        img  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        f    = self._load_font(30)
        text = f"Source: t.me/{channel}"
        bb   = draw.textbbox((0, 0), text, font=f)
        tw   = bb[2] - bb[0]
        draw.text(((w - tw) // 2, int(h * 0.88)), text, font=f, fill=(200, 200, 200, 210))
        return np.array(img)

    # ── Build word timings (linear, global 0–59s) ─────────────────────

    @staticmethod
    def _build_word_timings(words: list, duration: float, pad: float = 0.3) -> list:
        speak_dur = max(duration - pad, duration * 0.9)
        if not words:
            return []
        per_word = speak_dur / len(words)
        return [
            {"word": w, "start": i * per_word, "end": (i + 1) * per_word}
            for i, w in enumerate(words)
        ]

    # ── TTS: 1 file toàn video, auto-fit duration ─────────────────────

    async def _generate_continuous_tts(
        self, script_text: str, target_duration: float, out_path: Path
    ) -> Tuple[Optional[str], list]:
        """
        Tạo TTS liên tục cho toàn bộ script, tăng tốc atempo để khớp target_duration.
        Trả về (detected_lang, word_timings).
        """
        import re
        import edge_tts

        def strip_emojis(s: str) -> str:
            return re.sub(
                r"[🌀-🪿😀-🙏🚀-🛿☀-⛿✀-➿🤀-🧿🇠-🇿‍︀-️\U0001F300-\U0001FABF\U0001F600-\U0001F64F]+",
                "", s
            ).strip()

        clean = strip_emojis(script_text)
        if not clean:
            clean = "Next news will be very interesting."

        raw_path = Path(str(out_path) + ".raw.mp3")
        word_timings_raw: list = []

        communicate = edge_tts.Communicate(clean, voice="en-SG-LunaNeural")
        with open(str(raw_path), "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_timings_raw.append({
                        "word":  chunk["text"],
                        "start": chunk["offset"] / 10_000_000,
                        "dur":   chunk["duration"] / 10_000_000,
                    })

        raw_clip = AudioFileClip(str(raw_path))
        raw_dur  = raw_clip.duration
        raw_clip.close()
        logger.info(f"TTS raw duration: {raw_dur:.2f}s  target: {target_duration:.1f}s")

        avail = max(target_duration - self.config.TTS_PAD, 0.5)
        speed = raw_dur / avail
        speed = min(max(speed, self.config.TTS_SPEED_MIN), self.config.TTS_SPEED_MAX)

        if abs(speed - 1.0) < 0.02:
            raw_path.rename(out_path)
        else:
            atempo = f"atempo={speed:.4f}" if speed <= 2.0 else f"atempo=2.0,atempo={speed/2.0:.4f}"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(raw_path), "-filter:a", atempo, "-vn", str(out_path)],
                check=True, capture_output=True
            )
            if raw_path.exists():
                raw_path.unlink()

        final_clip = AudioFileClip(str(out_path))
        final_dur  = final_clip.duration
        final_clip.close()
        logger.info(f"TTS final duration: {final_dur:.2f}s  speed={speed:.2f}x")

        # Re-scale timings to match actual final_dur
        if word_timings_raw:
            scale = final_dur / raw_dur if raw_dur > 0 else 1.0
            words = [wt["word"] for wt in word_timings_raw]
            timings = [
                {
                    "word":  wt["word"],
                    "start": wt["start"] * scale,
                    "end":   (wt["start"] + wt["dur"]) * scale,
                }
                for wt in word_timings_raw
            ]
        else:
            words = clean.split()
            timings = self._build_word_timings(words, final_dur)

        return "en", timings

    # ── Segment builders ──────────────────────────────────────────────

    def _composite(self, layers: list, duration: float):
        w, h = self.config.OUTPUT_RESOLUTION
        comp = CompositeVideoClip(layers, size=(w, h))
        comp = _c_duration(comp, duration)
        return _c_audio(comp, _make_silence(duration))

    def _build_main(self, img: Image.Image, words: list, timings: list,
                    t_offset: float, duration: float, fps: int) -> "CompositeVideoClip":
        """0–11s: ảnh tin tức chính + TTS + CC."""
        bg   = self._ken_burns_clip(img, duration, zoom_in=True)
        title_text = " ".join(words[:6]) + ("…" if len(words) > 6 else "")
        title_clip = _make_video_clip(
            lambda t: self._make_title_frame(t, title_text),
            duration
        )
        cc   = self._make_cc_overlay(words, timings, t_offset, duration)
        layers = [bg, title_clip, cc]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _build_clip_a(self, clip_files: List[str], pip_img: Image.Image,
                      words: list, timings: list, t_offset: float,
                      duration: float, fps: int, amp_array: np.ndarray) -> "CompositeVideoClip":
        """11–26s: jump-cut video + PiP ảnh nhỏ wiggle + CC."""
        base = self._build_jumpcut_sequence(clip_files, duration, self.config.JUMPCUT_SEGMENTS_A)
        base = _c_duration(base, duration)

        w, h = self.config.OUTPUT_RESOLUTION
        pip_w = int(w * self.config.PIP_SCALE)
        pip_h = int(pip_w * pip_img.height / pip_img.width)
        pip   = pip_img.resize((pip_w, pip_h), Image.LANCZOS)
        bx    = w - pip_w - self.config.PIP_MARGIN
        by    = self.config.PIP_MARGIN

        pip_clip = self._make_wiggle_clip(pip.convert("RGBA"), duration, fps, amp_array, t_offset, bx, by)
        cc       = self._make_cc_overlay(words, timings, t_offset, duration)
        layers   = [base, pip_clip, cc]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _build_flash(self, flash_img: Image.Image, words: list, timings: list,
                     t_offset: float, duration: float, fps: int,
                     amp_array: np.ndarray) -> "CompositeVideoClip":
        """26–32s: flash ảnh wiggle + chớp trắng + CC."""
        w, h = self.config.OUTPUT_RESOLUTION
        black  = _c_duration(ColorClip(size=(w, h), color=(10, 10, 10)), duration)
        full   = self._cover_crop(flash_img.convert("RGBA"), w, h)
        wiggle = self._make_wiggle_clip(full, duration, fps, amp_array, t_offset, 0, 0)
        flash  = self._flash_overlay(duration)
        cc     = self._make_cc_overlay(words, timings, t_offset, duration)
        layers = [black, wiggle, flash, cc]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _build_clip_b(self, clip_files: List[str], words: list, timings: list,
                      t_offset: float, duration: float, fps: int,
                      amp_array: np.ndarray) -> "CompositeVideoClip":
        """32–47s: video cao trào — jump-cut + zoom-pulse sync beat + CC."""
        base = self._build_jumpcut_sequence(clip_files, duration, self.config.JUMPCUT_SEGMENTS_B)
        base = _c_duration(base, duration)
        base = self._apply_beat_zoom_pulse(base, amp_array, fps, t_offset)
        cc   = self._make_cc_overlay(words, timings, t_offset, duration)
        layers = [base, cc]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _build_summary(self, summary_img: Image.Image, words: list, timings: list,
                       t_offset: float, duration: float, fps: int) -> "CompositeVideoClip":
        """47–53s: ảnh bài 3 + tóm tắt 1 dòng + CC lớn giữa màn hình."""
        bg   = self._ken_burns_clip(summary_img, duration, zoom_in=False, max_zoom=1.10)
        dark = self._dark_overlay(duration, alpha=120)
        cc   = self._make_cc_overlay(words, timings, t_offset, duration,
                                     font_size=int(self.config.CC_FONT_SIZE * 0.85),
                                     y_ratio=0.42, max_line_words=4)
        layers = [bg, dark, cc]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _build_outro(self, main_img: Image.Image, channel: str,
                     words: list, timings: list, t_offset: float,
                     duration: float, fps: int) -> "CompositeVideoClip":
        """53–59s: CTA + credits."""
        bg      = self._ken_burns_clip(main_img, duration, zoom_in=True, max_zoom=1.12)
        dark    = self._dark_overlay(duration, alpha=150)
        cc      = self._make_cc_overlay(words, timings, t_offset, duration, y_ratio=0.52)
        credit_arr = self._make_credit_frame(channel)
        credit  = _make_video_clip(lambda t: credit_arr, duration)
        layers  = [bg, dark, cc, credit]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    # ── List clip files ────────────────────────────────────────────────

    def _list_clips(self) -> List[str]:
        p = Path(self.config.CLIPS_DIR)
        if not p.exists():
            return []
        return [str(f) for f in sorted(p.iterdir())
                if f.is_file() and f.suffix.lower() in CLIP_EXTS]

    # ── Public API ─────────────────────────────────────────────────────

    async def create_compiled_short(
        self, posts: List[Tuple[str, str, str]]
    ) -> Tuple[Optional[Path], List[str]]:
        """
        Biên soạn một short 9:16, 59s có bố cục cố định từ *posts*:
          bài 1 → ảnh MAIN + PiP overlay
          bài 2 → ảnh FLASH (fallback: lật bài 1)
          bài 3 → ảnh SUMMARY (fallback: lật bài 2)
        Caption của TẤT CẢ bài được ghép thành 1 script TTS liên tục.
        """
        cfg  = self.config
        fps  = 30
        w, h = cfg.OUTPUT_RESOLUTION
        tmp_tts = Path("temp_tts_full.mp3")

        TOTAL = float(cfg.MAX_DURATION)  # 59.0

        # ── 1. Download images ─────────────────────────────────────────
        imgs: List[Image.Image] = []
        all_captions: List[str] = []
        for img_url, caption, _ in posts:
            try:
                data = requests.get(img_url, timeout=30).content
                img  = Image.open(__import__("io").BytesIO(data)).convert("RGBA")
                imgs.append(img)
                all_captions.append(caption if caption != "No caption" else "")
            except Exception as e:
                logger.error(f"Image download failed: {e}")

        if not imgs:
            logger.error("No images downloaded.")
            return None, []

        def variant(img: Image.Image, idx: int) -> Image.Image:
            return img.transpose(Image.FLIP_LEFT_RIGHT if idx % 2 == 1 else Image.FLIP_TOP_BOTTOM)

        main_img    = imgs[0]
        flash_img   = imgs[1] if len(imgs) > 1 else variant(main_img, 1)
        summary_img = imgs[2] if len(imgs) > 2 else variant(flash_img, 2)

        # ── 2. Build script & continuous TTS ──────────────────────────
        cta_text = cfg.OUTRO_CTA
        script   = " ".join(c.strip() for c in all_captions if c.strip())
        if not script:
            script = "Breaking news. Stay tuned for updates."
        script = f"{script} {cta_text}"

        logger.info(f"TTS script ({len(script.split())} words): {script[:120]}...")
        lang, word_timings = await self._generate_continuous_tts(script, TOTAL, tmp_tts)
        all_words = [wt["word"] for wt in word_timings]

        # ── 3. Amplitude array for wiggle & zoom-pulse ─────────────────
        music_path = self._download_music(cfg.MUSIC_OPTION)
        amp_array  = np.zeros(int(TOTAL * fps) + 10)
        if music_path.exists():
            try:
                amp_array = self._extract_amplitude(str(music_path), fps, TOTAL + 2)
            except Exception as e:
                logger.warning(f"Amplitude extraction failed: {e} — wiggle disabled")

        # ── 4. Timeline offsets ────────────────────────────────────────
        t_main    = 0.0
        t_clipa   = t_main  + cfg.DUR_MAIN
        t_flash   = t_clipa + cfg.DUR_CLIPA
        t_clipb   = t_flash + cfg.DUR_FLASH
        t_summary = t_clipb + cfg.DUR_CLIPB
        t_outro   = t_summary + cfg.DUR_SUMMARY

        # ── 5. Check clip files ────────────────────────────────────────
        clip_files = self._list_clips()
        if not clip_files:
            logger.warning(
                f"No video clips found in '{cfg.CLIPS_DIR}/'. "
                "Clip A & B will use static images instead."
            )

        # ── 6. Build segments ──────────────────────────────────────────
        logger.info("[0–11s]   MAIN — ảnh chính + CC + title...")
        seg_main = self._build_main(main_img, all_words, word_timings, t_main, cfg.DUR_MAIN, fps)

        if clip_files:
            logger.info("[11–26s]  CLIP A — jump-cut + PiP wiggle + CC...")
            seg_clipa = self._build_clip_a(
                clip_files, main_img, all_words, word_timings, t_clipa, cfg.DUR_CLIPA, fps, amp_array
            )
        else:
            logger.info("[11–26s]  CLIP A (fallback: ảnh tĩnh + wiggle)...")
            seg_clipa = self._build_flash(
                main_img, all_words, word_timings, t_clipa, cfg.DUR_CLIPA, fps, amp_array
            )

        logger.info("[26–32s]  FLASH — ảnh 2 wiggle + chớp + CC...")
        seg_flash = self._build_flash(
            flash_img, all_words, word_timings, t_flash, cfg.DUR_FLASH, fps, amp_array
        )

        if clip_files:
            logger.info("[32–47s]  CLIP B — jump-cut cao trào + zoom-pulse + CC...")
            seg_clipb = self._build_clip_b(
                clip_files, all_words, word_timings, t_clipb, cfg.DUR_CLIPB, fps, amp_array
            )
        else:
            logger.info("[32–47s]  CLIP B (fallback: ảnh tĩnh)...")
            seg_clipb = self._build_main(
                flash_img, all_words, word_timings, t_clipb, cfg.DUR_CLIPB, fps
            )

        logger.info("[47–53s]  SUMMARY — ảnh 3 + tóm tắt + CC...")
        seg_summary = self._build_summary(
            summary_img, all_words, word_timings, t_summary, cfg.DUR_SUMMARY, fps
        )

        logger.info("[53–59s]  OUTRO — CTA + credits...")
        seg_outro = self._build_outro(
            main_img, cfg.TG_CHANNEL_NAME, all_words, word_timings, t_outro, cfg.DUR_OUTRO, fps
        )

        # ── 7. Concatenate segments ────────────────────────────────────
        segments  = [seg_main, seg_clipa, seg_flash, seg_clipb, seg_summary, seg_outro]
        logger.info("Nối 6 đoạn thành video 59s...")
        final = concatenate_videoclips(segments, method="compose")
        total_dur = final.duration
        logger.info(f"Tổng thời lượng: {total_dur:.2f}s")

        # ── 8. Mix audio: TTS (1 track) + bg music ────────────────────
        tts_clip = AudioFileClip(str(tmp_tts))
        tts_clip = _c_vol(tts_clip, cfg.TTS_VOL)
        if tts_clip.duration > total_dur:
            tts_clip = _c_subclip(tts_clip, 0, total_dur)

        audio_tracks = [tts_clip]

        if music_path.exists():
            bg = AudioFileClip(str(music_path))
            bg = _c_audio_loop(bg, total_dur)
            bg = _c_vol(bg, cfg.BG_MUSIC_VOL)
            audio_tracks.insert(0, bg)

        final = _c_audio(final, CompositeAudioClip(audio_tracks))

        # ── 9. Write output ────────────────────────────────────────────
        output_path = Path("output_short.mp4")
        logger.info(f"Xuất {output_path} ({total_dur:.1f}s)...")
        final.write_videofile(
            str(output_path),
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            logger="bar",
        )
        logger.info(f"✓ Compiled short saved: {output_path}")
        return output_path, all_captions

    # Clean up temp files
        if tmp_tts.exists():
            tmp_tts.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# YouTube uploader  (unchanged logic)
# ---------------------------------------------------------------------------
class YouTubeUploader:
    def __init__(self, credentials: dict):
        self.credentials = credentials

    def upload_short(self, video_path: Path, config: Config, caption: str = "", all_captions: List[str] = None):
        try:
            creds = Credentials.from_authorized_user_info(self.credentials)
            youtube = build("youtube", "v3", credentials=creds)

            if all_captions is None:
                all_captions = [caption] if caption else []

            import re as _re

            def sanitize_tag(t: str) -> str:
                t = t.strip("#.,!?:;\"'()[]{}").strip()
                t = _re.sub(r"[^\w\s]", "", t, flags=_re.UNICODE).strip()
                return t[:30] if t else ""

            caption_tags, seen_tags = [], set()
            for cap in all_captions:
                for word in cap.split():
                    clean = sanitize_tag(word.lower())
                    if len(clean) > 2 and clean not in seen_tags:
                        caption_tags.append(clean); seen_tags.add(clean)

            brand_tags = [sanitize_tag(t) for t in config.BRAND_HASHTAGS if sanitize_tag(t)]
            base_tags  = [sanitize_tag(t) for t in config.TAGS if sanitize_tag(t)]

            raw_tags = base_tags + brand_tags + caption_tags
            all_tags, seen_final, total_chars = [], set(), 0
            for t in raw_tags:
                if t in seen_final: continue
                if total_chars + len(t) + 1 > 500: break
                all_tags.append(t); seen_final.add(t); total_chars += len(t) + 1

            brand_hashtag_str  = " ".join(f"#{t}" for t in brand_tags)
            caption_hashtag_str = " ".join(f"#{t}" for t in caption_tags[:10])
            hashtags = f"{brand_hashtag_str} {caption_hashtag_str}".strip()

            date_str    = datetime.now().strftime("%Y-%m-%d %H:%M")
            primary_cap = all_captions[0] if all_captions else caption
            title       = f"Video Short {primary_cap[:50]} {date_str}"

            caption_lines = "\n".join(
                f"📌 {cap.strip()}" for cap in all_captions if cap and cap != "No caption"
            )
            caption_section = f"\n\n{caption_lines}" if caption_lines else ""
            description = (
                f"{config.DESCRIPTION}{caption_section}\n\n{hashtags}\n#Shorts\n\nxeonbit24.com"
            )

            publish_at = (
                datetime.now(timezone.utc) + timedelta(hours=config.PUBLISH_DELAY_HOURS)
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            logger.info(f"Scheduling publish at: {publish_at} UTC")

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": all_tags,
                    "categoryId": "22",
                },
                "status": {
                    "privacyStatus": "private",
                    "publishAt": publish_at,
                    "selfDeclaredMadeForKids": False,
                },
            }

            media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
            request = youtube.videos().insert(
                part=",".join(body.keys()), body=body, media_body=media
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Uploaded {int(status.progress() * 100)}%")

            video_id = response["id"]
            logger.info(f"Video uploaded: https://youtu.be/{video_id} (scheduled: {publish_at})")

            if config.PLAYLIST_ID:
                try:
                    youtube.playlistItems().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "playlistId": config.PLAYLIST_ID,
                                "resourceId": {"kind": "youtube#video", "videoId": video_id},
                            }
                        },
                    ).execute()
                    logger.info(f"Added to playlist: {config.PLAYLIST_ID}")
                except Exception as pe:
                    logger.error(f"Playlist insert failed: {pe}")

            return response
        except Exception as e:
            err_str = str(e)
            if "rateLimitExceeded" in err_str or "Video Uploads per day" in err_str:
                logger.error(
                    "YouTube daily upload quota exceeded. "
                    "Quota resets at midnight Pacific Time."
                )
                raise SystemExit(1)
            logger.error(f"Upload failed: {err_str}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def _main():
    try:
        config = Config(
            TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN"),
            TELEGRAM_CHANNELS=get_env_json("TELEGRAM_CHANNELS", '["@xeonbitchannel"]'),
            YOUTUBE_CLIENT_SECRETS=get_env_json("YOUTUBE_CLIENT_SECRETS", "{}"),
            TITLE_TEMPLATE=os.getenv("TITLE_TEMPLATE", "Video Short - {date}"),
            DESCRIPTION=os.getenv("DESCRIPTION", "Automated YouTube Short"),
            TAGS=get_env_json("TAGS", '["Shorts", "Auto-generated"]'),
            PRIVACY_STATUS=os.getenv("PRIVACY_STATUS", "private"),
            PLAYLIST_ID=os.getenv("PLAYLIST_ID", "PLL7BH4dMy6VxNRcIWVpKYlC-9AJR3Tytn"),
            PUBLISH_DELAY_HOURS=int(os.getenv("PUBLISH_DELAY_HOURS", 1)),
            BRAND_HASHTAGS=get_env_json("BRAND_HASHTAGS", '["xeonbit24", "xeonbit24.com"]'),
            MAX_DURATION=int(os.getenv("MAX_DURATION", 59)),
            MUSIC_OPTION=os.getenv("MUSIC_OPTION", "music.mp3"),
            CLIPS_DIR=os.getenv("CLIPS_DIR", "clips"),
            FONT_PATH=os.getenv("FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            FONT_BOLD_PATH=os.getenv("FONT_BOLD_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            LOGO_PATH=os.getenv("LOGO_PATH", "brand_logo.png"),
            LOGO_POSITION=os.getenv("LOGO_POSITION", "top-left"),
            LOGO_WIDTH_RATIO=float(os.getenv("LOGO_WIDTH_RATIO", "0.20")),
            LOGO_OPACITY=float(os.getenv("LOGO_OPACITY", "0.92")),
            TG_CHANNEL_NAME=os.getenv("TG_CHANNEL_NAME", "xeonbitchannel"),
            # Timeline — env override nếu cần điều chỉnh
            DUR_MAIN=float(os.getenv("DUR_MAIN", "11.0")),
            DUR_CLIPA=float(os.getenv("DUR_CLIPA", "15.0")),
            DUR_FLASH=float(os.getenv("DUR_FLASH", "6.0")),
            DUR_CLIPB=float(os.getenv("DUR_CLIPB", "15.0")),
            DUR_SUMMARY=float(os.getenv("DUR_SUMMARY", "6.0")),
            DUR_OUTRO=float(os.getenv("DUR_OUTRO", "6.0")),
            BG_MUSIC_VOL=float(os.getenv("BG_MUSIC_VOL", "0.12")),
            TTS_VOL=float(os.getenv("TTS_VOL", "1.0")),
            INTRO_LABEL=os.getenv("INTRO_LABEL", "BREAKING"),
            OUTRO_CTA=os.getenv("OUTRO_CTA", "Follow the channel for latest news. Like & Subscribe!"),
        )

        if not config.TELEGRAM_TOKEN:
            raise ValueError("TELEGRAM_TOKEN is required")
        if not config.YOUTUBE_CLIENT_SECRETS:
            raise ValueError("YOUTUBE_CLIENT_SECRETS must be configured")
        if not config.TELEGRAM_CHANNELS:
            raise ValueError("At least one TELEGRAM_CHANNEL must be specified")

        # ── Load published IDs ─────────────────────────────────────────
        published_ids_file = Path(config.PUBLISHED_IDS_FILE)
        published_ids: list = []
        if published_ids_file.exists():
            try:
                published_ids = json.loads(published_ids_file.read_text())
            except Exception as e:
                logger.warning(f"Could not load published IDs: {e}")

        # ── Gather new photos from all channels ────────────────────────
        telegram = TelegramClient(config.TELEGRAM_TOKEN)
        max_per_channel = int(os.getenv("MAX_TELEGRAM_POSTS", 1))
        all_posts: List[Tuple[str, str, str]] = []

        for channel in config.TELEGRAM_CHANNELS:
            posts = telegram.get_latest_images(channel, set(published_ids), max_posts=max_per_channel)
            all_posts.extend(posts)

        if not all_posts:
            logger.error("No new suitable content found.")
            return

        logger.info(
            f"Collected {len(all_posts)} image(s) across {len(config.TELEGRAM_CHANNELS)} channel(s). "
            "Building 59s structured short..."
        )

        # ── Build video ────────────────────────────────────────────────
        creator = VideoCreator(config)
        video_path, all_captions = await creator.create_compiled_short(all_posts)

        if not video_path or not video_path.exists():
            logger.error("Video creation failed — nothing to upload.")
            return

        # ── Upload to YouTube ──────────────────────────────────────────
        uploader = YouTubeUploader(config.YOUTUBE_CLIENT_SECRETS)
        upload_result = uploader.upload_short(
            video_path, config,
            caption=all_posts[0][1] if all_posts else "",
            all_captions=all_captions
        )

        # ── Save published IDs ─────────────────────────────────────────
        if upload_result:
            for _, _, unique_key in all_posts:
                if unique_key not in published_ids:
                    published_ids.append(unique_key)
            MAX_KEEP = 30
            if len(published_ids) > MAX_KEEP:
                published_ids = published_ids[-MAX_KEEP:]
            try:
                published_ids_file.write_text(json.dumps(published_ids))
                logger.info(f"Saved {len(all_posts)} new published ID(s).")
            except Exception as save_err:
                logger.warning(f"Could not save published IDs: {save_err}")
        else:
            logger.warning("Upload did not succeed — published IDs NOT saved.")

        if video_path.exists():
            video_path.unlink()
            logger.info("Cleaned up compiled video file.")

    except Exception:
        logger.exception("Fatal error in main process")
        sys.exit(1)


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
