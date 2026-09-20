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
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

try:
    from .config import load_host_config, save_host_config, HostConfig, DATA_DIR
    from .scanner import library_cache, scan_directory_streaming
    from .metadata import metadata_engine
    from .plex_client import HostPlexClient, load_host_playlists, save_host_playlists
except (ImportError, ValueError):
    from config import load_host_config, save_host_config, HostConfig, DATA_DIR
    from scanner import library_cache, scan_directory_streaming
    from metadata import metadata_engine
    from plex_client import HostPlexClient, load_host_playlists, save_host_playlists

HOST_VERSION = "1.0.0"
START_TIME = time.time()

app = FastAPI(
    title="Tonarr Host",
    version=HOST_VERSION,
    description="Dedicated High-Performance Music & Metadata Server for the Tonarr Suite"
)

@app.on_event("startup")
async def on_startup():
    config = load_host_config()
    print(f"[Tonarr Host] v{HOST_VERSION} started. Data directory: {DATA_DIR}. Music directory: {config.music_directory}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Type"],
)

# Optional Token Dependency (Accepts X-Tonarr-Token, X-SoundSphere-Token, or ?token=)
async def verify_token(
    request: Request,
    x_tonarr_token: Optional[str] = Header(None),
    x_soundsphere_token: Optional[str] = Header(None)
):
    config = load_host_config()
    if not config.api_token:
        return True
    
    query_token = request.query_params.get("token")
    auth_header = request.headers.get("authorization", "").replace("Bearer ", "")
    passed = x_tonarr_token or x_soundsphere_token or query_token or auth_header
    
    if passed != config.api_token:
        raise HTTPException(status_code=401, detail="Ungültiger oder fehlender Tonarr Host Token.")
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
        "app": "Tonarr Host",
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
    offset: int = Query(0),
    client: Optional[str] = Query(None),
    include_creator_only: bool = Query(False)
):
    config = load_host_config()
    tracks = library_cache.tracks

    # If folder is used only as source for LRCCreator & MediaManager,
    # exclude local folder tracks from Player requests
    if config.folder_source_for_creator_and_manager_only:
        is_creator_or_manager = (client or "").lower().strip() in ("creator", "lrccreator", "mediamanager", "manager") or include_creator_only
        if not is_creator_or_manager:
            tracks = [t for t in tracks if t.get("source") == "plex"]

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

@app.get("/api/creator/tracks")
async def get_creator_tracks(
    q: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    album: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    offset: int = Query(0)
):
    """Returns local folder tracks exclusively for LRCCreator and MediaManager."""
    tracks = [t for t in library_cache.tracks if t.get("source") != "plex" or (t.get("file_path") and not str(t.get("file_path")).startswith("plex://"))]

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
@app.get("/api/stream/{track_id}")
async def stream_audio(
    track_id: Optional[str] = None,
    id: Optional[str] = Query(None),
    path: Optional[str] = Query(None),
    request: Request = None
):
    query_id = track_id or id
    target_path = None

    if query_id:
        clean_id = str(query_id).replace("host://", "").strip()
        track = library_cache.get_track(clean_id) or library_cache.get_track(query_id)
        if track:
            if track.get("stream_url") and (not track.get("file_path") or not os.path.exists(str(track.get("file_path")))):
                return RedirectResponse(track["stream_url"])
            if track.get("file_path") and os.path.exists(track.get("file_path")):
                target_path = track.get("file_path")

    if not target_path and path:
        clean_p = path.replace("host://", "").strip()
        track = library_cache.get_track(clean_p)
        if track and track.get("stream_url") and (not track.get("file_path") or not os.path.exists(str(track.get("file_path")))):
            return RedirectResponse(track["stream_url"])
        if os.path.exists(clean_p):
            target_path = clean_p
        elif track and track.get("file_path") and os.path.exists(track.get("file_path")):
            target_path = track.get("file_path")

    if not target_path or not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Audiodatei nicht gefunden.")

    ext = Path(target_path).suffix.lower()
    content_type_map = {
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".wav": "audio/wav",
        ".alac": "audio/mp4",
        ".aiff": "audio/aiff"
    }
    content_type = content_type_map.get(ext, "audio/mpeg")

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length, Content-Type",
        "Accept-Ranges": "bytes",
    }
    return FileResponse(
        target_path,
        media_type=content_type,
        headers=headers
    )

