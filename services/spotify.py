from __future__ import annotations
import spotipy
import spotipy.oauth2
from PyQt6.QtCore import QThread, pyqtSignal
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from platformdirs import user_config_dir
import os
from dotenv import load_dotenv
from typing import Any, List
import logging
from models.spotify import SpotifyPlaylist, SpotifyTrack
import time
import random

load_dotenv()

CACHE_DIR = Path(user_config_dir("Spoti2Tidal")) / "spotify_cache"
CACHE_FILE = CACHE_DIR / "spotify_cache.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class TrackFetchWorker(QThread):
    """Worker thread for fetching a batch of tracks at a specific offset"""

    finished = pyqtSignal(list, int)  # tracks, offset
    error = pyqtSignal(str, int)  # error message, offset

    def __init__(self, sp_client, offset, limit=50, market="NL"):
        super().__init__()
        self.sp_client = sp_client
        self.offset = offset
        self.limit = limit
        self.market = market

    def run(self):
        try:
            response = self.sp_client.current_user_saved_tracks(
                limit=self.limit, offset=self.offset, market=self.market
            )
            self.finished.emit(response["items"], self.offset)
        except Exception as e:
            self.error.emit(str(e), self.offset)


class PlaylistTrackFetchWorker(QThread):
    """Worker thread for fetching a batch of tracks at a specific offset"""

    finished = pyqtSignal(list, int)
    error = pyqtSignal(str, int)

    def __init__(self, sp_client, playlist_id, offset, limit=50, market="NL"):
        super().__init__()
        self.sp_client = sp_client
        self.playlist_id = playlist_id
        self.offset = offset
        self.limit = limit
        self.market = market

    def run(self):
        try:
            response = self.sp_client.playlist_tracks(
                self.playlist_id,
                limit=self.limit,
                offset=self.offset,
                market=self.market,
            )
            self.finished.emit(response["items"], self.offset)
        except Exception as e:
            self.error.emit(str(e), self.offset)


