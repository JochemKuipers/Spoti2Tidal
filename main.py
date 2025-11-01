from __future__ import annotations

import argparse
import logging
import sys

from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow
from logging_config import setup_logging
from models.spotify import SpotifyTrack
from services.spotify import Spotify
from services.tidal import Tidal


def _match_spotify_items_to_tidal_ids(td: Tidal, items: list[SpotifyTrack]) -> list[str]:
    matched_ids: list[str] = []
    total = len(items)
    for idx, it in enumerate(items, start=1):
        # Support both raw Spotify API dict items and model instances
        # Playlist/saved items from Spotify API are typically shaped like {"track": {...}}
        if isinstance(it, dict):
            sp_track = it.get("track") or it
            sp_name = (sp_track or {}).get("name")
            sp_artists = (sp_track or {}).get("artists")
            sp_dur = (sp_track or {}).get("duration_ms")
            external_ids = (sp_track or {}).get("external_ids") or {}
            sp_isrc = external_ids.get("isrc")
            album_obj = (sp_track or {}).get("album") or {}
            sp_album = album_obj.get("name")
        else:
            sp_track = it
            sp_name = getattr(sp_track, "name", None)
            sp_artists = getattr(sp_track, "artists", None)
            sp_dur = getattr(sp_track, "duration_ms", None)
            external_ids = getattr(sp_track, "external_ids", {}) or {}
            sp_isrc = external_ids.get("isrc")
            album_obj = getattr(sp_track, "album", {}) or {}
            sp_album = (
                album_obj.get("name")
                if isinstance(album_obj, dict)
                else getattr(album_obj, "name", None)
            )

        best = td.resolve_best_match(
            isrc=sp_isrc,
            name=sp_name,
            artists=sp_artists,
            duration_ms=sp_dur,
            album=sp_album,
        )
        if best is not None:
            tid = getattr(best, "id", None)
            if tid is not None:
                matched_ids.append(str(tid))
        if idx % 50 == 0 or idx == total:
            print(f"  Matched {len(matched_ids)}/{total} tracks…")
    print(f"  Final matches: {len(matched_ids)}/{total}")
    return matched_ids


def run_cli(
    dry_run: bool,
    *,
    do_playlists: bool,
    do_saved_tracks: bool,
    verbose: bool,
    playlist_name: str | None = None,
) -> int:
    """Run the headless CLI flow.

    - Always fetch Spotify playlists and resolve TIDAL matches
    - If dry_run: print a summary and exit without writing to TIDAL
    - If do_sync and not dry_run: create a TIDAL playlist and add matched tracks
    - If playlist_name is provided, only process that specific playlist
    """
    setup_logging(logging.INFO if verbose else logging.WARNING)

    sp = Spotify()
    td = Tidal()

    # Ensure TIDAL login or guide the user via PKCE
    if not td.ensure_logged_in():
        print("You are not logged in to TIDAL.")
        try:
            url = td.open_browser_login()
            print("If your browser didn't open, use this URL:")
            print(url)
        except Exception:
            url = td.get_pkce_login_url()
            print("Open this URL to login to TIDAL:")
            print(url)
        redirected = input("After login, paste the final redirected URL here: ").strip()
        if not redirected:
            print("No redirect URL provided. Exiting.")
            return 2
        ok = td.complete_pkce_login(redirected)
        if not ok:
            print("TIDAL login failed. Exiting.")
            return 2

    # Fetch user
    try:
        sp.get_user()
    except Exception:
        pass
    overall_added = 0

    if do_playlists:
        playlists = sp.get_user_playlists()
        if not playlists:
            print("No Spotify playlists found for this user.")
        else:
            # If a specific playlist name is provided, filter to only that playlist
            if playlist_name:
                matching_playlists = []
                for pl in playlists:
                    if isinstance(pl, dict):
                        name = pl.get("name") or ""
                    else:
                        name = getattr(pl, "name", None) or ""
                    if name.lower() == playlist_name.lower():
                        matching_playlists.append(pl)

                if not matching_playlists:
                    print(f"Playlist '{playlist_name}' not found in your Spotify playlists.")
                    return 1
                playlists = matching_playlists

            for pl in playlists:
                # Handle dict-shaped playlists returned by services.spotify
                if isinstance(pl, dict):
                    pid = pl.get("id")
                    name = pl.get("name") or pid or "Spotify Playlist"
                else:
                    pid = getattr(pl, "id", None)
                    name = getattr(pl, "name", None) or pid or "Spotify Playlist"
                if not pid:
                    print("  Skipping playlist without id")
                    continue
                print(f"Processing playlist: {name}")
                try:
                    items = sp.get_playlist_tracks(pid)
                except Exception as e:
                    print(f"  Failed to fetch tracks: {e}")
                    continue

                matched_ids = _match_spotify_items_to_tidal_ids(td, items)

                if dry_run:
                    print("  Dry-run enabled: not creating TIDAL playlist or adding tracks.")
                    continue

                if matched_ids:
                    pl = td.get_or_create_playlist(name, description="Synced from Spotify")
                    ok = td.add_tracks_to_playlist(str(pl.id), matched_ids)
                    if not ok:
                        print("  Failed to add tracks to TIDAL playlist.")
                    else:
                        overall_added += len(matched_ids)
                        print(f"  Added {len(matched_ids)} tracks to TIDAL playlist")

    if do_saved_tracks:
        print("Processing saved tracks (Liked Songs)")
        try:
            items = sp.get_user_tracks()
        except Exception as e:
            print(f"  Failed to fetch saved tracks: {e}")
            items = []

        matched_ids = _match_spotify_items_to_tidal_ids(td, items)

        if dry_run:
            print("  Dry-run enabled: not adding tracks to TIDAL favorites.")
        else:
            if matched_ids:
                ok = td.add_tracks_to_favorites(matched_ids)
                if ok:
                    print(f"  Added {len(matched_ids)} tracks to TIDAL favorites")
                    overall_added += len(matched_ids)
                else:
                    print("  Failed to add tracks to TIDAL favorites")
            else:
                print("  No matches to add to TIDAL favorites")

    if dry_run:
        print("Dry-run completed.")
    else:
        print(f"Completed. Total tracks added: {overall_added}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Spoti2Tidal")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in CLI mode instead of launching the GUI",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve matches and show summary without creating playlists or adding tracks",
    )
    parser.add_argument(
        "--playlist",
        help="Name of a specific Spotify playlist to transfer (only transfers this playlist)",
        default=None,
    )
    parser.add_argument(
        "--playlists",
        action="store_true",
        help="Sync Spotify playlists to TIDAL playlists",
    )
    parser.add_argument(
        "--saved-tracks",
        action="store_true",
        help="Sync Spotify saved tracks (Liked Songs) to TIDAL favorites",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (DEBUG level)",
    )
    args = parser.parse_args()

    # Run CLI mode only when explicitly requested
    if args.cli:
        # If --playlist is specified, enable playlist mode automatically
        do_playlists = args.playlists or (args.playlist is not None)
        do_saved = args.saved_tracks and (
            args.playlist is None
        )  # Don't do saved tracks if specific playlist is requested

        # Default behavior: transfer all playlists and saved tracks
        if not do_playlists and not do_saved:
            do_playlists = True
            do_saved = True

        code = run_cli(
            dry_run=args.dry_run,
            do_playlists=do_playlists,
            do_saved_tracks=do_saved,
            verbose=args.verbose,
            playlist_name=args.playlist,
        )
        sys.exit(code)

    setup_logging()
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
