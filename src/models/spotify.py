import time


class SpotifyTrack:
    """
    Represents a single track fetched from the Spotify API
    using nearly all track properties returned by the API (see prompt for fields).
    """

    def __init__(
        self,
        id,
        name,
        artists,
        album,
        available_markets,
        disc_number,
        duration_ms,
        explicit,
        external_ids,
        external_urls,
        href,
        is_local,
        is_playable,
        popularity,
        preview_url,
        track_number,
        type_,
        uri,
    ):
        self.id = id
        self.name = name
        self.artists = artists  # List of dicts, artist objects
        self.album = album  # Album dict/object
        self.available_markets = available_markets  # List of strings
        self.disc_number = disc_number
        self.duration_ms = duration_ms
        self.explicit = explicit
        self.external_ids = external_ids
        self.external_urls = external_urls
        self.href = href
        self.is_local = is_local
        self.is_playable = is_playable
        self.popularity = popularity
        self.preview_url = preview_url
        self.track_number = track_number
        self.type = type_
        self.uri = uri

    @classmethod
    def from_api(cls, track_obj):
        """
        Build SpotifyTrack from a Spotify track dict (as from API).
        """
        return cls(
            id=track_obj["id"],
            name=track_obj["name"],
            artists=track_obj["artists"],  # List of dicts
            album=track_obj["album"],
            available_markets=track_obj.get("available_markets"),
            disc_number=track_obj.get("disc_number"),
            duration_ms=track_obj.get("duration_ms"),
            explicit=track_obj.get("explicit"),
            external_ids=track_obj.get("external_ids"),
            external_urls=track_obj.get("external_urls"),
            href=track_obj.get("href"),
            is_local=track_obj.get("is_local"),
            is_playable=track_obj.get("is_playable"),
            popularity=track_obj.get("popularity"),
            preview_url=track_obj.get("preview_url"),
            track_number=track_obj.get("track_number"),
            type_=track_obj.get("type"),
            uri=track_obj.get("uri"),
        )

    @property
    def artists_names(self):
        return ", ".join([artist["name"] for artist in self.artists])

    @property
    def album_name(self):
        return self.album["name"]

    @property
    def duration_formatted(self):
        return time.strftime("%M:%S", time.gmtime(self.duration_ms / 1000))

    @property
    def explicit(self):
        return self.explicit

    @property
    def local(self):
        return self.is_local

    @property
    def playable(self):
        return self.is_playable

    @property
    def uri(self):
        return self.uri

    @property
    def external_urls(self):
        return self.external_urls


class SpotifyPlaylist:
    def __init__(
        self,
        id,
        name,
        tracks,
        collaborative,
        description,
        external_urls,
        href,
        images,
        owner,
        primary_color,
        public,
        snapshot_id,
        type_,
        uri,
    ):
        self.id = id
        self.name = name
        self.tracks = tracks
        self.collaborative = collaborative
        self.description = description
        self.external_urls = external_urls
        self.href = href
        self.images = images
        self.owner = owner
        self.primary_color = primary_color
        self.public = public
        self.snapshot_id = snapshot_id
        self.type = type_
        self.uri = uri

    @classmethod
    def from_api(cls, playlist_obj):
        return cls(
            id=playlist_obj["id"],
            name=playlist_obj["name"],
            tracks=playlist_obj["tracks"],
            collaborative=playlist_obj.get("collaborative"),
            description=playlist_obj.get("description"),
            external_urls=playlist_obj.get("external_urls"),
            href=playlist_obj.get("href"),
            images=playlist_obj.get("images"),
            owner=playlist_obj.get("owner"),
            primary_color=playlist_obj.get("primary_color"),
            public=playlist_obj.get("public"),
            snapshot_id=playlist_obj.get("snapshot_id"),
            type_=playlist_obj.get("type"),
            uri=playlist_obj.get("uri"),
        )

    @property
    def tracks_count(self):
        return self.tracks.get("total")

    @property
    def images(self):
        return self.images

    @property
    def external_urls(self):
        return self.external_urls

    @property
    def id(self):
        return self.id
