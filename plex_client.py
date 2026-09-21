import os
import json
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import xml.etree.ElementTree as ET
import httpx

try:
    from .config import load_host_config, save_host_config, HostConfig, DATA_DIR
except (ImportError, ValueError):
    from config import load_host_config, save_host_config, HostConfig, DATA_DIR

_GLOBAL_EFFECTIVE_URL: Dict[str, str] = {}
HOST_PLAYLISTS_FILE = DATA_DIR / "host_playlists.json"

class HostPlexClient:
    def __init__(self, base_url: str = "", token: str = ""):
        url = (base_url or "").strip().rstrip("/")
        if url and not url.startswith("http://") and not url.startswith("https://"):
            url = f"http://{url}"
        self.base_url = url
        self.token = (token or "").strip()
        self._effective_url: Optional[str] = None

    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-Plex-Token": self.token,
            "Accept": "application/json",
            "X-Plex-Product": "Tonarr Host",
            "X-Plex-Version": "1.0.0",
            "X-Plex-Client-Identifier": "Tonarr-Host",
            "X-Plex-Platform": "Linux",
            "X-Plex-Platform-Version": "Docker",
            "X-Plex-Device": "Server",
            "X-Plex-Device-Name": "Tonarr Host",
            "X-Plex-Model": "HostEdition",
            "User-Agent": "Tonarr-Host/1.0.0"
        }

    async def _get_working_base_url(self) -> str:
        """Finds or validates working base URL."""
        if not self.base_url and not self.token:
            return ""

        # Return validated cached URL immediately without redundant network pings
        if "current" in _GLOBAL_EFFECTIVE_URL and _GLOBAL_EFFECTIVE_URL["current"]:
            return _GLOBAL_EFFECTIVE_URL["current"]
        if self._effective_url:
            return self._effective_url

        if self.base_url:
            try:
                async with httpx.AsyncClient(timeout=2.5, verify=False) as client:
                    res = await client.get(f"{self.base_url}/identity", headers=self._get_headers())
                    if res.status_code == 200:
                        _GLOBAL_EFFECTIVE_URL["current"] = self.base_url
                        self._effective_url = self.base_url
                        return self.base_url
            except Exception:
                pass

        # Fast parallel auto-discovery via plex.tv resources
        if self.token:
            try:
                headers = {
                    "Accept": "application/json",
                    "X-Plex-Token": self.token,
                    "X-Plex-Client-Identifier": "Tonarr-Host"
                }
                candidates = []
                if self.base_url:
                    candidates.append((self.base_url, self.token))

                async with httpx.AsyncClient(timeout=5.0, verify=False) as http_client:
                    res = await http_client.get("https://plex.tv/api/v2/resources?includeHttps=1", headers=headers)
                    if res.status_code == 200:
                        resources = res.json()
                        for r in resources:
                            if "server" in r.get("provides", []):
                                server_token = r.get("accessToken") or self.token
                                connections = r.get("connections", [])
                                for c in connections:
                                    uri = c.get("uri")
                                    addr = c.get("address")
                                    port = c.get("port", 32400)
                                    if uri:
                                        candidates.append((uri, server_token))
                                    if addr:
                                        candidates.append((f"http://{addr}:{port}", server_token))
                                        candidates.append((f"https://{addr}:{port}", server_token))

                if candidates:
                    async def probe(url: str, tok: str):
                        try:
                            async with httpx.AsyncClient(timeout=1.8, verify=False) as probe_client:
                                p_res = await probe_client.get(f"{url}/identity", headers={"X-Plex-Token": tok})
                                if p_res.status_code == 200:
                                    return (url, tok)
                        except Exception:
                            pass
                        return None

                    tasks = [asyncio.create_task(probe(u, t)) for u, t in candidates]
                    for fut in asyncio.as_completed(tasks):
                        result = await fut
                        if result:
                            winning_url, winning_token = result
                            for t in tasks:
                                t.cancel()
                            self.base_url = winning_url
                            self.token = winning_token
                            self._effective_url = winning_url
                            _GLOBAL_EFFECTIVE_URL["current"] = winning_url
                            
                            # Update config
                            try:
                                cfg = load_host_config()
                                if cfg.plex_url != winning_url or cfg.plex_token != winning_token:
                                    cfg.plex_url = winning_url
                                    cfg.plex_token = winning_token
                                    save_host_config(cfg)
                            except Exception:
                                pass
                            return winning_url
            except Exception as e:
                print(f"[HostPlexClient] Auto-discovery error: {e}")

        return self.base_url or ""

    async def test_connection(self) -> Dict[str, Any]:
        """Tests Plex connection and returns server details."""
        if not self.is_configured():
            return {"success": False, "error": "Plex Server URL oder Token fehlt"}
        try:
            url = await self._get_working_base_url()
            async with httpx.AsyncClient(timeout=6.0, verify=False) as client:
                res = await client.get(f"{url}/identity", headers=self._get_headers())
                if res.status_code == 200:
                    data = res.json().get("MediaContainer", {})
                    return {
                        "success": True,
                        "machineIdentifier": data.get("machineIdentifier", ""),
                        "version": data.get("version", "Plex Media Server"),
                        "effective_url": url
                    }
                return {"success": False, "error": f"Plex Fehler (HTTP {res.status_code})"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_music_sections(self) -> List[Dict[str, Any]]:
        """Retrieves music/audio sections from Plex server."""
        if not self.is_configured():
            return []
        try:
            base_url = await self._get_working_base_url()
            if not base_url:
                return []
            headers = self._get_headers()
            async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
                res = await client.get(f"{base_url}/library/sections", headers=headers)
                if res.status_code == 200:
                    dirs = res.json().get("MediaContainer", {}).get("Directory", [])
                    matched = []
                    for d in dirs:
                        stype = str(d.get("type", "")).lower()
                        title = str(d.get("title", "")).lower()
                        if stype in ("artist", "audio", "music", "track", "album") or "musik" in title or "music" in title or "audio" in title:
                            matched.append({
                                "key": str(d.get("key")),
                                "title": d.get("title", "Musik"),
                                "type": d.get("type", "artist"),
                                "agent": d.get("agent", ""),
                                "uuid": d.get("uuid", "")
                            })
                    if not matched and dirs:
                        matched = [{"key": str(d.get("key")), "title": d.get("title", "Mediathek"), "type": d.get("type", "")} for d in dirs]
                    return matched
        except Exception as e:
            print(f"[HostPlexClient] Error getting music sections: {e}")
        return []

    async def get_all_tracks(self, section_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves all tracks from Plex music library."""
        if not self.is_configured():
            return []
        
        tracks = []
        try:
            base_url = await self._get_working_base_url()
            if not base_url:
                return []

            target_sections = []
            if section_key and section_key != "all":
                target_sections = [{"key": str(section_key)}]
            else:
                target_sections = await self.get_music_sections()
                if not target_sections:
                    target_sections = [{"key": "all"}]

            seen_rating_keys = set()
            async with httpx.AsyncClient(timeout=40.0, verify=False) as client:
                for sec in target_sections:
                    sec_key = sec.get("key")
                    if not sec_key:
                        continue
                    
                    metadata = []
                    query_urls = [
                        f"{base_url}/library/sections/{sec_key}/all?type=10" if sec_key != "all" else f"{base_url}/library/all?type=10",
                        f"{base_url}/library/sections/{sec_key}/allLeaves" if sec_key != "all" else f"{base_url}/library/allLeaves"
                    ]
                    for qurl in query_urls:
                        try:
                            res = await client.get(qurl, headers=self._get_headers())
                            if res.status_code == 200:
                                items = res.json().get("MediaContainer", {}).get("Metadata", [])
                                if items:
                                    metadata = items
                                    break
                        except Exception:
                            continue

                    for item in metadata:
                        rating_key = str(item.get("ratingKey", "")).strip()
                        if not rating_key or rating_key in seen_rating_keys:
                            continue
                        seen_rating_keys.add(rating_key)
                            
                        media_list = item.get("Media", [])
                        media_obj = media_list[0] if media_list else {}
                        part = media_obj.get("Part", [{}])[0] if media_list else {}
                        part_key = part.get("key", "")
                        part_file = part.get("file", "")
                        container = (media_obj.get("container") or "mp3").lower()
                        audio_codec = (media_obj.get("audioCodec") or container).lower()
                        bitrate = media_obj.get("bitrate", 320)
                        sample_rate = media_obj.get("samplingRate")
                        bit_depth = media_obj.get("audioBitDepth")
                        channels = media_obj.get("audioChannels")

                        is_lossless = audio_codec in ("flac", "alac", "wav", "aiff", "dsd") or container in ("flac", "alac", "wav", "aiff")
                        is_hi_res = is_lossless and ((bit_depth and int(bit_depth) > 16) or (sample_rate and int(sample_rate) > 48000))

                        codec_upper = audio_codec.upper()
                        if is_lossless:
                            parts = [codec_upper]
                            if bit_depth:
                                parts.append(f"{bit_depth}-Bit")
                            if sample_rate:
                                khz = round(int(sample_rate) / 1000.0, 1)
                                parts.append(f"{khz} kHz")
                            quality_str = " ".join(parts) if len(parts) > 1 else f"{codec_upper} Lossless"
                        else:
                            quality_str = f"{codec_upper} {bitrate} kbps" if bitrate else codec_upper

                        thumb = item.get("thumb") or item.get("parentThumb") or item.get("grandparentThumb") or ""
                        dur_ms = item.get("duration") or 0
                        dur_sec = dur_ms / 1000.0
                        mins = int(dur_sec // 60)
                        secs = int(dur_sec % 60)
                        dur_str = f"{mins:02d}:{secs:02d}"

                        tracks.append({
                            "id": f"plex_{rating_key}",
                            "host_id": f"plex_{rating_key}",
                            "plex_key": rating_key,
                            "file_path": f"plex://{rating_key}",
                            "server_file_path": part_file,
                            "file_name": f"{item.get('title', 'track')}.{container}",
                            "title": item.get("title", "Unbekannter Titel"),
                            "artist": item.get("grandparentTitle") or item.get("originalTitle") or "Unbekannter Interpret",
                            "album": item.get("parentTitle", "Unbekanntes Album"),
                            "year": item.get("year"),
                            "track_number": item.get("index"),
                            "disc_number": item.get("parentIndex", 1),
                            "genre": item.get("Genre", [{}])[0].get("tag") if item.get("Genre") else None,
                            "duration": dur_sec,
                            "stream_url": f"/api/plex/stream/{rating_key}",
                            "cover_url": f"/api/cover?id=plex_{rating_key}",
                            "thumb": thumb,
                            "part_key": part_key,
                            "plex_cover_url": f"{base_url}{thumb}?X-Plex-Token={self.token}" if thumb else "",
                            "plex_stream_url": f"{base_url}{part_key}?X-Plex-Token={self.token}" if part_key else "",
                            "extension": f".{container}",
                            "codec": codec_upper,
                            "bitrate": bitrate,
                            "sample_rate": sample_rate,
                            "bit_depth": bit_depth,
                            "channels": channels,
                            "quality_str": quality_str,
                            "is_lossless": is_lossless,
                            "is_hi_res": is_hi_res,
                            "source": "plex"
                        })
        except Exception as e:
            print(f"[HostPlexClient] Error getting all tracks: {e}")
        return tracks

    async def get_playlists(self) -> List[Dict[str, Any]]:
        """Retrieves audio playlists from Plex."""
        if not self.is_configured():
            return []
        try:
            url = await self._get_working_base_url()
            async with httpx.AsyncClient(timeout=12.0, verify=False) as client:
                res = await client.get(f"{url}/playlists?playlistType=audio", headers=self._get_headers())
                metadata = []
                if res.status_code == 200:
                    metadata = res.json().get("MediaContainer", {}).get("Metadata", [])
                
                # Fallback: Query all playlists if audio query yielded nothing
                if not metadata:
                    res_all = await client.get(f"{url}/playlists", headers=self._get_headers())
                    if res_all.status_code == 200:
                        all_meta = res_all.json().get("MediaContainer", {}).get("Metadata", [])
                        metadata = [
                            p for p in all_meta 
                            if p.get("playlistType") == "audio" or str(p.get("type", "")).lower() in ("audio", "playlist", "track")
                        ]
                
                result = []
                for p in metadata:
                    r_key = str(p.get("ratingKey", "")).strip()
                    if not r_key:
                        continue
                    thumb_val = p.get("thumb") or ""
                    comp_val = p.get("composite") or ""
                    thumb_key = thumb_val or comp_val
                    cover_url = f"/api/playlist/cover?key={r_key}&thumb={thumb_val}&composite={comp_val}" if thumb_key else ""
                    result.append({
                        "id": f"plex_{r_key}",
                        "host_id": f"plex_{r_key}",
                        "plex_key": r_key,
                        "name": p.get("title", "Plex Playlist"),
                        "track_count": p.get("leafCount", 0),
                        "duration": (p.get("duration") or 0) / 1000.0,
                        "composite": comp_val,
                        "thumb": thumb_val,
                        "cover_url": cover_url,
                        "source": "plex"
                    })
                return result
        except Exception as e:
            print(f"[HostPlexClient] Error getting playlists: {e}")
        return []

    async def get_playlist_tracks(self, rating_key: str) -> List[Dict[str, Any]]:
        """Retrieves sequential tracks of a specific Plex playlist with rich audio quality."""
        if not self.is_configured() or not rating_key:
            return []
        clean_key = str(rating_key).replace("plex_", "").replace("plex://", "").strip()
        try:
            base_url = await self._get_working_base_url()
            async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
                res = await client.get(f"{base_url}/playlists/{clean_key}/items?X-Plex-Token={self.token}", headers=self._get_headers())
                if res.status_code == 200:
                    items = res.json().get("MediaContainer", {}).get("Metadata", [])
                    tracks = []
                    for idx, item in enumerate(items):
                        r_key = str(item.get("ratingKey") or item.get("key", "").replace("/library/metadata/", "")).strip()
                        if not r_key:
                            continue
                        media_list = item.get("Media", [])
                        media_obj = media_list[0] if media_list else {}
                        part = media_obj.get("Part", [{}])[0] if media_list else {}
                        part_key = part.get("key", "")
                        part_file = part.get("file", "")
                        container = (media_obj.get("container") or "mp3").lower()
                        audio_codec = (media_obj.get("audioCodec") or container).lower()
                        bitrate = media_obj.get("bitrate", 320)
                        sample_rate = media_obj.get("samplingRate")
                        bit_depth = media_obj.get("audioBitDepth")
                        channels = media_obj.get("audioChannels")

                        is_lossless = audio_codec in ("flac", "alac", "wav", "aiff", "dsd") or container in ("flac", "alac", "wav", "aiff")
                        is_hi_res = is_lossless and ((bit_depth and int(bit_depth) > 16) or (sample_rate and int(sample_rate) > 48000))

                        codec_upper = audio_codec.upper()
                        if is_lossless:
                            parts = [codec_upper]
                            if bit_depth:
                                parts.append(f"{bit_depth}-Bit")
                            if sample_rate:
                                khz = round(int(sample_rate) / 1000.0, 1)
                                parts.append(f"{khz} kHz")
                            quality_str = " ".join(parts) if len(parts) > 1 else f"{codec_upper} Lossless"
                        else:
                            quality_str = f"{codec_upper} {bitrate} kbps" if bitrate else codec_upper

                        thumb = item.get("thumb") or item.get("parentThumb") or item.get("grandparentThumb") or ""
                        dur_ms = item.get("duration") or 0
                        dur_sec = dur_ms / 1000.0
                        mins = int(dur_sec // 60)
                        secs = int(dur_sec % 60)

                        plex_stream_url = f"{base_url}{part_key}?X-Plex-Token={self.token}" if part_key else ""
                        plex_cover_url = f"{base_url}{thumb}?X-Plex-Token={self.token}" if thumb else ""

                        tracks.append({
                            "id": f"plex_{r_key}",
                            "host_id": f"plex_{r_key}",
                            "plex_key": r_key,
                            "order": idx + 1,
                            "file_path": f"plex://{r_key}",
                            "server_file_path": part_file,
                            "file_name": f"{item.get('title', 'track')}.{container}",
                            "title": item.get("title", "Unbekannter Titel"),
                            "artist": item.get("grandparentTitle") or item.get("originalTitle") or "Unbekannter Interpret",
                            "album": item.get("parentTitle", "Unbekanntes Album"),
                            "duration": dur_sec,
                            "duration_str": f"{mins:02d}:{secs:02d}",
                            "stream_url": f"/api/plex/stream/{r_key}",
                            "cover_url": f"/api/cover?id=plex_{r_key}",
                            "extension": f".{container}",
                            "codec": codec_upper,
                            "bitrate": bitrate,
                            "sample_rate": sample_rate,
                            "bit_depth": bit_depth,
                            "channels": channels,
                            "quality_str": quality_str,
                            "is_lossless": is_lossless,
                            "thumb": thumb,
                            "part_key": part_key,
                            "plex_cover_url": plex_cover_url,
                            "plex_stream_url": plex_stream_url,
                            "is_hi_res": is_hi_res,
                            "source": "plex"
                        })
                    return tracks
        except Exception as e:
            print(f"[HostPlexClient] Error getting playlist tracks: {e}")
        return []

    async def get_artist_thumbs(self) -> Dict[str, str]:
        """Fetches a mapping of artist_name -> thumb path from Plex."""
        if not self.is_configured():
            return {}
        artist_map = {}
        try:
            base_url = await self._get_working_base_url()
            sections = await self.get_music_sections()
            async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
                for sec in sections:
                    sec_key = sec.get("key")
                    if not sec_key:
                        continue
                    res = await client.get(f"{base_url}/library/sections/{sec_key}/all?type=8", headers=self._get_headers())
                    if res.status_code == 200:
                        artists = res.json().get("MediaContainer", {}).get("Metadata", [])
                        for a in artists:
                            title = a.get("title", "").strip().lower()
                            thumb = a.get("thumb")
                            if title and thumb:
                                artist_map[title] = thumb
        except Exception as e:
            print(f"[HostPlexClient] Error getting artist thumbs: {e}")
        return artist_map

    async def get_track_lyrics(self, rating_key: str) -> Optional[str]:
        """Fetches synchronized LRC or text lyrics from Plex for a track."""
        if not self.is_configured() or not rating_key:
            return None
        clean_key = str(rating_key).replace("plex://", "").replace("plex_", "").replace("/library/metadata/", "").strip()
        try:
            base_url = await self._get_working_base_url()
            headers = self._get_headers()
            async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
                r = await client.get(f"{base_url}/library/metadata/{clean_key}?includeLyrics=1", headers=headers)
                if r.status_code == 200:
                    data = r.json().get("MediaContainer", {})
                    metadata_list = data.get("Metadata", [])
                    if not metadata_list:
                        return None
                    meta = metadata_list[0]
                    stream_keys = []
                    for m in meta.get("Media", []):
                        for p in m.get("Part", []):
                            for s in p.get("Stream", []):
                                stype = str(s.get("streamType", ""))
                                fmt = str(s.get("format", "")).lower()
                                sk = s.get("key")
                                if stype == "4" or fmt in ("lrc", "txt") or "lyric" in str(sk).lower():
                                    if sk and sk not in stream_keys:
                                        stream_keys.append(sk)
                    
                    for skey in stream_keys:
                        lyr_url = f"{base_url}{skey}" if not skey.startswith("http") else skey
                        sep = "&" if "?" in lyr_url else "?"
                        lyr_res = await client.get(f"{lyr_url}{sep}X-Plex-Token={self.token}", headers={"X-Plex-Token": self.token})
                        if lyr_res.status_code == 200 and lyr_res.text.strip():
                            raw = lyr_res.text.strip()
                            if raw.startswith("{") and "MediaContainer" in raw:
                                try:
                                    jdata = json.loads(raw)
                                    lyrics_list = jdata.get("MediaContainer", {}).get("Lyrics", [])
                                    if lyrics_list:
                                        lines = lyrics_list[0].get("Line", [])
                                        lrc_lines = []
                                        for l in lines:
                                            start_ms = l.get("startOffset", 0)
                                            mins = start_ms // 60000
                                            secs = (start_ms % 60000) / 1000.0
                                            spans = l.get("Span", [])
                                            text = "".join(span.get("text", "") for span in spans).strip()
                                            lrc_lines.append(f"[{mins:02d}:{secs:05.2f}] {text}")
                                        if lrc_lines:
                                            return "\n".join(lrc_lines)
                                except Exception:
                                    pass
                            return raw
        except Exception as e:
            print(f"[HostPlexClient] Lyrics error for {clean_key}: {e}")
        return None

    @staticmethod
    async def create_auth_pin(callback_url: str = "") -> Dict[str, Any]:
        """Creates a Plex PIN matching official Plex OAuth standard."""
        url = "https://plex.tv/api/v2/pins"
        client_id = "Tonarr-Host-Server"
        product = "Tonarr Host"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Plex-Product": product,
            "X-Plex-Client-Identifier": client_id,
            "X-Plex-Version": "1.0.0"
        }
        data = {
            "strong": "true",
            "X-Plex-Product": product,
            "X-Plex-Client-Identifier": client_id
        }
        try:
            import urllib.parse
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                res = await client.post(url, data=data, headers=headers)
                if res.status_code in (200, 201):
                    data_json = res.json()
                    pin_id = data_json.get("id")
                    code = data_json.get("code")
                    params = {
                        "clientID": client_id,
                        "code": code,
                        "context[device][product]": product,
                        "context[device][platform]": "Web",
                        "context[device][device]": "Tonarr Host Server",
                    }
                    if callback_url:
                        params["forwardUrl"] = callback_url
                    query_string = urllib.parse.urlencode(params)
                    auth_url = f"https://app.plex.tv/auth#?{query_string}"
                    return {"success": True, "pin_id": pin_id, "code": code, "auth_url": auth_url}
                return {"success": False, "error": f"Plex PIN Fehler: HTTP {res.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    async def check_auth_pin(pin_id: int, code: str = "") -> Dict[str, Any]:
        """Checks if a Plex PIN has been approved."""
        url = f"https://plex.tv/api/v2/pins/{pin_id}"
        params = {}
        if code:
            params["code"] = code
        client_id = "Tonarr-Host-Server"
        headers = {
            "Accept": "application/json",
            "X-Plex-Product": "Tonarr Host",
            "X-Plex-Client-Identifier": client_id
        }
        try:
            async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
                res = await client.get(url, params=params, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    token = data.get("authToken")
                    if token:
                        servers = []
                        try:
                            res_servers = await client.get(
                                "https://plex.tv/api/v2/resources",
                                params={
                                    "includeHttps": 1,
                                    "X-Plex-Product": "Tonarr Host",
                                    "X-Plex-Client-Identifier": client_id,
                                    "X-Plex-Token": token
                                },
                                headers=headers,
                                timeout=8.0
                            )
                            if res_servers.status_code == 200:
                                resources = res_servers.json()
                                for item in resources:
                                    if item.get("product") == "Plex Media Server" or "server" in item.get("provides", []):
                                        server_token = item.get("accessToken") or token
                                        conns = item.get("connections", [])
                                        https_required = bool(item.get("httpsRequired"))
                                        best_uri = ""
                                        for c in conns:
                                            uri = c.get("uri", "")
                                            if https_required and uri.startswith("http://"):
                                                uri = uri.replace("http://", "https://")
                                            if c.get("local") and uri:
                                                best_uri = uri
                                                break
                                        if not best_uri and conns:
                                            uri = conns[0].get("uri", "")
                                            if https_required and uri.startswith("http://"):
                                                uri = uri.replace("http://", "https://")
                                            best_uri = uri
                                        if not best_uri and conns and conns[0].get("address"):
                                            scheme = "https" if https_required else "http"
                                            best_uri = f"{scheme}://{conns[0].get('address')}:{conns[0].get('port', 32400)}"

                                        servers.append({
                                            "name": item.get("name"),
                                            "id": item.get("clientIdentifier"),
                                            "uri": best_uri,
                                            "token": server_token,
                                            "connections": conns
                                        })
                        except Exception as e:
                            print(f"[HostPlexClient] Server discovery error: {e}")
                        return {
                            "authorized": True,
                            "token": token,
                            "servers": servers
                        }
                    return {"authorized": False}
                return {"authorized": False, "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"authorized": False, "error": str(e)}

def load_host_playlists() -> List[Dict[str, Any]]:
    if HOST_PLAYLISTS_FILE.exists():
        try:
            with open(HOST_PLAYLISTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_host_playlists(playlists: List[Dict[str, Any]]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(HOST_PLAYLISTS_FILE, "w", encoding="utf-8") as f:
            json.dump(playlists, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[HostPlaylists] Error saving playlists: {e}")
