# YouTube Downloader UI (yt-dlp)

A clean Python desktop UI to download:
- Single video
- Playlist
- Video links from a TXT file

## Features
- Neat Tkinter UI with dedicated modes using checkboxes
- Real-time progress bar, downloaded size, ETA, and speed
- Quality selection (`best`, `1080p`, `720p`, `480p`, `360p`, `worst`, `audio only`)
- Fail-safe behavior:
  - Input validation for URL/path/file
  - Worker thread so UI never freezes
  - Soft stop request during active downloads
  - Retries and resume support

## Setup

1. Create/activate your Python environment.
2. Install dependency:

```bash
pip install -r requirements.txt
```

3. Run app:

```bash
python app.py
```

## Notes
- In "Videos From File" mode, each non-empty line in TXT is treated as one link.
- Lines starting with `#` are ignored.
- If a selected quality is unavailable, yt-dlp fallback rules are used.
