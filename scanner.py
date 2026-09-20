import os
import json
import time
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, AsyncGenerator
try:
    from .config import load_host_config, HostConfig, CACHE_FILE, DATA_DIR
    from .metadata import metadata_engine
except (ImportError, ValueError):
    from config import load_host_config, HostConfig, CACHE_FILE, DATA_DIR
    from metadata import metadata_engine

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".alac", ".aiff", ".wma"}

class HostLibraryCache:
    def __init__(self):
        self.music_directory: str = ""
        self.last_scanned: float = 0
        self.tracks: List[Dict[str, Any]] = []
        self._track_map: Dict[str, Dict[str, Any]] = {}
        self.is_scanning: bool = False
        self.load()

    def load(self):
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.music_directory = data.get("music_directory", "")
                    self.last_scanned = data.get("last_scanned", 0)
                    self.tracks = data.get("tracks", [])
                    self._rebuild_map()
            except Exception as e:
                print(f"[HostScanner] Cache load error: {e}")

    def save(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "music_directory": self.music_directory,
                    "last_scanned": self.last_scanned,
                    "tracks": self.tracks
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[HostScanner] Cache save error: {e}")

    def _rebuild_map(self):
        self._track_map = {}
        for t in self.tracks:
            tid = t.get("id")
            if tid:
                self._track_map[tid] = t
            fpath = t.get("file_path")
            if fpath:
                self._track_map[fpath] = t

    def get_track(self, track_id_or_path: str) -> Optional[Dict[str, Any]]:
        return self._track_map.get(track_id_or_path)

    def get_stats(self) -> Dict[str, Any]:
        total = len(self.tracks)
        synced_count = sum(1 for t in self.tracks if t.get("is_synced"))
        plain_count = sum(1 for t in self.tracks if t.get("has_lyrics") and not t.get("is_synced"))
        cover_count = sum(1 for t in self.tracks if t.get("has_cover"))
        
        artists = set(t.get("artist", "").strip() for t in self.tracks if t.get("artist"))
        albums = set(t.get("album", "").strip() for t in self.tracks if t.get("album"))
        
        total_duration = sum(t.get("duration", 0) for t in self.tracks)

        return {
            "total_tracks": total,
            "synced_lyrics": synced_count,
            "plain_lyrics": plain_count,
            "missing_lyrics": total - (synced_count + plain_count),
            "covers_count": cover_count,
            "total_artists": len(artists),
            "total_albums": len(albums),
            "total_duration": total_duration,
            "total_duration_str": f"{int(total_duration // 3600)}h {int((total_duration % 3600) // 60)}m",
            "last_scanned": self.last_scanned
        }

library_cache = HostLibraryCache()

async def scan_directory_streaming(custom_dir: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
    """Scans the music directory recursively with real-time SSE progress updates."""
    config = load_host_config()
    target_dir = custom_dir or config.music_directory

    if not target_dir or not os.path.exists(target_dir):
        yield {
            "phase": "error",
            "message": f"Verzeichnis existiert nicht: {target_dir}",
            "current_index": 0,
            "total": 0
        }
        return

    library_cache.is_scanning = True
    yield {
        "phase": "discovery",
        "message": "Suche nach Musikdateien...",
        "current_index": 0,
        "total": 0
    }

    dir_path = Path(target_dir)
    audio_files: List[Path] = []

    for root, _, files in os.walk(dir_path):
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in SUPPORTED_EXTENSIONS and not p.name.startswith("._"):
                audio_files.append(p)

    total_files = len(audio_files)
    if total_files == 0:
        library_cache.tracks = []
        library_cache.last_scanned = time.time()
        library_cache.music_directory = str(dir_path.resolve())
        library_cache.save()
        library_cache._rebuild_map()
        library_cache.is_scanning = False
        yield {
            "phase": "complete",
            "message": "Keine Musikdateien gefunden.",
            "current_index": 0,
            "total": 0,
            "tracks": []
        }
        return

    yield {
        "phase": "scanning",
        "message": f"{total_files} Musikdateien gefunden. Lese Metadaten...",
        "current_index": 0,
        "total": total_files
    }

    scanned_tracks: List[Dict[str, Any]] = []
    
    # Process files with progress
    for idx, fpath in enumerate(audio_files):
        try:
            track = await metadata_engine.resolve_track_metadata(fpath, config)
            scanned_tracks.append(track)
        except Exception as e:
            print(f"[HostScanner] Error scanning {fpath}: {e}")

        if (idx + 1) % 5 == 0 or (idx + 1) == total_files:
            yield {
                "phase": "reading",
                "message": f"Analysiere: {fpath.name}",
                "current_index": idx + 1,
                "total": total_files,
                "current_track": fpath.name
            }
            await asyncio.sleep(0.001)

    # Sort tracks by Artist, Album, Track Number, Title
    scanned_tracks.sort(key=lambda t: (
        (t.get("artist") or "").lower(),
        (t.get("album") or "").lower(),
        int(t.get("track_number") or 0) if str(t.get("track_number") or "").isdigit() else 0,
        (t.get("title") or "").lower()
    ))

    existing_plex = [t for t in library_cache.tracks if t.get("source") == "plex"]
    library_cache.tracks = scanned_tracks + existing_plex
    library_cache.music_directory = str(dir_path.resolve())
    library_cache.last_scanned = time.time()
    library_cache.save()
    library_cache._rebuild_map()
    library_cache.is_scanning = False

    yield {
        "phase": "complete",
        "message": f"Scan abgeschlossen! {len(scanned_tracks)} Titel indiziert.",
        "current_index": total_files,
        "total": total_files,
        "tracks_count": len(scanned_tracks),
        "stats": library_cache.get_stats()
    }
