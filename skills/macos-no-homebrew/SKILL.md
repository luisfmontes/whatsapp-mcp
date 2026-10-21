---
name: macos-no-homebrew
description: Install the WhatsApp MCP on a Mac that has no Homebrew and no admin rights (e.g. a corporate laptop) — user-space Go and uv, reviewed installer with launchd service, Claude Code registration, QR pairing, and local audio transcription with a self-built whisper.cpp. Use when `/whatsapp:setup` stops because `go`, `uv` or `ffmpeg` is missing and `brew` is not available, or when the user wants private local transcription and has no `whisper-cli` yet.
---

# WhatsApp MCP on macOS without Homebrew

`/whatsapp:setup` stops when a dependency is missing and leaves whisper.cpp out of scope. This skill fills both gaps using only user-space installs under `~/.local` — no `brew`, no `sudo`.

Show the user the plan (what gets installed where) and get an explicit OK before step 1. Everything below is reversible by deleting the listed directories.

## 0. Preflight

```bash
uname -m                                   # arm64 or x86_64 — pick matching downloads
for c in git go python3 uv ffmpeg brew; do printf "%s: " $c; command -v $c || echo MISSING; done
xcode-select -p                            # Command Line Tools: needed for clang/make (whisper.cpp)
lsof -iTCP:8080 -sTCP:LISTEN -n -P         # bridge port must be free
```

- `git` and `python3` come with the Command Line Tools. If `xcode-select -p` fails, ask the user to run `xcode-select --install` (GUI prompt) and wait.
- Already have a recent `go` (1.25+) / `uv` / `ffmpeg`? Skip the matching step.

## 1. Go (official tarball, checksum verified)

```bash
mkdir -p ~/.local/bin
GOV=$(curl -fsSL 'https://go.dev/VERSION?m=text' | head -1)        # e.g. go1.27.3
ARCH=$(uname -m | sed 's/x86_64/amd64/')
F="$GOV.darwin-$ARCH.tar.gz"
curl -fsSLO "https://go.dev/dl/$F"
SHA=$(curl -fsSL 'https://go.dev/dl/?mode=json' | python3 -c "import sys,json;print(next(f['sha256'] for r in json.load(sys.stdin) for f in r['files'] if f['filename']=='$F'))")
echo "$SHA  $F" | shasum -a 256 -c                                  # must print OK — stop otherwise
rm -rf ~/.local/go && tar -C ~/.local -xzf "$F" && rm "$F"
```

## 2. uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                    # installs to ~/.local/bin
```

Persist the PATH once (both tools live under `~/.local`):

```bash
grep -q '.local/go/bin' ~/.zshrc 2>/dev/null || echo 'export PATH="$HOME/.local/go/bin:$HOME/.local/bin:$PATH"' >> ~/.zshrc
export PATH="$HOME/.local/go/bin:$HOME/.local/bin:$PATH"
go version && uv --version
```

## 3. Bridge + launchd service

Download `install.sh` to a file, **read it**, then run it — never pipe straight into `bash`:

```bash
curl -fsSL https://raw.githubusercontent.com/luisfmontes/whatsapp-mcp/main/install.sh -o /tmp/wa-install.sh
bash /tmp/wa-install.sh --service
```

The installer clones into `~/.whatsapp-mcp`, builds the bridge, and loads `com.whatsapp-mcp.bridge` in launchd (starts at login, port 8080). It only writes `claude_desktop_config.json` if that file does not exist yet — it never registers with Claude Code.

## 4. Register in Claude Code

Use absolute paths — the MCP client does not read `~/.zshrc`:

```bash
claude mcp add whatsapp -s user -e WHATSAPP_BRIDGE_PORT=8080 -- \
  "$HOME/.local/bin/uv" --directory "$HOME/.whatsapp-mcp/whatsapp-mcp-server" run main.py
```

## 5. Pair

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/qr   # expect 200
open http://localhost:8080/qr
```

User scans it on the phone: **WhatsApp → Linked devices → Link a device**. The QR expires quickly — reload the page if needed. Confirm in `~/.whatsapp-mcp/bridge.log`, then tell the user to restart Claude Code so the `whatsapp` tools load. Run `/whatsapp:doctor` afterwards.

## 6. Local transcription (whisper.cpp)

