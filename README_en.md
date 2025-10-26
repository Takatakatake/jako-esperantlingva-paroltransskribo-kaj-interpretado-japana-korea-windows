# Esperanto Realtime Transcription

English version. For Japanese, see `README.md`.

Realtime transcription pipeline tailored for Esperanto conversations on Zoom and Google Meet.  
The implementation follows the design principles captured in the proposal document:
“エスペラント（Esperanto）会話を“常時・高精度・低遅延”に文字起こしするための実現案1.md”.

- Speechmatics Realtime STT (official `eo` support, diarization, custom dictionary)
- Vosk offline backend as a zero-cost / air-gapped fallback
- Zoom Closed Caption API injection for native on-screen subtitles
- Pipeline abstraction ready for additional engines (e.g., Whisper streaming, Google STT)
- Browser-based caption board with optional JA/KO translations and Discord batching

Note:
- Speechmatics and Zoom endpoints require valid credentials and meeting-level permissions.
- Inform participants about live transcription to comply with privacy/policy requirements.

---

## 1. Prerequisites

- Python 3.10+ (tested on CPython 3.10/3.11)
- Use Python 3.11 and create the virtual environment named `.venv311`.
- Audio loopback from Zoom/Meet into the local machine (VB-Audio, VoiceMeeter, BlackHole, JACK)
- Speechmatics account with realtime entitlement and API key
- Zoom host privileges to obtain the Closed Caption POST URL (or Recall.ai/Meeting SDK)

Optional:
- GPU or a fast CPU if using the Whisper backend (e.g., RTX 4070+ or Apple M2 Pro+)
- Google Meet Media API (preview) for direct capture
- Vosk Esperanto model (`vosk-model-small-eo-0.42`+) for fully offline use

---

## 0. Quickstart (from GitHub)

```bash
git clone git@github.com:Takatakatake/esperanto_onsei_mojiokosi.git
cd esperanto_onsei_mojiokosi
python3.11 -m venv .venv311
source .venv311/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
test -f .env || cp .env.example .env
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv311    # or python -m venv .venv311 if py.exe is unavailable
.\.venv311\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit these fields (example):

```ini
SPEECHMATICS_API_KEY=****************************   # replace with your real key
SPEECHMATICS_CONNECTION_URL=wss://<region>.rt.speechmatics.com/v2   # region base URL form (e.g. eu2 or us2)
SPEECHMATICS_LANGUAGE=eo                                     # language code (e.g. eo). The actual connection will be to <base>/v2/<language> (e.g. wss://eu2.rt.speechmatics.com/v2/eo).
AUDIO_DEVICE_INDEX=8                               # from --list-devices
WEB_UI_ENABLED=true
TRANSLATION_ENABLED=true
TRANSLATION_TARGETS=ja,ko
```

On Windows, run `python -m transcriber.cli --list-devices` and set the index for Stereo Mix or your VB-Audio Cable loopback device.

Then verify devices and start:

```bash
python -m transcriber.cli --list-devices
python -m transcriber.cli --log-level=INFO
```

Open the Web UI at `http://127.0.0.1:8765` (set `WEB_UI_OPEN_BROWSER=true` to auto-open).

---

## 2. Bootstrap

```bash
cd <project-root>   # replace with the directory where you cloned the repo
python3.11 -m venv .venv311
source .venv311/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
test -f .env || cp .env.example .env
```

On Windows PowerShell (`<project-root>` likewise needs to be substituted):

```powershell
Set-Location '<project-root>'
py -3.11 -m venv .venv311
.\.venv311\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# If `py` is unavailable, use `python -m venv .venv311` instead.
```

Edit `.env` (sample values are masked; replace with real values):

```ini
TRANSCRIPTION_BACKEND=speechmatics  # or vosk / whisper
SPEECHMATICS_API_KEY=sk_live_************************
SPEECHMATICS_APP_ID=realtime
SPEECHMATICS_LANGUAGE=eo
ZOOM_CC_POST_URL=https://wmcc.zoom.us/closedcaption?... (host-provided URL)
```

Optional overrides (you can leave these unset when using defaults):

