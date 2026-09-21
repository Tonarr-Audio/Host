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
    from .plex_client import HostPlexClient, load_host_playlists, save_host_playlists, rewrite_plex_direct_url
except (ImportError, ValueError):
    from config import load_host_config, save_host_config, HostConfig, DATA_DIR
    from scanner import library_cache, scan_directory_streaming
    from metadata import metadata_engine
    from plex_client import HostPlexClient, load_host_playlists, save_host_playlists, rewrite_plex_direct_url

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
async def update_configuration(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    current = load_host_config()
    curr_dict = current.model_dump()

    # Update provided fields safely
    for k, v in body.items():
        if k in ("plex_url", "plex_token"):
            # Never overwrite valid existing Plex credentials with empty strings from clients
            if v and str(v).strip():
                curr_dict[k] = str(v).strip()
                curr_dict["plex_enabled"] = True
        elif k == "plex_enabled":
            # Only disable if plex_url is also empty or during explicit logout
            if v or not curr_dict.get("plex_url"):
                curr_dict[k] = bool(v)
        elif k in curr_dict:
            curr_dict[k] = v

    val = bool(curr_dict.get("plex_as_only_player_source") or curr_dict.get("folder_source_for_creator_and_manager_only"))
    curr_dict["plex_as_only_player_source"] = val
    curr_dict["folder_source_for_creator_and_manager_only"] = val

    updated_config = HostConfig(**curr_dict)
    save_host_config(updated_config)
    return {"success": True, "config": updated_config}

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

def _clean_track_text(s: str) -> str:
    if not s:
        return ""
    import re
    txt = s.lower().strip()
    txt = re.sub(r'^\d+[\s\.\-_]+', '', txt).strip()
    txt = re.sub(r'\s*(feat\.?|featuring|ft\.).*$', '', txt, flags=re.IGNORECASE).strip()
    txt = re.sub(r'\s*[\(\[](remastered|remaster|album version|official|deluxe|bonus|live).*?[\)\]]', '', txt, flags=re.IGNORECASE).strip()
    txt = re.sub(r'[^\w\s]', '', txt)
    return re.sub(r'\s+', ' ', txt).strip()

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
    raw_tracks = library_cache.tracks

    # If folder is used only as source for LRCCreator & MediaManager / Plex is the only Player source:
    # exclude local folder tracks from Player requests
    plex_only = bool(config.plex_as_only_player_source or config.folder_source_for_creator_and_manager_only)
    is_creator_or_manager = (client or "").lower().strip() in ("creator", "lrccreator", "mediamanager", "manager") or include_creator_only

    def is_plex_available(t):
        if not t:
            return False
        if t.get("source") == "plex":
            return True
        if bool(t.get("plex_key")):
            return True
        fpath = str(t.get("file_path", ""))
        if fpath.startswith("plex://"):
            return True
        tid = str(t.get("id", ""))
        if tid.startswith("plex_") or tid.startswith("plex://"):
            return True
        return False

    if plex_only and not is_creator_or_manager:
        candidate_tracks = [t for t in raw_tracks if is_plex_available(t)]
    else:
        candidate_tracks = raw_tracks

    # Deduplicate tracks so each song appears exactly once
    seen_keys = set()
    seen_norms = set()
    seen_files = set()
    tracks = []
    for t in candidate_tracks:
        pkey = str(t.get("plex_key") or "").strip()
        fpath = Path(str(t.get("server_file_path") or t.get("file_path") or "")).name.lower()
        norm_at = (_clean_track_text(t.get("artist") or ""), _clean_track_text(t.get("title") or ""))

        if pkey and pkey in seen_keys:
            continue
        if fpath and not fpath.startswith("plex:") and fpath in seen_files:
            continue
        if norm_at[0] and norm_at[1] and norm_at in seen_norms:
            continue

        if pkey:
            seen_keys.add(pkey)
        if fpath and not fpath.startswith("plex:"):
            seen_files.add(fpath)
        if norm_at[0] and norm_at[1]:
            seen_norms.add(norm_at)
        tracks.append(t)

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
@app.get("/api/audio/stream/{track_id}")
@app.get("/api/stream")
@app.get("/api/stream/{track_id}")
@app.head("/api/audio/stream")
@app.head("/api/audio/stream/{track_id}")
@app.head("/api/stream")
@app.head("/api/stream/{track_id}")
async def stream_audio(
    track_id: Optional[str] = None,
    id: Optional[str] = Query(None),
    path: Optional[str] = Query(None),
    key: Optional[str] = Query(None),
    request: Request = None
):
    query_id = track_id or id or key
    target_path = None
    track = None

    if query_id:
        clean_id = urllib.parse.unquote(str(query_id)).replace("host://", "").strip()
        track = library_cache.get_track(clean_id) or library_cache.get_track(query_id)
        if track and track.get("file_path") and os.path.exists(str(track.get("file_path"))):
            target_path = track.get("file_path")

    if not target_path and path:
        clean_p = urllib.parse.unquote(str(path)).replace("host://", "").strip()
        if os.path.exists(clean_p):
            target_path = clean_p
        else:
            track = track or library_cache.get_track(clean_p)
            if track and track.get("file_path") and os.path.exists(str(track.get("file_path"))):
                target_path = track.get("file_path")

    # 1. Local file on host: Stream with FileResponse (HTTP 206 Range support)
    if target_path and os.path.exists(target_path):
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
        return FileResponse(target_path, media_type=content_type, headers=headers)

    # 2. Plex track streaming via Host with HTTP 206 Range proxying
    raw_ident = query_id or path or ""
    clean_ident = urllib.parse.unquote(str(raw_ident)).replace("host://", "").replace("plex://", "").strip()
    clean_plex_key = None
    if clean_ident.startswith("plex_"):
        clean_plex_key = clean_ident.replace("plex_", "")
    elif clean_ident.isdigit():
        clean_plex_key = clean_ident
    elif track and (track.get("source") == "plex" or track.get("plex_key")):
        clean_plex_key = str(track.get("plex_key") or str(track.get("id", "")).replace("plex_", ""))

    if clean_plex_key:
        config = load_host_config()
        if config.plex_url and config.plex_token:
            try:
                client = HostPlexClient(config.plex_url, config.plex_token)
                base_url = await client._get_working_base_url()
                base_url = rewrite_plex_direct_url(base_url).rstrip("/")
                
                part_key = track.get("part_key") if track else None
                container = (track.get("extension", ".mp3").replace(".", "") if track else "mp3")
                
                if not part_key and base_url:
                    async with httpx.AsyncClient(timeout=12.0, verify=False, follow_redirects=True) as http_client:
                        m_res = await http_client.get(
                            f"{base_url}/library/metadata/{clean_plex_key}?X-Plex-Token={config.plex_token}",
                            headers=client._get_api_headers()
                        )
                        if m_res.status_code == 200:
                            meta_item = m_res.json().get("MediaContainer", {}).get("Metadata", [{}])[0]
                            m_list = meta_item.get("Media", [{}])
                            if m_list:
                                container = m_list[0].get("container", container)
                                parts = m_list[0].get("Part", [{}])
                                if parts:
                                    part_key = parts[0].get("key", "")
                
                if not part_key:
                    part_key = f"/library/parts/{clean_plex_key}/file"

                sep = "" if part_key.startswith("/") else "/"
                token_sep = "&" if "?" in part_key else "?"
                full_stream_url = f"{base_url}{sep}{part_key}{token_sep}X-Plex-Token={config.plex_token}"
                req_headers = {
                    "X-Plex-Token": config.plex_token,
                    "Accept": "*/*",
                    "User-Agent": "Tonarr-Host/1.0.0",
                }
                range_hdr = request.headers.get("range") if request else None
                if range_hdr:
                    req_headers["Range"] = range_hdr

                stream_client = httpx.AsyncClient(timeout=60.0, verify=False, follow_redirects=True)

                # If HEAD request, probe upstream without streaming payload
                if request and request.method.upper() == "HEAD":
                    upstream_resp = await stream_client.head(full_stream_url, headers=req_headers)
                    resp_headers = {
                        "Accept-Ranges": "bytes",
                        "Content-Type": upstream_resp.headers.get("content-type", f"audio/{container}"),
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                        "Access-Control-Allow-Headers": "*",
                        "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length, Content-Type",
                    }
                    if "content-length" in upstream_resp.headers:
                        resp_headers["Content-Length"] = upstream_resp.headers["content-length"]
                    if "content-range" in upstream_resp.headers:
                        resp_headers["Content-Range"] = upstream_resp.headers["content-range"]
                    await upstream_resp.aclose()
                    await stream_client.aclose()
                    return Response(status_code=upstream_resp.status_code, headers=resp_headers)

                upstream_req = stream_client.build_request("GET", full_stream_url, headers=req_headers)
                upstream_resp = await stream_client.send(upstream_req, stream=True)

                if upstream_resp.status_code >= 400:
                    print(f"[HostPlexStream] Upstream Plex returned {upstream_resp.status_code} for {full_stream_url}")
                    await upstream_resp.aclose()
                    await stream_client.aclose()
                    raise HTTPException(status_code=upstream_resp.status_code, detail="Plex Stream nicht erreichbar")

                resp_headers = {
                    "Accept-Ranges": "bytes",
                    "Content-Type": upstream_resp.headers.get("content-type", f"audio/{container}"),
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length, Content-Type",
                }
                if "content-length" in upstream_resp.headers:
                    resp_headers["Content-Length"] = upstream_resp.headers["content-length"]
                if "content-range" in upstream_resp.headers:
                    resp_headers["Content-Range"] = upstream_resp.headers["content-range"]

                async def host_plex_stream_generator():
                    try:
                        async for chunk in upstream_resp.aiter_bytes(chunk_size=65536):
                            yield chunk
                    finally:
                        await upstream_resp.aclose()
                        await stream_client.aclose()

                return StreamingResponse(host_plex_stream_generator(), status_code=upstream_resp.status_code, headers=resp_headers)
            except Exception as e:
                print(f"[HostPlexStream] Error streaming Plex audio: {e}")

    raise HTTPException(status_code=404, detail="Audiodatei nicht gefunden.")

@app.options("/api/audio/stream")
@app.options("/api/audio/stream/{track_id}")
@app.options("/api/stream")
@app.options("/api/stream/{track_id}")
@app.options("/api/plex/stream/{key}")
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

# --- Cover Art & Image Proxies ---
_cover_cache: Dict[str, Tuple[bytes, str]] = {}
_host_artist_map_cache: Optional[Dict[str, str]] = None

@app.get("/api/track/cover")
@app.get("/api/cover")
@app.get("/api/cover/{track_id}")
@app.head("/api/track/cover")
@app.head("/api/cover")
@app.head("/api/cover/{track_id}")
async def get_cover_art(
    track_id: Optional[str] = None,
    path: Optional[str] = Query(None),
    id: Optional[str] = Query(None),
    key: Optional[str] = Query(None),
    thumb: Optional[str] = Query(None)
):
    query_id = track_id or id or key
    clean_id = urllib.parse.unquote(str(query_id)).replace("host://", "").strip() if query_id else ""
    target_path = None
    track = None

    if clean_id:
        track = library_cache.get_track(clean_id)
        if track and track.get("file_path") and os.path.exists(str(track.get("file_path"))):
            target_path = track.get("file_path")

    if not target_path and path:
        clean_p = urllib.parse.unquote(str(path)).replace("host://", "").strip()
        if os.path.exists(clean_p):
            target_path = clean_p
        else:
            track = track or library_cache.get_track(clean_p)
            if track and track.get("file_path") and os.path.exists(str(track.get("file_path"))):
                target_path = track.get("file_path")

    # 1. Local audio file exists on host: extract embedded or folder cover
    if target_path and os.path.exists(target_path):
        if target_path in _cover_cache:
            data, mime = _cover_cache[target_path]
            return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

        p = Path(target_path)
        cover_data = metadata_engine.extract_embedded_cover_bytes(p)
        if cover_data:
            data, mime = cover_data
            _cover_cache[target_path] = (data, mime)
            return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

        local_img = metadata_engine.find_local_cover_file(p)
        if local_img and local_img.exists():
            mime = "image/png" if local_img.suffix.lower() == ".png" else "image/jpeg"
            data = local_img.read_bytes()
            _cover_cache[target_path] = (data, mime)
            return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

    # 2. Plex cover lookup
    clean_key = ""
    if clean_id.startswith("plex_"):
        clean_key = clean_id.replace("plex_", "")
    elif path and "plex://" in str(path):
        clean_key = str(path).replace("plex://", "")
    elif clean_id.isdigit():
        clean_key = clean_id
    elif track and track.get("plex_key"):
        clean_key = str(track.get("plex_key"))

    cache_key = f"plex_track_cover_{clean_key or clean_id}"
    if cache_key in _cover_cache:
        data, mime = _cover_cache[cache_key]
        return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

    covers_dir = DATA_DIR / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    disk_file = covers_dir / f"{clean_key or clean_id}.jpg"
    if disk_file.exists():
        try:
            data = disk_file.read_bytes()
            if len(data) > 200:
                _cover_cache[cache_key] = (data, "image/jpeg")
                return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})
        except Exception:
            pass

    thumb_paths_to_try = []
    if thumb:
        thumb_paths_to_try.append(thumb)
    if track:
        if track.get("thumb"):
            thumb_paths_to_try.append(track["thumb"])
        if track.get("parent_thumb"):
            thumb_paths_to_try.append(track["parent_thumb"])
        if track.get("grandparent_thumb"):
            thumb_paths_to_try.append(track["grandparent_thumb"])
        if track.get("plex_cover_url") and str(track["plex_cover_url"]).startswith("http"):
            thumb_paths_to_try.append(track["plex_cover_url"])

    config = load_host_config()
    if config.plex_url and config.plex_token:
        try:
            client = HostPlexClient(config.plex_url, config.plex_token)
            base_url = rewrite_plex_direct_url(await client._get_working_base_url()).rstrip("/")
            if base_url:
                async with httpx.AsyncClient(timeout=15.0, verify=False, follow_redirects=True) as http_client:
                    # Try direct thumb paths
                    for tp in thumb_paths_to_try:
                        if not tp:
                            continue
                        sep = "" if str(tp).startswith("/") else "/"
                        if str(tp).startswith("http"):
                            final_cov = tp if "X-Plex-Token=" in tp else f"{tp}{'&' if '?' in tp else '?'}X-Plex-Token={config.plex_token}"
                        else:
                            tok_sep = "&" if "?" in str(tp) else "?"
                            final_cov = f"{base_url}{sep}{tp}{tok_sep}X-Plex-Token={config.plex_token}"
                        try:
                            r = await http_client.get(final_cov, headers={"X-Plex-Token": config.plex_token, "Accept": "image/*,*/*"})
                            if r.status_code == 200 and r.content and len(r.content) > 200:
                                c_type = r.headers.get("Content-Type", "image/jpeg")
                                _cover_cache[cache_key] = (r.content, c_type)
                                try:
                                    disk_file.write_bytes(r.content)
                                except Exception:
                                    pass
                                return Response(content=r.content, media_type=c_type, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})
                        except Exception:
                            continue

                    # Fallback: Query metadata from Plex by ratingKey
                    if clean_key:
                        try:
                            meta_res = await http_client.get(
                                f"{base_url}/library/metadata/{clean_key}?X-Plex-Token={config.plex_token}",
                                headers=client._get_api_headers()
                            )
                            if meta_res.status_code == 200:
                                meta = meta_res.json().get("MediaContainer", {}).get("Metadata", [{}])[0]
                                candidate_thumbs = [
                                    meta.get("thumb"),
                                    meta.get("parentThumb"),
                                    meta.get("grandparentThumb")
                                ]
                                parent_key = meta.get("parentRatingKey") or (track.get("parent_key") if track else None)
                                if parent_key:
                                    try:
                                        p_res = await http_client.get(
                                            f"{base_url}/library/metadata/{parent_key}?X-Plex-Token={config.plex_token}",
                                            headers=client._get_api_headers()
                                        )
                                        if p_res.status_code == 200:
                                            p_meta = p_res.json().get("MediaContainer", {}).get("Metadata", [{}])[0]
                                            candidate_thumbs.extend([p_meta.get("thumb"), p_meta.get("parentThumb")])
                                    except Exception:
                                        pass

                                for cand in candidate_thumbs:
                                    if cand:
                                        sep = "" if str(cand).startswith("/") else "/"
                                        tok_sep = "&" if "?" in str(cand) else "?"
                                        c_url = f"{base_url}{sep}{cand}{tok_sep}X-Plex-Token={config.plex_token}"
                                        r = await http_client.get(c_url, headers={"X-Plex-Token": config.plex_token, "Accept": "image/*,*/*"})
                                        if r.status_code == 200 and r.content and len(r.content) > 200:
                                            c_type = r.headers.get("Content-Type", "image/jpeg")
                                            _cover_cache[cache_key] = (r.content, c_type)
                                            try:
                                                disk_file.write_bytes(r.content)
                                            except Exception:
                                                pass
                                            return Response(content=r.content, media_type=c_type, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})
                        except Exception as e:
                            print(f"[HostCover] Metadata fallback error: {e}")
        except Exception as e:
            print(f"[HostCover] Plex fetch error: {e}")

    # Clean SVG fallback placeholder (no-cache so browser fetches newly synced artwork)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#1e1b4b"/><stop offset="100%" stop-color="#0f172a"/></linearGradient></defs><rect width="100" height="100" fill="url(#g)" rx="12"/><path d="M40 30v30a10 10 0 1 1-6-9.2V30l26-6v26a10 10 0 1 1-6-9.2V24l-14 3z" fill="#8b5cf6" opacity="0.8"/></svg>'
    return Response(content=svg.encode("utf-8"), media_type="image/svg+xml", headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Access-Control-Allow-Origin": "*"})

@app.get("/api/plex/cover/{key}")
@app.head("/api/plex/cover/{key}")
async def get_plex_cover_proxy(key: str, thumb: Optional[str] = None):
    """Proxies Plex track or album cover by rating key."""
    clean_key = str(key).replace("plex_", "").strip()
    return await get_cover_art(track_id=f"plex_{clean_key}", thumb=thumb)

@app.get("/api/playlist/cover")
@app.head("/api/playlist/cover")
async def get_host_playlist_cover(
    key: Optional[str] = None,
    id: Optional[str] = None,
    composite: Optional[str] = None,
    thumb: Optional[str] = None
):
    """Returns cover art for a playlist on the Host."""
    raw_key = (key if isinstance(key, str) else "") or (id if isinstance(id, str) else "")
    rating_key = str(raw_key).replace("plex_", "").replace("plex://", "").strip()
    target_path = (thumb or composite or "").strip()

    cache_key = f"pl_cover_{rating_key}_{target_path}"
    if cache_key in _cover_cache:
        data, mime = _cover_cache[cache_key]
        return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

    covers_dir = DATA_DIR / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    disk_file = covers_dir / f"pl_{rating_key}.jpg"
    if disk_file.exists():
        try:
            data = disk_file.read_bytes()
            if len(data) > 200:
                _cover_cache[cache_key] = (data, "image/jpeg")
                return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})
        except Exception:
            pass

    config = load_host_config()
    if config.plex_url and config.plex_token:
        try:
            client = HostPlexClient(config.plex_url, config.plex_token)
            base = await client._get_working_base_url()
            if base:
                async with httpx.AsyncClient(timeout=15.0, verify=False, follow_redirects=True) as http_client:
                    # 1. Fetch composite or thumb directly if provided
                    if target_path:
                        sep = "" if target_path.startswith("/") else "/"
                        url_to_fetch = target_path if target_path.startswith("http") else f"{base}{sep}{target_path}"
                        token_sep = "&" if "?" in url_to_fetch else "?"
                        final_url = url_to_fetch if "X-Plex-Token=" in url_to_fetch else f"{url_to_fetch}{token_sep}X-Plex-Token={config.plex_token}"
                        r = await http_client.get(final_url, headers={"X-Plex-Token": config.plex_token, "Accept": "image/*,*/*"})
                        if r.status_code == 200 and r.content and len(r.content) > 200:
                            mime = r.headers.get("Content-Type", "image/jpeg")
                            _cover_cache[cache_key] = (r.content, mime)
                            try:
                                disk_file.write_bytes(r.content)
                            except Exception:
                                pass
                            return Response(content=r.content, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

                    # 2. Query first track of this playlist from Plex to use its artwork
                    if rating_key:
                        items_res = await http_client.get(
                            f"{base}/playlists/{rating_key}/items?X-Plex-Container-Start=0&X-Plex-Container-Size=1&X-Plex-Token={config.plex_token}",
                            headers=client._get_api_headers()
                        )
                        if items_res.status_code == 200:
                            items = items_res.json().get("MediaContainer", {}).get("Metadata", [])
                            if items:
                                first_thumb = items[0].get("thumb") or items[0].get("parentThumb") or items[0].get("grandparentThumb")
                                if first_thumb:
                                    sep = "" if first_thumb.startswith("/") else "/"
                                    r = await http_client.get(f"{base}{sep}{first_thumb}?X-Plex-Token={config.plex_token}", headers={"X-Plex-Token": config.plex_token, "Accept": "image/*,*/*"})
                                    if r.status_code == 200 and r.content and len(r.content) > 200:
                                        mime = r.headers.get("Content-Type", "image/jpeg")
                                        _cover_cache[cache_key] = (r.content, mime)
                                        try:
                                            disk_file.write_bytes(r.content)
                                        except Exception:
                                            pass
                                        return Response(content=r.content, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})
        except Exception as e:
            print(f"[HostPlaylistCover] Error: {e}")

    # 3. Fallback to first track of playlist from host cache if available
    pls = load_host_playlists()
    pl = next((p for p in pls if str(p.get("id")) == str(raw_key) or str(p.get("id")) == f"plex_{rating_key}" or str(p.get("plex_key")) == rating_key), None)
    if pl and pl.get("track_ids"):
        first_tid = pl["track_ids"][0]
        return await get_cover_art(id=str(first_tid))

    # 4. Vibrant SVG gradient placeholder (no-cache)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#8b5cf6"/><stop offset="100%" stop-color="#ec4899"/></linearGradient></defs><rect width="100" height="100" fill="url(#g)" rx="14"/><text x="50" y="58" font-family="system-ui, -apple-system, sans-serif" font-size="44" font-weight="900" fill="white" text-anchor="middle" dominant-baseline="middle">♪</text></svg>'
    return Response(content=svg.encode("utf-8"), media_type="image/svg+xml", headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Access-Control-Allow-Origin": "*"})

@app.get("/api/artist/image")
@app.head("/api/artist/image")
async def get_host_artist_image(name: str = Query(...)):
    """Returns artist avatar image from Plex, library tracks, or cache."""
    clean_name = name.strip().lower()
    if not clean_name:
        raise HTTPException(status_code=404, detail="Kein Künstlername angegeben.")

    cache_key = f"artist_img_{clean_name}"
    if cache_key in _cover_cache:
        data, mime = _cover_cache[cache_key]
        return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})

    config = load_host_config()
    if config.plex_url and config.plex_token:
        global _host_artist_map_cache
        try:
            client = HostPlexClient(config.plex_url, config.plex_token)
            if _host_artist_map_cache is None:
                _host_artist_map_cache = await client.get_artist_thumbs()

            thumb_path = _host_artist_map_cache.get(clean_name)
            if not thumb_path:
                for k, v in _host_artist_map_cache.items():
                    if clean_name in k or k in clean_name:
                        thumb_path = v
                        break

            if thumb_path:
                base = await client._get_working_base_url()
                if base:
                    async with httpx.AsyncClient(timeout=12.0, verify=False, follow_redirects=True) as http_client:
                        sep = "" if str(thumb_path).startswith("/") else "/"
                        r = await http_client.get(f"{base}{sep}{thumb_path}?X-Plex-Token={config.plex_token}", headers={"X-Plex-Token": config.plex_token, "Accept": "image/*,*/*"})
                        if r.status_code == 200 and r.content and len(r.content) > 200:
                            mime = r.headers.get("Content-Type", "image/jpeg")
                            _cover_cache[cache_key] = (r.content, mime)
                            return Response(content=r.content, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})
        except Exception as e:
            print(f"[HostArtistImage] Plex error: {e}")

    # Fallback to cover of any track by this artist
    for t in library_cache.tracks:
        if t.get("artist") and clean_name in str(t.get("artist", "")).lower():
            try:
                cov_resp = await get_cover_art(id=t.get("id"), path=t.get("file_path"))
                if cov_resp and getattr(cov_resp, "body", None):
                    _cover_cache[cache_key] = (cov_resp.body, getattr(cov_resp, "media_type", "image/jpeg"))
                    return cov_resp
            except Exception:
                pass

    raise HTTPException(status_code=404, detail="Kein Künstlerbild gefunden.")

