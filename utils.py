import requests
from rich import print
import subprocess
import os
from typing import List, Optional, Tuple


def url_to_filename(url: str):
    if url.startswith("https://x.com/") or url.startswith("https://twitter.com/"):
        url = url.split("?")[0]
        vid_id = url.split("/")[-1]
        vid_name = f"{vid_id}.mp4"
    elif url.startswith("https://www.instagram.com/reel/"):
        url = url.split("?")[0]
        vid_id = url.split("/")[-2]
        vid_name = f"{vid_id}.mp4"
    elif url.startswith("https://instagram.com/") or url.startswith("https://www.instagram.com/"):
        url = url.split("?")[0]
        if url.endswith("/"):
            url = url[:-1]
        vid_id = url.split("/")[-1]
        vid_name = f"{vid_id}.mp4"
    elif url.startswith("https://www.reddit.com"):
        url = url.split("?")[0]
        vid_id = [_ for _ in url.split("/") if _][-1]
        vid_name = f"{vid_id}.mp4"
    else:
        print(f"This is not a known link! {url}")
        vid_name = "ERROR.mp4"
    return f"videos/{vid_name}"


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


def calculate_target_bitrate(vid_name: str, max_size_mb: int) -> int:
    """bitrate in kbps (killabits per second) for a video to be reduced to a certain size

    Bitrate (in kbps) = 1000*(Target size (in MB)×8/Duration (in seconds))"""
    vid_length = get_vid_length(vid_name)
    if not vid_length:
        return 0
    target_bitrate = ((max_size_mb * 8) * 1000) / vid_length
    if not target_bitrate:
        print("ERROR: Target bitrate is 0!")
        return 0
    return target_bitrate


def reduce_video_size(vid_name: str, *, max_size_mb: int = 50):
    if not vid_name:
        return
    if not max_size_mb:
        return vid_name
    if get_file_size_in_mb(vid_name) < max_size_mb:
        return vid_name
    vid_name_reduced = vid_name.replace(".mp4", "_reduced.mp4")
    target_bitrate = calculate_target_bitrate(vid_name, max_size_mb)
    subprocess.run(
        f"ffmpeg -y -i {vid_name} -b:v {target_bitrate}k -b:a 128k -maxrate {target_bitrate}k -bufsize {target_bitrate * 2}k {vid_name_reduced}",
        shell=True,
    )
    return vid_name_reduced


def convert_gif_to_mp4(gif_path: str, mp4_path: str):
    if not mp4_path:
        mp4_path = gif_path.replace(".gif", ".mp4")
    subprocess.run(
        f'ffmpeg -i {gif_path} -movflags faststart -pix_fmt yuv420p -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" {mp4_path} -y',
        shell=True,
    )
    return mp4_path


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
