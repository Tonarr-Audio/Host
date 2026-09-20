import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

# Determine persistent config path (either in /data for docker or in user home)
if os.path.exists("/data"):
    DATA_DIR = Path("/data")
elif (Path.home() / ".soundsphere_host").exists() and not (Path.home() / ".tonarr_host").exists():
    DATA_DIR = Path.home() / ".soundsphere_host"
else:
    DATA_DIR = Path.home() / ".tonarr_host"

DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "host_config.json"
CACHE_FILE = DATA_DIR / "host_library_cache.json"

class MetadataSourceSettings(BaseModel):
    id3_tags: bool = True
    local_lrc_files: bool = True
    local_cover_images: bool = True
    lrclib: bool = True
    musicbrainz: bool = True
    genius: bool = False
    spotify: bool = False

class HostConfig(BaseModel):
    host_name: str = "Tonarr Host"
    port: int = 8765
    music_directory: str = "/music" if os.path.exists("/music") else str(Path.home() / "Music")
    folder_source_for_creator_and_manager_only: bool = False
    plex_as_only_player_source: bool = False
    
    # Priority & Behavior Configuration
    prefer_local_metadata: bool = True     # Prefer embedded tags (ID3/FLAC/Vorbis) over online scrapers
    prefer_plex_metadata: bool = True      # Prefer Plex metadata & covers over ID3 tags when Plex is connected
    prefer_local_lyrics: bool = True       # Prefer local .lrc/.txt & embedded lyrics over online LRCLIB
    fetch_missing_online: bool = True      # Fetch missing lyrics/metadata from online sources
    save_fetched_lrc_locally: bool = True  # Automatically save fetched online .lrc files next to audio
    save_next_to_audio: bool = True        # Save .lrc in the audio file's directory
    custom_lrc_dir: str = ""               # Optional centralized LRC directory
    
    # Sources
    sources: MetadataSourceSettings = Field(default_factory=MetadataSourceSettings)
    
    # Plex Media Server Integration
    plex_enabled: bool = False
    plex_url: str = ""
    plex_token: str = ""
    plex_section: str = ""                 # Selected music library section key
    sync_plex_on_scan: bool = True
    
    # Optional Security
    api_token: str = ""                    # If set, clients must pass X-SoundSphere-Token or ?token=
    
    # Provider API keys (optional)
    genius_api_key: str = ""
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # Web Player Client Preferences
    ambient_cover_bg: bool = False
    eq_preset: str = "flat"
    eq_bands: list = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    eq_preamp: float = 0.0
    eq_limiter: bool = True

def load_host_config() -> HostConfig:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Keep plex_as_only_player_source and folder_source_for_creator_and_manager_only synchronized
                is_plex_only = bool(data.get("plex_as_only_player_source") or data.get("folder_source_for_creator_and_manager_only"))
                data["plex_as_only_player_source"] = is_plex_only
                data["folder_source_for_creator_and_manager_only"] = is_plex_only
                return HostConfig(**data)
        except Exception as e:
            print(f"[HostConfig] Error loading {CONFIG_FILE}: {e}")
            return HostConfig()
    
    # First start: save default config so files appear in data dir
    cfg = HostConfig()
    save_host_config(cfg)
    return cfg

def save_host_config(config: HostConfig) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[HostConfig] Error saving {CONFIG_FILE}: {e}")