@app.get("/api/plex/stream/{key}")
@app.head("/api/plex/stream/{key}")
async def stream_plex_proxy_key(key: str, request: Request):
    """Streams a Plex track by rating key through the Host."""
    clean_key = str(key).replace("plex_", "").strip()
    return await stream_audio(track_id=f"plex_{clean_key}", request=request)

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
    raw_url = (url or config.plex_url or "").strip()
    target_token = (token or config.plex_token or "").strip()
    if not raw_url or not target_token:
        return {
            "success": False,
            "configured": False,
            "reachable": False,
            "error": "Plex Server ist auf dem Host noch nicht konfiguriert."
        }
    
    # Auto-sanitize any .plex.direct domain stored previously in config
    target_url = rewrite_plex_direct_url(raw_url).rstrip("/")
    if not url and target_url != config.plex_url:
        config.plex_url = target_url
        save_host_config(config)

    client = HostPlexClient(target_url, target_token)
    res = await client.test_connection()
    is_ok = bool(res.get("success"))
    effective = res.get("effective_url", target_url)

    # Persist working effective URL if discovered
    if is_ok and effective and not url and effective != config.plex_url:
        config.plex_url = effective
        save_host_config(config)

    return {
        "success": is_ok,
        "configured": True,
        "reachable": is_ok,
        "server_name": res.get("name") or "Plex Media Server",
        "version": res.get("version", ""),
        "effective_url": effective,
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
            best_uri = rewrite_plex_direct_url(best_server.get("uri", "")).rstrip("/")
            config.plex_url = best_uri
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
    matched_plex_keys = set()
    matched_plex_files = set()

    if tracks:
        plex_by_title_artist = {}
        plex_by_path = {}
        for pt in tracks:
            t_norm = _clean_track_text(pt.get("title") or "")
            a_norm = _clean_track_text(pt.get("artist") or "")
            if t_norm:
                plex_by_title_artist[(t_norm, a_norm)] = pt
                plex_by_title_artist[(t_norm, "")] = pt
            s_path = pt.get("server_file_path") or ""
            if s_path:
                plex_by_path[Path(s_path).name.lower()] = pt

        for lt in library_cache.tracks:
            lt_title = _clean_track_text(lt.get("title") or "")
            lt_artist = _clean_track_text(lt.get("artist") or "")
            lt_file = Path(lt.get("file_path", "")).name.lower()
            
            match = plex_by_title_artist.get((lt_title, lt_artist)) or plex_by_title_artist.get((lt_title, "")) or plex_by_path.get(lt_file)
            if match:
                pkey = str(match.get("plex_key") or match.get("id") or "")
                lt["plex_key"] = match.get("plex_key")
                if pkey:
                    matched_plex_keys.add(pkey)
                if match.get("server_file_path"):
                    matched_plex_files.add(Path(match["server_file_path"]).name.lower())
                if match.get("stream_url"):
                    lt["stream_url"] = match.get("stream_url")
                if match.get("cover_url"):
                    lt["plex_cover_url"] = match.get("cover_url")
                    lt["cover_url"] = match.get("cover_url")
                    lt["has_cover"] = True
                if config.prefer_plex_metadata:
                    lt["title"] = match.get("title") or lt.get("title")
                    lt["artist"] = match.get("artist") or lt.get("artist")
                    lt["album"] = match.get("album") or lt.get("album")
                    if match.get("genre"):
                        lt["genre"] = match.get("genre")
                    if match.get("year"):
                        lt["year"] = match.get("year")
                    if match.get("track_number"):
                        lt["track_number"] = match.get("track_number")
                updated_count += 1

    # Cleanly store and deduplicate Plex tracks in Host library cache
    if tracks:
        # Keep local folder tracks for LRCCreator / MediaManager
        local_tracks = [t for t in library_cache.tracks if t.get("source") != "plex" and not str(t.get("file_path", "")).startswith("plex://")]
        
        seen_pids = set()
        unique_plex = []
        for pt in tracks:
            pkey = str(pt.get("plex_key") or pt.get("id") or "").strip()
            p_name = Path(pt.get("server_file_path", "")).name.lower() if pt.get("server_file_path") else ""
            if pkey in matched_plex_keys or (p_name and p_name in matched_plex_files):
                continue
            pid = str(pt.get("id") or f"plex_{pt.get('plex_key')}").strip()
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            unique_plex.append(pt)

        library_cache.tracks = local_tracks + unique_plex
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
    try:
        client = HostPlexClient(config.plex_url, config.plex_token)
        pls = await client.get_playlists()
        if pls:
            save_host_playlists(pls)
            return pls
    except Exception as e:
        print(f"[HostPlexPlaylists] Error: {e}")
    return load_host_playlists()

@app.get("/api/plex/playlists/{key}/tracks")
async def get_plex_playlist_tracks_endpoint(key: str):
    config = load_host_config()
    client = HostPlexClient(config.plex_url, config.plex_token)
    tracks = await client.get_playlist_tracks(key)
    if tracks:
        known_ids = {t.get("id") for t in library_cache.tracks if t.get("id")}
        to_add = [t for t in tracks if t.get("id") not in known_ids]
        if to_add:
            library_cache.tracks.extend(to_add)
            library_cache.save()
            library_cache._rebuild_map()
    return tracks

@app.post("/api/plex/playlists/sync-all")
@app.post("/api/plex/playlists/sync")
async def sync_all_plex_playlists_endpoint():
    config = load_host_config()
    if not config.plex_url or not config.plex_token:
        return {
            "success": False,
            "count": 0,
            "playlists": load_host_playlists(),
            "message": "Plex Server ist auf dem Host nicht konfiguriert."
        }

    client = HostPlexClient(config.plex_url, config.plex_token)
    plex_pls = await client.get_playlists()
    synced_pls = []
    new_tracks_all = []
    known_ids = {t.get("id") for t in library_cache.tracks if t.get("id")}

    for pl in plex_pls:
        pl_key = pl.get("plex_key") or pl.get("id")
        if pl_key:
            pl_tracks = await client.get_playlist_tracks(str(pl_key))
            track_ids = []
            for pt in pl_tracks:
                tid = pt.get("id") or f"plex_{pt.get('plex_key')}"
                track_ids.append(tid)
                if tid not in known_ids:
                    known_ids.add(tid)
                    new_tracks_all.append(pt)

            synced_pls.append({
                "id": pl.get("id") or f"plex_{pl_key}",
                "name": pl.get("name"),
                "source": "plex",
                "track_count": len(track_ids),
                "track_ids": track_ids,
                "cover_url": pl.get("cover_url")
            })

    if new_tracks_all:
        library_cache.tracks.extend(new_tracks_all)
        library_cache.save()
        library_cache._rebuild_map()

    save_host_playlists(synced_pls)
    return {
        "success": True,
        "count": len(synced_pls),
        "playlists": synced_pls,
        "new_tracks": new_tracks_all,
        "message": f"{len(synced_pls)} Plex Playlists synchronisiert."
    }

# --- Universal Host Playlists API ---
@app.get("/api/playlists")
async def get_all_host_playlists():
    current = load_host_playlists()
    config = load_host_config()
    if config.plex_url and config.plex_token:
        try:
            client = HostPlexClient(config.plex_url, config.plex_token)
            pls = await client.get_playlists()
            if pls:
                known_ids = {p.get("id") for p in current}
                for pl in pls:
                    if pl.get("id") not in known_ids:
                        current.append(pl)
                save_host_playlists(current)
        except Exception as e:
            print(f"[HostPlaylists] Auto-fetch error: {e}")
    return current

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
