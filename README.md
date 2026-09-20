# SoundSphere Host 🎧

SoundSphere Host is a fast, lightweight media streaming and metadata backend designed to power the [SoundSphere Player](https://github.com/Tonarr-Audio/Player) ecosystem. Built on FastAPI and Uvicorn, it indexes your music directory, streams audio with range-request support, serves album covers, and fetches synchronized lyrics dynamically.

---

## 🌟 Features

- **Blazing Fast Library Scanning**: Recursively scans `.mp3`, `.flac`, `.m4a`, `.ogg`, `.opus`, and `.wav` audio files and extracts metadata via `mutagen`.
- **Audio Streaming**: Range-request capable audio streaming directly to any client.
- **Embedded Cover Extraction**: Dynamically extracts and caches embedded album art from audio files.
- **Synchronized Lyrics Provider**: Fetches real-time time-synced lyrics with fallback providers.
- **Web Management UI**: Built-in web dashboard accessible on port `8765`.
- **Zero-Setup Docker**: Ready-to-go Docker container published on GitHub Container Registry (`ghcr.io/tonarr-audio/host:latest`).

---

## 🚀 Quick Start with Docker Compose

Create a `docker-compose.yml` file in your preferred directory:

```yaml
version: "3.8"

services:
  soundsphere-host:
    image: ghcr.io/tonarr-audio/host:latest
    container_name: soundsphere-host
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      # Map your host music library to /music inside the container
      - /DATA/Media/Music:/music:ro
      # Persistent configuration & metadata database
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
      - HOST_PORT=8765
      - HOST_BIND=0.0.0.0
      - HOST_DATA_DIR=/data
```

Start the container:
```bash
docker compose pull
docker compose up -d
```

Your SoundSphere Host will now be live at:
```
http://<your-server-ip>:8765
```

---

## 🖥️ Installation on ZimaOS / CasaOS

1. Open the **App Store** in ZimaOS or CasaOS.
2. Click **Custom Install** (top right).
3. Switch to **Import** and paste the `docker-compose.yml` snippet above.
4. Verify that:
   - **Port**: `8765` is mapped to `8765`.
   - **Volumes**: `/music` points to your actual music folder (e.g. `/DATA/Media/Music` or your storage drive path).
   - **Volumes**: `/data` points to a local persistent directory (e.g. `/DATA/AppData/soundsphere/data`).
5. Click **Submit / Install**.

---

## ⚙️ Configuration & Environment Variables

| Variable | Default | Description |
|---|---|---|
| `HOST_PORT` | `8765` | Internal listening port |
| `HOST_BIND` | `0.0.0.0` | Network binding interface |
| `HOST_DATA_DIR` | `/data` | Directory where `host_config.json` and database files are stored |
| `PYTHONUNBUFFERED` | `1` | Ensures immediate container log flushing |

### Volume Mounts
- `/music` *(Required, read-only recommended)*: Your music collection.
- `/data` *(Required, read-write)*: Configuration and persistent cache storage.

---

## 📡 API Endpoints

SoundSphere Player connects to the following endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/api/info` | `GET` | Host status, version, music directory, and total track count |
| `/api/library` | `GET` | Full track catalog with metadata (artist, title, album, duration) |
| `/api/stream/{track_id}` | `GET` | Audio stream for a track with HTTP range support |
| `/api/cover/{track_id}` | `GET` | Album artwork extracted from the audio file |
| `/api/lyrics` | `GET` | Synced LRC lyrics query (`?title=...&artist=...`) |
| `/api/scan` | `POST` | Trigger a background scan of the music directory |

---

## 🛠️ Local Development

To run directly from source with Python 3.10+:

```bash
git clone https://github.com/Tonarr-Audio/Host.git
cd Host
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8765
```

---

## 📄 License
MIT License. Part of the [Tonarr-Audio](https://github.com/Tonarr-Audio) ecosystem.
