import glob
import hashlib
import os
import subprocess
from typing import List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from rich import print


# Query params to always strip from any URL
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid",
    "mc_cid", "mc_eid", "msclkid", "twclid",
    "igshid", "ig_rid",
    "ref", "ref_src", "ref_url", "referer",
    "feature", "src", "s", "share_id",
}

# YouTube: only these params matter for playback
YOUTUBE_KEEP_PARAMS = {"v", "t", "list", "index"}


# Reserve room for MP4 container overhead and two-pass rate-control error.
SIZE_SAFETY_MARGIN = 0.95

AUDIO_BUDGET_SHARE = 0.2
MIN_AUDIO_KBPS = 32
MAX_AUDIO_KBPS = 128
MIN_VIDEO_KBPS = 50

# (short side in px, minimum video kbps worth encoding at that size), largest first.
RESOLUTION_LADDER = ((1080, 1500), (720, 800), (480, 400), (360, 200), (240, 0))

TELEGRAM_UPLOAD_LIMIT_MB = 50
# Shrink only if the result can stay at 720p or better; split anything longer.
MIN_SHRINK_VIDEO_KBPS = 800
MAX_PART_SIZE_MB = 47
MAX_PARTS = 10
# Aim below the part limit, since bitrate varies across a video.
SPLIT_HEADROOM = 0.9
MAX_SPLIT_ATTEMPTS = 5


