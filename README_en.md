# Esperanto Realtime Transcription (Windows Edition)

This repository contains a streamlined, Windows-first workflow for running a realtime Esperanto transcription pipeline on Zoom or Google Meet. It combines Speechmatics realtime STT, optional fallback engines (Vosk / Whisper), a browser caption board, Zoom Closed Caption posting, translation, and Discord webhook delivery.

## 1. Requirements

- **OS:** Windows 10 or later (PowerShell available on the system PATH)  
- **Python:** CPython 3.11+ (3.12 verified; keep the venv name `.venv311`)  
- **Audio loopback:** VB-Audio Virtual Cable or VoiceMeeter (Stereo Mix may work on some devices)  
- **Speechmatics:** Realtime subscription + API key (or JWT)  
- **Zoom:** Host permission to retrieve the Closed Caption POST URL (or a trusted bridge such as Recall.ai)  
- **Optional:**  
  - NVIDIA GPU (RTX 4070 class or better) for Whisper streaming  
  - [Vosk Esperanto model (`vosk-model-small-eo-0.42`+)](https://alphacephei.com/vosk/models) for fully offline use  
  - Google Cloud Translation credentials or LibreTranslate endpoint for automatic translation

## 2. Quickstart

Open **Windows Terminal / PowerShell** and run the following commands in a directory of your choice:

```powershell
git clone https://github.com/Takatakatake/esperanto_onsei_mojiokosi.git
Set-Location .\esperanto_onsei_mojiokosi
py -3.11 -m venv .venv311
.venv311\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Double-click `easy_start.cmd` (or `easy_setup.cmd`) from Explorer, *or* run the helper manually:

```powershell
.\easy_start.cmd
```

The helper:
1. Activates the virtual environment (when present)  
2. Runs dependency checks (`python -m transcriber.cli --easy-start`)  
3. Guides you through audio diagnostics and the final launch  
   - If loopback audio is not configured yet, re-run `scripts\setup_audio_loopback_windows.ps1` when prompted.

## 3. Configure `.env`

Open `.env` with a text editor and adjust the following keys:

```ini
SPEECHMATICS_API_KEY=sk_live_************************
SPEECHMATICS_CONNECTION_URL=wss://<region>.rt.speechmatics.com/v2
SPEECHMATICS_LANGUAGE=eo
ZOOM_CC_POST_URL=https://wmcc.zoom.us/closedcaption?...   # paste the host-provided URL
AUDIO_DEVICE_INDEX=8                                      # device number from --list-devices
AUDIO_DEVICE_SAMPLE_RATE=48000
ZOOM_CC_ENABLED=true
TRANSLATION_ENABLED=true
TRANSLATION_TARGETS=ja,ko
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...  # optional
```

Additional knobs (leave blank unless needed):

```ini
TRANSCRIPTION_BACKEND=speechmatics   # speechmatics | vosk | whisper
AUDIO_WINDOWS_LOOPBACK_DEVICE=CABLE Output (VB-Audio Virtual Cable)
WEB_UI_ENABLED=true
TRANSLATION_PROVIDER=google          # google | libre
GOOGLE_TRANSLATE_CREDENTIALS_PATH=C:\path\to\service-account.json
WHISPER_MODEL_SIZE=medium            # tiny/small/medium/large
WHISPER_DEVICE=auto                  # auto/cpu/cuda
WHISPER_COMPUTE_TYPE=default         # default/float16/int8_float16 etc.
TRANSCRIPT_LOG_PATH=logs\esperanto.log
VOSK_MODEL_PATH=C:\path\to\vosk-model-small-eo-0.42
```

## 4. Everyday Commands

Activate the virtual environment (if you are not using `easy_start.cmd`):

```powershell
.venv311\Scripts\activate
```

Useful CLI flags:

```powershell
python -m transcriber.cli --check-environment   # validate packages/files/.env
python -m transcriber.cli --list-devices        # enumerate WASAPI devices
python -m transcriber.cli --diagnose-audio      # generate a detailed audio report
python -m transcriber.cli --log-level=INFO      # launch the pipeline
python -m transcriber.cli --backend=vosk        # override backend on the fly
python -m transcriber.cli --setup-wizard        # Windows setup hints
python -m transcriber.cli --show-config         # current configuration (secrets masked)
```

The caption board is served at **http://127.0.0.1:8765** when `WEB_UI_ENABLED=true`. Set `WEB_UI_OPEN_BROWSER=true` to auto-launch your default browser. If the page does not open, verify both flags and allow the port in local firewalls/security tools.

Stop the pipeline with `Ctrl+C`. Logs show each final segment under `Final:` and Zoom caption posting status.

## 5. Audio & Diagnostics

- Ensure the loopback device (VB-Audio / VoiceMeeter) is visible in `--list-devices`.  
- If Speechmatics remains silent, re-run `python -m transcriber.cli --diagnose-audio` to confirm the loopback input is active and not muted.  
- To revert to the system defaults, open **Sound Settings > Recording** and change the default device back to your preferred microphone.

### Translation smoke test

```powershell
python scripts\test_translation.py "Bonvenon al nia kunsido."
```

The script uses your current `.env` settings and prints any returned translations.

## 6. Troubleshooting (Windows)

| Symptom | Fix |
|---------|-----|
| `ImportError: No module named sounddevice` | Run `python -m pip install -r requirements.txt` inside `.venv311\Scripts\activate`. |
| `Speechmatics connection failed` | Verify `SPEECHMATICS_API_KEY`, `SPEECHMATICS_CONNECTION_URL`, firewall exceptions, and that your plan has realtime entitlement. |
| Zoom captions missing | Confirm `ZOOM_CC_POST_URL`, set `ZOOM_CC_ENABLED=true`, ensure host permissions, and watch logs for HTTP status codes (401/403 = invalid/expired URL). |
| Whisper missing GPU acceleration | Install the latest NVIDIA driver. Ensure `WHISPER_DEVICE=cuda` and that CUDA-enabled PyTorch is available. |
| Loopback device not listed | Run `scripts\setup_audio_loopback_windows.ps1` directly, then re-open the recording devices panel and enable "Stereo Mix" or VB-Audio cable. |
| Port 8765 still in use | Close any existing `python -m transcriber.cli` windows. If necessary, in PowerShell: `Stop-Process -Name python -Force` (use with caution) and ensure local firewalls allow the port. |

## 7. Security reminders

- `.env.example` ships with masked values. Copy it to `.env` and store secrets locally only.  
- Never commit real keys. Add sensitive files (`.env`, `*.json`) to `.gitignore` if you create new ones.  
- If secrets are accidentally committed: remove them with `git rm --cached`, rotate the credentials immediately, and coordinate with your team on history rewrites if the secret reached a remote.  

## 8. Repository layout highlights

- `transcriber/cli.py` - entry point (`python -m transcriber.cli`) with helpers such as `--easy-start`, `--diagnose-audio`, etc.  
- `transcriber/audio.py` - async WASAPI capture with automatic device monitoring and resampling.  
- `transcriber/asr/` - Speechmatics, Vosk, and Whisper streaming clients (all present, only Speechmatics enabled by default).  
- `transcriber/pipeline.py` - orchestrates audio ingest, backend consumption, Zoom posting, translation, Discord batching, and Web UI broadcasting.  
- `scripts/` - Windows helpers (`easy_start.ps1`, `setup_audio_loopback_windows.ps1`, `check_environment.py`, `diagnose_audio.py`).  
- `tests/` - unit tests for environment checks and audio heuristics (run with `pytest` once dependencies are installed).  
- `web/` - static assets for the browser caption board (styles, client WebSocket logic).  

## 9. Next steps

1. Collect Zoom participant consent and share that live transcription is active.  
2. Populate the Speechmatics custom dictionary with domain terminology for better accuracy.  
3. Benchmark Vosk/Whisper backends if you require offline or on-premise alternatives.  
4. Configure scheduled restarts or supervisors (Task Scheduler, NSSM, pm2) if you need 24/7 reliability.  
5. Keep your `.env` and translation credentials outside version control and review them regularly.  

Enjoy your low-latency Esperanto transcription experience on Windows!
