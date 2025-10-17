from pathlib import Path
import webbrowser
import tidalapi
from platformdirs import user_config_dir
from PyQt6.QtCore import QThread, pyqtSignal
from typing import List, Optional
import logging

DEFAULT_SESSION_DIR = Path(user_config_dir("Spoti2Tidal"))
DEFAULT_SESSION_FILE = DEFAULT_SESSION_DIR / "tidal_session.json"


class TidalTrackFetchWorker(QThread):
    """Worker thread for fetching a batch of tracks at a specific offset"""

    finished = pyqtSignal(list, int)  # tracks, offset
    error = pyqtSignal(str, int)  # error message, offset

    def __init__(self, tidal_session, playlist_id, offset, limit=50):
        super().__init__()
        self.tidal_session = tidal_session
        self.offset = offset
        self.limit = limit
        self.playlist_id = playlist_id

    def run(self):
        try:
            tracks = self.tidal_session.playlist(self.playlist_id).tracks(
                limit=self.limit, offset=self.offset
            )
            self.finished.emit(tracks, self.offset)
        except Exception as e:
            self.error.emit(str(e), self.offset)


class TidalPlaylistFetchWorker(QThread):
    """Worker thread for fetching a batch of playlists at a specific offset"""

    finished = pyqtSignal(list, int)  # playlists, offset
    error = pyqtSignal(str, int)  # error message, offset

    def __init__(self, tidal_session, offset, limit=50):
        super().__init__()
        self.tidal_session = tidal_session
        self.offset = offset
        self.limit = limit

    def run(self):
        try:
            playlists = self.tidal_session.user.playlists(
                limit=self.limit, offset=self.offset
            )
            self.finished.emit(playlists, self.offset)
        except Exception as e:
            self.error.emit(str(e), self.offset)


