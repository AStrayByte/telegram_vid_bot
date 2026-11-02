apt-get update
echo "INSTALLING FFMPEG"
apt-get install -y ffmpeg curl unzip
echo "INSTALLING FFMPEG DONE?"
git config --global --add safe.directory /code
git pull
pip install --upgrade pip
pip install -r requirements.txt
playwright install

# Install deno + the PO token provider script, needed for yt-dlp to fetch
# full-quality (not just 360p) YouTube video. See README.md step 5.
if ! command -v deno >/dev/null 2>&1; then
    echo "INSTALLING DENO"
    curl -fsSL https://deno.land/install.sh | sh
    export PATH="$HOME/.deno/bin:$PATH"
fi
if [ ! -f "$HOME/bgutil-ytdlp-pot-provider/server/src/generate_once.ts" ]; then
    echo "INSTALLING bgutil-ytdlp-pot-provider script"
    git clone --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git "$HOME/bgutil-ytdlp-pot-provider"
    (cd "$HOME/bgutil-ytdlp-pot-provider/server" && deno install)
fi

python3 telegrambot.py