def clean_url(url: str) -> str:
    """Strip tracking/analytics parameters from URLs to improve privacy."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    # YouTube - keep only essential params
    if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        cleaned = {k: v for k, v in params.items() if k in YOUTUBE_KEEP_PARAMS}
    elif parsed.hostname in ("youtu.be",):
        # Short URLs like youtu.be/VIDEO_ID?si=tracking - strip everything
        cleaned = {k: v for k, v in params.items() if k in ("t",)}
    else:
        # Everything else - strip known tracking params
        cleaned = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}

    # Rebuild the query string (flatten single-value lists)
    query = urlencode({k: v[0] if len(v) == 1 else v for k, v in cleaned.items()}, doseq=True)
    return urlunparse(parsed._replace(query=query))


def url_to_filename(url: str):
    # Hash the full (already de-tracked) URL so every distinct link gets its own
    # file. Per-site parsing kept collapsing different videos to the same name
    # (YouTube ?v=, tweet /photo/1 suffixes, trailing slashes, etc.).
    vid_id = hashlib.md5(url.encode()).hexdigest()[:12]
    return f"videos/{vid_id}.mp4"


def filename_enumerated(vid_name: str, i: int):
    """
    vid_name: str example: "videos/1234567890.mp4"
    i: int example: 1

    returns: str example: "videos/1234567890_1.mp4"
    """
    vid_name_without_ext = vid_name[:-4]
    return f"{vid_name_without_ext}_{i}.mp4"


def get_vid_dimensions(vid_name: str):
    resp = None
    try:
        resp = subprocess.run(
            f"ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of default=noprint_wrappers=1:nokey=1 {vid_name}  | paste -sdx -",
            shell=True,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return None, None
    resp = f"{resp.stdout.decode()}"
    if "x" not in resp:
        return None, None
    width, height = resp.split("x")
    return width, height


def get_vid_length(vid_name: str) -> Optional[int]:
    resp = None
    try:
        resp = subprocess.run(
            f'ffprobe -i {vid_name} -show_entries format=duration -v quiet -of csv="p=0"',
            shell=True,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return None
    return int(float(resp.stdout.decode()))


def get_file_size_in_mb(file_path):
    return os.path.getsize(file_path) / (1024 * 1024)


def calculate_target_bitrate(vid_name: str, max_size_mb: int) -> float:
    """Total (video + audio) kbps that fits a video into max_size_mb.

    Bitrate (in kbps) = 1000*(Target size (in MB)×8/Duration (in seconds)), less a
    margin for container overhead and encoder rate-control error."""
    vid_length = get_vid_length(vid_name)
    if not vid_length:
        return 0
    return ((max_size_mb * 8) * 1000) / vid_length * SIZE_SAFETY_MARGIN


def _split_bitrate_budget(total_kbps: float) -> Tuple[int, int]:
    """Divide a total bitrate into (video_kbps, audio_kbps)."""
    audio_kbps = int(min(MAX_AUDIO_KBPS, max(MIN_AUDIO_KBPS, total_kbps * AUDIO_BUDGET_SHARE)))
    return int(total_kbps - audio_kbps), audio_kbps


def _short_side_for_bitrate(video_kbps: float) -> int:
    """Largest short side (px) the video bitrate can encode without turning to mush."""
    for short_side, min_kbps in RESOLUTION_LADDER:
        if video_kbps >= min_kbps:
            return short_side
    return RESOLUTION_LADDER[-1][0]


def reduce_video_size(vid_name: str, *, max_size_mb: int = 50):
    if not vid_name:
        return
    if not max_size_mb:
        return vid_name
    if get_file_size_in_mb(vid_name) < max_size_mb:
        return vid_name

    video_kbps, audio_kbps = _split_bitrate_budget(
        calculate_target_bitrate(vid_name, max_size_mb)
    )
    if video_kbps < MIN_VIDEO_KBPS:
        print(f"ERROR: can't fit {vid_name} in {max_size_mb}MB, not reducing!")
        return vid_name

    vid_name_reduced = vid_name.replace(".mp4", "_reduced.mp4")
    passlog = f"{vid_name}.2pass"
    side = _short_side_for_bitrate(video_kbps)
    # Scales the short side so portrait videos work too; min() prevents upscaling.
    scale = f"scale='if(gt(iw,ih),-2,min(iw,{side}))':'if(gt(iw,ih),min(ih,{side}),-2)'"
    encode = [
        "ffmpeg", "-y", "-i", vid_name,
        "-vf", scale,
        "-c:v", "libx264", "-b:v", f"{video_kbps}k",
        "-pix_fmt", "yuv420p",
        "-passlogfile", passlog,
    ]
    first_pass = [*encode, "-pass", "1", "-an", "-f", "null", os.devnull]
    second_pass = [
        *encode, "-pass", "2",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart",
        vid_name_reduced,
    ]
    try:
        subprocess.run(first_pass, check=True)
        subprocess.run(second_pass, check=True)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: failed to reduce {vid_name}: {e}")
        return vid_name
    finally:
        for log_file in glob.glob(f"{glob.escape(passlog)}*"):
            os.remove(log_file)
    return vid_name_reduced


def split_video(vid_name: str, *, max_part_mb: int = MAX_PART_SIZE_MB) -> List[str]:
    """Split a video on keyframes without re-encoding, into parts under max_part_mb.

    Parts are contiguous, but bitrate varies, so if any part comes out too big the
    split is redone with shorter segments. Returns [vid_name] if ffmpeg fails."""
    stem = vid_name.replace(".mp4", "")
    part_glob = f"{glob.escape(stem)}_part*.mp4"
    segment_seconds = (
        get_vid_length(vid_name) * max_part_mb * SPLIT_HEADROOM / get_file_size_in_mb(vid_name)
    )
    parts = [vid_name]
    for _ in range(MAX_SPLIT_ATTEMPTS):
        for old_part in glob.glob(part_glob):
            os.remove(old_part)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", vid_name,
                    "-map", "0:v:0", "-map", "0:a?",
                    "-c", "copy",
                    "-f", "segment",
                    "-segment_time", str(segment_seconds),
                    "-reset_timestamps", "1",
                    "-segment_format_options", "movflags=+faststart",
                    f"{stem}_part%03d.mp4",
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"ERROR: failed to split {vid_name}: {e}")
            return [vid_name]
        parts = sorted(glob.glob(part_glob))
        largest_mb = max(get_file_size_in_mb(part) for part in parts)
        if largest_mb <= max_part_mb:
            return parts
        segment_seconds *= max_part_mb / largest_mb * SPLIT_HEADROOM
    print(f"ERROR: {vid_name} still has parts over {max_part_mb}MB after splitting!")
    return parts


def prepare_for_upload(vid_name: str) -> List[str]:
    """Return the file(s) to send so each fits Telegram's upload limit, in order.

    Shrinks a video if it can keep decent quality, otherwise splits it into parts. A
    video that would need more than MAX_PARTS parts is shrunk first, then split."""
    size_mb = get_file_size_in_mb(vid_name)
    if size_mb <= TELEGRAM_UPLOAD_LIMIT_MB:
        return [vid_name]
    total_kbps = calculate_target_bitrate(vid_name, TELEGRAM_UPLOAD_LIMIT_MB)
    if not total_kbps:
        return [vid_name]
    video_kbps, _ = _split_bitrate_budget(total_kbps)
    if video_kbps >= MIN_SHRINK_VIDEO_KBPS:
        return [reduce_video_size(vid_name)]
    # Splitting aims below the part limit, so that's what MAX_PARTS parts can really hold.
    max_split_size_mb = int(MAX_PARTS * MAX_PART_SIZE_MB * SPLIT_HEADROOM)
    if size_mb > max_split_size_mb:
        vid_name = reduce_video_size(vid_name, max_size_mb=max_split_size_mb)
    return split_video(vid_name)


def convert_gif_to_mp4(gif_path: str, mp4_path: str):
    if not mp4_path:
        mp4_path = gif_path.replace(".gif", ".mp4")
    subprocess.run(
        f'ffmpeg -i {gif_path} -movflags faststart -pix_fmt yuv420p -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" {mp4_path} -y',
        shell=True,
    )
    return mp4_path


def get_video_codec(vid_name: str) -> Optional[str]:
    """Return the video stream codec name (e.g. 'h264', 'av1', 'hevc') or None."""
    try:
        resp = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                vid_name,
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return None
    return resp.stdout.decode().strip() or None


def ensure_h264(vid_name: str) -> str:
    """Telegram's in-app player only handles H.264. Transcode if the file is AV1/HEVC/etc."""
    codec = get_video_codec(vid_name)
    if codec == "h264":
        return vid_name
    print(f"Video codec is {codec!r}, transcoding to h264 for Telegram compatibility...")
    transcoded = vid_name.replace(".mp4", "_h264.mp4")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", vid_name,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            transcoded,
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        print("Transcode failed:", result.stderr.decode()[-500:])
        return vid_name
    os.replace(transcoded, vid_name)
    return vid_name