Recommended when audio may contain third-party or client data: nothing leaves the machine and there is no per-call cost. The build takes a few minutes; the model is ~550 MB.

**cmake** (no brew needed):

```bash
uv tool install cmake
```

**whisper.cpp** (only the CLI target):

```bash
mkdir -p ~/.local/share && cd ~/.local/share
git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j --config Release --target whisper-cli
bash models/download-ggml-model.sh large-v3-turbo-q5_0
```

`large-v3-turbo-q5_0` keeps large-v3 accuracy on product names and jargon (medium transcribes them phonetically) while running fast on Apple Silicon.

**ffmpeg** (voice notes are Opus; whisper needs WAV). Use a signed static build and check the signature before trusting it:

```bash
curl -fsSL -o /tmp/ffmpeg.zip https://ffmpeg.martin-riedl.de/redirect/latest/macos/$(uname -m)/release/ffmpeg.zip
unzip -o -q /tmp/ffmpeg.zip -d /tmp/ffx && mv /tmp/ffx/ffmpeg ~/.local/bin/ && chmod +x ~/.local/bin/ffmpeg
codesign -dv ~/.local/bin/ffmpeg 2>&1 | grep -E 'Authority=Developer ID|TeamIdentifier'   # must show a Developer ID
ffmpeg -version | head -1
```

If that host is down (it returns HTTP 521 at times), take the arm64/x86_64 binary shipped in the `imageio-ffmpeg` wheel on PyPI instead:

```bash
P=$(uvx -q --from imageio-ffmpeg python -I -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
cp "$P" ~/.local/bin/ffmpeg && chmod +x ~/.local/bin/ffmpeg
```

Either way, confirm it decodes Opus: `ffmpeg -hide_banner -decoders | grep -i opus`.

**Wire it up.** launchd does not see your shell, so the vars go in `transcription.env`, created owner-only. Point `FFMPEG_BIN` at the binary — `transcribe.py` otherwise looks for `ffmpeg` on a `PATH` launchd does not have:

```bash
(umask 077; cat > ~/.whatsapp-mcp/transcription.env <<EOF
export TRANSCRIPTION_ENGINE=local
export WHISPER_CLI=$HOME/.local/share/whisper.cpp/build/bin/whisper-cli
export WHISPER_MODEL=$HOME/.local/share/whisper.cpp/models/ggml-large-v3-turbo-q5_0.bin
export FFMPEG_BIN=$HOME/.local/bin/ffmpeg
export TRANSCRIPTION_PROMPT="<names, products and jargon the user says often>"
EOF
)
launchctl kickstart -k gui/$(id -u)/com.whatsapp-mcp.bridge
grep -i "transcription sweep started" ~/.whatsapp-mcp/bridge.log | tail -1
```

Smoke test the engine without waiting for a real voice note:

```bash
say "Olá, este é um teste de transcrição." -o /tmp/t.aiff
ffmpeg -loglevel error -y -i /tmp/t.aiff -c:a libopus /tmp/t.ogg
ffmpeg -loglevel error -y -i /tmp/t.ogg -ar 16000 -ac 1 /tmp/t.wav
~/.local/share/whisper.cpp/build/bin/whisper-cli -m ~/.local/share/whisper.cpp/models/ggml-large-v3-turbo-q5_0.bin -l pt -nt -np -f /tmp/t.wav
```

Offer a `TRANSCRIPTION_PROMPT` draft built from what you already know about the user's work (client names, product names, acronyms) and let them edit it.

**Backfill** existing audios (the script does not load `transcription.env` by itself):

```bash
cd ~/.whatsapp-mcp/whatsapp-mcp-server
set -a && source ~/.whatsapp-mcp/transcription.env && set +a && \
  WHATSAPP_BRIDGE_PORT=8080 uv run transcribe.py
```

New voice messages are transcribed by the bridge's 5-minute sweep from then on.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.whatsapp-mcp.bridge.plist && rm ~/Library/LaunchAgents/com.whatsapp-mcp.bridge.plist
claude mcp remove whatsapp -s user
# also deletes the local message history (messages.db) — back it up first if needed
rm -rf ~/.whatsapp-mcp ~/.local/share/whisper.cpp ~/.local/go ~/.local/bin/ffmpeg
uv tool uninstall cmake
```

Then unlink the device on the phone (**Linked devices**).
