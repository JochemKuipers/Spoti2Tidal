from __future__ import annotations
import logging
from typing import Any

from PyQt6 import uic
from PyQt6.QtCore import QThreadPool, Qt
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton

from src.gui.workers import run_in_background
from src.services.spotify import Spotify
from src.services.tidal import Tidal


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        uic.loadUi("src/gui/ui/main_window.ui", self)

        # Apply global dark theme
        self.setStyleSheet("""
            QMainWindow {
                background-color: #171717;
                color: #e6e6e6;
            }
            QFrame#sidebarFrame {
                background-color: #171717;
                border: 1px solid #232323;
            }
            QFrame#mainFrame {
                background-color: #1b1b1b;
                border: 1px solid #e5e7eb;
            }
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background-color: #2a2a2a;
                width: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background-color: #4a4a4a;
                border-radius: 6px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #6a6a6a;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        self.logger = logging.getLogger(__name__)
        self.thread_pool = QThreadPool.globalInstance()

        # Services
        self.spotify = Spotify()
        self.tidal = Tidal()

        # In-memory models for selection mapping
        self._spotify_playlists: list[object] = []
        self._tidal_playlists: list[object] = []
        self._current_playlist: object | None = None

        # Wire buttons - map UI elements to existing functionality
        self.transferButton.clicked.connect(self.on_sync_clicked)
        self.spotifySignInButton.clicked.connect(self.on_spotify_login)
        self.tidalSignInButton.clicked.connect(self.on_tidal_login)

        # Initial state
        self._update_auth_status()

        # Lazy-load initial data
        self._load_initial_data()

    # ---- UI helpers ----
    def set_progress(self, value: int) -> None:
        self.progressBar.setValue(max(0, min(100, int(value))))

    def _update_auth_status(self) -> None:
        # Check auth status and update UI accordingly
        try:
            sp_user = self.spotify.get_user()
            sp_logged_in = sp_user is not None
        except Exception:
            sp_logged_in = False

        try:
            tidal_logged_in = self.tidal.is_logged_in()
        except Exception:
            tidal_logged_in = False

        # Update sync button state
        self.transferButton.setEnabled(sp_logged_in and tidal_logged_in)
        
        # Update status labels
        self.spotifyStatusLabel.setText("Spotify: Signed in" if sp_logged_in else "Spotify: Signed out")
        self.tidalStatusLabel.setText("TIDAL: Signed in" if tidal_logged_in else "TIDAL: Signed out")

    # ---- Data loading ----
    def _load_initial_data(self) -> None:
        # Load playlists if possible; errors are non-fatal
        run_in_background(
            self.thread_pool,
            self.spotify.get_user_playlists,
            on_done=self.populate_sidebar_playlists,
            on_error=self._on_bg_error,
            on_progress=self.set_progress,
        )

    # ---- Slots ----
    def on_sync_clicked(self) -> None:
        if not self._current_playlist:
            QMessageBox.information(self, "Sync", "Select a playlist first.")
            return

        playlist_name = getattr(self._current_playlist, "name", None)
        playlist_id = getattr(self._current_playlist, "id", None)
        if not playlist_id:
            # dict fallback
            if isinstance(self._current_playlist, dict):
                playlist_id = self._current_playlist.get("id")
                playlist_name = playlist_name or self._current_playlist.get("name")
        if not playlist_id:
            QMessageBox.warning(self, "Sync", "Could not resolve playlist id.")
            return

        def do_transfer(progress_callback=None) -> dict:
            # Find existing TIDAL playlist with the same name; otherwise create
            if progress_callback:
                progress_callback(2)
            try:
                # refresh list to be safe
                tidal_playlists = self.tidal.get_user_playlists()
            except Exception:
                tidal_playlists = self._tidal_playlists

            target_name = str(playlist_name or "")
            target_lower = target_name.strip().lower()
            existing = None
            for p in tidal_playlists or []:
                nm = getattr(p, "name", None) or getattr(p, "title", None)
                if isinstance(nm, str) and nm.strip().lower() == target_lower:
                    existing = p
                    break

            if existing is not None:
                tidal_playlist_id = getattr(existing, "id", None)
                created_new = False
            else:
                tidal_playlist = self.tidal.create_playlist(target_name, "")
                if not tidal_playlist:
                    raise RuntimeError("Failed to create TIDAL playlist")
                tidal_playlist_id = getattr(tidal_playlist, "id", None)
                created_new = True

            # Fetch Spotify tracks for the selected playlist
            if progress_callback:
                progress_callback(5)
            sp_items = self.spotify.get_playlist_tracks(playlist_id, progress_callback=progress_callback)

            # Resolve and collect TIDAL track IDs
            track_ids: list[str] = []
            total = max(1, len(sp_items))
            for idx, item in enumerate(sp_items):
                try:
                    # item may be API dict or SpotifyTrack
                    if hasattr(item, "id") and hasattr(item, "name"):
                        name = getattr(item, "name")
                        artists = getattr(item, "artists", None)
                        duration_ms = getattr(item, "duration_ms", None)
                        album = getattr(item, "album", None)
                        external_ids = getattr(item, "external_ids", None)
                        isrc = external_ids.get("isrc") if isinstance(external_ids, dict) else None
                    else:
                        track = item.get("track", item)
                        name = track.get("name")
                        artists = track.get("artists")
                        duration_ms = track.get("duration_ms")
                        album = track.get("album", {}).get("name") if isinstance(track.get("album"), dict) else None
                        external_ids = track.get("external_ids", {})
                        isrc = external_ids.get("isrc")

                    artist_names = [a.get("name") for a in artists] if isinstance(artists, list) else artists
                    tidal_track = self.tidal.resolve_best_match(
                        isrc=isrc,
                        name=name,
                        artists=artist_names,
                        duration_ms=duration_ms,
                        album=album,
                    )
                    if tidal_track is not None:
                        tid = getattr(tidal_track, "id", None)
                        if tid is not None:
                            track_ids.append(str(tid))
                except Exception:
                    # Keep going on individual track failures
                    pass

                if progress_callback:
                    progress_callback(5 + int((idx + 1) / total * 85))

            # Add to playlist
            ok = self.tidal.add_tracks_to_playlist(str(tidal_playlist_id), track_ids)
            if not ok:
                raise RuntimeError("Failed to add tracks to TIDAL playlist")

            if progress_callback:
                progress_callback(100)
            return {"created": created_new, "playlist": tidal_playlist_id, "added": len(track_ids)}

        def done(result: dict) -> None:
            self.statusLabel.setText("Sync completed")
            QMessageBox.information(self, "Sync", f"Sync completed: {result['added']} tracks added")

        self.statusLabel.setText("Syncing...")
        run_in_background(
            self.thread_pool,
            do_transfer,
            on_done=done,
            on_error=self._on_bg_error,
            on_progress=self.set_progress,
        )

    def on_external_link(self) -> None:
        if self._current_playlist:
            external_urls = getattr(self._current_playlist, "external_urls", None)
            if external_urls and isinstance(external_urls, dict):
                spotify_url = external_urls.get("spotify")
                if spotify_url:
                    import webbrowser
                    webbrowser.open(spotify_url)

    # ---- Populate views ----
    def populate_sidebar_playlists(self, playlists: Any) -> None:
        self._spotify_playlists = list(playlists or [])
        
        # Populate the Spotify playlists view
        from PyQt6.QtCore import QStringListModel
        model = QStringListModel()
        playlist_names = []
        for playlist in self._spotify_playlists:
            name = getattr(playlist, "name", None) or (playlist.get("name") if isinstance(playlist, dict) else "Unknown")
            playlist_names.append(str(name))
        model.setStringList(playlist_names)
        self.spotifyPlaylistsView.setModel(model)
        
        # Connect selection to update current playlist
        self.spotifyPlaylistsView.selectionModel().selectionChanged.connect(self._on_playlist_selection_changed)

    def _on_playlist_selection_changed(self) -> None:
        """Handle playlist selection change"""
        selection = self.spotifyPlaylistsView.selectionModel().selectedIndexes()
        if selection:
            index = selection[0].row()
            if 0 <= index < len(self._spotify_playlists):
                self._current_playlist = self._spotify_playlists[index]
                self.statusLabel.setText(f"Selected: {getattr(self._current_playlist, 'name', 'Unknown')}")

    def on_spotify_login(self) -> None:
        """Handle Spotify login"""
        try:
            # Trigger Spotify OAuth flow
            self.spotify.get_user()
            self._update_auth_status()
            self._load_initial_data()
        except Exception as e:
            QMessageBox.warning(self, "Spotify Login", f"Failed to login to Spotify: {e}")

    def on_tidal_login(self) -> None:
        """Handle TIDAL login"""
        try:
            # For now, just show a message - PKCE flow would need a dialog
            QMessageBox.information(self, "TIDAL Login", "TIDAL login dialog not implemented yet. Please implement PKCE flow.")
        except Exception as e:
            QMessageBox.warning(self, "TIDAL Login", f"Failed to login to TIDAL: {e}")

    # ---- Errors ----
    def _on_bg_error(self, e: Exception) -> None:
        self.logger.exception("Background error", exc_info=e)
        QMessageBox.critical(self, "Error", str(e))


