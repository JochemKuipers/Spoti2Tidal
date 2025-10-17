from __future__ import annotations
from typing import List, Dict, Tuple
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QListWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QSplitter,
    QProgressBar,
    QDialog,
    QDialogButtonBox,
    QTextEdit,
)
from PyQt6.QtCore import Qt, QThreadPool
import logging

from src.services.spotify import Spotify
from src.services.tidal import Tidal
from src.models.spotify import SpotifyTrack, SpotifyPlaylist
from tidalapi.media import Track as TidalTrack
from tidalapi.playlist import UserPlaylist as TidalPlaylist
from src.gui.workers import run_in_background


class ConfirmDialog(QDialog):
    def __init__(self, parent: QWidget | None, text: str):
        super().__init__(parent)
        self.setWindowTitle("Confirm Transfer")
        layout = QVBoxLayout(self)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(text)
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spoti2Tidal")
        self.resize(1200, 800)
        self.logger = logging.getLogger(__name__)

        self.spotify = Spotify()
        self.tidal = Tidal()
        self.pool = QThreadPool.globalInstance()

        container = QWidget()
        root = QVBoxLayout(container)

        # Top controls
        top = QHBoxLayout()
        self.btn_sp_login = QPushButton("Connect Spotify")
        self.btn_td_login = QPushButton("Login TIDAL")
        self.status_label = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        top.addWidget(self.btn_sp_login)
        top.addWidget(self.btn_td_login)
        top.addStretch(1)
        top.addWidget(self.status_label)
        top.addWidget(self.progress)
        root.addLayout(top)

        # Tabs for Spotify and Tidal
        tabs = QTabWidget()
        tabs.addTab(self._build_spotify_tab(), "Spotify")
        tabs.addTab(self._build_tidal_tab(), "TIDAL")
        root.addWidget(tabs)

        # Transfer section
        transfer_bar = QHBoxLayout()
        self.btn_fetch = QPushButton("Fetch Playlists")
        self.btn_crossref = QPushButton("Cross-reference")
        self.btn_transfer = QPushButton("Transfer to TIDAL")
        transfer_bar.addWidget(self.btn_fetch)
        transfer_bar.addWidget(self.btn_crossref)
        transfer_bar.addWidget(self.btn_transfer)
        transfer_bar.addStretch(1)
        root.addLayout(transfer_bar)

        # Cross-reference results table
        self.xref_table = QTableWidget(0, 4)
        self.xref_table.setHorizontalHeaderLabels(
            ["Spotify", "Match (TIDAL)", "Quality", "Status"]
        )
        root.addWidget(self.xref_table)

        self.setCentralWidget(container)

        # Signals
        self.btn_sp_login.clicked.connect(self._connect_spotify)
        self.btn_td_login.clicked.connect(self._connect_tidal)
        self.btn_fetch.clicked.connect(self._fetch_all)
        self.btn_crossref.clicked.connect(self._cross_reference)
        self.btn_transfer.clicked.connect(self._transfer)

        # Data holders
        self.spotify_playlists: List[SpotifyPlaylist] = []
        self.tidal_playlists: List[TidalPlaylist] = []
        self.spotify_tracks_by_playlist: Dict[str, List[SpotifyTrack]] = {}
        self.tidal_tracks_by_playlist: Dict[str, List[TidalTrack]] = {}
        self.crossref_selection: List[Tuple[SpotifyTrack, TidalTrack]] = []

    def _build_spotify_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.sp_list = QListWidget()
        self.sp_tracks = QTableWidget(0, 4)
        self.sp_tracks.setHorizontalHeaderLabels(
            ["Name", "Artist", "Album", "Duration"]
        )
        splitter.addWidget(self.sp_list)
        splitter.addWidget(self.sp_tracks)
        layout.addWidget(splitter)
        self.sp_list.itemSelectionChanged.connect(
            self._load_spotify_tracks_for_selected
        )
        return page

    def _build_tidal_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.td_list = QListWidget()
        self.td_tracks = QTableWidget(0, 5)
        self.td_tracks.setHorizontalHeaderLabels(
            ["Name", "Artist", "Album", "Quality", "ID"]
        )
        splitter.addWidget(self.td_list)
        splitter.addWidget(self.td_tracks)
        layout.addWidget(splitter)
        self.td_list.itemSelectionChanged.connect(self._load_tidal_tracks_for_selected)
        return page

    # ---- actions ----
    def _connect_spotify(self):
        self.logger.info("Connect Spotify clicked")

        def work():
            return self.spotify.get_user()

        def done(user):
            try:
                self.status_label.setText(f"Spotify: {user.get('display_name', 'OK')}")
            except Exception:
                self.status_label.setText("Spotify connected")

        run_in_background(
            self.pool,
            work,
            done,
            on_error=lambda e: self.status_label.setText(f"Spotify login failed: {e}"),
        )

    def _connect_tidal(self):
        self.logger.info("Connect TIDAL clicked")

        def work():
            if not self.tidal.ensure_logged_in():
                self.tidal.open_browser_login()
                return None
            return self.tidal.get_user()

        def done(user):
            if user is None:
                self.status_label.setText("Complete TIDAL login in browser...")
            else:
                name = getattr(user, "name", "OK") if user else "OK"
                self.status_label.setText(f"TIDAL: {name}")

        run_in_background(
            self.pool,
            work,
            done,
            on_error=lambda e: self.status_label.setText(f"TIDAL login failed: {e}"),
        )

    def _fetch_all(self):
        self.logger.info("Fetching playlists for Spotify and TIDAL")
        self.progress.setValue(0)

        def sp_fetch(progress_callback=None):
            return self.spotify.get_user_playlists(progress_callback=progress_callback)

        def sp_done(pls):
            self.spotify_playlists = pls
            self.sp_list.clear()
            for pl in pls:
                name = (
                    pl.get("name") if isinstance(pl, dict) else getattr(pl, "name", "")
                )
                pid = pl.get("id") if isinstance(pl, dict) else getattr(pl, "id", "")
                self.sp_list.addItem(f"{name} ({pid})")
            self.progress.setValue(40)

        def sp_progress(p):
            self.progress.setValue(int(p * 0.4))

        run_in_background(self.pool, sp_fetch, sp_done, on_progress=sp_progress)

        def td_fetch(progress_callback=None):
            return self.tidal.get_user_playlists(progress_callback=progress_callback)

        def td_done(tpls):
            self.tidal_playlists = tpls
            self.td_list.clear()
            for pl in tpls:
                name = getattr(pl, "name", "")
                pid = getattr(pl, "id", "")
                self.td_list.addItem(f"{name} ({pid})")
            self.progress.setValue(80)

        def td_progress(p):
            self.progress.setValue(40 + int(p * 0.4))

        run_in_background(self.pool, td_fetch, td_done, on_progress=td_progress)

    def _load_spotify_tracks_for_selected(self):
        self.logger.debug("Loading Spotify tracks for selected playlist")
        row = self.sp_list.currentRow()
        if row < 0:
            return
        pl = self.spotify_playlists[row]
        pid = pl.get("id") if isinstance(pl, dict) else getattr(pl, "id", "")

        def fetch(progress_callback=None):
            return self.spotify.get_playlist_tracks(
                pid, progress_callback=progress_callback
            )

        def done(tracks):
            self.spotify_tracks_by_playlist[pid] = tracks
            self._populate_spotify_tracks(tracks)

        run_in_background(self.pool, fetch, done)

    def _populate_spotify_tracks(self, items: List[dict]):
        self.sp_tracks.setRowCount(0)
        for item in items:
            track = SpotifyTrack.from_api(item) if isinstance(item, dict) else item
            if not track:
                continue
            name = track.name
            artist = track.artists_names
            album = track.album_name
            duration = track.duration_formatted
            row = self.sp_tracks.rowCount()
            self.sp_tracks.insertRow(row)
            self.sp_tracks.setItem(row, 0, QTableWidgetItem(name))
            self.sp_tracks.setItem(row, 1, QTableWidgetItem(artist))
            self.sp_tracks.setItem(row, 2, QTableWidgetItem(album))
            self.sp_tracks.setItem(row, 3, QTableWidgetItem(duration))

    def _load_tidal_tracks_for_selected(self):
        self.logger.debug("Loading TIDAL tracks for selected playlist")
        row = self.td_list.currentRow()
        if row < 0:
            return
        pl = self.tidal_playlists[row]
        pid = getattr(pl, "id", "")

        def fetch(progress_callback=None):
            return self.tidal.get_playlist_tracks(
                pid, progress_callback=progress_callback
            )

        def done(tracks):
            self.tidal_tracks_by_playlist[pid] = tracks
            self._populate_tidal_tracks(tracks)

        run_in_background(self.pool, fetch, done)

    def _populate_tidal_tracks(self, items: List):
        self.td_tracks.setRowCount(0)
        for t in items:
            name = getattr(t, "name", "") or getattr(t, "full_name", "")
            artist = ", ".join(
                getattr(a, "name", "") for a in (getattr(t, "artists", []) or [])
            )
            album = getattr(getattr(t, "album", None), "name", "")
            quality = Tidal.quality_label(t)
            tid = str(getattr(t, "id", ""))
            row = self.td_tracks.rowCount()
            self.td_tracks.insertRow(row)
            self.td_tracks.setItem(row, 0, QTableWidgetItem(name))
            self.td_tracks.setItem(row, 1, QTableWidgetItem(artist))
            self.td_tracks.setItem(row, 2, QTableWidgetItem(album))
            self.td_tracks.setItem(row, 3, QTableWidgetItem(quality))
            self.td_tracks.setItem(row, 4, QTableWidgetItem(tid))

    def _cross_reference(self):
        self.logger.info("Starting cross-reference")
        self.status_label.setText("Cross-referencing...")
        # indeterminate progress until first update
        self.progress.setRange(0, 0)
        # disable buttons while running
        self.btn_crossref.setEnabled(False)
        self.btn_transfer.setEnabled(False)
        self.btn_fetch.setEnabled(False)
        row = self.sp_list.currentRow()
        if row < 0:
            self.status_label.setText("Select a Spotify playlist first")
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.btn_crossref.setEnabled(True)
            self.btn_transfer.setEnabled(True)
            self.btn_fetch.setEnabled(True)
            return
        pl = self.spotify_playlists[row]
        pid = pl.get("id") if isinstance(pl, dict) else getattr(pl, "id", "")

        def ensure_tracks():
            tr = self.spotify_tracks_by_playlist.get(pid)
            if not tr:
                tr = self.spotify.get_playlist_tracks(pid, progress_callback=None)
            return tr

        def work(progress_callback=None):
            tracks = ensure_tracks()
            matches: List[Tuple[SpotifyTrack, TidalTrack]] = []
            total = len(tracks)
            done_count = 0
            for item in tracks:
                sp = SpotifyTrack.from_api(item) if isinstance(item, dict) else item
                isrc = (sp.external_ids or {}).get("isrc") if sp.external_ids else None
                best = self.tidal.resolve_best_match(
                    isrc=isrc,
                    name=sp.name,
                    artists=sp.artists,
                    duration_ms=sp.duration_ms,
                )
                matches.append((sp, best))
                done_count += 1
                if progress_callback and total:
                    progress_callback(int(done_count / total * 100))
            return matches

        def on_progress(p):
            if self.progress.maximum() == 0:
                self.progress.setRange(0, 100)
            self.progress.setValue(p)

        def done(matches):
            self.crossref_selection = matches
            matched = sum(1 for _, b in matches if b)
            self.status_label.setText(f"Matched {matched} / {len(matches)}")
            # populate table
            self.xref_table.setRowCount(0)
            for sp, td in matches:
                row_idx = self.xref_table.rowCount()
                self.xref_table.insertRow(row_idx)
                sp_artist_str = ", ".join(a.get("name", "") for a in getattr(sp, "artists", []) or [])
                self.xref_table.setItem(
                    row_idx, 0, QTableWidgetItem(f"{sp.name} — {sp_artist_str}")
                )
                if td:
                    name = getattr(td, "name", "") or getattr(td, "full_name", "")
                    artist = ", ".join(
                        getattr(a, "name", "")
                        for a in (getattr(td, "artists", []) or [])
                    )
                    qual = Tidal.quality_label(td)
                    self.xref_table.setItem(
                        row_idx, 1, QTableWidgetItem(f"{name} — {artist}")
                    )
                    self.xref_table.setItem(row_idx, 2, QTableWidgetItem(qual))
                    self.xref_table.setItem(row_idx, 3, QTableWidgetItem("OK"))
                else:
                    self.xref_table.setItem(row_idx, 1, QTableWidgetItem("No match"))
                    self.xref_table.setItem(row_idx, 2, QTableWidgetItem("-"))
                    self.xref_table.setItem(row_idx, 3, QTableWidgetItem("Missing"))
            # re-enable
            self.btn_crossref.setEnabled(True)
            self.btn_transfer.setEnabled(True)
            self.btn_fetch.setEnabled(True)
            self.progress.setRange(0, 100)
            self.progress.setValue(100)

        def on_error(e):
            self.status_label.setText(f"Cross-reference failed: {e}")
            self.btn_crossref.setEnabled(True)
            self.btn_transfer.setEnabled(True)
            self.btn_fetch.setEnabled(True)
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

        run_in_background(
            self.pool, work, done, on_error=on_error, on_progress=on_progress
        )

    def _transfer(self):
        self.logger.info("Starting transfer to TIDAL")
        if not self.crossref_selection:
            self.status_label.setText("Run cross-reference first")
            return

        def preview_text():
            lines = []
            for sp, td in self.crossref_selection[:100]:
                td_n = getattr(td, "name", "-") if td else "-"
                td_q = Tidal.quality_label(td) if td else ""
                lines.append(f"{sp.name} -> {td_n} [{td_q}]")
            return "\n".join(lines)

        dlg = ConfirmDialog(self, preview_text())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        row = self.sp_list.currentRow()
        pl = self.spotify_playlists[row]
        sp_name = pl.get("name") if isinstance(pl, dict) else getattr(pl, "name", "")

        def work():
            # find or create
            target = None
            for tpl in self.tidal_playlists:
                if getattr(tpl, "name", "") == sp_name:
                    target = tpl
                    break
            if not target:
                target = self.tidal.create_playlist(
                    sp_name, description="Imported from Spotify"
                )
                if target:
                    self.tidal_playlists.append(target)
            if not target:
                raise RuntimeError("Failed to create TIDAL playlist")
            target_id = getattr(target, "id", None)
            if not target_id:
                raise RuntimeError("Invalid TIDAL playlist id")
            existing_ids = set(self.tidal.get_playlist_track_ids(target_id))
            to_add = []
            for _, td in self.crossref_selection:
                if not td:
                    continue
                tid = int(getattr(td, "id", -1))
                if tid > 0 and tid not in existing_ids:
                    to_add.append(tid)
            ok = self.tidal.add_tracks_to_playlist(target_id, to_add)
            return ok, sp_name, len(to_add)

        def done(result):
            ok, name, count = result
            if ok:
                self.status_label.setText(f"Added {count} tracks to '{name}'")
            else:
                self.status_label.setText("Failed to add tracks")

        run_in_background(
            self.pool, work, done, on_error=lambda e: self.status_label.setText(str(e))
        )
