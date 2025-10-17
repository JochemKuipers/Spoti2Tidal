from pathlib import Path
import webbrowser
import tidalapi
from platformdirs import user_config_dir
from PyQt6.QtCore import QThread, pyqtSignal
from typing import List, Optional
import logging
import re
import time
import os
import random
from threading import Semaphore

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

        # Simple rate limiting primitives to avoid HTTP 429
        try:
            max_conc = int(os.getenv("TIDAL_MAX_CONCURRENCY", "5"))
        except Exception:
            max_conc = 5
        try:
            self._per_request_delay = float(os.getenv("TIDAL_REQUEST_DELAY_SEC", "0.15"))
        except Exception:
            self._per_request_delay = 0.15
        try:
            self._max_retries = int(os.getenv("TIDAL_MAX_RETRIES", "4"))
        except Exception:
            self._max_retries = 4
        self._search_sem = Semaphore(max(1, max_conc))

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
        self, progress_callback=None
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
        # Rate-limited call with retries/backoff
        def _call():
            # Gentle pacing between calls to avoid bursts
            if self._per_request_delay > 0:
                time.sleep(self._per_request_delay)
            return self.session.search(query=query, models=[tidalapi.media.Track], limit=limit)

        # Acquire concurrency slot
        self._search_sem.acquire()
        try:
            delay = 0.5
            for attempt in range(1, self._max_retries + 1):
                try:
                    results = _call()
                    # Normalize shape
                    if isinstance(results, list):
                        tracks = results
                    elif isinstance(results, dict) and "tracks" in results:
                        tracks = results.get("tracks") or []
                    else:
                        tracks = getattr(results, "tracks", []) or []
                    self.logger.info(f"Found {len(tracks)} TIDAL tracks for search: {query}")
                    return tracks
                except Exception as e:
                    # Heuristic: if it's a 429 or rate-related, back off and retry
                    msg = str(e).lower()
                    if "429" in msg or "too many" in msg or "rate" in msg:
                        self.logger.warning(f"TIDAL rate limited (attempt {attempt}/{self._max_retries}); backing off…")
                        time.sleep(delay + random.uniform(0, 0.25))
                        delay = min(8.0, delay * 2)
                        continue
                    # Other errors: log and break
                    self.logger.exception("TIDAL search failed")
                    break
        finally:
            self._search_sem.release()
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
        self, name: str, artists: list | str
    ) -> List[tidalapi.media.Track]:
        self.logger.info(
            f"Searching TIDAL for tracks by name and artist(s): {name} | {artists}"
        )
        if not name:
            return []
        if not artists:
            return self._search_tracks(name, limit=25)

        # Normalize artists to a list of strings
        if isinstance(artists, str):
            artists_list = [artists]
        else:
            artists_list = [
                a.get("name") if isinstance(a, dict) else a.strip() for a in artists
            ]
        artists_list = [a for a in artists_list if a]  # remove empty

        all_results: List[tidalapi.media.Track] = []
        seen_ids = set()

        # 1. Perform one query for each single artist
        for artist_name in artists_list:
            sub_query = f"{name} {artist_name}"
            results = self._search_tracks(sub_query, limit=25)
            for t in results:
                tid = getattr(t, "id", None)
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                all_results.append(t)

        # 2. Also perform a query with all artists together (if more than one), e.g. "name artist1 artist2 ..."
        if len(artists_list) > 1:
            combined_artists = " ".join(artists_list)
            sub_query = f"{name} {combined_artists}"
            results = self._search_tracks(sub_query, limit=25)
            for t in results:
                tid = getattr(t, "id", None)
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                all_results.append(t)

        self.logger.info(
            f"Found {len(all_results)} TIDAL tracks across progressive artist queries for: {name} | {artists}"
        )
        return all_results

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

    # ---- matching utilities ----
    @staticmethod
    def _normalize_text(text: str) -> str:
        try:
            # Only strip feat/with patterns, don't remove content in parentheses/brackets unconditionally
            text = re.sub(
                r"\s*[\[(]\s*(feat\.?|with\.?)\s.*?[\])]", "", text, flags=re.IGNORECASE
            ).strip()
            text = re.sub(
                r"\s+(feat\.?|with\.?)\s.*$", "", text, flags=re.IGNORECASE
            ).strip()
        except Exception as e:
            logging.getLogger(__name__).error(f"Error cleaning name: {e}")
            raise e
        return text

    @staticmethod
    def _token_set(text: str) -> set:
        return set(Tidal._normalize_text(text).split())

    @staticmethod
    def _duration_score(sp_ms: Optional[int], td_seconds: Optional[int]) -> int:
        if not sp_ms or td_seconds is None:
            return 0
        sp_s = int(round(sp_ms / 1000))
        delta = abs(sp_s - int(td_seconds))
        if delta <= 2:
            return 30
        if delta <= 5:
            return 20
        if delta <= 10:
            return 10
        return -30

    @staticmethod
    def _title_score(sp_name: str, td_name: str) -> int:
        sp_n = Tidal._normalize_text(sp_name)
        td_n = Tidal._normalize_text(td_name)
        if not sp_n or not td_n:
            return 0
        if sp_n == td_n:
            return 50
        sp_tokens = set(sp_n.split())
        td_tokens = set(td_n.split())
        overlap = len(sp_tokens & td_tokens)
        if overlap >= max(1, int(0.6 * len(sp_tokens))):
            return 30
        if overlap >= max(1, int(0.4 * len(sp_tokens))):
            return 15
        return 0

    @staticmethod
    def _artist_score(sp_artists, td_artists_list) -> int:
        # sp_artists may be a string, list[str], or list[dict{name}]
        if not sp_artists:
            return 0
        if isinstance(sp_artists, list):
            try:
                names = [
                    (a if isinstance(a, str) else a.get("name", "")) for a in sp_artists
                ]
                sp_joined = ", ".join([n for n in names if n])
            except Exception:
                sp_joined = ""
        else:
            sp_joined = sp_artists
        sp_tokens = Tidal._token_set(sp_joined)
        td_names = ", ".join(getattr(a, "name", "") for a in (td_artists_list or []))
        td_tokens = Tidal._token_set(td_names)
        if not td_tokens:
            return 0
        overlap = len(sp_tokens & td_tokens)
        if overlap == 0:
            return -40
        frac = overlap / max(1, len(sp_tokens))
        if frac >= 0.66:
            return 40
        if frac >= 0.4:
            return 25
        return 10

    def resolve_best_match(
        self,
        *,
        isrc: Optional[str],
        name: str,
        artists: Optional[list | str],
        duration_ms: Optional[int] = None,
        album: Optional[str] = None,
    ) -> Optional[tidalapi.media.Track]:
        self.logger.info(
            f"Resolving best match for ISRC: {isrc}, name: {name}, artists: {artists}"
        )
        # Use normalized forms for search queries; keep originals for scoring/display
        search_name = self._normalize_text(name)
        candidates: List[tidalapi.media.Track] = []
        if isrc:
            candidates = self.search_by_isrc(isrc)
        # Always include name-based candidates
        name_candidates: List[tidalapi.media.Track] = self.search_by_name(search_name)
        if name_candidates:
            candidates.extend(name_candidates)
        # Always include name+artist candidates when artist is available
        if artists:
            name_artist_candidates = self.search_by_name_artist(search_name, artists)
            if name_artist_candidates:
                candidates.extend(name_artist_candidates)
        # Try including album keyword to disambiguate
        if not candidates and album:
            more = self._search_tracks(f"{search_name} {self._normalize_text(album)}", limit=25)
            if more:
                candidates.extend(more)
        # de-duplicate by id
        seen = set()
        uniq = []
        for c in candidates:
            cid = getattr(c, "id", None)
            if cid in seen:
                continue
            seen.add(cid)
            uniq.append(c)
        candidates = uniq

        if not candidates:
            self.logger.info("No candidates found")
            return None

        for c in candidates:
            if getattr(c, "isrc", None) and isrc and c.isrc == isrc:
                self.logger.info("Selected by exact ISRC match")
                return c

        best_track = None
        best_score = -(10**9)
        for c in candidates:
            c_name = getattr(c, "name", "") or getattr(c, "full_name", "")
            score = 0
            score += self._title_score(name, c_name)
            a_score = self._artist_score(artists or "", getattr(c, "artists", []))
            score += a_score
            score += self._duration_score(duration_ms, getattr(c, "duration", None))
            score += {3: 5, 2: 3, 1: 0}.get(self._quality_rank(c), 0)

            # Hard reject only if artist clearly mismatches
            if a_score < -30:
                continue
            if score > best_score:
                best_score = score
                best_track = c

        if best_track is None:
            self.logger.info("No viable scored candidates")
            return None
        # Adaptive threshold: if exact normalized title and duration is close, allow lower
        exact_title = self._normalize_text(name) == self._normalize_text(
            getattr(best_track, "name", "") or getattr(best_track, "full_name", "")
        )
        duration_close = self._duration_score(
            duration_ms, getattr(best_track, "duration", None)
        ) >= 20
        threshold = 30
        if exact_title and duration_close:
            threshold = 15
        if best_score < threshold:
            self.logger.info(f"Best score {best_score} below threshold {threshold}; no match")
            return None

        self.logger.info(f"Best match score {best_score}: {best_track}")
        return best_track

    # ---- playlist management ----
    def create_playlist(
        self, name: str, description: str = ""
    ) -> Optional[tidalapi.playlist.UserPlaylist]:
        self.logger.info(f"Creating TIDAL playlist: {name}")
        try:
            return self.session.user.create_playlist(title=name, description=description)
        except Exception as e:
            self.logger.exception(f"Failed to create TIDAL playlist {name}: {e}")
            return None

    def add_tracks_to_playlist(self, playlist_id: str, track_ids: List[str]) -> bool:
        self.logger.info(
            f"Adding {len(track_ids)} tracks to TIDAL playlist {playlist_id}"
        )
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
        except Exception as e:
            self.logger.exception(f"Failed to add tracks to TIDAL playlist: {e}")
            return False

    def get_playlist_track_ids(self, playlist_id: str) -> List[int]:
        self.logger.info(
            f"Fetching TIDAL playlist track IDs for playlist {playlist_id}"
        )
        try:
            tracks = self.get_playlist_tracks(playlist_id)
            return [int(getattr(t, "id", -1)) for t in tracks if getattr(t, "id", None)]
        except Exception as e:
            self.logger.exception(f"Failed to fetch TIDAL playlist tracks: {e}")
            return []