class Tidal:
    def __init__(self, session_file: Path | str | None = None, logger=print):
        self.session = tidalapi.Session()
        self.logger = logger if logger is not print else logging.getLogger(__name__)
        self.session_file = Path(session_file) if session_file else DEFAULT_SESSION_FILE
        self.session_file.parent.mkdir(parents=True, exist_ok=True)

        # Try to load an existing session; if invalid, stay unauthenticated until user completes PKCE
        self._load_session_silent()

    def _load_session_silent(self) -> bool:
        try:
            self.logger.debug(f"Loading TIDAL session from {self.session_file}")
            loaded = self.session.load_session_from_file(self.session_file)
            if loaded and self.session.check_login():
                return True
        except Exception:
            self.logger.exception("Failed to load TIDAL session")
        return False

    # ---- PKCE login helpers ----
    def get_pkce_login_url(self) -> str:
        return self.session.pkce_login_url()

    def open_browser_login(self) -> str:
        url = self.get_pkce_login_url()
        try:
            webbrowser.open(url)
        except Exception:
            self.logger.warning("Failed to open browser for TIDAL login")
        self.logger.info(
            "Opened TIDAL login in your browser. If it didn't open, use the URL below:"
        )
        self.logger.info(url)
        self.logger.info(
            "After logging in, you'll land on an 'Oops' page. Copy that page's full URL and provide it to complete login."
        )
        return url

    def complete_pkce_login(self, redirected_url: str) -> bool:
        token_json = self.session.pkce_get_auth_token(redirected_url)
        self.session.process_auth_token(token_json, is_pkce_token=True)
        self.save_tokens()
        return self.session.check_login()

    # ---- persistence ----
    def save_tokens(self) -> None:
        try:
            self.session.save_session_to_file(self.session_file)
        except Exception as e:
            self.logger.error(
                f"Failed to save TIDAL session to {self.session_file}: {e}"
            )

    def load_tokens(self) -> bool:
        return self._load_session_silent()

    # ---- convenience accessors ----
    def is_logged_in(self) -> bool:
        return self.session.check_login()

    def ensure_logged_in(self) -> bool:
        if self.is_logged_in():
            return True
        if self.session.refresh_token:
            try:
                if self.session.token_refresh(self.session.refresh_token):
                    self.save_tokens()
                    return True
            except Exception:
                pass
        return False

    def get_session(self):
        return self.session

    def get_user(self):
        return self.session.user

    def get_user_playlists(
        self, progress_callback=None, page_limit: int = 50
    ) -> List[tidalapi.playlist.UserPlaylist]:
        self.logger.info("Fetching TIDAL user playlists")
        try:
            playlists = self.session.user.playlists()
        except Exception:
            playlists = []
        if progress_callback:
            progress_callback(100)
        return playlists

    def get_user_tracks(
        self, progress_callback=None, page_limit: int = 100
    ) -> List[tidalapi.media.Track]:
        self.logger.info("Fetching TIDAL user tracks")
        tracks = []
        try:
            total = self.session.user.favorites.get_tracks_count()
        except Exception:
            self.logger.exception("Failed to fetch TIDAL user favorites count")
            total = 0

        offset = 0
        while True:
            page = self.session.user.favorites.tracks(limit=page_limit, offset=offset)
            if not page:
                break
            tracks.extend(page)
            offset += len(page)
            if progress_callback and total > 0:
                progress_callback(min(99, int(len(tracks) / total * 100)))
            if total and len(tracks) >= total:
                break

        if progress_callback:
            progress_callback(100)
        self.logger.info(f"Fetched {len(tracks)} TIDAL user tracks")
        return tracks

    def get_playlist(self, playlist_id) -> tidalapi.playlist.UserPlaylist:
        self.logger.info(f"Fetching TIDAL playlist {playlist_id}")
        return self.session.playlist(playlist_id)

    def get_playlist_tracks(
        self, playlist_id, progress_callback=None, page_limit: int = 100
    ) -> List[tidalapi.media.Track]:
        self.logger.info(f"Fetching TIDAL playlist tracks {playlist_id}")
        playlist = self.session.playlist(playlist_id)
        tracks = []
        try:
            total = playlist.get_tracks_count()
        except Exception:
            total = 0

        offset = 0
        while True:
            page = playlist.tracks(limit=page_limit, offset=offset)
            if not page:
                break
            tracks.extend(page)
            offset += len(page)
            if progress_callback and total > 0:
                progress_callback(min(99, int(len(tracks) / total * 100)))
            if total and len(tracks) >= total:
                break

        if progress_callback:
            progress_callback(100)
        self.logger.info(f"Fetched {len(tracks)} TIDAL playlist tracks")
        return tracks

    def get_track(self, track_id) -> tidalapi.media.Track:
        self.logger.info(f"Fetching TIDAL track {track_id}")
        return self.session.track(track_id)

    # ---- search & matching helpers ----
    def _search_tracks(self, query: str, limit: int = 25) -> List[tidalapi.media.Track]:
        self.logger.info(f"Searching TIDAL for tracks: {query}")
        try:
            # Use the Track class per tidalapi docs for models
            results = self.session.search(query=query, models=[tidalapi.media.Track], limit=limit)
            # Handle possible shapes: list, dict with 'tracks', or object with .tracks
            tracks = []
            if isinstance(results, list):
                tracks = results
            elif isinstance(results, dict) and "tracks" in results:
                tracks = results.get("tracks") or []
            else:
                tracks = getattr(results, "tracks", []) or []
            self.logger.info(f"Found {len(tracks)} TIDAL tracks for search: {query}")
            return tracks
        except Exception:
            self.logger.exception("TIDAL search failed")
            return []

    def search_by_isrc(self, isrc: str) -> List[tidalapi.media.Track]:
        self.logger.info(f"Searching TIDAL for tracks by ISRC: {isrc}")
        if not isrc:
            return []
        # TIDAL search supports fielded queries for ISRC
        candidates = self._search_tracks(f"isrc:{isrc}", limit=10)
        # Ensure exact ISRC match first
        exact = [t for t in candidates if getattr(t, "isrc", None) == isrc]
        self.logger.info(f"Found {len(exact)} exact TIDAL tracks for ISRC: {isrc}")
        return exact or candidates

    def search_by_name(self, name: str) -> List[tidalapi.media.Track]:
        self.logger.info(f"Searching TIDAL for tracks by name: {name}")
        if not name:
            return []
        tracks = self._search_tracks(name, limit=25)
        self.logger.info(f"Found {len(tracks)} TIDAL tracks for name: {name}")
        return tracks

    def search_by_name_artist(
        self, name: str, artist: str
    ) -> List[tidalapi.media.Track]:
        self.logger.info(f"Searching TIDAL for tracks by name and artist: {name} {artist}")
        if not name:
            return []
        query = f"{name} {artist}" if artist else name
        tracks = self._search_tracks(query, limit=25)
        self.logger.info(f"Found {len(tracks)} TIDAL tracks for name and artist: {name} {artist}")
        return tracks

    @staticmethod
    def _quality_rank(track: tidalapi.media.Track) -> int:
        # Higher is better; use boolean flags only
        if getattr(track, "is_hi_res_lossless", False):
            return 3
        if getattr(track, "is_lossless", False):
            return 2
        # treat everything else as lossy
        return 1 if getattr(track, "available", True) else 0

    @staticmethod
    def quality_label(track: tidalapi.media.Track) -> str:
        if getattr(track, "is_hi_res_lossless", False):
            return "Hi-Res Lossless"
        if getattr(track, "is_lossless", False):
            return "Lossless"
        return "Lossy"

    def pick_best_quality(
        self, tracks: List[tidalapi.media.Track]
    ) -> Optional[tidalapi.media.Track]:
        self.logger.info(f"Picking best quality track from {len(tracks)} tracks")
        if not tracks:
            return None
        return sorted(
            tracks,
            key=lambda t: (
                self._quality_rank(t),
                getattr(getattr(t, "album", None), "release_date", None) or 0,
                getattr(t, "popularity", -1),
            ),
            reverse=True,
        )[0]

    def resolve_best_match(
        self, *, isrc: Optional[str], name: str, artist: Optional[str]
    ) -> Optional[tidalapi.media.Track]:
        self.logger.info(f"Resolving best match for ISRC: {isrc}, name: {name}, artist: {artist}")
        # Search order: ISRC -> name -> name+artist
        candidates: List[tidalapi.media.Track] = []
        if isrc:
            candidates = self.search_by_isrc(isrc)
        if not candidates:
            candidates = self.search_by_name(name)
        if not candidates and artist:
            candidates = self.search_by_name_artist(name, artist)
        best = self.pick_best_quality(candidates)
        self.logger.info(f"Best match found: {best}")
        return best

    # ---- playlist management ----
    def create_playlist(
        self, name: str, description: str = ""
    ) -> Optional[tidalapi.playlist.UserPlaylist]:
        self.logger.info(f"Creating TIDAL playlist: {name}")
        try:
            return self.session.user.create_playlist(name=name, description=description)
        except Exception:
            self.logger.exception("Failed to create TIDAL playlist")
            return None

    def add_tracks_to_playlist(self, playlist_id: str, track_ids: List[int]) -> bool:
        self.logger.info(f"Adding {len(track_ids)} tracks to TIDAL playlist {playlist_id}")
        try:
            playlist = self.session.playlist(playlist_id)
            if not track_ids:
                return True
            # TIDAL API supports adding in batches
            batch_size = 50
            for i in range(0, len(track_ids), batch_size):
                batch = track_ids[i : i + batch_size]
                playlist.add(batch)
            return True
        except Exception:
            self.logger.exception("Failed to add tracks to TIDAL playlist")
            return False

    def get_playlist_track_ids(self, playlist_id: str) -> List[int]:
        self.logger.info(f"Fetching TIDAL playlist track IDs for playlist {playlist_id}")
        try:
            tracks = self.get_playlist_tracks(playlist_id)
            return [int(getattr(t, "id", -1)) for t in tracks if getattr(t, "id", None)]
        except Exception:
            self.logger.exception("Failed to fetch TIDAL playlist tracks")
            return []
