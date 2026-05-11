# MediaDock

MediaDock is a local web app for downloading video and audio through `yt-dlp`.
It runs only on your machine, provides a Russian-language interface, and supports a download queue, MP3/MP4/WebM and other formats, quality presets, retries, cancellation, optional browser cookies, and YouTube PO-token handling.

## Features

- Local web UI at `http://127.0.0.1:8765`
- Queue for one or many URLs
- Audio formats: MP3, M4A, WAV, FLAC
- Video formats: MP4, WebM, best/original
- Audio quality presets: best, 320k, 192k, 128k
- Video quality caps: best, 2160p, 1440p, 1080p, 720p, 480p
- Optional playlist mode
- Optional cookies from Firefox, Chrome, or Edge
- Progress, speed, ETA, retry, cancel, open file, open folder
- YouTube support with Deno and `bgutil-ytdlp-pot-provider`

## Requirements

- Windows PowerShell
- Python 3.14
- `yt-dlp` installed for Python 3.14
- `ffmpeg`
- Deno
- `bgutil-ytdlp-pot-provider` server folder for YouTube PO tokens

The app was built to use the local workspace layout created during setup:

```text
workspace/
  deno/deno.exe
  bgutil-ytdlp-pot-provider/server/
  video-downloader-app/
```

You can change all tool paths from the Settings panel in the app.

## Run

Open PowerShell:

```powershell
cd "C:\path\to\video-downloader-app"
.\Start-Downloader.ps1
```

The script starts the local backend in the background and opens:

```text
http://127.0.0.1:8765
```

To stop the app:

```powershell
.\Stop-Downloader.ps1
```

## Manual Run

```powershell
py -3.14 server.py --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765`.

## Tests

```powershell
py -3.14 -m unittest discover -s tests -v
```

## Notes

- Downloads are saved to `downloads/` by default.
- Local settings are saved to `data/config.json`; this file is intentionally ignored by git because it contains machine-specific paths.
- Cookies are never used silently. Choose a browser in the UI when needed, for example after a YouTube sign-in or 403 error.
- Use MediaDock only for content you have the right to download.
