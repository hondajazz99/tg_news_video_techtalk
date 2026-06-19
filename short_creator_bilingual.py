# short_creator_bilingual.py
#
# Bilingual upgrade of short_creator_2_.py
#
# ARCHITECTURE:
#   1. Fetch posts from @xeonbitchannel (English)  → build EN video  → upload to YouTube EN channel
#   2. Fetch posts from @Techtalk66    (Vietnamese) → build VI video  → upload to YouTube VI channel
#
# Each language pipeline is completely independent:
#   - Separate TTS voice  (en-SG-LunaNeural  vs  vi-VN-HoaiMyNeural)
#   - Separate YouTube credentials / playlist / metadata
#   - Separate brand hashtags / CTA text
#   - Separate published-ID tracking files
#
# NEW ENV VARS:
#   YOUTUBE_CLIENT_SECRETS_EN   – OAuth JSON for English YT channel
#   YOUTUBE_CLIENT_SECRETS_VI   – OAuth JSON for Vietnamese YT channel
#   PLAYLIST_ID_EN / _VI        – YouTube playlist IDs
#   TG_CHANNEL_EN               – default "@xeonbitchannel"
#   TG_CHANNEL_VI               – default "@Techtalk66"
#   TG_CHANNEL_NAME_EN / _VI    – credit name (no @)
#   TTS_VOICE_EN / _VI          – override TTS voice
#   OUTRO_CTA_EN / _VI          – per-lang CTA text
#   INTRO_LABEL_EN / _VI        – badge label (BREAKING / TIN MỚI)
#   BRAND_HASHTAGS_EN / _VI     – JSON arrays
#   DESCRIPTION_EN / _VI
#   TAGS_EN / _VI               – JSON arrays
#   LOGO_PATH_EN / _VI          – optional separate logos
#   CLIPS_DIR_EN / _VI          – optional separate clips folders
#   PUBLISHED_IDS_FILE_EN / _VI – separate tracking files
#   PUBLISH_DELAY_HOURS_EN / _VI
#   MAX_TELEGRAM_POSTS          – max images per channel (default 3)
#
# All original shared env vars (TELEGRAM_TOKEN, MUSIC_OPTION, font paths,
# timeline durations, BG_MUSIC_VOL, etc.) continue to work as shared defaults.

