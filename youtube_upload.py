"""
youtube_upload.py — Upload video lên YouTube Shorts + thêm vào playlist
==========================================================================
Đọc metadata từ video_meta.json (do tg_news_video_59s_best.py tạo ra),
upload video lên YouTube, sau đó thêm vào playlist đã cấu hình.

Cách xác thực (chọn 1 trong 2):

  A) Service Account (khuyên dùng cho GitHub Actions / CI):
     - Tạo Service Account trên Google Cloud Console
     - Cấp quyền Editor cho YouTube channel (qua YouTube Studio → Settings
       → Permissions → "Manage permissions" → thêm service account email
       với quyền Manager)
     - Tải file JSON credentials, lưu nội dung vào GitHub Secret:
         YOUTUBE_SERVICE_ACCOUNT_JSON
     - Workflow sẽ ghi ra file tạm và set biến môi trường:
         GOOGLE_APPLICATION_CREDENTIALS=path/to/sa.json

  B) OAuth 2.0 (dùng lần đầu / local):
     - Tạo OAuth 2.0 client ID (Desktop App) trên Google Cloud Console
     - Tải client_secret.json về cùng thư mục
     - Chạy lần đầu: python youtube_upload.py --auth
       → trình duyệt mở, đăng nhập, token được lưu vào token.json
     - Các lần sau dùng token.json tự động (hoặc set biến môi trường
         YOUTUBE_TOKEN_JSON với nội dung file token.json)

Dependencies:
  pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIG (override bằng env var hoặc args)
# ─────────────────────────────────────────────
VIDEO_META_FILE   = os.environ.get("VIDEO_META_FILE",   "video_meta.json")
CLIENT_SECRET_FILE = os.environ.get("CLIENT_SECRET_FILE", "client_secret.json")
TOKEN_FILE        = os.environ.get("TOKEN_FILE",         "token.json")
SCOPES            = ["https://www.googleapis.com/auth/youtube.upload",
                     "https://www.googleapis.com/auth/youtube"]

CHUNK_SIZE        = 8 * 1024 * 1024   # 8 MB per chunk
MAX_RETRIES       = 5
RETRY_DELAY_SEC   = 10


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def log(msg):
    print(f"[YT] {msg}", flush=True)


def set_github_output(name, value):
    out_file = os.environ.get("GITHUB_OUTPUT")
    if not out_file:
        return
    try:
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    except Exception:
        pass


def get_credentials():
    """
    Ưu tiên:
      1. Service Account (GOOGLE_APPLICATION_CREDENTIALS hoặc
         YOUTUBE_SERVICE_ACCOUNT_JSON env var)
      2. OAuth2 token.json / YOUTUBE_TOKEN_JSON env var
      3. OAuth2 interactive (client_secret.json)
    """
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    # ── Service Account từ env ──
    sa_json_str = os.environ.get("YOUTUBE_SERVICE_ACCOUNT_JSON")
    if sa_json_str:
        log("Dùng Service Account từ env YOUTUBE_SERVICE_ACCOUNT_JSON")
        info = json.loads(sa_json_str)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if sa_path and Path(sa_path).exists():
        log(f"Dùng Service Account từ file: {sa_path}")
        return service_account.Credentials.from_service_account_file(sa_path, scopes=SCOPES)

    # ── OAuth2 token từ env ──
    token_json_str = os.environ.get("YOUTUBE_TOKEN_JSON")
    if token_json_str:
        log("Dùng OAuth2 token từ env YOUTUBE_TOKEN_JSON")
        info = json.loads(token_json_str)
        creds = Credentials.from_authorized_user_info(info, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return creds

    # ── OAuth2 token.json file ──
    token_p = Path(TOKEN_FILE)
    if token_p.exists():
        log(f"Dùng OAuth2 token từ file: {TOKEN_FILE}")
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_p.write_text(creds.to_json(), encoding="utf-8")
        return creds

    # ── OAuth2 interactive ──
    if not Path(CLIENT_SECRET_FILE).exists():
        print(f"[!] Không tìm thấy credentials.\n"
              f"    Xem hướng dẫn trong file README.md → mục 'Xác thực YouTube'.")
        sys.exit(1)
    log("Mở trình duyệt để xác thực OAuth2 (lần đầu)...")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    token_p.write_text(creds.to_json(), encoding="utf-8")
    log(f"Token đã lưu → {TOKEN_FILE}")
    return creds


def build_youtube_service(creds):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds)


def upload_video(youtube, meta):
    """
    Upload video lên YouTube Shorts với resumable upload (hỗ trợ retry).
    Trả về video_id sau khi upload thành công.
    """
    from googleapiclient.http import MediaFileUpload

    video_path = meta["video_path"]
    if not Path(video_path).exists():
        print(f"[!] Không tìm thấy file video: {video_path}")
        sys.exit(1)

    body = {
        "snippet": {
            "title"      : meta["title"],
            "description": meta["description"],
            "tags"       : meta.get("tags", []),
            "categoryId" : meta.get("category_id", "25"),
        },
        "status": {
            "privacyStatus"         : meta.get("privacy_status", "public"),
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        chunksize=CHUNK_SIZE,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    log(f"Bắt đầu upload: {Path(video_path).name} ({Path(video_path).stat().st_size / 1e6:.1f} MB)")
    log(f"  Title: {meta['title']}")

    response = None
    attempt  = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"\r  Upload: {pct}%", end="", flush=True)
        except Exception as e:
            attempt += 1
            if attempt > MAX_RETRIES:
                print(f"\n[!] Upload thất bại sau {MAX_RETRIES} lần thử: {e}")
                sys.exit(1)
            log(f"\n  Lỗi (lần {attempt}/{MAX_RETRIES}): {e}. Thử lại sau {RETRY_DELAY_SEC}s...")
            time.sleep(RETRY_DELAY_SEC)

    print()  # newline sau progress
    video_id = response["id"]
    log(f"✓ Upload xong! Video ID: {video_id}")
    log(f"  URL: https://www.youtube.com/shorts/{video_id}")
    return video_id


def add_to_playlist(youtube, video_id, playlist_id):
    """Thêm video vào playlist YouTube."""
    if not playlist_id:
        log("Không có playlist_id — bỏ qua bước thêm vào playlist")
        return
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind"   : "youtube#video",
                        "videoId": video_id,
                    },
                }
            },
        ).execute()
        log(f"✓ Đã thêm vào playlist: https://www.youtube.com/playlist?list={playlist_id}")
    except Exception as e:
        log(f"  ⚠ Không thêm được vào playlist: {e}")


def main():
    parser = argparse.ArgumentParser(description="Upload video lên YouTube Shorts")
    parser.add_argument("--meta",  default=VIDEO_META_FILE,
                        help=f"Đường dẫn file metadata JSON (mặc định: {VIDEO_META_FILE})")
    parser.add_argument("--auth",  action="store_true",
                        help="Chỉ xác thực OAuth2 (tạo token.json), không upload")
    parser.add_argument("--dry-run", action="store_true",
                        help="In thông tin sẽ upload nhưng không thực sự gửi lên YouTube")
    args = parser.parse_args()

    creds   = get_credentials()
    youtube = build_youtube_service(creds)

    if args.auth:
        log("✓ Xác thực thành công. Token đã lưu.")
        sys.exit(0)

    meta_path = Path(args.meta)
    if not meta_path.exists():
        print(f"[!] Không tìm thấy file metadata: {meta_path}")
        print(f"    Hãy chạy tg_news_video_59s_best.py trước để tạo video và metadata.")
        sys.exit(1)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    log(f"Đọc metadata từ {meta_path}")

    if args.dry_run:
        log("=== DRY RUN — không upload thật ===")
        log(f"  Video : {meta['video_path']}")
        log(f"  Title : {meta['title']}")
        log(f"  Tags  : {', '.join(meta.get('tags', [])[:5])}...")
        log(f"  List  : {meta.get('playlist_id')}")
        sys.exit(0)

    video_id = upload_video(youtube, meta)
    add_to_playlist(youtube, video_id, meta.get("playlist_id"))

    # Lưu video_id vào metadata để tham chiếu sau
    meta["youtube_video_id"] = video_id
    meta["youtube_url"]      = f"https://www.youtube.com/shorts/{video_id}"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    set_github_output("youtube_video_id", video_id)
    set_github_output("youtube_url",      meta["youtube_url"])

    log(f"✓ Hoàn tất! → {meta['youtube_url']}")


if __name__ == "__main__":
    main()