@app.options("/api/audio/stream")
@app.options("/api/stream")
@app.options("/api/stream/{track_id}")
async def options_stream():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length, Content-Type",
        }
    )

# --- Cover Art ---
_cover_cache: Dict[str, Tuple[bytes, str]] = {}

@app.get("/api/track/cover")
@app.get("/api/cover")
@app.get("/api/cover/{track_id}")
async def get_cover_art(track_id: Optional[str] = None, path: Optional[str] = Query(None), id: Optional[str] = Query(None)):
    query_id = track_id or id
    target_path = None

    if query_id:
        clean_id = str(query_id).replace("host://", "").strip()
        track = library_cache.get_track(clean_id) or library_cache.get_track(query_id)
        if track and track.get("file_path") and os.path.exists(track.get("file_path")):
            target_path = track.get("file_path")

    if not target_path and path:
        clean_p = path.replace("host://", "").strip()
        if os.path.exists(clean_p):
            target_path = clean_p
        else:
            track = library_cache.get_track(clean_p)
            if track and track.get("file_path") and os.path.exists(track.get("file_path")):
                target_path = track.get("file_path")

    if not target_path or not os.path.exists(target_path):
        if track and track.get("plex_cover_url"):
            try:
                import httpx
                async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
                    r = await client.get(track["plex_cover_url"])
                    if r.status_code == 200:
                        c_type = r.headers.get("Content-Type", "image/jpeg")
                        return Response(content=r.content, media_type=c_type, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})
            except Exception:
                pass
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
    target_path = None
    track_obj = None

    if id:
        clean_id = str(id).replace("host://", "").strip()
        track_obj = library_cache.get_track(clean_id) or library_cache.get_track(id)
        if track_obj and track_obj.get("file_path") and os.path.exists(track_obj.get("file_path")):
            target_path = track_obj.get("file_path")

    if not target_path and path:
        clean_p = path.replace("host://", "").strip()
        if os.path.exists(clean_p):
            target_path = clean_p
        else:
            track_obj = library_cache.get_track(clean_p)
            if track_obj and track_obj.get("file_path") and os.path.exists(track_obj.get("file_path")):
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

# --- PLEX MEDIA SERVER ENDPOINTS (Host Sync & Metadata Integration) ---
@app.get("/api/plex/status")
@app.get("/api/plex/test")
@app.post("/api/plex/test")
async def get_plex_status(url: Optional[str] = Query(None), token: Optional[str] = Query(None)):
    config = load_host_config()
    target_url = (url or config.plex_url or "").strip()
    target_token = (token or config.plex_token or "").strip()
    if not target_url or not target_token:
        return {
            "success": False,
            "configured": False,
            "reachable": False,
            "error": "Plex Server ist auf dem Host noch nicht konfiguriert."
        }
    client = HostPlexClient(target_url, target_token)
    res = await client.test_connection()
    is_ok = bool(res.get("success"))
    return {
        "success": is_ok,
        "configured": True,
        "reachable": is_ok,
        "server_name": res.get("name") or "Plex Media Server",
        "version": res.get("version", ""),
        "effective_url": res.get("effective_url", target_url),
        "section": config.plex_section,
        "prefer_plex_metadata": config.prefer_plex_metadata,
        "error": res.get("error")
    }

class HostPlexPinRequest(BaseModel):
    callback_url: Optional[str] = ""

@app.post("/api/plex/auth/pin")
async def create_plex_pin(req: Optional[HostPlexPinRequest] = None):
    cb = req.callback_url if req else ""
    return await HostPlexClient.create_auth_pin(cb)

@app.get("/api/plex/auth/check")
async def check_plex_pin(pin_id: int, code: str = ""):
    res = await HostPlexClient.check_auth_pin(pin_id, code)
    if res.get("authorized"):
        token = res.get("token")
        servers = res.get("servers", [])
        config = load_host_config()
        config.plex_token = token
        config.plex_enabled = True
        if servers:
            best_server = servers[0]
            config.plex_url = best_server.get("uri", "")
            config.plex_token = best_server.get("token") or token
        save_host_config(config)
    return res

