from __future__ import annotations

import functools
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.gui.workers import run_in_background
from src.services.spotify import Spotify
from src.services.tidal import Tidal


# ---- simple data holders ----
@dataclass
class TrackState:
    index: int
    sp_item: dict  # Spotify API track item dict
    widget: QWidget
    progress_bar: QProgressBar
    tidal_label: QLabel
    status_label: QLabel
    matched_track_id: Optional[int] = None
    progress: int = 0


@dataclass
class PlaylistState:
    playlist: dict  # Spotify playlist dict
    list_item: QListWidgetItem
    list_widget: QWidget
    name_label: QLabel
    progress_bar: QProgressBar
    container: QWidget  # right panel content container for this playlist
    tracks_area: QWidget  # child container where track widgets are added
    tracks_layout: QVBoxLayout
    tracks: List[TrackState] = field(default_factory=list)
    started: bool = False
    completed: bool = False
    tidal_playlist_id: Optional[str] = None


class PlaylistListItem(QWidget):
    def __init__(self, name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self.name_label = QLabel(name)
        self.name_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        layout.addWidget(self.name_label)
        layout.addWidget(self.progress)


class TrackItemWidget(QWidget):
    def __init__(self, sp_title: str, sp_artists: str, sp_album: str, sp_dur: str,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # top: progress + status
        top = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.status = QLabel("Queued")
        self.status.setStyleSheet("color: #666;")
        top.addWidget(self.progress, 1)
        top.addWidget(self.status, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(top)

        # bottom: two groups side-by-side
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        sp_group = QGroupBox("Spotify")
        sp_layout = QVBoxLayout(sp_group)
        sp_title_lbl = QLabel(f"<b>{sp_title}</b>")
        sp_title_lbl.setTextFormat(Qt.TextFormat.RichText)
        sp_artists_lbl = QLabel(sp_artists)
        sp_album_lbl = QLabel(sp_album)
        sp_dur_lbl = QLabel(sp_dur)
        for w in (sp_title_lbl, sp_artists_lbl, sp_album_lbl, sp_dur_lbl):
            w.setWordWrap(True)
            sp_layout.addWidget(w)

        td_group = QGroupBox("TIDAL")
        td_layout = QVBoxLayout(td_group)
        self.td_label = QLabel("Pending…")
        self.td_label.setWordWrap(True)
        self.td_label.setTextFormat(Qt.TextFormat.RichText)
        td_layout.addWidget(self.td_label)

        bottom.addWidget(sp_group, 1)
        bottom.addWidget(td_group, 1)
        outer.addLayout(bottom)

        self.setObjectName("TrackItem")
        self.setStyleSheet(
            "#TrackItem { border: 1px solid #ddd; border-radius: 6px; }"
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spoti2Tidal – Playlist Sync")
        self.resize(1200, 800)

        # services
        self.spotify = Spotify()
        self.tidal = Tidal()

        # worker pool
        self.pool = QThreadPool.globalInstance()
        # Allow parallel track matching
        try:
            # Keep modest concurrency to avoid API rate limits
            self.pool.setMaxThreadCount(6)
        except Exception:
            pass

        # UI
        self._build_menu()
        self._build_ui()

        # state
        self.playlists: Dict[str, PlaylistState] = {}

        # kick off
        self._load_spotify_playlists()

    # ---- UI scaffold ----
    def _build_menu(self):
        bar = self.menuBar()
        acct = bar.addMenu("Account")
        self.act_tidal_login = QAction("Connect TIDAL (PKCE)", self)
        self.act_tidal_login.triggered.connect(self._handle_tidal_login)
        acct.addAction(self.act_tidal_login)

    def _build_ui(self):
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # Left: sidebar playlists list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)

        self.btn_reload = QPushButton("Reload Spotify Playlists")
        self.btn_reload.clicked.connect(self._load_spotify_playlists)
        left_layout.addWidget(self.btn_reload)

        self.playlist_list = QListWidget()
        self.playlist_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.playlist_list.currentItemChanged.connect(self._on_playlist_selected)
        self.playlist_list.setSpacing(6)
        left_layout.addWidget(self.playlist_list, 1)

        splitter.addWidget(left)

    # Right: scroll area for tracks of selected playlist
        self.right_stack = QWidget()
        self.right_layout = QVBoxLayout(self.right_stack)
        self.right_layout.setContentsMargins(8, 8, 8, 8)
        self.right_layout.setSpacing(8)
        # Placeholder
        self.placeholder = QLabel("Select a playlist to start syncing.")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color: #666; font-size: 14px;")
        self.right_layout.addWidget(self.placeholder, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.right_stack)

        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    # ---- Actions ----
    def _handle_tidal_login(self):
        if self.tidal.ensure_logged_in():
            QMessageBox.information(self, "TIDAL", "Already logged in to TIDAL.")
            return
        url = self.tidal.get_pkce_login_url()
        QApplication.clipboard().setText(url)
        QMessageBox.information(
            self,
            "TIDAL Login",
            "A PKCE login URL has been copied to your clipboard.\n"
            "Open it in a browser, complete login, then copy the final redirect URL and paste it in the next dialog.",
        )
        ok = False
        from PyQt6.QtWidgets import QInputDialog

        redirected, ok = QInputDialog.getText(
            self,
            "Complete TIDAL Login",
            "Paste the final redirect URL after logging in:",
        )
        if ok and redirected:
            try:
                success = self.tidal.complete_pkce_login(redirected)
                if success:
                    QMessageBox.information(self, "TIDAL", "Logged in successfully.")
                else:
                    QMessageBox.warning(self, "TIDAL", "Login failed.")
            except Exception as e:
                QMessageBox.critical(self, "TIDAL", f"Login error: {e}")

    # ---- Spotify playlists ----
    def _load_spotify_playlists(self):
        self.playlist_list.clear()
        self.playlists.clear()

        def on_done(items: List[dict]):
            if not items:
                QMessageBox.information(self, "Spotify", "No playlists found or not authenticated.")
                return
            for pl in items:
                pid = pl.get("id")
                name = pl.get("name") or pid
                widget = PlaylistListItem(name)
                item = QListWidgetItem(self.playlist_list)
                item.setSizeHint(widget.sizeHint())
                self.playlist_list.addItem(item)
                self.playlist_list.setItemWidget(item, widget)

                # right-side container for this playlist
                container = QWidget()
                c_layout = QVBoxLayout(container)
                c_layout.setContentsMargins(0, 0, 0, 0)
                c_layout.setSpacing(8)
                header_row = QHBoxLayout()
                hdr = QLabel(f"<h2>{name}</h2>")
                hdr.setTextFormat(Qt.TextFormat.RichText)
                header_row.addWidget(hdr, 1)
                btn_sync = QPushButton("Sync to TIDAL")
                btn_sync.setToolTip("Create a TIDAL playlist and add all matched tracks")
                # bind handler later when we have state dict key
                header_row.addWidget(btn_sync, 0)
                c_layout.addLayout(header_row)

                tracks_area = QWidget()
                tracks_layout = QVBoxLayout(tracks_area)
                tracks_layout.setContentsMargins(0, 0, 0, 0)
                tracks_layout.setSpacing(8)
                c_layout.addWidget(tracks_area)
                c_layout.addStretch(1)

                self.playlists[pid] = PlaylistState(
                    playlist=pl,
                    list_item=item,
                    list_widget=widget,
                    name_label=widget.name_label,
                    progress_bar=widget.progress,
                    container=container,
                    tracks_area=tracks_area,
                    tracks_layout=tracks_layout,
                )
                # Connect button now that state exists
                btn_sync.clicked.connect(functools.partial(self._transfer_to_tidal, pid))

            # Select first by default
            if self.playlist_list.count() > 0:
                self.playlist_list.setCurrentRow(0)

        def on_error(e: Exception):
            QMessageBox.critical(self, "Spotify", f"Failed to load playlists: {e}")

        # Ensure we have user to set market, etc.
        def fetch_playlists(progress_callback=None):
            try:
                self.spotify.get_user()
            except Exception:
                pass
            return self.spotify.get_user_playlists(progress_callback=progress_callback)

        run_in_background(
            self.pool,
            fetch_playlists,
            on_done=on_done,
            on_error=on_error,
            on_progress=None,  # per requirement, no global bar; we could animate list later
        )

    def _on_playlist_selected(self, current: QListWidgetItem, previous: Optional[QListWidgetItem]):
        if not current:
            return
        # find playlist id by matching item
        pid = None
        for k, st in self.playlists.items():
            if st.list_item is current:
                pid = k
                break
        if not pid:
            # match by pointer equality; fallback: index
            row = self.playlist_list.currentRow()
            keys = list(self.playlists.keys())
            if 0 <= row < len(keys):
                pid = keys[row]
        if not pid:
            return

        # swap in this playlist container
        # clear right_layout and insert this container
        while self.right_layout.count():
            item = self.right_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self.right_layout.addWidget(self.playlists[pid].container, 1)

        # kick off sync if not started
        if not self.playlists[pid].started:
            self._start_playlist_sync(pid)

    # ---- per-playlist flow ----
    def _start_playlist_sync(self, playlist_id: str):
        st = self.playlists[playlist_id]
        st.started = True
        st.progress_bar.setFormat("Fetching tracks… %p%")

        def on_tracks_done(items: List[dict]):
            # Build track widgets
            for idx, it in enumerate(items):
                track = it.get("track") or {}
                name = track.get("name", "<unknown>")
                artists = ", ".join(a.get("name") for a in (track.get("artists") or []) if a)
                album = (track.get("album") or {}).get("name", "")
                dur_ms = track.get("duration_ms") or 0
                dur_txt = self._fmt_duration(dur_ms)

                tw = TrackItemWidget(name, artists, album, dur_txt)
                st.tracks_layout.addWidget(tw)
                tstate = TrackState(
                    index=idx,
                    sp_item=it,
                    widget=tw,
                    progress_bar=tw.progress,
                    tidal_label=tw.td_label,
                    status_label=tw.status,
                )
                st.tracks.append(tstate)

            st.progress_bar.setFormat("Matching tracks… %p%")
            # Begin matching concurrently
            for tstate in st.tracks:
                self._match_track_async(playlist_id, tstate)

        def on_tracks_progress(pct: int):
            # during fetch, reflect percent
            st.progress_bar.setValue(pct)

        def on_tracks_error(e: Exception):
            QMessageBox.critical(self, "Spotify", f"Failed to fetch tracks: {e}")
            st.started = False

        run_in_background(
            self.pool,
            functools.partial(self.spotify.get_playlist_tracks, playlist_id),
            on_done=on_tracks_done,
            on_error=on_tracks_error,
            on_progress=on_tracks_progress,
        )

    # ---- transfer to TIDAL ----
    def _transfer_to_tidal(self, playlist_id: str):
        st = self.playlists.get(playlist_id)
        if not st:
            return
        # collect matched ids
        ids = [t.matched_track_id for t in st.tracks if t.matched_track_id]
        if not ids:
            QMessageBox.information(self, "Transfer", "No matched tracks to transfer yet.")
            return
        if not self.tidal.ensure_logged_in():
            QMessageBox.warning(self, "TIDAL", "Please connect your TIDAL account first (Account > Connect TIDAL).")
            return

        name = st.playlist.get("name") or "From Spotify"
        st.progress_bar.setFormat("Transferring… %p%")
        st.progress_bar.setValue(0)

        def do_transfer(progress_callback=None) -> Tuple[bool, Optional[str]]:
            # Create playlist if needed
            if not st.tidal_playlist_id:
                created = self.tidal.create_playlist(name, description="Imported from Spotify")
                if not created:
                    return False, "Failed to create TIDAL playlist"
                st.tidal_playlist_id = getattr(created, "id", None)
            pid = st.tidal_playlist_id
            if not pid:
                return False, "No TIDAL playlist id"
            # Add in batches with progress
            batch_size = 50
            total = len(ids)
            added = 0
            from math import ceil

            for i in range(0, total, batch_size):
                batch = ids[i:i+batch_size]
                ok = self.tidal.add_tracks_to_playlist(pid, batch)
                added += len(batch) if ok else 0
                if progress_callback:
                    progress_callback(min(99, int(added / total * 100)))
            if progress_callback:
                progress_callback(100)
            return True, None

        def on_done(res: Tuple[bool, Optional[str]]):
            ok, err = res
            if ok:
                QMessageBox.information(self, "Transfer", "Playlist transferred to TIDAL.")
                st.progress_bar.setFormat("Completed 100%")
                st.progress_bar.setValue(100)
            else:
                QMessageBox.critical(self, "Transfer", err or "Transfer failed")

        def on_progress(pct: int):
            st.progress_bar.setValue(pct)

        def on_error(e: Exception):
            QMessageBox.critical(self, "Transfer", f"Error: {e}")

        run_in_background(
            self.pool,
            do_transfer,
            on_done=on_done,
            on_error=on_error,
            on_progress=on_progress,
        )

    def _update_playlist_progress(self, playlist_id: str):
        st = self.playlists[playlist_id]
        if not st.tracks:
            return
        total = len(st.tracks)
        done = sum(1 for t in st.tracks if t.progress >= 100)
        # If matching in-progress, average of per-track progress is more informative
        avg = int(sum(t.progress for t in st.tracks) / max(1, total))
        st.progress_bar.setValue(avg)
        if done == total:
            st.progress_bar.setFormat("Completed 100%")
            st.completed = True

    # ---- per-track matching ----
    def _match_track_async(self, playlist_id: str, tstate: TrackState):
        sp_track = tstate.sp_item.get("track") or {}
        name = sp_track.get("name")
        artists = sp_track.get("artists") or []
        duration_ms = sp_track.get("duration_ms")
        isrc = (sp_track.get("external_ids") or {}).get("isrc")
        album = (sp_track.get("album") or {}).get("name")

        # matching wrapper with pseudo-progress milestones
        def do_match(progress_callback=None) -> Tuple[Optional[int], Optional[str]]:
            # Ensure TIDAL session if possible (won't block)
            try:
                self.tidal.ensure_logged_in()
            except Exception:
                pass

            if progress_callback:
                progress_callback(5)

            best = self.tidal.resolve_best_match(
                isrc=isrc, name=name, artists=artists, duration_ms=duration_ms, album=album
            )
            if progress_callback:
                progress_callback(100 if best else 100)
            if best is None:
                return None, None
            tid = getattr(best, "id", None)
            # Build nice label
            td_name = getattr(best, "name", "") or getattr(best, "full_name", "")
            td_artists = ", ".join(getattr(a, "name", "") for a in getattr(best, "artists", []) if a)
            td_album = getattr(getattr(best, "album", None), "name", "")
            # Format like Spotify group: bold title + lines for artists, album, duration
            try:
                dur_s = int(getattr(best, "duration", 0) or 0)
            except Exception:
                dur_s = 0
            dur_txt = MainWindow._fmt_duration(dur_s * 1000)
            label = (
                f"<b>{td_name}</b><br>"
                f"{td_artists}<br>"
                f"{td_album}<br>"
                f"{dur_txt}"
            )
            return int(tid) if tid is not None else None, label

        def on_done(res: Tuple[Optional[int], Optional[str]]):
            tid, label = res
            if tid is None:
                tstate.tidal_label.setText("No match found")
                tstate.tidal_label.setStyleSheet("color: #b00;")
                tstate.status_label.setText("No match")
            else:
                tstate.matched_track_id = tid
                tstate.tidal_label.setText(label or "Matched")
                tstate.tidal_label.setStyleSheet("")
                tstate.status_label.setText("Matched")
            tstate.progress = 100
            tstate.progress_bar.setValue(100)
            self._update_playlist_progress(playlist_id)

        def on_progress(pct: int):
            tstate.progress = pct
            tstate.progress_bar.setValue(pct)
            if pct < 100:
                tstate.status_label.setText(f"Matching… {pct}%")
            self._update_playlist_progress(playlist_id)

        def on_error(e: Exception):
            tstate.tidal_label.setText(f"Error: {e}")
            tstate.tidal_label.setStyleSheet("color: #b00;")
            tstate.status_label.setText("Error")
            tstate.progress = 100
            tstate.progress_bar.setValue(100)
            self._update_playlist_progress(playlist_id)

        run_in_background(
            self.pool,
            do_match,
            on_done=on_done,
            on_error=on_error,
            on_progress=on_progress,
        )

    # ---- helpers ----
    @staticmethod
    def _fmt_duration(ms: int) -> str:
        try:
            total_s = int(round((ms or 0) / 1000))
            m = total_s // 60
            s = total_s % 60
            return f"{m}:{s:02d}"
        except Exception:
            return "-:--"


__all__ = ["MainWindow"]
