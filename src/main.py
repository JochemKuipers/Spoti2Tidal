from services.tidal import Tidal


def main():
    tidal = Tidal()

    # Fetch the first 10 liked tracks from Spotify
    results = tidal.get_user_playlists()
    playlist = results[0]

    print(playlist.name)
    print(playlist.get_tracks_count())


if __name__ == "__main__":
    main()