import argparse
import asyncio
import os
import json
import logging
import random
import re as _re
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
    """Create VideoClip, auto-handling RGBA frames."""
    probe = frame_func(0)
    is_rgba = (isinstance(probe, np.ndarray) and probe.ndim == 3 and probe.shape[2] == 4)
    if not is_rgba:
        if MOVIEPY_V2:
            return VideoClip(frame_func, duration=duration)
        return VideoClip(frame_func, duration=duration, ismask=False)
    def rgb_frame(t):
        return frame_func(t)[:, :, :3]
    def mask_frame(t):
        return frame_func(t)[:, :, 3].astype(float) / 255.0
    if MOVIEPY_V2:
        clip = VideoClip(rgb_frame, duration=duration)
        mask = VideoClip(mask_frame, duration=duration, ismask=True)
    else:
        clip = VideoClip(rgb_frame, duration=duration, ismask=False)
        mask = VideoClip(mask_frame, duration=duration, ismask=True)
    return clip.set_mask(mask)


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
# Configuration  — one instance per language pipeline
# ---------------------------------------------------------------------------
@dataclass
class Config:
    # Core
    TELEGRAM_TOKEN: str
    TELEGRAM_CHANNEL: str

    # YouTube (per-language)
    YOUTUBE_CLIENT_SECRETS: dict
    PLAYLIST_ID: str = ""
    PUBLISH_DELAY_HOURS: int = 1
    PRIVACY_STATUS: str = "private"

    # Metadata
    TITLE_TEMPLATE: str = "Video Short - {date}"
    DESCRIPTION: str = "Automated YouTube Short"
    TAGS: List[str] = field(default_factory=lambda: ["Shorts", "Auto-generated"])
    BRAND_HASHTAGS: List[str] = field(default_factory=list)

    # Layout
    MAX_DURATION: int = 59
    MUSIC_OPTION: str = "music.mp3"
    CLIPS_DIR: str = "clips"
    FONT_PATH: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_BOLD_PATH: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    OUTPUT_RESOLUTION: Tuple[int, int] = field(default_factory=lambda: (1080, 1920))
    LOGO_PATH: str = "brand_logo.png"
    LOGO_POSITION: str = "top-left"
    LOGO_WIDTH_RATIO: float = 0.20
    LOGO_MARGIN_PX: int = 28
    LOGO_OPACITY: float = 0.92
    TG_CHANNEL_NAME: str = "xeonbitchannel"
    PUBLISHED_IDS_FILE: str = ".published_ids.json"

    # Timeline
    DUR_MAIN: float = 11.0
    DUR_CLIPA: float = 15.0
    DUR_FLASH: float = 6.0
    DUR_CLIPB: float = 15.0
    DUR_SUMMARY: float = 6.0
    DUR_OUTRO: float = 6.0

    # Audio
    BG_MUSIC_VOL: float = 0.12
    TTS_VOL: float = 1.0
    TTS_SPEED_MIN: float = 1.10
    TTS_SPEED_MAX: float = 1.85
    TTS_PAD: float = 0.1

    # TTS voice — set per pipeline
    TTS_VOICE: str = "en-SG-LunaNeural"

    # Wiggle
    WIGGLE_AMP_MIN_PX: int = 8
    WIGGLE_AMP_MAX_PX: int = 20
    WIGGLE_FREQ_MIN_HZ: float = 2.0
    WIGGLE_FREQ_MAX_HZ: float = 3.0
    WIGGLE_SMOOTH: int = 5

    # PiP
    PIP_SCALE: float = 0.42
    PIP_MARGIN: int = 24

    # CC
    CC_FONT_SIZE: int = 62
    CC_BOX_ALPHA: int = 210
    CC_Y_RATIO: float = 0.78
    CC_MAX_LINE_WORDS: int = 2

    # Jump-cut
    JUMPCUT_SEGMENTS_A: int = 3
    JUMPCUT_SEGMENTS_B: int = 2
    EDGE_BLUR_SEC: float = 0.12
    EDGE_BLUR_RADIUS: int = 7
    BEAT_ZOOM_MAX: float = 0.07
    KEN_BURNS_ZOOM: float = 1.18

    # Labels
    INTRO_LABEL: str = "BREAKING"
    OUTRO_CTA: str = "Follow the channel for latest news"

    # Language tag  ("en" | "vi")
    LANG: str = "en"


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
            logger.info(f"[{channel}] Updates received: {len(updates.get('result', []))}")
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
# Video creator
# ---------------------------------------------------------------------------
class VideoCreator:
    def __init__(self, config: Config):
        self.config = config
        self.music_cache = Path(".music_cache")
        self.music_cache.mkdir(exist_ok=True)
        self._logo_arr: Optional[np.ndarray] = None

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

    # ── helpers ───────────────────────────────────────────────────────
    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        candidates = [
            self.config.FONT_BOLD_PATH, self.config.FONT_PATH,
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
            x0 = (big_w - win_w) // 2
            y0 = (big_h - win_h) // 2
            region = big_arr[y0:y0 + win_h, x0:x0 + win_w]
            pil = Image.fromarray(region).resize((w, h), Image.BILINEAR)
            return np.array(pil)
        return _make_video_clip(make_frame, duration)

    def _extract_amplitude(self, music_path: str, fps: int, total_sec: float) -> np.ndarray:
        n = int(total_sec * fps) + 10
        try:
            clip = AudioFileClip(music_path)
            dur = min(clip.duration, total_sec + 2)
            clip = _c_subclip(clip, 0, dur)
            frames = clip.to_soundarray(fps=fps)
            clip.close()
            rms = np.sqrt(np.mean(frames ** 2, axis=1)) if frames.ndim > 1 else np.abs(frames)
            mn, mx = rms.min(), rms.max()
            if mx > mn:
                rms = (rms - mn) / (mx - mn)
            return np.pad(rms, (0, max(0, n - len(rms))))[:n]
        except Exception:
            return np.zeros(n)

    def _make_wiggle_clip(self, pil_img_rgba: Image.Image, duration: float,
                          fps: int, amp_array: np.ndarray,
                          t_offset: float, base_x: int, base_y: int) -> "VideoClip":
        cfg = self.config
        arr = np.array(pil_img_rgba)
        freq = random.uniform(cfg.WIGGLE_FREQ_MIN_HZ, cfg.WIGGLE_FREQ_MAX_HZ)
        phase = random.uniform(0, 2 * np.pi)
        h_img, w_img = arr.shape[:2]
        def frame(t):
            gi = min(int((t + t_offset) * fps), len(amp_array) - 1)
            amp_norm = float(amp_array[gi])
            amp = cfg.WIGGLE_AMP_MIN_PX + amp_norm * (cfg.WIGGLE_AMP_MAX_PX - cfg.WIGGLE_AMP_MIN_PX)
            dx = int(amp * np.sin(2 * np.pi * freq * (t + t_offset) + phase))
            dy = int(amp * 0.5 * np.cos(2 * np.pi * freq * (t + t_offset) + phase + 1.0))
            w_out, h_out = cfg.OUTPUT_RESOLUTION
            canvas = np.zeros((h_out, w_out, 4), dtype=np.uint8)
            px = base_x + dx; py = base_y + dy
            x1s = max(0, px); y1s = max(0, py)
            x2s = min(w_out, px + w_img); y2s = min(h_out, py + h_img)
            x1i = x1s - px; y1i = y1s - py
            if x2s > x1s and y2s > y1s:
                canvas[y1s:y2s, x1s:x2s] = arr[y1i:y1i+(y2s-y1s), x1i:x1i+(x2s-x1s)]
            return canvas
        return _make_video_clip(frame, duration)

    def _build_jumpcut_sequence(self, clip_files: List[str], duration: float,
                                n_segments: int) -> "VideoClip":
        chosen = random.sample(clip_files, min(n_segments, len(clip_files)))
        if not chosen:
            chosen = clip_files[:1]
        seg_dur = duration / len(chosen)
        tw, th = self.config.OUTPUT_RESOLUTION
        segs = []
        for cf in chosen:
            try:
                c = VideoFileClip(cf)
                if c.w < 2 or c.h < 2 or c.duration < 0.2:
                    raise ValueError(f"degenerate source clip ({c.w}x{c.h}, {c.duration}s)")
                c = self._resize_clip_to_shorts(c)
                if c.w != tw or c.h != th:
                    raise ValueError(f"resize produced unexpected size {c.w}x{c.h}")
                # Force-decode a frame now so corrupt files fail here, not deep
                # inside a later transform where the error is much harder to trace.
                test_frame = c.get_frame(0)
                if test_frame is None or test_frame.shape[0] < 2 or test_frame.shape[1] < 2:
                    raise ValueError("decoded frame is degenerate")
                start = random.uniform(0, max(0, c.duration - seg_dur - 0.1))
                c = _c_subclip(c, start, min(start + seg_dur, c.duration))
                c = _c_duration(c, seg_dur)
                segs.append(c)
            except Exception as e:
                logger.warning(f"Clip error {cf}: {e}")
        if not segs:
            w, h = self.config.OUTPUT_RESOLUTION
            return _c_duration(ColorClip(size=(w, h), color=(10, 10, 10)), duration)
        return concatenate_videoclips(segs, method="compose")

    def _apply_beat_zoom_pulse(self, clip, amp_array: np.ndarray, fps: int, t_offset: float):
        cfg = self.config
        def fx(get_frame, t):
            frame = get_frame(t)
            gi = min(int((t + t_offset) * fps), len(amp_array) - 1)
            z = 1.0 + float(amp_array[gi]) * cfg.BEAT_ZOOM_MAX
            h_f, w_f = frame.shape[:2]
            nw, nh = int(w_f * z), int(h_f * z)
            pil = Image.fromarray(frame).resize((nw, nh), Image.BILINEAR)
            x = (nw - w_f) // 2; y = (nh - h_f) // 2
            return np.array(pil.crop((x, y, x + w_f, y + h_f)))
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

    # ── CC caption ────────────────────────────────────────────────────
    def _wrap_words(self, words: list, n: int) -> list:
        return [words[i:i+n] for i in range(0, len(words), n)]

    def _make_cc_frame(self, t_global: float, words: list, timings: list,
                       font_size: int = None, y_ratio: float = None,
                       max_line_words: int = None) -> np.ndarray:
        cfg = self.config
        if font_size is None:      font_size      = cfg.CC_FONT_SIZE
        if y_ratio is None:        y_ratio        = cfg.CC_Y_RATIO
        if max_line_words is None: max_line_words = cfg.CC_MAX_LINE_WORDS
        w, h = cfg.OUTPUT_RESOLUTION
        img  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if not words or not timings:
            return np.array(img)
        fn  = self._load_font(font_size)
        fnh = self._load_font(int(font_size * 1.18))
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
                col = (COLOR_HIGHLIGHT if wi2 == active_idx
                       else (COLOR_DONE if wi2 < active_idx else COLOR_NORMAL))
                draw.text((x, y), word, font=f, fill=col)
                bb = draw.textbbox((x, y), word + " ", font=f)
                x += bb[2] - bb[0]
            gwi += len(line_words)
        return np.array(img)

    def _make_cc_overlay(self, words, timings, t_offset, duration,
                         font_size=None, y_ratio=None, max_line_words=None):
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
        grad_h = int(h * 0.40)
        for gy in range(grad_h):
            a = int(180 * (1 - gy / grad_h))
            draw.line([(0, gy), (w, gy)], fill=(0, 0, 0, a))
        label = self.config.INTRO_LABEL
        bb = draw.textbbox((0, 0), label, font=lf)
        lw2, lh2 = bb[2] - bb[0], bb[3] - bb[1]
        label_y = int(h * 0.12)
        draw.rounded_rectangle(
            [(w - lw2) // 2 - 22, label_y - 8, (w + lw2) // 2 + 22, label_y + lh2 + 16],
            radius=14, fill=(230, 30, 30, alpha)
        )
        draw.text(((w - lw2) // 2, label_y), label, font=lf, fill=(255, 255, 255, alpha))
        words_t = title_text.split()
        lines = self._wrap_words(words_t, 3)
        line_h = 84
        y0 = int(h * 0.20)
        for li, line in enumerate(lines):
            ls = " ".join(line)
            bb = draw.textbbox((0, 0), ls, font=f)
            tw2 = bb[2] - bb[0]
            x   = (w - tw2) // 2
            y   = y0 + li * line_h
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
        tw2  = bb[2] - bb[0]
        draw.text(((w - tw2) // 2, int(h * 0.88)), text, font=f, fill=(200, 200, 200, 210))
        return np.array(img)

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

    # ── TTS — voice comes from config.TTS_VOICE ───────────────────────
    async def _generate_continuous_tts(
        self, script_text: str, target_duration: float, out_path: Path,
    ) -> Tuple[str, list]:
        import edge_tts

        def strip_emojis(s: str) -> str:
            return _re.sub(
                r"[\U0001F300-\U0001FABF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
                r"\u2600-\u27BF\u2300-\u23FF]+",
                "", s
            ).strip()

        clean = strip_emojis(script_text)
        if not clean:
            clean = ("Next news will be very interesting." if self.config.LANG == "en"
                     else "Tin tức tiếp theo sẽ rất thú vị.")

        voice = self.config.TTS_VOICE
        logger.info(f"[{self.config.LANG.upper()}] TTS voice: {voice}")

        raw_path = Path(str(out_path) + ".raw.mp3")
        word_timings_raw: list = []

        communicate = edge_tts.Communicate(clean, voice=voice)
        with open(str(raw_path), "wb") as fh:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_timings_raw.append({
                        "word":  chunk["text"],
                        "start": chunk["offset"] / 10_000_000,
                        "dur":   chunk["duration"] / 10_000_000,
                    })

        raw_clip = AudioFileClip(str(raw_path))
        raw_dur  = raw_clip.duration
        raw_clip.close()
        logger.info(f"[{self.config.LANG.upper()}] TTS raw {raw_dur:.2f}s → target {target_duration:.1f}s")

        avail = max(target_duration - self.config.TTS_PAD, 0.5)
        speed = raw_dur / avail
        speed = min(max(speed, self.config.TTS_SPEED_MIN), self.config.TTS_SPEED_MAX)

        if abs(speed - 1.0) < 0.02:
            raw_path.rename(out_path)
        else:
            atempo = (f"atempo={speed:.4f}" if speed <= 2.0
                      else f"atempo=2.0,atempo={speed/2.0:.4f}")
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(raw_path), "-filter:a", atempo, "-vn", str(out_path)],
                check=True, capture_output=True
            )
            if raw_path.exists():
                raw_path.unlink()

        final_clip = AudioFileClip(str(out_path))
        final_dur  = final_clip.duration
        final_clip.close()
        logger.info(f"[{self.config.LANG.upper()}] TTS final {final_dur:.2f}s  speed={speed:.2f}x")

        if word_timings_raw:
            scale = final_dur / raw_dur if raw_dur > 0 else 1.0
            timings = [
                {"word": wt["word"],
                 "start": wt["start"] * scale,
                 "end": (wt["start"] + wt["dur"]) * scale}
                for wt in word_timings_raw
            ]
        else:
            words = clean.split()
            timings = self._build_word_timings(words, final_dur)

        return "ok", timings

    # ── Segment builders ──────────────────────────────────────────────
    def _composite(self, layers, duration):
        w, h = self.config.OUTPUT_RESOLUTION
        comp = CompositeVideoClip(layers, size=(w, h))
        comp = _c_duration(comp, duration)
        return _c_audio(comp, _make_silence(duration))

    def _build_main(self, img, words, timings, t_offset, duration, fps):
        bg   = self._ken_burns_clip(img, duration, zoom_in=True)
        title_text = " ".join(words[:6]) + ("…" if len(words) > 6 else "")
        title_clip = _make_video_clip(lambda t: self._make_title_frame(t, title_text), duration)
        cc   = self._make_cc_overlay(words, timings, t_offset, duration)
        layers = [bg, title_clip, cc]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _build_clip_a(self, clip_files, pip_img, words, timings, t_offset, duration, fps, amp_array):
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

    def _build_flash(self, flash_img, words, timings, t_offset, duration, fps, amp_array):
        w, h = self.config.OUTPUT_RESOLUTION
        black  = _c_duration(ColorClip(size=(w, h), color=(10, 10, 10)), duration)
        full   = self._cover_crop(flash_img.convert("RGBA"), w, h)
        wiggle = self._make_wiggle_clip(full, duration, fps, amp_array, t_offset, 0, 0)
        flash  = self._flash_overlay(duration)
        cc     = self._make_cc_overlay(words, timings, t_offset, duration)
        layers = [black, wiggle, flash, cc]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _build_clip_b(self, clip_files, words, timings, t_offset, duration, fps, amp_array):
        base = self._build_jumpcut_sequence(clip_files, duration, self.config.JUMPCUT_SEGMENTS_B)
        base = _c_duration(base, duration)
        base = self._apply_beat_zoom_pulse(base, amp_array, fps, t_offset)
        cc   = self._make_cc_overlay(words, timings, t_offset, duration)
        layers = [base, cc]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _build_summary(self, summary_img, words, timings, t_offset, duration, fps):
        bg   = self._ken_burns_clip(summary_img, duration, zoom_in=False, max_zoom=1.10)
        dark = self._dark_overlay(duration, alpha=120)
        cc   = self._make_cc_overlay(words, timings, t_offset, duration,
                                     font_size=int(self.config.CC_FONT_SIZE * 0.85),
                                     y_ratio=0.42, max_line_words=4)
        layers = [bg, dark, cc]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _build_outro(self, main_img, channel, words, timings, t_offset, duration, fps):
        bg         = self._ken_burns_clip(main_img, duration, zoom_in=True, max_zoom=1.12)
        dark       = self._dark_overlay(duration, alpha=150)
        cc         = self._make_cc_overlay(words, timings, t_offset, duration, y_ratio=0.52)
        credit_arr = self._make_credit_frame(channel)
        credit     = _make_video_clip(lambda t: credit_arr, duration)
        layers     = [bg, dark, cc, credit]
        self._add_logo(layers, duration)
        return self._composite(layers, duration)

    def _list_clips(self) -> List[str]:
        p = Path(self.config.CLIPS_DIR)
        if not p.exists():
            return []
        return [str(f) for f in sorted(p.iterdir())
                if f.is_file() and f.suffix.lower() in CLIP_EXTS]

    # ── Main entry ─────────────────────────────────────────────────────
    async def create_compiled_short(
        self, posts: List[Tuple[str, str, str]]
    ) -> Tuple[Optional[Path], List[str]]:
        cfg     = self.config
        fps     = 30
        TOTAL   = float(cfg.MAX_DURATION)
        lang_tag = cfg.LANG.upper()
        tmp_tts  = Path(f"temp_tts_{cfg.LANG}.mp3")

        # 1. Download images
        imgs: List[Image.Image] = []
        all_captions: List[str] = []
        for img_url, caption, _ in posts:
            try:
                data = requests.get(img_url, timeout=30).content
                img  = Image.open(__import__("io").BytesIO(data)).convert("RGBA")
                imgs.append(img)
                all_captions.append(caption if caption != "No caption" else "")
            except Exception as e:
                logger.error(f"[{lang_tag}] Image download failed: {e}")

        if not imgs:
            logger.error(f"[{lang_tag}] No images downloaded.")
            return None, []

        def variant(img, idx):
            return img.transpose(Image.FLIP_LEFT_RIGHT if idx % 2 == 1 else Image.FLIP_TOP_BOTTOM)

        main_img    = imgs[0]
        flash_img   = imgs[1] if len(imgs) > 1 else variant(main_img, 1)
        summary_img = imgs[2] if len(imgs) > 2 else variant(flash_img, 2)

        # 2. TTS script
        def _strip_hashtags(text):
            return _re.sub(r'\s*#\w+', '', text).strip()

        script = " ".join(_strip_hashtags(c).strip() for c in all_captions if c.strip())
        script = _re.sub(r'\s+', ' ', script).strip()
        if not script:
            script = ("Breaking news. Stay tuned." if cfg.LANG == "en"
                      else "Tin tức mới. Hãy theo dõi.")
        script = f"{script} {cfg.OUTRO_CTA}"

        logger.info(f"[{lang_tag}] Script ({len(script.split())} words): {script[:120]}...")
        _, word_timings = await self._generate_continuous_tts(script, TOTAL, tmp_tts)
        all_words = [wt["word"] for wt in word_timings]

        # 3. Amplitude
        music_path = self._download_music(cfg.MUSIC_OPTION)
        amp_array  = np.zeros(int(TOTAL * fps) + 10)
        if music_path.exists():
            try:
                amp_array = self._extract_amplitude(str(music_path), fps, TOTAL + 2)
            except Exception as e:
                logger.warning(f"[{lang_tag}] Amplitude failed: {e}")

        # 4. Timeline offsets
        t_main    = 0.0
        t_clipa   = t_main  + cfg.DUR_MAIN
        t_flash   = t_clipa + cfg.DUR_CLIPA
        t_clipb   = t_flash + cfg.DUR_FLASH
        t_summary = t_clipb + cfg.DUR_CLIPB
        t_outro   = t_summary + cfg.DUR_SUMMARY

        # 5. Clips
        clip_files = self._list_clips()
        if not clip_files:
            logger.warning(f"[{lang_tag}] No clips in '{cfg.CLIPS_DIR}/' — using static images.")

        # 6. Build segments
        logger.info(f"[{lang_tag}] [0–11s]  MAIN...")
        seg_main = self._build_main(main_img, all_words, word_timings, t_main, cfg.DUR_MAIN, fps)

        if clip_files:
            logger.info(f"[{lang_tag}] [11–26s] CLIP A...")
            seg_clipa = self._build_clip_a(
                clip_files, main_img, all_words, word_timings, t_clipa, cfg.DUR_CLIPA, fps, amp_array)
        else:
            seg_clipa = self._build_flash(
                main_img, all_words, word_timings, t_clipa, cfg.DUR_CLIPA, fps, amp_array)

        logger.info(f"[{lang_tag}] [26–32s] FLASH...")
        seg_flash = self._build_flash(
            flash_img, all_words, word_timings, t_flash, cfg.DUR_FLASH, fps, amp_array)

        if clip_files:
            logger.info(f"[{lang_tag}] [32–47s] CLIP B...")
            seg_clipb = self._build_clip_b(
                clip_files, all_words, word_timings, t_clipb, cfg.DUR_CLIPB, fps, amp_array)
        else:
            seg_clipb = self._build_main(
                flash_img, all_words, word_timings, t_clipb, cfg.DUR_CLIPB, fps)

        logger.info(f"[{lang_tag}] [47–53s] SUMMARY...")
        seg_summary = self._build_summary(
            summary_img, all_words, word_timings, t_summary, cfg.DUR_SUMMARY, fps)

        logger.info(f"[{lang_tag}] [53–59s] OUTRO...")
        seg_outro = self._build_outro(
            main_img, cfg.TG_CHANNEL_NAME, all_words, word_timings, t_outro, cfg.DUR_OUTRO, fps)

        # 7. Concatenate
        segments = [seg_main, seg_clipa, seg_flash, seg_clipb, seg_summary, seg_outro]
        logger.info(f"[{lang_tag}] Concatenating 6 segments...")
        final     = concatenate_videoclips(segments, method="compose")
        total_dur = final.duration

        # 8. Mix audio
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

        # 9. Write
        output_path = Path(f"output_short_{cfg.LANG}.mp4")
        logger.info(f"[{lang_tag}] Writing {output_path} ({total_dur:.1f}s)...")
        final.write_videofile(
            str(output_path), fps=fps, codec="libx264", audio_codec="aac", logger="bar"
        )
        logger.info(f"[{lang_tag}] ✓ Saved: {output_path}")

        if tmp_tts.exists():
            tmp_tts.unlink(missing_ok=True)

        return output_path, all_captions


# ---------------------------------------------------------------------------
# YouTube uploader
# ---------------------------------------------------------------------------
class YouTubeUploader:
    def __init__(self, credentials: dict):
        self.credentials = credentials

    def upload_short(self, video_path: Path, config: Config,
                     caption: str = "", all_captions: List[str] = None):
        try:
            creds   = Credentials.from_authorized_user_info(self.credentials)
            youtube = build("youtube", "v3", credentials=creds)

            if all_captions is None:
                all_captions = [caption] if caption else []

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
            raw_tags   = base_tags + brand_tags + caption_tags
            all_tags, seen_final, total_chars = [], set(), 0
            for t in raw_tags:
                if t in seen_final: continue
                if total_chars + len(t) + 1 > 500: break
                all_tags.append(t); seen_final.add(t); total_chars += len(t) + 1

            brand_hashtag_str   = " ".join(f"#{t}" for t in brand_tags)
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
                f"{config.DESCRIPTION}{caption_section}\n\n"
                f"{hashtags}\n#Shorts\n\nt.me/{config.TG_CHANNEL_NAME}"
            )

            publish_at = (
                datetime.now(timezone.utc) + timedelta(hours=config.PUBLISH_DELAY_HOURS)
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            logger.info(f"[{config.LANG.upper()}] Scheduling publish at: {publish_at} UTC")

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

            media   = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
            request = youtube.videos().insert(
                part=",".join(body.keys()), body=body, media_body=media
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"[{config.LANG.upper()}] Uploaded {int(status.progress() * 100)}%")

            video_id = response["id"]
            logger.info(f"[{config.LANG.upper()}] ✓ https://youtu.be/{video_id}  (scheduled: {publish_at})")

            if config.PLAYLIST_ID:
                try:
                    youtube.playlistItems().insert(
                        part="snippet",
                        body={"snippet": {
                            "playlistId": config.PLAYLIST_ID,
                            "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        }},
                    ).execute()
                    logger.info(f"Added to playlist: {config.PLAYLIST_ID}")
                except Exception as pe:
                    logger.error(f"Playlist insert failed: {pe}")

            return response

        except Exception as e:
            err_str = str(e)
            if "rateLimitExceeded" in err_str or "Video Uploads per day" in err_str:
                logger.error(f"[{config.LANG.upper()}] YouTube daily quota exceeded.")
                raise SystemExit(1)
            logger.error(f"[{config.LANG.upper()}] Upload failed: {err_str}")
            return None


# ---------------------------------------------------------------------------
# Per-language pipeline
# ---------------------------------------------------------------------------
async def run_language_pipeline(
    lang: str,
    tg_channel: str,
    tg_channel_name: str,
    tts_voice: str,
    yt_secrets: dict,
    published_ids_file: str,
    config_overrides: dict,
    telegram: TelegramClient,
    max_per_channel: int,
) -> bool:
    """Full fetch → create → upload pipeline for one language. Returns True on success."""
    lang_tag = lang.upper()
    logger.info(f"\n{'='*60}\n[{lang_tag}] Pipeline start — {tg_channel}\n{'='*60}")

    # Load published IDs
    pub_file = Path(published_ids_file)
    published_ids: list = []
    if pub_file.exists():
        try:
            published_ids = json.loads(pub_file.read_text())
        except Exception as e:
            logger.warning(f"[{lang_tag}] Could not load published IDs: {e}")

    # Fetch posts
    posts = telegram.get_latest_images(tg_channel, set(published_ids), max_posts=max_per_channel)
    if not posts:
        logger.error(f"[{lang_tag}] No new content from {tg_channel}.")
        return False

    logger.info(f"[{lang_tag}] {len(posts)} post(s) collected. Building 59s short...")

    # Build Config
    cfg = Config(
        TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN"),
        TELEGRAM_CHANNEL=tg_channel,
        YOUTUBE_CLIENT_SECRETS=yt_secrets,
        TG_CHANNEL_NAME=tg_channel_name,
        TTS_VOICE=tts_voice,
        LANG=lang,
        PUBLISHED_IDS_FILE=published_ids_file,
        **config_overrides,
    )

    # Create video
    creator = VideoCreator(cfg)
    video_path, all_captions = await creator.create_compiled_short(posts)

    if not video_path or not video_path.exists():
        logger.error(f"[{lang_tag}] Video creation failed.")
        return False

    # Upload
    uploader = YouTubeUploader(yt_secrets)
    result   = uploader.upload_short(
        video_path, cfg,
        caption=posts[0][1] if posts else "",
        all_captions=all_captions,
    )

    # Save published IDs
    if result:
        for _, _, uid in posts:
            if uid not in published_ids:
                published_ids.append(uid)
        MAX_KEEP = 30
        if len(published_ids) > MAX_KEEP:
            published_ids = published_ids[-MAX_KEEP:]
        try:
            pub_file.write_text(json.dumps(published_ids))
            logger.info(f"[{lang_tag}] Saved {len(posts)} new published ID(s).")
        except Exception as e:
            logger.warning(f"[{lang_tag}] Could not save published IDs: {e}")
    else:
        logger.warning(f"[{lang_tag}] Upload failed — IDs NOT saved.")

    if video_path.exists():
        video_path.unlink()
        logger.info(f"[{lang_tag}] Cleaned up {video_path}.")

    return bool(result)


# ---------------------------------------------------------------------------
# Shared overrides (same for both languages)
# ---------------------------------------------------------------------------
def _shared_overrides() -> dict:
    return dict(
        TITLE_TEMPLATE=os.getenv("TITLE_TEMPLATE", "Video Short - {date}"),
        MAX_DURATION=int(os.getenv("MAX_DURATION", 59)),
        MUSIC_OPTION=os.getenv("MUSIC_OPTION", "music.mp3"),
        FONT_PATH=os.getenv("FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        FONT_BOLD_PATH=os.getenv("FONT_BOLD_PATH",
                                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        LOGO_POSITION=os.getenv("LOGO_POSITION", "top-left"),
        LOGO_WIDTH_RATIO=float(os.getenv("LOGO_WIDTH_RATIO", "0.20")),
        LOGO_OPACITY=float(os.getenv("LOGO_OPACITY", "0.92")),
        PRIVACY_STATUS=os.getenv("PRIVACY_STATUS", "private"),
        DUR_MAIN=float(os.getenv("DUR_MAIN", "11.0")),
        DUR_CLIPA=float(os.getenv("DUR_CLIPA", "15.0")),
        DUR_FLASH=float(os.getenv("DUR_FLASH", "6.0")),
        DUR_CLIPB=float(os.getenv("DUR_CLIPB", "15.0")),
        DUR_SUMMARY=float(os.getenv("DUR_SUMMARY", "6.0")),
        DUR_OUTRO=float(os.getenv("DUR_OUTRO", "6.0")),
        BG_MUSIC_VOL=float(os.getenv("BG_MUSIC_VOL", "0.12")),
        TTS_VOL=float(os.getenv("TTS_VOL", "1.0")),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _parse_args():
    parser = argparse.ArgumentParser(description="Bilingual Telegram-to-Shorts pipeline")
    parser.add_argument(
        "--lang",
        choices=["en", "vi", "both"],
        default=os.getenv("RUN_LANG", "both"),
        help="Which language pipeline to run (default: both)",
    )
    return parser.parse_args()


async def _main():
    try:
        args = _parse_args()
        run_en = args.lang in ("en", "both")
        run_vi = args.lang in ("vi", "both")

        token = os.getenv("TELEGRAM_TOKEN")
        if not token:
            raise ValueError("TELEGRAM_TOKEN is required")

        yt_secrets_en = get_env_json("YOUTUBE_CLIENT_SECRETS_EN", "{}") if run_en else {}
        yt_secrets_vi = get_env_json("YOUTUBE_CLIENT_SECRETS_VI", "{}") if run_vi else {}
        if run_en and not yt_secrets_en:
            raise ValueError("YOUTUBE_CLIENT_SECRETS_EN must be configured")
        if run_vi and not yt_secrets_vi:
            raise ValueError("YOUTUBE_CLIENT_SECRETS_VI must be configured")

        telegram        = TelegramClient(token)
        max_per_channel = int(os.getenv("MAX_TELEGRAM_POSTS", 3))
        shared          = _shared_overrides()

        # ── English pipeline ─────────────────────────────────────────────
        if not run_en:
            logger.info("Skipping EN pipeline (--lang %s)", args.lang)
        en_overrides = {
            **shared,
            "DESCRIPTION":         os.getenv("DESCRIPTION_EN",
                                             "Latest crypto & tech news in English"),
            "TAGS":                get_env_json("TAGS_EN",
                                               '["Shorts","Crypto","Bitcoin","Tech"]'),
            "BRAND_HASHTAGS":      get_env_json("BRAND_HASHTAGS_EN",
                                               '["xeonbit24","xeonbit24.com"]'),
            "PLAYLIST_ID":         os.getenv("PLAYLIST_ID_EN", ""),
            "PUBLISH_DELAY_HOURS": int(os.getenv("PUBLISH_DELAY_HOURS_EN", 1)),
            "LOGO_PATH":           os.getenv("LOGO_PATH_EN",
                                            os.getenv("LOGO_PATH", "brand_logo.png")),
            "CLIPS_DIR":           os.getenv("CLIPS_DIR_EN",
                                            os.getenv("CLIPS_DIR", "clips")),
            "OUTRO_CTA":           os.getenv("OUTRO_CTA_EN",
                                            "Follow for latest crypto news. Like & Subscribe!"),
            "INTRO_LABEL":         os.getenv("INTRO_LABEL_EN", "BREAKING"),
        }
        if run_en:
            await run_language_pipeline(
                lang="en",
                tg_channel=os.getenv("TG_CHANNEL_EN", "@xeonbitchannel"),
                tg_channel_name=os.getenv("TG_CHANNEL_NAME_EN", "xeonbitchannel"),
                tts_voice=os.getenv("TTS_VOICE_EN", "en-SG-LunaNeural"),
                yt_secrets=yt_secrets_en,
                published_ids_file=os.getenv("PUBLISHED_IDS_FILE_EN", ".published_ids_en.json"),
                config_overrides=en_overrides,
                telegram=telegram,
                max_per_channel=max_per_channel,
            )

        # ── Vietnamese pipeline ───────────────────────────────────────────
        if not run_vi:
            logger.info("Skipping VI pipeline (--lang %s)", args.lang)
        vi_overrides = {
            **shared,
            "DESCRIPTION":         os.getenv("DESCRIPTION_VI",
                                             "Tin tức công nghệ và crypto bằng tiếng Việt"),
            "TAGS":                get_env_json("TAGS_VI",
                                               '["Shorts","CryptoViet","TinTuc","CongNghe"]'),
            "BRAND_HASHTAGS":      get_env_json("BRAND_HASHTAGS_VI",
                                               '["techtalk66","techtalk"]'),
            "PLAYLIST_ID":         os.getenv("PLAYLIST_ID_VI", ""),
            "PUBLISH_DELAY_HOURS": int(os.getenv("PUBLISH_DELAY_HOURS_VI", 1)),
            "LOGO_PATH":           os.getenv("LOGO_PATH_VI",
                                            os.getenv("LOGO_PATH", "brand_logo.png")),
            "CLIPS_DIR":           os.getenv("CLIPS_DIR_VI",
                                            os.getenv("CLIPS_DIR", "clips")),
            "OUTRO_CTA":           os.getenv("OUTRO_CTA_VI",
                                            "Theo dõi để cập nhật tin tức mới nhất. Like & Đăng ký!"),
            "INTRO_LABEL":         os.getenv("INTRO_LABEL_VI", "TIN MỚI"),
        }
        if run_vi:
            await run_language_pipeline(
                lang="vi",
                tg_channel=os.getenv("TG_CHANNEL_VI", "@Techtalk66"),
                tg_channel_name=os.getenv("TG_CHANNEL_NAME_VI", "Techtalk66"),
                tts_voice=os.getenv("TTS_VOICE_VI", "vi-VN-HoaiMyNeural"),
                yt_secrets=yt_secrets_vi,
                published_ids_file=os.getenv("PUBLISHED_IDS_FILE_VI", ".published_ids_vi.json"),
                config_overrides=vi_overrides,
                telegram=telegram,
                max_per_channel=max_per_channel,
            )

        ran = (["EN"] if run_en else []) + (["VI"] if run_vi else [])
        logger.info("\n✓ %s pipeline(s) completed.", " + ".join(ran))

    except SystemExit:
        raise
    except Exception:
        logger.exception("Fatal error in main process")
        sys.exit(1)


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