@app.get("/api/plex/callback", response_class=HTMLResponse)
async def host_plex_auth_callback():
    """Auto-closing callback page after Plex authentication."""
    html_content = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Plex Anmeldung erfolgreich</title>
  <style>
    body {
      background-color: #0f1117;
      color: #ffffff;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
    }
    .card {
      background: #1a1d26;
      border: 1px solid #2e3446;
      border-radius: 12px;
      padding: 32px 24px;
      text-align: center;
      max-width: 380px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.5);
    }
    .icon { font-size: 48px; margin-bottom: 12px; }
    h2 { margin: 0 0 8px 0; font-size: 1.3rem; color: #4ade80; }
    p { color: #94a3b8; font-size: 0.9rem; line-height: 1.4; margin: 0 0 16px 0; }
    .badge {
      display: inline-block;
      font-size: 0.8rem;
      padding: 4px 10px;
      border-radius: 6px;
      background: rgba(229, 160, 13, 0.15);
      color: #e5a00d;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h2>Plex Anmeldung erfolgreich!</h2>
    <p>Tonarr Host wurde erfolgreich autorisiert.<br>Dieses Fenster schließt sich in Kürze automatisch...</p>
    <div class="badge">Tonarr Host</div>
  </div>
  <script>
    try {
      if (window.opener) {
        window.opener.postMessage({ type: "PLEX_AUTH_SUCCESS" }, "*");
      }
    } catch (e) {}
    setTimeout(function() {
      try { window.close(); } catch (e) {}
    }, 1000);
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)

@app.post("/api/plex/auth/logout")
async def logout_plex():
    config = load_host_config()
    config.plex_token = ""
    config.plex_url = ""
    config.plex_enabled = False
    save_host_config(config)
    return {"success": True, "message": "Plex Server erfolgreich vom Host getrennt."}

@app.get("/api/plex/sections")
async def get_plex_sections():
    config = load_host_config()
    client = HostPlexClient(config.plex_url, config.plex_token)
    return await client.get_music_sections()

class SetSectionRequest(BaseModel):
    section: str

@app.post("/api/plex/set-section")
async def set_plex_section(body: SetSectionRequest):
    config = load_host_config()
    config.plex_section = body.section
    save_host_config(config)
    return {"success": True, "section": body.section}

@app.get("/api/plex/tracks")
async def get_plex_tracks(section: Optional[str] = Query(None)):
    config = load_host_config()
    client = HostPlexClient(config.plex_url, config.plex_token)
    return await client.get_all_tracks(section or config.plex_section or None)

@app.post("/api/plex/sync")
async def sync_plex_endpoint():
    """Synchronizes Plex library & playlists on the Host. Metadata from Plex is prioritized."""
    config = load_host_config()
    if not config.plex_url or not config.plex_token:
        raise HTTPException(status_code=400, detail="Plex Server ist auf dem Host nicht konfiguriert.")
    
    client = HostPlexClient(config.plex_url, config.plex_token)
    tracks = await client.get_all_tracks(config.plex_section or None)
    
    updated_count = 0
    if tracks and config.prefer_plex_metadata:
        plex_by_title_artist = {}
        plex_by_path = {}
        for pt in tracks:
            t_norm = (pt.get("title") or "").strip().lower()
            a_norm = (pt.get("artist") or "").strip().lower()
            if t_norm:
                plex_by_title_artist[(t_norm, a_norm)] = pt
                plex_by_title_artist[(t_norm, "")] = pt
            s_path = pt.get("server_file_path") or ""
            if s_path:
                plex_by_path[Path(s_path).name.lower()] = pt

        for lt in library_cache.tracks:
            lt_title = (lt.get("title") or "").strip().lower()
            lt_artist = (lt.get("artist") or "").strip().lower()
            lt_file = Path(lt.get("file_path", "")).name.lower()
            
            match = plex_by_title_artist.get((lt_title, lt_artist)) or plex_by_title_artist.get((lt_title, "")) or plex_by_path.get(lt_file)
            if match:
                lt["title"] = match.get("title") or lt.get("title")
                lt["artist"] = match.get("artist") or lt.get("artist")
                lt["album"] = match.get("album") or lt.get("album")
                lt["plex_key"] = match.get("plex_key")
                if match.get("cover_url"):
                    lt["plex_cover_url"] = match.get("cover_url")
                    lt["cover_url"] = match.get("cover_url")
                    lt["has_cover"] = True
                if match.get("genre"):
                    lt["genre"] = match.get("genre")
                if match.get("year"):
                    lt["year"] = match.get("year")
                if match.get("track_number"):
                    lt["track_number"] = match.get("track_number")
                updated_count += 1
                
        # Ensure all Plex tracks are available in the Host library
        existing_plex_ids = {t.get("id") for t in library_cache.tracks if t.get("source") == "plex"}
        new_plex_tracks = [pt for pt in tracks if pt.get("id") not in existing_plex_ids]
        if new_plex_tracks:
            library_cache.tracks.extend(new_plex_tracks)

        library_cache.save()
        library_cache._rebuild_map()

    # Synchronize all Plex Playlists
    plex_pls = await client.get_playlists()
    synced_pls = []
    for pl in plex_pls:
        pl_key = pl.get("plex_key")
        if pl_key:
            pl_tracks = await client.get_playlist_tracks(str(pl_key))
            track_ids = []
            for pt in pl_tracks:
                pt_norm = (pt.get("title") or "").strip().lower()
                pa_norm = (pt.get("artist") or "").strip().lower()
                matched_local = None
                for lt in library_cache.tracks:
                    if (lt.get("title") or "").strip().lower() == pt_norm and (lt.get("artist") or "").strip().lower() == pa_norm:
                        matched_local = lt
                        break
                if matched_local:
                    track_ids.append(matched_local.get("id"))
                else:
                    track_ids.append(pt.get("id") or f"plex_{pt.get('plex_key')}")
            
            synced_pls.append({
                "id": pl.get("id"),
                "name": pl.get("name"),
                "source": "plex",
                "track_count": len(track_ids),
                "track_ids": track_ids,
                "cover_url": pl.get("cover_url")
            })

    save_host_playlists(synced_pls)

    return {
        "success": True,
        "count": len(tracks),
        "updated_metadata_count": updated_count,
        "playlist_count": len(synced_pls),
        "tracks": tracks,
        "playlists": synced_pls,
        "message": f"{len(tracks)} Plex Songs & {len(synced_pls)} Playlists synchronisiert. {updated_count} lokale Titel mit Plex-Metadaten aktualisiert."
    }

@app.get("/api/plex/playlists")
async def get_plex_playlists_endpoint():
    config = load_host_config()
    if not config.plex_url or not config.plex_token:
        return load_host_playlists()
    client = HostPlexClient(config.plex_url, config.plex_token)
    pls = await client.get_playlists()
    return pls if pls else load_host_playlists()

@app.get("/api/plex/playlists/{key}/tracks")
async def get_plex_playlist_tracks_endpoint(key: str):
    config = load_host_config()
    client = HostPlexClient(config.plex_url, config.plex_token)
    return await client.get_playlist_tracks(key)

@app.post("/api/plex/playlists/sync-all")
async def sync_all_plex_playlists_endpoint():
    res = await sync_plex_endpoint()
    return {
        "success": True,
        "count": res.get("playlist_count", 0),
        "playlists": res.get("playlists", []),
        "new_tracks": res.get("tracks", [])
    }

# --- Universal Host Playlists API ---
@app.get("/api/playlists")
async def get_all_host_playlists():
    return load_host_playlists()

class SavePlaylistRequest(BaseModel):
    name: str
    source: Optional[str] = "custom"
    track_ids: List[str] = []

@app.post("/api/playlists")
async def save_host_playlist_endpoint(pl: SavePlaylistRequest):
    current = load_host_playlists()
    import uuid
    new_id = f"{pl.source}_{uuid.uuid4().hex[:8]}"
    obj = {
        "id": new_id,
        "name": pl.name,
        "source": pl.source or "custom",
        "track_count": len(pl.track_ids),
        "track_ids": pl.track_ids
    }
    current.append(obj)
    save_host_playlists(current)
    return {"success": True, "playlist": obj}

WEB_DIR = Path(__file__).parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    async def serve_dashboard():
        index_path = WEB_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return HTMLResponse("<h2>Tonarr Host ist aktiv!</h2>")
