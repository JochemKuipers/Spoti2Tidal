# Spoti2Tidal

Sync your Spotify playlists to TIDAL.

## Disclaimer

Matching tracks across services can take a long time, especially for large libraries. This is expected due to API rate limits and the need for careful, fuzzy matching to improve accuracy. Please be patient during the matching phase.

## How it works

1. Authenticate with TIDAL (PKCE). On first run, a browser opens for login; paste the final redirect URL when prompted.
2. Read your Spotify playlists and tracks.
3. For each Spotify track, search TIDAL using title, artists, album, and duration with fuzzy matching and heuristics to pick the best candidate.
4. Respect API rate limits with backoff; retries are applied for transient errors.
5. In dry-run, only resolve matches and produce a report; no changes are made to TIDAL.
6. In sync mode, create missing TIDAL playlists and add matched tracks, preserving order when possible. Unmatched tracks are reported.

## GUI

Launch the GUI:

```bash
python -m Spoti2Tidal
```

or

```bash
python main.py
```

## CLI

Run the app headlessly with optional dry-run, selectable modes, and verbose output.

- Dry-run (resolve matches only, no writes to TIDAL):

```bash
python main.py --cli --dry-run
```

- Sync playlists and saved tracks (default when no modes specified):

```bash
python main.py --cli
```

- Sync only playlists:

```bash
python main.py --cli --playlists
```

- Sync only saved tracks (Liked Songs) to TIDAL favorites:

```bash
python main.py --cli --saved-tracks
```

- Verbose output (DEBUG-level logs); can be combined with modes:

```bash
python main.py --cli --verbose
python main.py --cli --saved-tracks --verbose
```

- Automatic sync (create TIDAL playlists and add matched tracks):

```bash
python main.py --cli
```

On first run, if not logged in to TIDAL, the app will open a browser for PKCE login and ask you to paste the final redirect URL shown by TIDAL.
