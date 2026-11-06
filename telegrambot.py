import logging
import os
import time

import requests
from telegram import Update
from telegram.error import TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from utils import (
    TELEGRAM_UPLOAD_LIMIT_MB,
    clean_url,
    convert_gif_to_mp4,
    get_file_size_in_mb,
    get_vid_dimensions,
    prepare_for_upload,
    upload_log,
    url_to_filename,
)
from web_scrape_bot import scrape_generic, scrape_instagram, scrape_reddit, scrape_twitter

# python-telegram-bot's defaults (20s to upload, 5s for the reply) are too short for videos
# near the size limit: Telegram can spend longer than that processing one before replying.
UPLOAD_TIMEOUT_SECONDS = 300

TOKEN = "SECRET"

if TOKEN == "SECRET":
    try:
        from secret import ACCEPTED_TELEGRAM_USERS, TOKEN
    except:
        raise Exception("You need to set your telegram token in secret.py!")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="I'm a bot, please talk to me! FART!"
    )
    # respond with current username
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=f"Your username is {update.effective_user.username}"
    )


def check_for_downloaded_vid(vid_name: str):
    """Checks for up to 20 seconds if the video exists"""
    for i in range(20):
        try:
            with open(vid_name, "rb"):
                return True
        except FileNotFoundError:
            time.sleep(1)
    return False


async def send_video_parts(
    url: str,
    vid_name: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_id: int = None,
):
    """Send a video, shrinking or splitting it first if it's over the upload limit."""
    chat_id = update.effective_chat.id
    size = get_file_size_in_mb(vid_name)
    over_limit = size > TELEGRAM_UPLOAD_LIMIT_MB
    if over_limit:
        upload_log.info("Handling %s", url)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Video is too large! {size:.1f} > {TELEGRAM_UPLOAD_LIMIT_MB}MB. "
            "Shrinking or splitting it!",
            reply_to_message_id=message_id,
        )
    try:
        parts = prepare_for_upload(vid_name)
    except Exception as e:
        upload_log.exception("Failed to prepare %s for %s", vid_name, url)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Failed to prepare video! {e}",
            reply_to_message_id=message_id,
        )
        return
    for number, part in enumerate(parts, start=1):
        width, height = get_vid_dimensions(part)
        part_label = f"part {number}/{len(parts)}" if len(parts) > 1 else "the video"
        try:
            with open(part, "rb") as video:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video,
                    width=width,
                    height=height,
                    caption=f"Part {number}/{len(parts)}" if len(parts) > 1 else None,
                    reply_to_message_id=message_id,
                    write_timeout=UPLOAD_TIMEOUT_SECONDS,
                    read_timeout=UPLOAD_TIMEOUT_SECONDS,
                )
            if over_limit:
                upload_log.info("Sent %s: %s", part_label, part)
        except TimedOut:
            # The video may well have arrived, so keep going rather than dropping later parts.
            upload_log.warning(
                "Timed out sending %s: %s (%.1fMiB) for %s",
                part_label, part, get_file_size_in_mb(part), url,
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Timed out waiting for Telegram on {part_label}. It may still arrive.",
                reply_to_message_id=message_id,
            )
        except Exception as e:
            upload_log.error(
                "Failed to send %s: %s (%.1fMiB) for %s: %r",
                part_label, part, get_file_size_in_mb(part), url, e,
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Failed to send {part_label}! {e}",
                reply_to_message_id=message_id,
            )
            return


async def handle_instagram_url(
    url: str, update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: int = None
):
    print("This is an instagram link!")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Instagram {url}")
    vid_name = url_to_filename(url)
    # send video.mp4
    success = await scrape_instagram(url, vid_name)
    if not success:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=f"Failed to scrape somewhere!"
        )
        return
    found = check_for_downloaded_vid(vid_name)
    if not found:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=f"Failed to find video!"
        )
        return
    await send_video_parts(url, vid_name, update, context, message_id)


def unshorten_url(short_url):
    response = requests.head(short_url, allow_redirects=True)
    return response.url


