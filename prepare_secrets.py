#!/usr/bin/env python3
"""
prepare_secrets.py — Đóng gói assets thành chuỗi base64 để paste vào GitHub Secrets
======================================================================================
Chạy một lần trên máy local:
  python prepare_secrets.py

Sau đó copy từng giá trị in ra → paste vào GitHub → Settings → Secrets and variables
→ Actions → New repository secret.
"""

import base64
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def encode_file(path):
    """Đọc file, trả về chuỗi base64 một dòng."""
    return base64.b64encode(Path(path).read_bytes()).decode()


def encode_tar(directory):
    """Nén thư mục thành tar.gz trong RAM, trả về base64."""
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name
    with tarfile.open(tmp_path, "w:gz") as tar:
        tar.add(directory, arcname=Path(directory).name)
    data = base64.b64encode(Path(tmp_path).read_bytes()).decode()
    Path(tmp_path).unlink()
    return data


def print_secret(name, value, max_preview=80):
    print(f"\n{'='*60}")
    print(f"Secret name : {name}")
    print(f"Length      : {len(value)} chars")
    print(f"Preview     : {value[:max_preview]}...")
    print(f"{'='*60}")
    # Ghi ra file để dễ copy
    out = Path(f".secret_{name}.txt")
    out.write_text(value + "\n", encoding="utf-8")
    print(f"→ Đã lưu ra: {out}  (copy toàn bộ nội dung file này vào Secret)")


def main():
    errors = []

    # ── clips/ ──────────────────────────────
    if Path("clips").is_dir() and list(Path("clips").iterdir()):
        print("[•] Đóng gói clips/ ...")
        print_secret("CLIPS_TAR_B64", encode_tar("clips"))
    else:
        errors.append("clips/  : thư mục không tồn tại hoặc rỗng")

    # ── bg_music.mp3 ────────────────────────
    if Path("bg_music.mp3").exists():
        print("[•] Encode bg_music.mp3 ...")
        print_secret("BG_MUSIC_B64", encode_file("bg_music.mp3"))
    else:
        errors.append("bg_music.mp3 : file không tồn tại")

    # ── brand_logo.png ──────────────────────
    if Path("brand_logo.png").exists():
        print("[•] Encode brand_logo.png ...")
        print_secret("BRAND_LOGO_B64", encode_file("brand_logo.png"))
    else:
        print("[•] brand_logo.png không có — bỏ qua (watermark sẽ tắt)")

    if errors:
        print("\n[!] Các file còn thiếu:")
        for e in errors:
            print(f"    {e}")
        sys.exit(1)

    print("\n✓ Xong! Paste nội dung các file .secret_*.txt vào GitHub Secrets.")
    print("  Sau đó xóa các file .secret_*.txt (chứa dữ liệu nhạy cảm!):")
    print("  rm -f .secret_*.txt")


if __name__ == "__main__":
    main()
