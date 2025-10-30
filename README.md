# Spoti2Tidal

Sync your Spotify playlists to TIDAL.

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

Run the app headlessly with optional dry-run and automatic sync.

- Dry-run (resolve matches only, no writes to TIDAL):

```bash
python main.py --cli --dry-run
```

- Automatic sync (create TIDAL playlists and add matched tracks):

```bash
python main.py --cli
```

On first run, if not logged in to TIDAL, the app will open a browser for PKCE login and ask you to paste the final redirect URL shown by TIDAL.
