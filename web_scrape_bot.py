import asyncio
import os
import re
import subprocess
import urllib.request
from typing import TYPE_CHECKING, List, Tuple

from playwright.async_api import Playwright, async_playwright
from RedDownloader import RedDownloader
from redvid import Downloader
from rich import print
import yt_dlp
from utils import (
    best_vid_and_audio_steam,
    download_mp4_from_vid_and_audio,
    filename_enumerated,
)

if TYPE_CHECKING:
    from playwright.async_api._generated import Browser, Page

BROWSERLESS_URL = "SECRET"  # "ws://10.0.0.69:3000/"

if BROWSERLESS_URL == "SECRET":
    try:
        from secret import BROWSERLESS_URL
    except:
        raise Exception("You need to set your Browserless Url in secret.py!")


async def m3u8_to_mp4(url: str, vid_name: str):
    # print(f"Downloading video... {url}")
    # async with aiohttp.ClientSession() as session:
    #     async with session.get(url) as response:
    #         content = await response.read()

    # with open(vid_name, "wb") as f:
    #     f.write(content)
    # was getting 20 byte files for some reason
    # run ffmpeg to convert to mp4
    # This is now an m3u8 file
    print("Converting to mp4...")
    subprocess.run(f'ffmpeg -y -i "{url}" -c copy "{vid_name}"', shell=True, check=True)
    print("Done Converting!")
    # GET THE EXIT CODE AND RETURN TRUE OR FALSE?
    return True
    # print("Done!")


async def download_mp4_url(url: str, vid_name: str):
    urllib.request.urlretrieve(url, vid_name)


async def handle_request_twitter(vid_name: str, request, unused_variable):
    print(">>", request.method, request.url)
    # print(vid_name_small)
    if ".m3u8" in request.url:
        if "variant_version" in request.url:
            print("This is maybe the video!", request.url)

            return request.url

    elif "video.twimg.com/tweet_video/" in request.url:
        print("found a 'gif' video!", request.url)
        return request.url
    return "NOT_M3U8"


async def handle_request_instagram(vid_name: str, request, unused_variable):
    substrings = [".mp4", "bytestart=0"]
    if all(substring in request.url for substring in substrings):
        return request.url
    return "NOT_M3U8"


async def setup_browser(playwright: Playwright, storage_state: str) -> Tuple["Browser", "Page"]:
    chromium = playwright.chromium
    browser = await chromium.connect_over_cdp(BROWSERLESS_URL)
    context = await browser.new_context(storage_state=f"storage_states/{storage_state}")
    page = await context.new_page()
    return browser, page


def delete_file_if_exists(vid_name):
    if os.path.exists(vid_name):
        os.remove(vid_name)


