import os
import re
import asyncio
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
import mutagen
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.wave import WAVE
import httpx

try:
    from .config import HostConfig
except (ImportError, ValueError):
    from config import HostConfig

COVER_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"]
COVER_FILENAMES = ["cover", "folder", "front", "album", "artwork", "default"]

class MultiSourceMetadataEngine:
    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None

    def get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=10.0,
                headers={"User-Agent": "Tonarr-Host/1.0 (https://github.com/Tonarr-Audio/Host)"}
            )
        return self._http_client

    # 1. EXTRACT EMBEDDED TAGS & EMBEDDED LYRICS/COVER
    def extract_embedded_info(self, file_path: Path) -> Dict[str, Any]:
        """Extracts metadata, embedded cover and embedded lyrics directly from audio tags."""
        result = {
            "title": file_path.stem,
            "artist": "Unbekannter Interpret",
            "album": "Unbekanntes Album",
            "album_artist": "",
            "genre": "",
            "year": "",
            "track_number": "",
            "duration": 0.0,
            "bitrate": 0,
            "has_embedded_lyrics": False,
            "embedded_lyrics": "",
            "is_synced_embedded": False,
            "has_embedded_cover": False
        }

        try:
            audio = mutagen.File(str(file_path))
            if audio is None:
                return result

            # Duration & Bitrate
            if hasattr(audio, "info") and audio.info:
                result["duration"] = round(getattr(audio.info, "length", 0.0), 2)
                if hasattr(audio.info, "bitrate") and audio.info.bitrate:
                    result["bitrate"] = int(audio.info.bitrate // 1000)

            # MP3 / ID3
            if isinstance(audio, MP3) or (hasattr(audio, "tags") and isinstance(audio.tags, ID3)):
                tags = audio.tags or ID3()
                if "TIT2" in tags: result["title"] = str(tags["TIT2"].text[0]).strip()
                if "TPE1" in tags: result["artist"] = str(tags["TPE1"].text[0]).strip()
                if "TALB" in tags: result["album"] = str(tags["TALB"].text[0]).strip()
                if "TPE2" in tags: result["album_artist"] = str(tags["TPE2"].text[0]).strip()
                if "TCON" in tags: result["genre"] = str(tags["TCON"].text[0]).strip()
                if "TDRC" in tags: result["year"] = str(tags["TDRC"].text[0]).strip()
                elif "TYER" in tags: result["year"] = str(tags["TYER"].text[0]).strip()
                if "TRCK" in tags: result["track_number"] = str(tags["TRCK"].text[0]).split("/")[0].strip()

                # Check for lyrics
                for k, v in tags.items():
                    k_str = str(k).upper()
                    if k_str.startswith("USLT") or k_str.startswith("SYLT"):
                        text = getattr(v, "text", "")
                        if text and str(text).strip():
                            result["has_embedded_lyrics"] = True
                            result["embedded_lyrics"] = str(text).strip()
                            result["is_synced_embedded"] = k_str.startswith("SYLT") or bool(re.search(r"\[\d{2}:\d{2}", str(text)))
                            break

                # Check for embedded cover (APIC)
                for k in tags.keys():
                    if str(k).startswith("APIC"):
                        result["has_embedded_cover"] = True
                        break

            # FLAC
            elif isinstance(audio, FLAC):
                if "title" in audio: result["title"] = str(audio["title"][0]).strip()
                if "artist" in audio: result["artist"] = str(audio["artist"][0]).strip()
                if "album" in audio: result["album"] = str(audio["album"][0]).strip()
                if "albumartist" in audio: result["album_artist"] = str(audio["albumartist"][0]).strip()
                if "genre" in audio: result["genre"] = str(audio["genre"][0]).strip()
                if "date" in audio: result["year"] = str(audio["date"][0]).strip()
                if "tracknumber" in audio: result["track_number"] = str(audio["tracknumber"][0]).split("/")[0].strip()
                if "lyrics" in audio or "unsyncedlyrics" in audio:
                    lyr = audio.get("lyrics") or audio.get("unsyncedlyrics")
                    if lyr and str(lyr[0]).strip():
                        result["has_embedded_lyrics"] = True
                        result["embedded_lyrics"] = str(lyr[0]).strip()
                        result["is_synced_embedded"] = bool(re.search(r"\[\d{2}:\d{2}", result["embedded_lyrics"]))
                if hasattr(audio, "pictures") and len(audio.pictures) > 0:
                    result["has_embedded_cover"] = True

            # MP4 / M4A
            elif isinstance(audio, MP4):
                tags = audio.tags or {}
                if "\xa9nam" in tags: result["title"] = str(tags["\xa9nam"][0]).strip()
                if "\xa9ART" in tags: result["artist"] = str(tags["\xa9ART"][0]).strip()
                if "\xa9alb" in tags: result["album"] = str(tags["\xa9alb"][0]).strip()
                if "aART" in tags: result["album_artist"] = str(tags["aART"][0]).strip()
                if "\xa9gen" in tags: result["genre"] = str(tags["\xa9gen"][0]).strip()
                if "\xa9day" in tags: result["year"] = str(tags["\xa9day"][0]).strip()
                if "trkn" in tags:
                    tr = tags["trkn"][0]
                    if isinstance(tr, tuple): result["track_number"] = str(tr[0])
                if "\xa9lyr" in tags and str(tags["\xa9lyr"][0]).strip():
                    result["has_embedded_lyrics"] = True
                    result["embedded_lyrics"] = str(tags["\xa9lyr"][0]).strip()
                    result["is_synced_embedded"] = bool(re.search(r"\[\d{2}:\d{2}", result["embedded_lyrics"]))
                if "covr" in tags and len(tags["covr"]) > 0:
                    result["has_embedded_cover"] = True

            # OGG / OPUS
            elif isinstance(audio, (OggVorbis, OggOpus)):
                if "title" in audio: result["title"] = str(audio["title"][0]).strip()
                if "artist" in audio: result["artist"] = str(audio["artist"][0]).strip()
                if "album" in audio: result["album"] = str(audio["album"][0]).strip()
                if "albumartist" in audio: result["album_artist"] = str(audio["albumartist"][0]).strip()
                if "genre" in audio: result["genre"] = str(audio["genre"][0]).strip()
                if "date" in audio: result["year"] = str(audio["date"][0]).strip()
                if "tracknumber" in audio: result["track_number"] = str(audio["tracknumber"][0]).split("/")[0].strip()
                if "lyrics" in audio:
                    result["has_embedded_lyrics"] = True
                    result["embedded_lyrics"] = str(audio["lyrics"][0]).strip()
                    result["is_synced_embedded"] = bool(re.search(r"\[\d{2}:\d{2}", result["embedded_lyrics"]))

        except Exception as e:
            pass

        # Cleanup defaults
        if not result["title"] or result["title"] == file_path.stem:
            # Try splitting "Artist - Title" from filename
            stem = file_path.stem
            if " - " in stem:
                parts = stem.split(" - ", 1)
                if result["artist"] in ("Unbekannter Interpret", "Unknown"):
                    result["artist"] = parts[0].strip()
                result["title"] = parts[1].strip()

        return result

    # 2. LOCAL FILE SCAN (LRC, TXT, FOLDER COVERS)
    def find_local_lyrics_file(self, file_path: Path, custom_lrc_dir: Optional[str] = None) -> Tuple[Optional[Path], bool]:
        """Returns (lrc_path, is_synced) for local matching .lrc or .txt files."""
        # Check next to audio file
        stem = file_path.stem
        parent = file_path.parent
        
        # 1. Exact match .lrc
        exact_lrc = parent / f"{stem}.lrc"
        if exact_lrc.exists() and exact_lrc.is_file():
            return exact_lrc, True

        # 2. Centralized custom lrc dir if configured
        if custom_lrc_dir and os.path.exists(custom_lrc_dir):
            c_lrc = Path(custom_lrc_dir) / f"{stem}.lrc"
            if c_lrc.exists() and c_lrc.is_file():
                return c_lrc, True

        # 3. Exact match .txt (plain lyrics)
        exact_txt = parent / f"{stem}.txt"
        if exact_txt.exists() and exact_txt.is_file():
            return exact_txt, False

        return None, False

    def find_local_cover_file(self, file_path: Path) -> Optional[Path]:
        """Finds cover.jpg, folder.jpg, or [song].jpg in the folder."""
        parent = file_path.parent
        stem = file_path.stem

        # 1. Song specific image e.g. Song.jpg
        for ext in COVER_EXTENSIONS:
            p = parent / f"{stem}{ext}"
            if p.exists() and p.is_file():
                return p

        # 2. Standard album covers in directory
        for name in COVER_FILENAMES:
            for ext in COVER_EXTENSIONS:
                p = parent / f"{name}{ext}"
                if p.exists() and p.is_file():
                    return p
                # Also check uppercase
                p_upper = parent / f"{name.capitalize()}{ext}"
                if p_upper.exists() and p_upper.is_file():
                    return p_upper

        return None

    def extract_embedded_cover_bytes(self, file_path: Path) -> Optional[Tuple[bytes, str]]:
        """Extracts raw cover art bytes and mime type from an audio file."""
        try:
            audio = mutagen.File(str(file_path))
            if audio is None:
                return None

            # ID3 (MP3)
            if hasattr(audio, "tags") and isinstance(audio.tags, ID3):
                for k, v in audio.tags.items():
                    if str(k).startswith("APIC"):
                        return v.data, getattr(v, "mime", "image/jpeg")

            # FLAC
            if isinstance(audio, FLAC) and hasattr(audio, "pictures") and len(audio.pictures) > 0:
                pic = audio.pictures[0]
                return pic.data, getattr(pic, "mime", "image/jpeg")

            # MP4 / M4A
            if isinstance(audio, MP4) and hasattr(audio, "tags") and audio.tags:
                covr = audio.tags.get("covr")
                if covr and len(covr) > 0:
                    data = bytes(covr[0])
                    fmt = getattr(covr[0], "imageformat", MP4Cover.FORMAT_JPEG)
                    mime = "image/png" if fmt == MP4Cover.FORMAT_PNG else "image/jpeg"
                    return data, mime

        except Exception:
            pass
        return None

    # 3. ONLINE PROVIDER: LRCLIB (Lyrics)
    async def fetch_lrclib_lyrics(self, title: str, artist: str, album: Optional[str] = None, duration: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Fetches synchronized and plain lyrics from LRCLIB API."""
        if not title or not artist or artist in ("Unbekannter Interpret", "Unknown"):
            return None

        client = self.get_http_client()
        params = {
            "track_name": title,
            "artist_name": artist,
        }
        if album and album not in ("Unbekanntes Album", "Unknown"):
            params["album_name"] = album
        if duration and duration > 0:
            params["duration"] = int(duration)

        try:
            res = await client.get("https://lrclib.net/api/get", params=params, timeout=5.0)
            if res.status_code == 200:
                data = res.json()
                synced = data.get("syncedLyrics")
                plain = data.get("plainLyrics")
                instrumental = data.get("instrumental", False)
                
                if synced or plain or instrumental:
                    return {
                        "synced_lyrics": synced,
                        "plain_lyrics": plain,
                        "is_instrumental": instrumental,
                        "source": "lrclib"
                    }
            
            # Fallback search if exact match returned 404
            search_res = await client.get("https://lrclib.net/api/search", params={"q": f"{artist} {title}"}, timeout=5.0)
            if search_res.status_code == 200:
                results = search_res.json()
                if results and len(results) > 0:
                    first = results[0]
                    return {
                        "synced_lyrics": first.get("syncedLyrics"),
                        "plain_lyrics": first.get("plainLyrics"),
                        "is_instrumental": first.get("instrumental", False),
                        "source": "lrclib_search"
                    }
        except Exception:
            pass
        return None

    # 4. ONLINE PROVIDER: MUSICBRAINZ (Metadata Enrichment)
    async def enrich_musicbrainz(self, title: str, artist: str) -> Optional[Dict[str, Any]]:
        """Queries MusicBrainz to resolve accurate album, artist, year and genre."""
        if not title or not artist or artist in ("Unbekannter Interpret", "Unknown"):
            return None

        client = self.get_http_client()
        query = f'recording:"{title}" AND artist:"{artist}"'
        url = "https://musicbrainz.org/ws/2/recording"
        
        try:
            res = await client.get(
                url,
                params={"query": query, "fmt": "json", "limit": 1},
                headers={"User-Agent": "Tonarr-Host/1.0 (contact@tonarr.local)"},
                timeout=6.0
            )
            if res.status_code == 200:
                data = res.json()
                recordings = data.get("recordings", [])
                if recordings:
                    rec = recordings[0]
                    resolved = {}
                    if rec.get("title"):
                        resolved["title"] = rec["title"]
                    if rec.get("artist-credit") and len(rec["artist-credit"]) > 0:
                        resolved["artist"] = rec["artist-credit"][0].get("name")
                    releases = rec.get("releases", [])
                    if releases:
                        resolved["album"] = releases[0].get("title")
                        if releases[0].get("date"):
                            resolved["year"] = releases[0]["date"].split("-")[0]
                    return resolved
        except Exception:
            pass
        return None

    # 5. RESOLVE COMBINED METADATA FOR A TRACK WITH CONFIGURABLE PRIORITIES
    async def resolve_track_metadata(self, file_path: Path, config: HostConfig) -> Dict[str, Any]:
        """
        Extracts tags, cover, and lyrics with strict adherence to configurable priority:
        - Local embedded tags & local .lrc/.txt files are preferred if config.prefer_local_* is True.
        - Online providers are used as fallback (or skipped if disabled).
        """
        # Step 1: Extract Embedded Info
        info = self.extract_embedded_info(file_path)
        
        track_id = hashlib.md5(str(file_path.resolve()).encode("utf-8")).hexdigest()[:16]
        ext = file_path.suffix.lower()

        track_data = {
            "id": f"host_{track_id}",
            "file_path": str(file_path.resolve()),
            "relative_path": str(file_path.relative_to(config.music_directory)) if str(file_path).startswith(config.music_directory) else file_path.name,
            "title": info["title"],
            "artist": info["artist"],
            "album": info["album"],
            "album_artist": info["album_artist"],
            "genre": info["genre"],
            "year": info["year"],
            "track_number": info["track_number"],
            "duration": info["duration"],
            "duration_str": f"{int(info['duration'] // 60):02d}:{int(info['duration'] % 60):02d}",
            "bitrate": info["bitrate"],
            "extension": ext,
            "has_lyrics": False,
            "is_synced": False,
            "lyrics_source": "none",
            "has_cover": False,
            "cover_source": "none",
            "source": "tonarr_host"
        }

        # Step 2: Resolve Lyrics with Priority
        local_lrc_path, is_local_synced = (None, False)
        if config.sources.local_lrc_files:
            local_lrc_path, is_local_synced = self.find_local_lyrics_file(file_path, config.custom_lrc_dir)

        # 2a. Priority: Local .lrc file
        if local_lrc_path and config.prefer_local_lyrics:
            track_data["has_lyrics"] = True
            track_data["is_synced"] = is_local_synced
            track_data["lyrics_source"] = "local_lrc" if is_local_synced else "local_txt"
            track_data["lrc_path"] = str(local_lrc_path.resolve())

        # 2b. Priority: Embedded lyrics
        elif info["has_embedded_lyrics"] and config.prefer_local_lyrics:
            track_data["has_lyrics"] = True
            track_data["is_synced"] = info["is_synced_embedded"]
            track_data["lyrics_source"] = "embedded"

        # 2c. Fallback: Online LRCLIB
        elif config.fetch_missing_online and config.sources.lrclib:
            online_lyrics = await self.fetch_lrclib_lyrics(
                title=track_data["title"],
                artist=track_data["artist"],
                album=track_data["album"],
                duration=track_data["duration"]
            )
            if online_lyrics:
                track_data["has_lyrics"] = True
                track_data["is_synced"] = bool(online_lyrics.get("synced_lyrics"))
                track_data["lyrics_source"] = "lrclib"
                
                # Auto-save online lyrics locally if configured
                if config.save_fetched_lrc_locally and online_lyrics.get("synced_lyrics"):
                    try:
                        save_target = (Path(config.custom_lrc_dir) if config.custom_lrc_dir and not config.save_next_to_audio else file_path.parent) / f"{file_path.stem}.lrc"
                        save_target.write_text(online_lyrics["synced_lyrics"], encoding="utf-8")
                        track_data["lrc_path"] = str(save_target.resolve())
                        track_data["lyrics_source"] = "lrclib_saved_local"
                    except Exception:
                        pass

        # Step 3: Resolve Cover Art
        if info["has_embedded_cover"]:
            track_data["has_cover"] = True
            track_data["cover_source"] = "embedded"
        elif config.sources.local_cover_images:
            local_cover = self.find_local_cover_file(file_path)
            if local_cover:
                track_data["has_cover"] = True
                track_data["cover_source"] = "local_file"
                track_data["cover_path"] = str(local_cover.resolve())

        # Step 4: Metadata Enrichment (if local missing or prefer_local_metadata is False)
        missing_fields = not track_data["album"] or track_data["album"] == "Unbekanntes Album" or not track_data["year"]
        if missing_fields and config.fetch_missing_online and config.sources.musicbrainz:
            mb_data = await self.enrich_musicbrainz(track_data["title"], track_data["artist"])
            if mb_data:
                if not track_data["album"] or track_data["album"] == "Unbekanntes Album":
                    track_data["album"] = mb_data.get("album", track_data["album"])
                if not track_data["year"]:
                    track_data["year"] = mb_data.get("year", track_data["year"])

        return track_data

metadata_engine = MultiSourceMetadataEngine()