class Spotify:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.client_id = os.getenv("SPOTIPY_CLIENT_ID")
        self.client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
        self.scope = [
            "user-library-read",
            "playlist-read-private",
            "playlist-read-collaborative",
        ]
        self.auth_manager = spotipy.oauth2.SpotifyOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            scope=self.scope,
            redirect_uri="http://127.0.0.1:3000/callback",
            cache_path=CACHE_FILE,
        )
        self.sp = spotipy.Spotify(auth_manager=self.auth_manager)
        self.market = "NL"  # Default market, will be updated when user is fetched

    def get_client(self) -> spotipy.Spotify:
        return self.sp

    def get_user(self) -> Any | None:
        self.logger.info("Fetching Spotify current user")
        user = self.sp.current_user()
        # Update market based on user's country
        if user and "country" in user:
            self.market = user["country"] or "NL"
        return user

    def get_user_playlists(self, progress_callback=None) -> List[SpotifyPlaylist]:
        self.logger.info("Fetching Spotify user playlists")
        playlists = []
        response = self.sp.current_user_playlists()
        total = response.get("total", 0) or 0

        # Get current user id
        self.logger.info("Fetching current Spotify user")
        current_user = self.sp.current_user()
        self.logger.info(f"Current Spotify user: {current_user}")
        current_user_id = current_user.get("id") if current_user else None

        # include first page, only keep playlists owned by current user
        page_items = response.get("items", [])
        self.logger.info(f"First page items: {page_items}")
        if current_user_id:
            page_items = [
                pl
                for pl in page_items
                if pl.get("owner", {}).get("id") == current_user_id
            ]
        playlists.extend(page_items)
        self.logger.info(f"Playlists after first page: {playlists}")
        if progress_callback and total > 0:
            self.logger.debug(f"Progress callback: {progress_callback(min(99, int(len(playlists) / total * 100)))}")
            progress_callback(min(99, int(len(playlists) / total * 100)))

        # paginate
        while response.get("next"):
            response = self.sp.next(response)
            page_items = response.get("items", [])
            self.logger.info(f"Page items: {page_items}")
            if current_user_id:
                page_items = [
                    pl
                    for pl in page_items
                    if pl.get("owner", {}).get("id") == current_user_id
                ]
            playlists.extend(page_items)
            self.logger.debug(f"Playlists after pagination: {playlists}")
            if progress_callback and total > 0:
                progress_callback(min(99, int(len(playlists) / total * 100)))

        if progress_callback:
            self.logger.debug(f"Progress callback: {progress_callback(100)}")
            progress_callback(100)
        return playlists

    def get_playlist_tracks(
        self, playlist_id, max_workers=5, progress_callback=None
    ) -> List[SpotifyTrack]:
        self.logger.info(f"Fetching Spotify tracks for playlist {playlist_id}")
        response = self.sp.playlist_items(playlist_id)
        self.logger.info(f"Response: {response}")
        total = response["total"]

        batch_size = 50
        num_batches = math.ceil(total / batch_size)
        self.logger.info(f"Number of batches: {num_batches}")
        results = {}

        def fetch_batch(offset):
            delay = 0.5
            max_retries = 4
            for attempt in range(1, max_retries + 1):
                try:
                    # Gentle pacing to reduce burst traffic
                    time.sleep(0.1)
                    res = self.sp.playlist_items(
                        playlist_id, limit=50, offset=offset, market=self.market
                    )
                    return offset, res["items"], None
                except Exception as e:
                    msg = str(e).lower()
                    if "429" in msg or "too many" in msg or "rate" in msg:
                        self.logger.warning(
                            f"Spotify rate limited on offset {offset} (attempt {attempt}/{max_retries}); backing off…"
                        )
                        time.sleep(delay + random.uniform(0, 0.25))
                        delay = min(8.0, delay * 2)
                        continue
                    self.logger.exception("Failed to fetch Spotify playlist batch")
                    return offset, [], str(e)
            return offset, [], "rate limited"

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch_batch, batch_num * batch_size): batch_num
                for batch_num in range(num_batches)
            }
            self.logger.info(f"Submitted {len(futures)} futures for playlist tracks")

            completed = 0
            for future in as_completed(futures):
                offset, items, error = future.result()
                results[offset] = items
                completed += 1
                self.logger.info(f"Fetched {completed} of {num_batches} batches for playlist tracks")
                if progress_callback:
                    progress_callback(min(99, int(completed / num_batches * 100)))

        # Combine results in order
        tracks = []
        for batch_num in range(num_batches):
            offset = batch_num * batch_size
            if offset in results:
                tracks.extend(results[offset])

        if progress_callback:
            progress_callback(100)

        return tracks

    def get_user_tracks(
        self, max_workers=5, progress_callback=None
    ) -> List[SpotifyTrack]:
        self.logger.info("Fetching Spotify saved tracks")
        response = self.sp.current_user_saved_tracks(
            limit=50, offset=0, market=self.market
        )
        total = response["total"]

        batch_size = 50
        num_batches = math.ceil(total / batch_size)
        results = {}

        def fetch_batch(offset):
            try:
                response = self.sp.current_user_saved_tracks(
                    limit=50, offset=offset, market=self.market
                )
                return offset, response["items"], None
            except Exception as e:
                self.logger.exception("Failed to fetch Spotify saved tracks batch")
                return offset, [], str(e)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch_batch, batch_num * batch_size): batch_num
                for batch_num in range(num_batches)
            }

            completed = 0
            for future in as_completed(futures):
                offset, items, error = future.result()
                if error:
                    self.logger.error(f"Error fetching Spotify saved tracks batch at offset {offset}: {error}")
                    continue
                results[offset] = items
                completed += 1
                self.logger.info(f"Fetched {completed} of {num_batches} batches")
                if progress_callback:
                    progress_callback(min(99, int(completed / num_batches * 100)))

        # Combine results in order
        tracks = []
        for batch_num in range(num_batches):
            offset = batch_num * batch_size
            if offset in results:
                tracks.extend(results[offset])

        if progress_callback:
            progress_callback(100)

        return tracks

    def get_playlist(self, playlist_id) -> SpotifyPlaylist:
        return self.sp.playlist(playlist_id)