```ini
AUDIO_DEVICE_INDEX=8            # from --list-devices output
AUDIO_SAMPLE_RATE=16000
AUDIO_CHUNK_DURATION_SECONDS=0.5
ZOOM_CC_MIN_POST_INTERVAL_SECONDS=1.0
VOSK_MODEL_PATH=/absolute/path/to/vosk-model-small-eo-0.42
WHISPER_MODEL_SIZE=medium
WHISPER_DEVICE=auto              # e.g. cuda, cpu, mps
WHISPER_COMPUTE_TYPE=default     # e.g. float16 (GPU)
WHISPER_SEGMENT_DURATION=6.0
WHISPER_BEAM_SIZE=1
TRANSCRIPT_LOG_PATH=logs/esperanto-caption.log
WEB_UI_ENABLED=true
TRANSLATION_ENABLED=true
TRANSLATION_PROVIDER=google
TRANSLATION_SOURCE_LANGUAGE=eo
TRANSLATION_TARGETS=ja,ko
TRANSLATION_TIMEOUT_SECONDS=8.0
TRANSLATION_DEFAULT_VISIBILITY=ja:on,ko:off
GOOGLE_TRANSLATE_CREDENTIALS_PATH=/absolute/path/to/gen-lang-client-xxxx.json
GOOGLE_TRANSLATE_MODEL=nmt
# If using API-key based access: GOOGLE_TRANSLATE_API_KEY=...
DISCORD_WEBHOOK_ENABLED=true
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DISCORD_BATCH_FLUSH_INTERVAL=2.0
DISCORD_BATCH_MAX_CHARS=350
```

On Windows, run `python -m transcriber.cli --list-devices` and set the index for Stereo Mix or your VB-Audio Cable loopback device.

---

## 3. Usage

List capture devices and verify routing:

```bash
python -m transcriber.cli --list-devices
```

Start the pipeline (prints finals to stdout, pushes finals to Zoom):

```bash
python -m transcriber.cli --log-level=INFO
```

- With `WEB_UI_ENABLED=true` the caption board runs at `http://127.0.0.1:8765`. The layout now separates the latest utterance (left) and history (right), stores theme/font/toggle preferences, and offers copy / export / clear buttons for the transcript log.
- Translation toggles appear automatically from `TRANSLATION_TARGETS`, and their initial state can be tuned with `TRANSLATION_DEFAULT_VISIBILITY`.

### Web UI Cheat Sheet

- The help panel at the top summarises the main controls.
- Enable “Partial” to show interim captions; disable it to focus on confirmed utterances only.
- Font size and dark mode switches persist via `localStorage`, so your preferences carry over to the next session.
- Translation toggles switch each language on/off. Add or remove languages via `.env` (`TRANSLATION_TARGETS` / `TRANSLATION_DEFAULT_VISIBILITY`).
- The history panel keeps the newest lines at the top. Use the “Copy / Save / Clear” buttons to share or reset the log instantly.
- With a Discord webhook configured, the pipeline batches finals into natural sentences and posts a single message containing the Esperanto line and all enabled translations.

Switch backends or override log output on demand:

```bash
python -m transcriber.cli --backend=vosk --log-file=logs/offline.log
python -m transcriber.cli --backend=whisper --log-level=DEBUG
```

Translation smoke test (uses current `.env` settings):

```bash
scripts/test_translation.py "Bonvenon al nia kunsido."
```

Stop with `Ctrl+C` (graceful). Logs will show:
- `Final:` lines when Speechmatics emits confirmed segments
- Caption POST success/failure (watch for 401/403)
- When transcript logging is enabled, the log receives timestamped lines per final utterance

Zoom-specific steps:
1) Host enables Live Transcription and copies the Closed Caption API URL.
2) Paste it into `.env` as `ZOOM_CC_POST_URL` or export at runtime: `export ZOOM_CC_POST_URL=...`.
3) Participants enable subtitles in Zoom. Typical E2E latency is ~1 s.

Google Meet options:
- If the Meet Media API is available, consume the media stream and feed PCM into the same Speechmatics client.
- Otherwise, route audio via OS loopback (PipeWire/BlackHole/VoiceMeeter, etc.).

---

## 4. Architecture Overview

- `transcriber/audio.py`: async capture of PCM16 16 kHz mono
- `transcriber/asr/speechmatics_backend.py`: Realtime WebSocket client (Bearer JWT, parses partial/final JSON)
- `transcriber/asr/whisper_backend.py`: streaming recognition via faster-whisper (GPU/M-series friendly)
- `transcriber/asr/vosk_backend.py`: lightweight offline recognizer (Vosk/Kaldi)
- `transcriber/pipeline.py`: orchestrates audio, ASR, logging, caption delivery, translations, Web UI, Discord
- `transcriber/zoom_caption.py`: throttled POSTs to Zoom Closed Caption API (`text/plain`, adds `seq`)
- `transcriber/translate/service.py`: async translation client (LibreTranslate-compatible)
- `transcriber/discord/batcher.py`: debounce/aggregate Discord posts into natural sentences
- `transcriber/cli.py`: device discovery, config inspection, backend override, graceful shutdown

