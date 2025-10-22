from __future__ import annotations

import functools
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QThreadPool, QTimer, QAbstractListModel, QModelIndex, QSize
from PyQt6.QtGui import QAction, QPainter, QColor, QFont, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
    QStyle,
)

from src.gui.workers import run_in_background
from src.services.spotify import Spotify
from src.services.tidal import Tidal


# ---- simple data holders ----
@dataclass
class TrackState:
    index: int
    sp_item: dict  # Spotify API track item dict
    matched_track_id: Optional[int] = None
    matched_track_label: Optional[str] = None  # Store the formatted label for matched tracks
    progress: int = 0


@dataclass
class PlaylistState:
    playlist: dict  # Spotify playlist dict
    list_item: QListWidgetItem
    list_widget: QWidget
    name_label: QLabel
    progress_bar: QProgressBar
    container: QWidget  # right panel content container for this playlist
    tracks_view: Optional[QListView] = None  # Virtualized track list view
    tracks_model: Optional['TrackListModel'] = None  # Model for tracks
    tracks: List[TrackState] = field(default_factory=list)
    tracks_raw_items: List[dict] = field(default_factory=list)  # Store raw items for lazy widget creation
    widgets_built: bool = False  # Track if widgets have been built
    started: bool = False
    completed: bool = False
    tidal_playlist_id: Optional[str] = None


# ---- Virtualized Track List (Model + Delegate) ----
class TrackListModel(QAbstractListModel):
    """Model for track list - only stores data, doesn't create widgets."""
    
    def __init__(self, tracks: List[TrackState], parent=None):
        super().__init__(parent)
        self._tracks = tracks
    
    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._tracks)
    
    def data(self, index: QModelIndex, role: int):
        if not index.isValid() or index.row() >= len(self._tracks):
            return None
        
        if role == Qt.ItemDataRole.UserRole:
            # Return the full TrackState object
            return self._tracks[index.row()]
        
        return None
    
    def update_track(self, row: int):
        """Notify view that a specific track has changed."""
        if 0 <= row < len(self._tracks):
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.UserRole])


