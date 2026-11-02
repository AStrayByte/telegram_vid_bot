# Instructions

# 1. Create venv

# pyenv install 3.10

# pyenv virtualenv 3.10 telegrambot

# 2. Install requirements

# pip install -r requirements.txt

# 3. Run the following bash command to install playwright browsers

# playwright install

# 4. Save instagram or twitter credentials etc

playwright open --save-storage twitter.json

# Using yt-dlp

use cookies.txt addon for firefox to download cookies to a text file. save in twittercookies.txt
call:

yt-dlp --cookies storage_states/twittercookies.txt "https://x.com/usernamehere/status/123456789876543\?s\=20" # this is a fake url

# 5. YouTube: install deno + the PO token provider (needed for full quality)

YouTube now requires a "PO token" to serve anything above a muxed 360p stream on
most clients. Without this set up, yt-dlp silently falls back to a low quality
360p download instead of failing loudly. To get full quality (up to 4K, then
capped to H.264/1080p by our format selector for Telegram compatibility):

# install the deno JS runtime (used to solve YouTube's JS/PO token challenges)

brew install deno # or see https://deno.com/manual/getting_started/installation for other platforms

# clone the PO token provider's script (bgutil-ytdlp-pot-provider is already a

# pinned dependency in requirements.txt, but it needs its companion script from

# GitHub cloned locally so it can generate tokens with deno, no separate server needed)

git clone --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git ~/bgutil-ytdlp-pot-provider
cd ~/bgutil-ytdlp-pot-provider/server && deno install

If this isn't set up, YouTube downloads still work — they just land at 360p
instead of full resolution.
