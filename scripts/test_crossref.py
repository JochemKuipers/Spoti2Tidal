from __future__ import annotations

import logging
import sys
from urllib.parse import urlparse

from logging_config import setup_logging
from models.spotify import SpotifyTrack
from services.spotify import Spotify
from services.tidal import Tidal

SPOTIFY_TRACK_URL = "https://open.spotify.com/track/5iSEY9x2UHbDArz4NmlGTZ?si=46c86070ff894e59"


def extract_spotify_id(url: str) -> str | None:
    try:
        path = urlparse(url).path
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "track":
            return parts[1]
    except Exception:
        return None
    return None


def main():
    setup_logging()
    logging.getLogger().setLevel(logging.DEBUG)
    sp = Spotify()
    td = Tidal()

    # Fetch Spotify track metadata
    track_id = extract_spotify_id(SPOTIFY_TRACK_URL)
    if not track_id:
        print("Failed to extract Spotify track id")
        sys.exit(1)

    sp_client = sp.get_client()
    sp_track = SpotifyTrack.from_api(sp_client.track(track_id))
    name = sp_track.name
    artists = sp_track.artists
    duration_ms = sp_track.duration_ms
    isrc = sp_track.external_ids.get("isrc")

    print(f"Spotify track: {name} — {artists} | ISRC: {isrc} | duration_ms: {duration_ms}")

    # Ensure TIDAL login if possible
    td.ensure_logged_in()

    # Try resolve best match
    best = td.resolve_best_match(isrc=isrc, name=name, artists=artists, duration_ms=duration_ms)
    if best:
        td_name = getattr(best, "name", "") or getattr(best, "full_name", "")
        td_artists = ", ".join(getattr(a, "name", "") for a in (getattr(best, "artists", []) or []))
        td_quality = Tidal.quality_label(best)
        print(f"TIDAL match: {td_name} — {td_artists} | quality: {td_quality} | id: {best.id}")
    else:
        print("No TIDAL match found.")


if __name__ == "__main__":
    main()