class TrackItemDelegate(QStyledItemDelegate):
    """Custom delegate to paint track items efficiently without creating widgets."""
    
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        painter.save()
        
        tstate: TrackState = index.data(Qt.ItemDataRole.UserRole)
        if not tstate:
            painter.restore()
            return
        
        rect = option.rect
        
        # Get palette colors for dark mode support
        palette = option.palette
        is_selected = option.state & QStyle.StateFlag.State_Selected
        
        # Draw background
        if is_selected:
            painter.fillRect(rect, palette.highlight())
            text_color = palette.highlightedText().color()
        else:
            painter.fillRect(rect, palette.base())
            text_color = palette.text().color()
        
        # Draw border with appropriate color
        border_color = palette.mid().color()
        painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
        
        # Extract track data
        sp_track = tstate.sp_item.get("track") or {}
        name = sp_track.get("name", "<unknown>")
        artists = ", ".join(a.get("name") for a in (sp_track.get("artists") or []) if a)
        album = (sp_track.get("album") or {}).get("name", "")
        dur_ms = sp_track.get("duration_ms") or 0
        dur_txt = self._fmt_duration(dur_ms)
        
        # Layout areas
        margin = 10
        content_rect = rect.adjusted(margin, margin, -margin, -margin)
        
        # Progress bar area (top)
        progress_height = 20
        progress_rect = content_rect.adjusted(0, 0, 0, -(content_rect.height() - progress_height))
        
        # Draw progress bar with palette colors
        progress_bg_color = palette.alternateBase().color()
        painter.setPen(QPen(border_color, 1))
        painter.setBrush(progress_bg_color)
        painter.drawRect(progress_rect)
        
        if tstate.progress > 0:
            fill_width = int(progress_rect.width() * tstate.progress / 100)
            painter.fillRect(progress_rect.adjusted(0, 0, -(progress_rect.width() - fill_width), 0), 
                           QColor("#4a9eff"))
        
        # Progress text
        painter.setPen(text_color)
        painter.drawText(progress_rect, Qt.AlignmentFlag.AlignCenter, f"{tstate.progress}%")
        
        # Status text (right side of progress)
        status_text = self._get_status_text(tstate)
        status_rect = content_rect.adjusted(progress_rect.width() - 100, 0, 0, 
                                           -(content_rect.height() - progress_height))
        secondary_color = palette.placeholderText().color()
        painter.setPen(secondary_color)
        painter.drawText(status_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 
                        status_text)
        
        # Track info area (bottom half)
        y_offset = margin + progress_height + 8
        track_rect = rect.adjusted(margin, y_offset, -margin, -margin)
        
        # Split into Spotify (left) and TIDAL (right)
        mid_x = track_rect.width() // 2
        spotify_rect = track_rect.adjusted(0, 0, -mid_x - 5, 0)
        tidal_rect = track_rect.adjusted(mid_x + 5, 0, 0, 0)
        
        # Draw Spotify info
        painter.setPen(text_color)
        font_bold = QFont()
        font_bold.setBold(True)
        font_bold.setPointSize(10)
        painter.setFont(font_bold)
        
        y = spotify_rect.top()
        painter.drawText(spotify_rect.adjusted(0, 0, 0, -(spotify_rect.height() - 20)), 
                        Qt.AlignmentFlag.AlignLeft, "Spotify")
        
        font_normal = QFont()
        font_normal.setPointSize(9)
        painter.setFont(font_normal)
        
        y += 22
        painter.drawText(spotify_rect.adjusted(0, y - spotify_rect.top(), 0, 0), 
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, 
                        self._elide_text(name, spotify_rect.width(), painter.fontMetrics()))
        y += 16
        painter.setPen(secondary_color)
        painter.drawText(spotify_rect.adjusted(0, y - spotify_rect.top(), 0, 0), 
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, 
                        self._elide_text(artists, spotify_rect.width(), painter.fontMetrics()))
        y += 16
        painter.drawText(spotify_rect.adjusted(0, y - spotify_rect.top(), 0, 0), 
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, 
                        self._elide_text(f"{album} • {dur_txt}", spotify_rect.width(), painter.fontMetrics()))
        
        # Draw TIDAL info
        painter.setPen(text_color)
        painter.setFont(font_bold)
        painter.drawText(tidal_rect.adjusted(0, 0, 0, -(tidal_rect.height() - 20)), 
                        Qt.AlignmentFlag.AlignLeft, "TIDAL")
        
        painter.setFont(font_normal)
        tidal_text = self._get_tidal_text(tstate)
        
        # Check if it's a matched track with data (contains pipe delimiter)
        if "|" in tidal_text:
            # Parse: name|artists|album|duration
            parts = tidal_text.split("|")
            if len(parts) >= 4:
                td_name, td_artists, td_album, td_duration = parts[0], parts[1], parts[2], parts[3]
                
                y = tidal_rect.top()
                y += 22
                
                # Draw track name (normal color)
                painter.setPen(text_color)
                painter.drawText(tidal_rect.adjusted(0, y - tidal_rect.top(), 0, 0), 
                                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, 
                                self._elide_text(td_name, tidal_rect.width(), painter.fontMetrics()))
                y += 16
                
                # Draw artists (secondary color)
                painter.setPen(secondary_color)
                painter.drawText(tidal_rect.adjusted(0, y - tidal_rect.top(), 0, 0), 
                                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, 
                                self._elide_text(td_artists, tidal_rect.width(), painter.fontMetrics()))
                y += 16
                
                # Draw album + duration (secondary color)
                painter.drawText(tidal_rect.adjusted(0, y - tidal_rect.top(), 0, 0), 
                                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, 
                                self._elide_text(f"{td_album} • {td_duration}", tidal_rect.width(), painter.fontMetrics()))
            else:
                # Fallback if parsing fails
                painter.setPen(text_color)
                painter.drawText(tidal_rect.adjusted(0, 22, 0, 0), 
                                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, 
                                tidal_text)
        else:
            # Plain text (Pending, No match, Error)
            tidal_text_rect = tidal_rect.adjusted(0, 22, 0, 0)
            if "No match" in tidal_text or "Error" in tidal_text:
                tidal_color = QColor("#d33")
            else:
                tidal_color = secondary_color
            painter.setPen(tidal_color)
            painter.drawText(tidal_text_rect, 
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, 
                            tidal_text)
        
        painter.restore()
    
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Fixed height for each item - enables uniform item size optimization."""
        return QSize(option.rect.width(), 140)
    
    def _fmt_duration(self, ms: int) -> str:
        """Format duration in ms to M:SS."""
        secs = ms // 1000
        mins = secs // 60
        secs = secs % 60
        return f"{mins}:{secs:02d}"
    
    def _get_status_text(self, tstate: TrackState) -> str:
        """Get status text for track."""
        if tstate.progress >= 100:
            if tstate.matched_track_id:
                return "Matched"
            else:
                return "No match"
        elif tstate.progress > 0:
            return f"Matching…"
        return "Queued"
    
    def _get_tidal_text(self, tstate: TrackState) -> str:
        """Get TIDAL match text for track."""
        if tstate.matched_track_label:
            return tstate.matched_track_label
        elif tstate.matched_track_id:
            return "Matched"
        elif tstate.progress >= 100:
            return "No match found"
        return "Pending…"
    
    def _elide_text(self, text: str, width: int, font_metrics) -> str:
        """Elide text to fit width."""
        return font_metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)


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

        # worker pools: separate pools to avoid fetch tasks blocking on match tasks
        self.fetch_pool = QThreadPool()
        self.fetch_pool.setMaxThreadCount(10)  # Sequential-ish fetching to avoid rate limits
        self.match_pool = QThreadPool()
        self.match_pool.setMaxThreadCount(10)  # Sequential matching to avoid TIDAL rate limits
        # For backwards compatibility, keep self.pool pointing to match pool
        self.pool = self.match_pool

        # UI
        self._build_menu()
        self._build_ui()

        # state
        self.playlists: Dict[str, PlaylistState] = {}
        # processing queue state (internal, UI-independent)
        self.processing_queue: List[str] = []
        self.currently_processing_id: Optional[str] = None

        # kick off
        self._load_spotify_playlists()

    # ---- UI scaffold ----
    def _build_menu(self):
        bar = self.menuBar()
        acct = bar.addMenu("Account")
        self.act_tidal_login = QAction("Connect TIDAL", self)
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
            order_ids: List[str] = []
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

                # Create virtualized track list view
                tracks_view = QListView()
                tracks_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
                tracks_view.setUniformItemSizes(True)  # Major optimization for scrolling
                tracks_view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
                tracks_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                c_layout.addWidget(tracks_view, 1)

                self.playlists[pid] = PlaylistState(
                    playlist=pl,
                    list_item=item,
                    list_widget=widget,
                    name_label=widget.name_label,
                    progress_bar=widget.progress,
                    container=container,
                    tracks_view=tracks_view,
                )
                # Initialize progress bar format
                widget.progress.setFormat("Ready")
                widget.progress.setValue(0)
                order_ids.append(pid)
                # Connect button now that state exists
                btn_sync.clicked.connect(functools.partial(self._transfer_to_tidal, pid))

            # Select first by default
            if self.playlist_list.count() > 0:
                self.playlist_list.setCurrentRow(0)
            # Enqueue playlists for processing and start the first automatically
            if order_ids:
                self._enqueue_playlists(order_ids)

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
            self.fetch_pool,
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

        # Build model/view for this playlist if not already built
        st = self.playlists[pid]
        if not st.widgets_built and st.tracks:
            self._build_track_view_for_playlist(pid, st)

        # swap in this playlist container
        # clear right_layout and insert this container
        while self.right_layout.count():
            item = self.right_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self.right_layout.addWidget(self.playlists[pid].container, 1)

        # Do not auto-start on selection; processing is driven by internal queue now.

    # ---- per-playlist flow ----
    def _start_playlist_sync(self, playlist_id: str):
        st = self.playlists[playlist_id]
        st.started = True
        st.progress_bar.setValue(0)
        st.progress_bar.setFormat("Fetching tracks… %p%")

        def on_tracks_done(items: List[dict]):
            # Store raw track data without building widgets
            # Widgets will be built lazily when user views the playlist
            st.tracks_raw_items = items
            
            # Create TrackState objects without widgets
            for idx, it in enumerate(items):
                tstate = TrackState(
                    index=idx,
                    sp_item=it,
                )
                st.tracks.append(tstate)
            
            # Build the view if this is the currently selected playlist
            if self.playlist_list.currentRow() >= 0:
                current_pid = None
                current_item = self.playlist_list.currentItem()
                for k, pst in self.playlists.items():
                    if pst.list_item is current_item:
                        current_pid = k
                        break
                
                if current_pid == playlist_id and not st.widgets_built:
                    self._build_track_view_for_playlist(playlist_id, st)
            
            st.progress_bar.setFormat("Matching tracks… %p%")
            # Start matching immediately without building widgets
            self._start_matching_for_playlist(playlist_id, st)

        def on_tracks_progress(pct: int):
            # during fetch, reflect percent
            st.progress_bar.setValue(pct)

        def on_tracks_error(e: Exception):
            QMessageBox.critical(self, "Spotify", f"Failed to fetch tracks: {e}")
            st.started = False
            # On fetch error, continue pipeline with next playlist to avoid stalling.
            try:
                self._start_next_in_queue()
            except Exception:
                pass

        run_in_background(
            self.fetch_pool,
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
            self.fetch_pool,
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
        if done == total and not st.completed:
            st.progress_bar.setFormat("Completed 100%")
            st.completed = True
            # Advance queue instead of relying on UI selection
            self._on_playlist_complete(playlist_id)

    def _show_playlist_container(self, pid: str):
        # clear right_layout and insert this container
        while self.right_layout.count():
            item = self.right_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self.right_layout.addWidget(self.playlists[pid].container, 1)

    def _enqueue_playlists(self, ids: List[str]):
        # Initialize processing queue in given order
        self.processing_queue = [pid for pid in ids if pid in self.playlists]
        self.currently_processing_id = None
        self._start_next_in_queue()

    def _start_next_in_queue(self):
        # pick next not-started playlist
        next_id: Optional[str] = None
        while self.processing_queue:
            cand = self.processing_queue.pop(0)
            st = self.playlists.get(cand)
            if st and not st.started:
                next_id = cand
                break
        if not next_id:
            return
        self.currently_processing_id = next_id
        # Don't force-focus the UI to this playlist - let it fetch in background
        # Users can manually select playlists to see their progress
        # start sync
        if not self.playlists[next_id].started:
            self._start_playlist_sync(next_id)

    def _start_matching_for_playlist(self, playlist_id: str, st: PlaylistState):
        """Start matching all tracks for a playlist after widgets are built."""
        st.progress_bar.setFormat("Matching tracks… %p%")
        st.progress_bar.setValue(0)  # Reset to 0 before starting matching
        # Begin matching concurrently
        for tstate in st.tracks:
            self._match_track_async(playlist_id, tstate)
        # Pipeline: as soon as this playlist finished fetching, start fetching the next one
        # while this one is still matching.
        try:
            self._start_next_in_queue()
        except Exception:
            pass

    def _build_track_view_for_playlist(self, playlist_id: str, st: PlaylistState):
        """Build virtualized track view - instant, no widgets to create!"""
        if st.widgets_built or not st.tracks_view:
            return
        
        # Create model with track data
        st.tracks_model = TrackListModel(st.tracks, st.tracks_view)
        st.tracks_view.setModel(st.tracks_model)
        
        # Set custom delegate for painting
        delegate = TrackItemDelegate(st.tracks_view)
        st.tracks_view.setItemDelegate(delegate)
        
        st.widgets_built = True

    def _on_playlist_complete(self, playlist_id: str):
        # mark current processing finished; do not trigger next fetch here because
        # we already pipeline the next fetch right after fetching completes.
        if self.currently_processing_id == playlist_id:
            self.currently_processing_id = None

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
            # Build label with plain text separated by delimiters
            td_name = getattr(best, "name", "") or getattr(best, "full_name", "")
            td_artists = ", ".join(getattr(a, "name", "") for a in getattr(best, "artists", []) if a)
            td_album = getattr(getattr(best, "album", None), "name", "")
            try:
                dur_s = int(getattr(best, "duration", 0) or 0)
            except Exception:
                dur_s = 0
            dur_txt = MainWindow._fmt_duration(dur_s * 1000)
            # Use pipe delimiter to separate fields: name|artists|album|duration
            label = f"{td_name}|{td_artists}|{td_album}|{dur_txt}"
            return int(tid) if tid is not None else None, label

        def on_done(res: Tuple[Optional[int], Optional[str]]):
            tid, label = res
            # Update internal state
            tstate.progress = 100
            if tid is None:
                # No match found
                tstate.matched_track_label = None
            else:
                tstate.matched_track_id = tid
                tstate.matched_track_label = label  # Store the label for later display
            
            # Notify model to repaint this item
            st = self.playlists.get(playlist_id)
            if st and st.tracks_model:
                st.tracks_model.update_track(tstate.index)
            
            self._update_playlist_progress(playlist_id)

        def on_progress(pct: int):
            tstate.progress = pct
            
            # Notify model to repaint this item
            st = self.playlists.get(playlist_id)
            if st and st.tracks_model:
                st.tracks_model.update_track(tstate.index)
            
            self._update_playlist_progress(playlist_id)

        def on_error(e: Exception):
            tstate.progress = 100
            tstate.matched_track_label = f"Error: {e}"
            
            # Notify model to repaint this item
            st = self.playlists.get(playlist_id)
            if st and st.tracks_model:
                st.tracks_model.update_track(tstate.index)
            
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