Anticipated extensions:
- Additional backends (Whisper streaming, Google STT)
- Post-processing (Esperanto diacritics normalisation, punctuation refinement)
- Observer hooks for on-screen display, translation, persistence

---

## 5. Validation & Next Steps

1) Validate Speechmatics handshake (`start` schema). Tune dictionary/`operating_point` as needed.  
2) Dry-run with recorded audio; measure WER/diarization/latency.  
3) Register frequent words in the Speechmatics Custom Dictionary; mirror vocabulary for Vosk post-processing if needed.  
4) Validate the offline path with Vosk and compare WER/latency.  
5) Benchmark Whisper on your hardware and tune `WHISPER_SEGMENT_DURATION`.  
6) For production, run under a supervisor (systemd/pm2) with persistent logs/metrics.  
7) Document participant consent; add automated “transcription active” notifications.  
8) End-to-end translation test: set `TRANSLATION_TARGETS=ja,ko`, ensure Google Cloud Translation (or LibreTranslate) responds quickly, and verify Web UI/Discord output shows bilingual lines.
  - If using Google Cloud Translation: set `TRANSLATION_PROVIDER=google`, and either `GOOGLE_TRANSLATE_CREDENTIALS_PATH=/path/to/service-account.json` or `GOOGLE_TRANSLATE_API_KEY`. Do NOT commit service-account JSON to the repository; prefer supplying the path via the `GOOGLE_APPLICATION_CREDENTIALS` environment variable or keeping the JSON outside the repo. Optionally set `GOOGLE_TRANSLATE_MODEL=nmt`. The service account must have Cloud Translation API permissions.

For alternate capture paths (Recall.ai bots, Meet Media API wrappers, Whisper fallback), reuse the abstractions in `audio.py` and `transcriber/asr/`—new producers/consumers slot in without changing pipeline control logic.

---

## 7. Recommended Launch Workflow

Keep the Web UI on a fixed port (8765) and avoid “already in use” loops with the tiny launcher:

```bash
install -Dm755 scripts/run_transcriber.sh ~/bin/run-transcriber.sh
source <project-root>/.venv311/bin/activate
~/bin/run-transcriber.sh              # defaults: backend=speechmatics, log-level=INFO
```

