from pathlib import Path
import webbrowser
import tidalapi
from platformdirs import user_config_dir
from PyQt6.QtCore import QThread, pyqtSignal
from typing import List

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
        self.logger = logger
        self.session_file = Path(session_file) if session_file else DEFAULT_SESSION_FILE
        self.session_file.parent.mkdir(parents=True, exist_ok=True)

        # Try to load an existing session; if invalid, stay unauthenticated until user completes PKCE
        self._load_session_silent()

    def _load_session_silent(self) -> bool:
        try:
            print(f"Loading session from {self.session_file}")
            loaded = self.session.load_session_from_file(self.session_file)
            if loaded and self.session.check_login():
                return True
        except Exception:
            pass
        return False

    # ---- PKCE login helpers ----
    def get_pkce_login_url(self) -> str:
        return self.session.pkce_login_url()

    def open_browser_login(self) -> str:
        url = self.get_pkce_login_url()
        try:
            webbrowser.open(url)
        except Exception:
            pass
        self.logger(
            "Opened TIDAL login in your browser. If it didn't open, use the URL below:"
        )
        self.logger(url)
        self.logger(
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
            self.logger(f"Failed to save TIDAL session to {self.session_file}: {e}")

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
        tracks = []
        try:
            total = self.session.user.favorites.get_tracks_count()
        except Exception:
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
        return tracks

    def get_playlist(self, playlist_id) -> tidalapi.playlist.UserPlaylist:
        return self.session.playlist(playlist_id)

    def get_playlist_tracks(
        self, playlist_id, progress_callback=None, page_limit: int = 100
    ) -> List[tidalapi.media.Track]:
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
        return tracks

    def get_track(self, track_id) -> tidalapi.media.Track:
        return self.session.track(track_id)
