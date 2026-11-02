"""Download all videos from a YouTube channel into a local folder.

Usage:
    python download_channel.py
    python download_channel.py https://www.youtube.com/@SomeOtherChannel/videos -o some_other_folder

Re-running is safe: already-downloaded videos are tracked in
<output_dir>/.archive.txt and skipped.
"""

import argparse
import os

import yt_dlp

DEFAULT_CHANNEL_URL = "https://www.youtube.com/@DannyGo/videos"
DEFAULT_OUTPUT_DIR = "dannygo"


def download_channel(channel_url: str, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        "outtmpl": os.path.join(output_dir, "%(upload_date)s - %(title)s [%(id)s].%(ext)s"),
        "download_archive": os.path.join(output_dir, ".archive.txt"),
        # Don't restrict to mp4/m4a here: YouTube only serves its highest
        # resolutions (1440p/4K) as VP9/AV1 in webm, so an mp4-only filter
        # silently caps quality at 1080p. Grab the actual best streams and
        # let yt-dlp remux/transcode to mp4 afterward instead.
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "ignoreerrors": True,  # keep going if one video fails/is private/etc.
        "no_warnings": True,
        # Matches the workaround used elsewhere in this repo for yt-dlp's
        # YouTube extractor without a JS runtime installed.
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([channel_url])


def main():
    parser = argparse.ArgumentParser(description="Download all videos from a YouTube channel.")
    parser.add_argument("channel_url", nargs="?", default=DEFAULT_CHANNEL_URL)
    parser.add_argument("-o", "--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    print(f"Downloading all videos from {args.channel_url} -> {args.output_dir}/")
    download_channel(args.channel_url, args.output_dir)
    print("Done.")


if __name__ == "__main__":
    main()