async def handle_reddit_url(
    url: str, update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: int = None
):
    print("This is a reddit link!")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Reddit link found.",
        reply_to_message_id=message_id,
        disable_notification=True,
    )
    if "v.redd.it" in url or "comments" not in url:
        print("attempting to expand video link!")
        print("old url:", url)
        url = unshorten_url(url)
        url = url.split("?")[0]
        print("new url:", url)
    vid_name = url_to_filename(url)
    # send video.mp4
    success, file_path = await scrape_reddit(url, vid_name_and_path=vid_name)
    if not success:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Failed to scrape somewhere!",
            reply_to_message_id=message_id,
        )
        return
    if file_path:  # if file path changed from assumption due to gifs or something else
        if file_path.endswith(".gif"):
            file_path = convert_gif_to_mp4(gif_path=file_path, mp4_path=vid_name)
        else:
            vid_name = file_path
    found = check_for_downloaded_vid(vid_name)
    if not found:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Failed to find video!",
            reply_to_message_id=message_id,
        )
        return
    await send_video_parts(url, vid_name, update, context, message_id)


async def handle_twitter_url(
    url: str, update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: int = None
):
    print("This is a twitter link!")
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=f"Twitter {url}", reply_to_message_id=message_id
    )
    vid_name = url_to_filename(url)
    # send video.mp4
    max_attempts = 5
    for i in range(max_attempts):
        vids = await scrape_twitter(url, vid_name)
        if vids:
            break
        print(f"Failed to scrape {i} times!")
    if not vids:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Failed to scrape somewhere!",
            reply_to_message_id=message_id,
        )
        return
    print(vids)
    for vid in vids:
        found = check_for_downloaded_vid(vid_name=vid)
        # found = None
        if not found:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Failed to find video!",
                reply_to_message_id=message_id,
            )
            return
        await send_video_parts(url, vid, update, context, message_id)
        return


async def handle_generic_url(
    url: str, update: Update, context: ContextTypes.DEFAULT_TYPE, message_id: int = None
):
    print("Trying generic yt-dlp download!")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Trying to download video from {url}",
        reply_to_message_id=message_id,
    )
    vid_name = url_to_filename(url)
    success = await scrape_generic(url, vid_name)
    if not success:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Failed to download video! Site may not be supported.",
            reply_to_message_id=message_id,
        )
        return
    found = check_for_downloaded_vid(vid_name)
    if not found:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Failed to find video!",
            reply_to_message_id=message_id,
        )
        return
    await send_video_parts(url, vid_name, update, context, message_id)


async def handle_text_input(text: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_id = update.message.message_id
    text = clean_url(text)
    if text.startswith("https://x.com/") or text.startswith("https://twitter.com/"):
        await handle_twitter_url(text, update, context, message_id)
    elif text.startswith("https://www.reddit.com"):
        await handle_reddit_url(text, update, context, message_id)
    elif "instagram.com/" in text:
        await handle_instagram_url(text, update, context, message_id)
    elif text.startswith("http://") or text.startswith("https://") or text.startswith("www."):
        await handle_generic_url(text, update, context, message_id)
    else:
        print(f"Not a URL: {text}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Not a valid URL: {text}",
            reply_to_message_id=update.message.message_id,
        )


async def url_handler_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.username not in ACCEPTED_TELEGRAM_USERS:
        print(f"User {update.effective_user.username} is not allowed to use this bot!")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"User {update.effective_user.username} is not allowed to use this bot!",
        )
        return
    if update.message is None:
        print("This is not a message!")
        return
    text = update.message.text
    if text is None:
        print("This is not text!")
        return
    await handle_text_input(text, update, context)
    # await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me! FARTSSSS! {}")


if __name__ == "__main__":
    application = ApplicationBuilder().token(TOKEN).build()

    start_handler = CommandHandler("start", start)

    # message handler to handle random strings
    message_handler = MessageHandler(filters.TEXT, url_handler_func)

    application.add_handler(start_handler)
    application.add_handler(message_handler)

    application.run_polling()
