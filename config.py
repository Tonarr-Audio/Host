import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

# Determine persistent config path (either in /data for docker or in user home)
if os.path.exists("/data"):
    DATA_DIR = Path("/data")
else:
    DATA_DIR = Path.home() / ".soundsphere_host"

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
    host_name: str = "SoundSphere Host"
    port: int = 8765
    music_directory: str = "/music" if os.path.exists("/music") else str(Path.home() / "Music")
    
    # Priority & Behavior Configuration
    prefer_local_metadata: bool = True     # Prefer embedded tags (ID3/FLAC/Vorbis) over online scrapers
    prefer_local_lyrics: bool = True       # Prefer local .lrc/.txt & embedded lyrics over online LRCLIB
    fetch_missing_online: bool = True      # Fetch missing lyrics/metadata from online sources
    save_fetched_lrc_locally: bool = True  # Automatically save fetched online .lrc files next to audio
    save_next_to_audio: bool = True        # Save .lrc in the audio file's directory
    custom_lrc_dir: str = ""               # Optional centralized LRC directory
    
    # Sources
    sources: MetadataSourceSettings = Field(default_factory=MetadataSourceSettings)
    
    # Optional Security
    api_token: str = ""                    # If set, clients must pass X-SoundSphere-Token or ?token=
    
    # Provider API keys (optional)
    genius_api_key: str = ""
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

def load_host_config() -> HostConfig:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
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