Windows PowerShell users can run `scripts\run_transcriber.ps1` to perform the same steps (honours `PORT`, `BACKEND`, and `LOG_LEVEL`).
The `install` command is not available on Windows; copy the script somewhere on your `$env:USERPROFILE\bin` (e.g. `Copy-Item scripts\run_transcriber.ps1 $env:USERPROFILE\bin\`) or execute the `.ps1` directly from the repository.

`run_transcriber.sh` closes stale listeners on port 8765, waits for the socket to free, then starts `python -m transcriber.cli`. The browser connects to `http://127.0.0.1:8765` and translations show immediately.

Override port/backend when needed:

```bash
PORT=8766 LOG_LEVEL=DEBUG BACKEND=whisper ~/bin/run-transcriber.sh
```


Prefer manual runs? Use the prep script once per run:

```bash
install -Dm755 scripts/prep_webui.sh ~/bin/prep-webui.sh
source <project-root>/.venv311/bin/activate
~/bin/prep-webui.sh && python -m transcriber.cli --backend=speechmatics --log-level=INFO
```

Windows PowerShell users can run `scripts\prep_webui.ps1` to free the port before launching the CLI. Pairing `scripts\run_transcriber.ps1` / `scripts\prep_webui.ps1` makes it easy to flip `PORT`, `BACKEND`, and `LOG_LEVEL` safely.
(Same note: on Windows, replace `install` with a manual copy such as `Copy-Item scripts\prep_webui.ps1 $env:USERPROFILE\bin\`, or invoke the `.ps1` directly.)

> When running `.ps1` scripts for the first time, you may need to relax the execution policy, e.g.  
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

`prep-webui.sh` terminates lingering CLI processes, frees port 8765, and waits until it is available so the subsequent CLI command binds on the first try.


To fully free port 8765, run these three lines (also kills any Chrome/NetworkService holder):

```bash
# Try graceful shutdown first; force-kill can have side effects.
pkill -f "python -m transcriber.cli" || true
sleep 0.2
lsof -t -iTCP:8765 | xargs -r kill || true   # SIGTERM first
sleep 0.5 && lsof -iTCP:8765 || true
# If the port remains held and you understand the risk, consider kill -9 only after consultation.
```

Windows PowerShell equivalent:
```powershell
$port = [int]($env:PORT ?? 8765)
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*transcriber*" } | Stop-Process -Force
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Start-Sleep -Milliseconds 500
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
```

Then restart as usual: `python -m transcriber.cli ...`.

---

## 8. Audio Loopback Stability

PipeWire/WirePlumber occasionally revert the default input to a hardware mic. To keep Meet loopback working, and auto-heal if state files change, see `docs/audio_loopback.md`:

```bash
install -Dm755 scripts/wp-force-monitor.sh ~/bin/wp-force-monitor.sh
~/bin/wp-force-monitor.sh                           # once: force analog monitor
cp systemd/wp-force-monitor.{service,path} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wp-force-monitor.service wp-force-monitor.path
```

`wp-force-monitor` keeps the default source on `alsa_output...analog-stereo.monitor`. Provide `SINK_NAME=...` only if you also want to pin the sink.
*(Linux only; Windows users can ignore this section and the `install`/systemd commands.)*

---

## 6. Audio Device Hot-Reload (Ubuntu/Linux)

Automatic detection of device changes and seamless reconnection to avoid pipeline interruptions.

### Features
- Automatic monitoring: checks default input every 2 seconds (configurable)
- Seamless reconnection on change
- Health checks: detects silent/blocked streams (5 s) and restarts
- Error recovery: retries on exceptions

### Configuration
Add to `.env`:
```ini
AUDIO_DEVICE_CHECK_INTERVAL=2.0
```

### Diagnostics
List devices and current defaults:
```bash
python scripts/diagnose_audio.py
```
(`python3` is fine on systems where `python` still points to Python 2.)

### Common Issues (Ubuntu/PulseAudio)
- Output switch mutes loopback: auto-recovers in 2–5 s. For persistent loopback:
  ```bash
  pactl load-module module-loopback latency_msec=1
  ```
- Frequent reconnects: increase interval or pin a specific device:
  ```ini
  AUDIO_DEVICE_CHECK_INTERVAL=5.0
  AUDIO_DEVICE_INDEX=8
  ```

On Windows, run `python -m transcriber.cli --list-devices` and set the index for Stereo Mix or your VB-Audio Cable loopback device.

See `docs/ubuntu_audio_troubleshooting.md` for more details.

---

## System-level dependencies (note)

This project requires some OS-level libraries in addition to Python packages (PortAudio, libsndfile, ffmpeg, etc.). Example install commands (adjust for your distro/environment):

- Debian/Ubuntu (example):
```bash
sudo apt update
sudo apt install -y build-essential libsndfile1-dev libportaudio2 portaudio19-dev ffmpeg
```

- macOS (Homebrew):
```bash
brew install portaudio ffmpeg libsndfile
```

- Windows: If a prebuilt wheel for `sounddevice` is not available, Visual C++ Build Tools may be required. Install `ffmpeg` from https://ffmpeg.org or via `choco`/scoop.

Before installing Python dependencies, it is recommended to upgrade pip and wheel:
```bash
python -m pip install --upgrade pip setuptools wheel
```


---

## Appendix: Security and .env Handling

- This repo tracks a masked `.env` to make setup easier. Replace placeholders with real values locally.
- For production, do not track real secrets in `.env`. Prefer an untracked variant (e.g., `.env.local`) and add it to `.gitignore`.
- Never commit or share real keys; rotate credentials regularly.

### Emergency steps if secrets are discovered in the repository (quick guide)

1. Locally, move the sensitive file(s) (e.g. `*.json`, `.env`) to a safe location and remove them from the repository with a commit that deletes the file (e.g. `git rm --cached <file>` to avoid re-adding to history in the next commit).
2. Immediately rotate/disable the exposed credentials. For Google service accounts, delete the compromised key in the Cloud Console.
3. If the secret was pushed to a remote (e.g. GitHub), coordinate with your team to consider history rewriting (using `git-filter-repo` / BFG) and follow your organization notification policy. History rewrites must be done carefully.
4. Prevent recurrence: add patterns to `.gitignore` (`.env`, `*.json`, `gen-lang-client-*.json`), enable secret scanning in your repo hosting service, and add CI checks where possible.

Note: This document only describes the steps. Do not perform these operations without team agreement and proper backups.