async def scrape_reddit(url: str, vid_name_and_path: str):
    path, vid_name = vid_name_and_path.rsplit("/", 1)
    reddit = Downloader(max_q=True, path=path, filename=vid_name)
    reddit.overwrite = True
    reddit.url = url
    resp = None
    try:
        # yt-dlp first
        ydl_opts = {
            "outtmpl": vid_name_and_path,
            "quiet": True,
            "no_warnings": True,
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            resp = ydl.download([url])
            print("yt-dlp download response:", resp)
        if resp == 0:
            return True, vid_name_and_path
    except Exception as e:
        print("Failed to download with yt-dlp:", e, "trying redvid...")
    try:
        resp = reddit.download()
    except BaseException as e:  # reddit package uses the BaseException class
        print(e)
    if isinstance(resp, str):
        return True, None
    # redvid failed but it doesnt download gifs so try RedDownloader
    # RedDownloader uses https://jackhammer.pythonanywhere.com/reddit/media/downloader/?url=INSERT_URL_HERE
    # to get the video or gif raw url... Sometimes these are gone though and redvid downloads the reddit video rather than the source
    print("Redvid failed to download the video. Trying RedDownloader...")
    try:
        vid_name = vid_name.rsplit(".", 1)[0]  # remove the extension
        obj = RedDownloader.Download(url, quality=1080, output=vid_name, destination=path + "/")
        file_path = f"{obj.destination}{obj.output}.{obj.mediaType}"

    except Exception as e:
        pattern = r"Destination path '(.+)' already exists"
        match = re.search(pattern, f"{e}")
        if match:
            path = match.group(1)
            return True, path
        else:
            print("No path detected in the error message.")
        return False, None
    return True, file_path


async def scrape_twitter(url: str, vid_name: str):
    try:
        ydl_opts = {
            "outtmpl": vid_name,
            "cookiefile": "storage_states/twittercookies.txt",
            "quiet": True,
            "no_warnings": True,
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            # if there are multiple videos, download all of them. this isnt working currently
            "noplaylist": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            resp = ydl.download([url])
            print("yt-dlp download response:", resp)
        return [vid_name]
    except Exception as e:
        print("Failed to download with yt-dlp:", e, "trying playwright...")
    try:
        async with async_playwright() as playwright:
            # Connect to Browser and load context
            browser, page = await setup_browser(playwright, "twitter.json")

            await page.goto(url)

            # Create an empty list to store the coroutines
            request_handlers = []

            # Subscribe to "request" event and append the coroutines to the list
            page.on(
                "request",
                lambda request: request_handlers.append(
                    handle_request_twitter(vid_name, request, "ignore this")
                ),
            )
            await asyncio.sleep(5)

            # Close the browser
            await browser.close()

            # Return the list of coroutines
            urls = []
            for i, req in enumerate(request_handlers):
                vid_url = await req
                if vid_url != "NOT_M3U8":
                    print(i, vid_url, req)
                    urls.append(vid_url)
            print("!!!!" * 90)
            print(urls)
            print("!!!!" * 90)
            vids = []
            for x, url in enumerate(urls):
                if ".m3u8" in url:
                    # change Vid_name to be unique for each video
                    vid_name_enumerated = filename_enumerated(vid_name, x)
                    video_succeeded = await m3u8_to_mp4(url, vid_name_enumerated)
                    if video_succeeded:
                        vids.append(vid_name_enumerated)
                elif url.endswith(".mp4"):
                    vid_name_enumerated = filename_enumerated(vid_name, x)
                    await download_mp4_url(url, vid_name_enumerated)
                    vids.append(vid_name_enumerated)
            return vids
    except Exception as e:
        print(e)
        return False


async def find_element_with_text(page, text):
    elements = await page.query_selector_all("*")
    for element in elements:
        if await element.text_content() == text:
            return element
    return None


def insta_urls_to_vid(urls: List[str], vid_name: str):
    # remove byteend=anything from the urls
    urls = [re.sub(r"byteend=\d+", "", url) for url in urls]
    # set bytestart=0
    urls = [re.sub(r"bytestart=\d+", "bytestart=0", url) for url in urls]
    # remove duplicates
    urls = list(set(urls))
    # get best video and audio stream
    vid_url, aud_url = best_vid_and_audio_steam(urls)
    # download video and audio combined
    success = download_mp4_from_vid_and_audio(vid_url, aud_url, vid_name)
    return vid_name


async def scrape_instagram(url: str, vid_name: str):
    try:
        # use yt-dlp first
        ydl_opts = {
            "outtmpl": vid_name,
            "cookiefile": "storage_states/instagramcookies.txt",
            "quiet": True,
            "no_warnings": True,
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
            "postprocessor_args": [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "26",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
            ],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            resp = ydl.download([url])
            print("yt-dlp download response:", resp)
            if resp == 0:
                return True
    except Exception as e:
        print("Failed to download with yt-dlp:", e, "trying playwright...")
    try:
        async with async_playwright() as playwright:
            # Connect to Browser and load context
            browser, page = await setup_browser(playwright, "instagram.json")

            # create an empty list to store the coroutines
            request_handlers = []

            page.on(
                "request",
                lambda request: request_handlers.append(
                    handle_request_instagram(vid_name, request, "ignore this")
                ),
            )

            await page.goto(url)
            view_story_button = await find_element_with_text(page, "View story")
            if view_story_button:
                "Found View Story button!"
                await view_story_button.click()
            await asyncio.sleep(5)
            await browser.close()

            # Return the list of coroutines
            urls = []
            for i, req in enumerate(request_handlers):
                vid_url = await req
                if vid_url != "NOT_M3U8":
                    print(i, vid_url, req)
                    urls.append(vid_url)
            print("!!!!" * 90)
            # At this point it is a list of video and audio urls
            print(urls)
            # find best video and audio
            vid = insta_urls_to_vid(urls=urls, vid_name=vid_name)
            print("!!!!" * 90)

        return True
    except Exception as e:
        print(e)
        return False
