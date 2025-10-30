from models.spotify import SpotifyTrack


from __future__ import annotations
import sys
import argparse
from PyQt6.QtWidgets import QApplication
from gui.main_window import MainWindow
from logging_config import setup_logging
from services.spotify import Spotify
from services.tidal import Tidal


def run_cli(dry_run: bool) -> int:
    """Run the headless CLI flow.

    - Always fetch Spotify playlists and resolve TIDAL matches
    - If dry_run: print a summary and exit without writing to TIDAL
    - If do_sync and not dry_run: create a TIDAL playlist and add matched tracks
    """
    setup_logging()

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

    # Fetch user and playlists
    try:
        sp.get_user()
    except Exception:
        pass
    playlists = sp.get_user_playlists()
    if not playlists:
        print("No Spotify playlists found for this user.")
        return 0

    overall_added = 0
    for pl in playlists:
        pid = pl.get("id")
        name = pl.get("name") or pid
        print(f"Processing playlist: {name}")
        try:
            items = sp.get_playlist_tracks(pid)
        except Exception as e:
            print(f"  Failed to fetch tracks: {e}")
            continue

        matched_ids = []
        total = len(items)
        for idx, it in enumerate[SpotifyTrack](items, start=1):
            sp_track = it.get("track") or {}
            sp_name = sp_track.get("name")
            sp_artists = sp_track.get("artists") or []
            sp_dur = sp_track.get("duration_ms")
            sp_isrc = (sp_track.get("external_ids") or {}).get("isrc")
            sp_album = (sp_track.get("album") or {}).get("name")

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
                    matched_ids.append(int(tid))
            if idx % 50 == 0 or idx == total:
                print(f"  Matched {len(matched_ids)}/{total} tracks…")

        print(f"  Final matches: {len(matched_ids)}/{total}")

        if dry_run:
            print("  Dry-run enabled: not creating TIDAL playlist or adding tracks.")
            continue

        if matched_ids:
            created = td.create_playlist(name, description="Imported from Spotify")
            if not created:
                print("  Failed to create TIDAL playlist.")
                continue
            tpid = getattr(created, "id", None)
            if not tpid:
                print("  No TIDAL playlist id returned.")
                continue
            ok = td.add_tracks_to_playlist(tpid, matched_ids)
            if ok:
                print(f"  Added {len(matched_ids)} tracks to TIDAL playlist '{name}'.")
                overall_added += len(matched_ids)
            else:
                print("  Failed to add tracks to TIDAL playlist.")

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
    args = parser.parse_args()

    # Run CLI mode only when explicitly requested
    if args.cli:
        code = run_cli(dry_run=args.dry_run)
        sys.exit(code)

    setup_logging()
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
