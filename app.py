import os
import sys
import json
import time
import socket
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from fastapi import FastAPI, HTTPException, Query, Request, Response, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel

from .config import load_host_config, save_host_config, HostConfig, DATA_DIR
from .scanner import library_cache, scan_directory_streaming
from .metadata import metadata_engine

HOST_VERSION = "1.0.0"
START_TIME = time.time()

app = FastAPI(
    title="SoundSphere Host",
    version=HOST_VERSION,
    description="Dedicated High-Performance Music & Metadata Server for the SoundSphere Suite"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional Token Dependency
async def verify_token(request: Request, x_soundsphere_token: Optional[str] = Header(None)):
    config = load_host_config()
    if not config.api_token:
        return True
    
    query_token = request.query_params.get("token")
    auth_header = request.headers.get("authorization", "").replace("Bearer ", "")
    passed = x_soundsphere_token or query_token or auth_header
    
    if passed != config.api_token:
        raise HTTPException(status_code=401, detail="Ungültiger oder fehlender SoundSphere Host Token.")
    return True

# --- Information & Status ---
@app.get("/api/info")
@app.get("/api/status")
async def get_host_status():
    config = load_host_config()
    stats = library_cache.get_stats()
    
    # Get local IP for easy client connection
    host_ip = "localhost"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    return {
        "status": "online",
        "app": "SoundSphere Host",
        "version": HOST_VERSION,
        "host_name": config.host_name,
        "port": config.port,
        "local_ip": host_ip,
        "connection_url": f"http://{host_ip}:{config.port}",
        "uptime_seconds": int(time.time() - START_TIME),
        "is_scanning": library_cache.is_scanning,
        "music_directory": config.music_directory,
        "stats": stats,
        "auth_required": bool(config.api_token)
    }

# --- Configuration ---
@app.get("/api/config")
async def get_configuration():
    return load_host_config()

@app.post("/api/config")
async def update_configuration(config: HostConfig):
    save_host_config(config)
    return {"success": True, "config": config}

# --- Directory Browser (Helper for Web UI Folder Selection) ---
class BrowseFolderRequest(BaseModel):
    path: Optional[str] = None

@app.post("/api/browse-directory")
@app.get("/api/browse-directory")
async def browse_directory(path: Optional[str] = Query(None)):
    current = Path(path).resolve() if path and os.path.exists(path) else Path("/music" if os.path.exists("/music") else Path.home())
    
    subdirs = []
    parent_path = str(current.parent) if current.parent != current else None

    try:
        for entry in os.scandir(current):
            if entry.is_dir() and not entry.name.startswith("."):
                subdirs.append({
                    "name": entry.name,
                    "path": entry.path
                })
        subdirs.sort(key=lambda x: x["name"].lower())
    except Exception as e:
        return {"current": str(current), "parent": parent_path, "directories": [], "error": str(e)}

    return {
        "current": str(current),
        "parent": parent_path,
        "directories": subdirs
    }

# --- Scanning & Indexing (SSE Streaming) ---
@app.get("/api/scan-stream")
async def scan_stream_endpoint(directory: Optional[str] = Query(None)):
    async def event_generator():
        async for event in scan_directory_streaming(directory):
            data_str = json.dumps(event, ensure_ascii=False)
            yield f"data: {data_str}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.post("/api/scan")
async def trigger_scan_endpoint(directory: Optional[str] = None):
    # Triggers scan in background if not already scanning
    if library_cache.is_scanning:
        return {"status": "scanning", "message": "Scan läuft bereits."}
    
    # Run in event loop
    async def _run():
        async for _ in scan_directory_streaming(directory):
            pass

    import asyncio
    asyncio.create_task(_run())
    return {"status": "started", "message": "Mediathek-Scan gestartet."}

# --- Tracks & Library API ---
@app.get("/api/tracks")
async def get_tracks_list(
    q: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    album: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    offset: int = Query(0)
):
    tracks = library_cache.tracks
    
    if artist:
        art_clean = artist.lower().strip()
        tracks = [t for t in tracks if (t.get("artist") or "").lower().strip() == art_clean]
        
    if album:
        alb_clean = album.lower().strip()
        tracks = [t for t in tracks if (t.get("album") or "").lower().strip() == alb_clean]

    if q:
        tokens = q.lower().strip().split()
        def match(t):
            txt = f"{t.get('title','')} {t.get('artist','')} {t.get('album','')} {t.get('genre','')}".lower()
            return all(tok in txt for tok in tokens)
        tracks = [t for t in tracks if match(t)]

    total = len(tracks)
    if limit:
        tracks = tracks[offset:offset+limit]

    return {
        "total": total,
        "count": len(tracks),
        "offset": offset,
        "tracks": tracks
    }

@app.get("/api/track/{track_id}")
async def get_single_track(track_id: str):
    track = library_cache.get_track(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Titel nicht gefunden.")
    return track

# --- Audio Streaming (HTTP 206 Range Support) ---
@app.get("/api/audio/stream")
@app.get("/api/stream")
async def stream_audio(path: Optional[str] = Query(None), id: Optional[str] = Query(None), request: Request = None):
    target_path = path
    if not target_path and id:
        track = library_cache.get_track(id)
        if track:
            target_path = track.get("file_path")

    if not target_path or not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Audiodatei nicht gefunden.")

    file_size = os.path.getsize(target_path)
    ext = Path(target_path).suffix.lower()
    
    content_type_map = {
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".wav": "audio/wav",
        ".alac": "audio/mp4",
        ".aiff": "audio/aiff"
    }
    content_type = content_type_map.get(ext, "audio/mpeg")

    range_header = request.headers.get("range") if request else None
    if range_header:
        byte_range = range_header.replace("bytes=", "").split("-")
        start = int(byte_range[0])
        end = int(byte_range[1]) if byte_range[1] else file_size - 1
        length = end - start + 1

        def iter_file():
            with open(target_path, "rb") as f:
                f.seek(start)
                bytes_left = length
                while bytes_left > 0:
                    chunk = f.read(min(bytes_left, 65536))
                    if not chunk:
                        break
                    bytes_left -= len(chunk)
                    yield chunk

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
        }
        return StreamingResponse(iter_file(), status_code=206, headers=headers)
    else:
        return FileResponse(target_path, media_type=content_type, headers={"Access-Control-Allow-Origin": "*"})

# --- Cover Art ---
_cover_cache: Dict[str, Tuple[bytes, str]] = {}

@app.get("/api/track/cover")
@app.get("/api/cover")
async def get_cover_art(path: Optional[str] = Query(None), id: Optional[str] = Query(None)):
    target_path = path
    if not target_path and id:
        track = library_cache.get_track(id)
        if track:
            target_path = track.get("file_path")

    if not target_path or not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")

    if target_path in _cover_cache:
        data, mime = _cover_cache[target_path]
        return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

    p = Path(target_path)
    # 1. Embedded Cover
    cover_data = metadata_engine.extract_embedded_cover_bytes(p)
    if cover_data:
        data, mime = cover_data
        _cover_cache[target_path] = (data, mime)
        return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

    # 2. Local Image in Directory (cover.jpg, folder.jpg)
    local_img = metadata_engine.find_local_cover_file(p)
    if local_img and local_img.exists():
        mime = "image/png" if local_img.suffix.lower() == ".png" else "image/jpeg"
        data = local_img.read_bytes()
        _cover_cache[target_path] = (data, mime)
        return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

    raise HTTPException(status_code=404, detail="Kein Cover vorhanden.")

# --- Lyrics API ---
@app.get("/api/track/lyrics")
@app.get("/api/lyrics")
async def get_track_lyrics(
    path: Optional[str] = Query(None),
    id: Optional[str] = Query(None),
    title: Optional[str] = Query(None),
    artist: Optional[str] = Query(None)
):
    target_path = path
    track_obj = None
    if not target_path and id:
        track_obj = library_cache.get_track(id)
        if track_obj:
            target_path = track_obj.get("file_path")

    config = load_host_config()
    content = ""
    is_synced = False
    source = "none"

    if target_path and os.path.exists(target_path):
        p = Path(target_path)
        
        # 1. Local .lrc/.txt file
        local_lrc, is_synced_local = metadata_engine.find_local_lyrics_file(p, config.custom_lrc_dir)
        if local_lrc and config.prefer_local_lyrics:
            try:
                content = local_lrc.read_text(encoding="utf-8", errors="ignore")
                is_synced = is_synced_local
                source = "local_file"
            except Exception:
                pass

        # 2. Embedded Lyrics
        if not content and config.prefer_local_lyrics:
            emb = metadata_engine.extract_embedded_info(p)
            if emb.get("has_embedded_lyrics"):
                content = emb.get("embedded_lyrics", "")
                is_synced = emb.get("is_synced_embedded", False)
                source = "embedded"

    # 3. Online LRCLIB Fallback
    if not content and config.fetch_missing_online and config.sources.lrclib:
        t_title = title or (track_obj.get("title") if track_obj else None) or (Path(target_path).stem if target_path else "")
        t_artist = artist or (track_obj.get("artist") if track_obj else None) or ""
        
        online = await metadata_engine.fetch_lrclib_lyrics(t_title, t_artist)
        if online:
            content = online.get("synced_lyrics") or online.get("plain_lyrics") or ""
            is_synced = bool(online.get("synced_lyrics"))
            source = "lrclib"

            # Auto-save online lyrics if configured
            if config.save_fetched_lrc_locally and online.get("synced_lyrics") and target_path:
                try:
                    save_p = Path(target_path).parent / f"{Path(target_path).stem}.lrc"
                    save_p.write_text(online["synced_lyrics"], encoding="utf-8")
                except Exception:
                    pass

    return {
        "has_lyrics": bool(content.strip()),
        "is_synced": is_synced,
        "source": source,
        "content": content
    }

# --- Mount Web UI Dashboard ---
WEB_DIR = Path(__file__).parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    async def serve_dashboard():
        index_path = WEB_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return HTMLResponse("<h2>SoundSphere Host ist aktiv!</h2>")