def get_media_type(url):
    # Download a portion of the file and analyze it with ffprobe
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.decode().strip()


def get_media_info(url):
    # Send HEAD request to get metadata
    response = requests.head(url)

    # Get content type and content length
    # content_type = response.headers.get("Content-Type")
    # changed out for get_media_type
    content_length = int(response.headers.get("Content-Length", -1))  # Default to -1 if not found

    return {"url": url, "content_type": get_media_type(url), "content_length": content_length}


def best_vid_and_audio_steam(urls: List[str]) -> Tuple[Optional[str], Optional[str]]:
    # Get metadata for all URLs
    media_files = [get_media_info(url) for url in urls]

    # Separate video and audio
    videos = [f for f in media_files if "video" in f["content_type"]]
    audios = [f for f in media_files if "audio" in f["content_type"]]

    # Sort by content_length to get the largest files
    largest_video = max(videos, key=lambda x: x["content_length"], default=None)
    largest_audio = max(audios, key=lambda x: x["content_length"], default=None)

    return largest_video["url"], largest_audio["url"]


def download_mp4_from_vid_and_audio(vid_url: str, aud_url: str, vid_name: str):
    # Create a command list
    command = [
        "ffmpeg",
        "-y",  # Overwrite output files without asking
        "-i",
        vid_url,
        "-i",
        aud_url,
        "-c:v",
        "libx264",  # Video codec
        "-c:a",
        "aac",  # Audio codec
        "-b:a",
        "192k",  # Audio bitrate
        "-movflags",
        "+faststart",  # Fast start for web playback
        vid_name,
    ]

    # Run the command
    result = subprocess.run(command, text=True, capture_output=True)

    # Check if the command was successful
    if result.returncode == 0:
        print("Video created successfully.")
        return True
    else:
        print("Error occurred:")
        print(result.stderr)  # Print the error message
        return False
