import datetime
import time
from dataclasses import dataclass

from typing import TypedDict, TypeVar, Generic
from collections.abc import Mapping

T = TypeVar("T")


class ExternalUrls(TypedDict):
    spotify: str

class Followers(TypedDict):
    href: str
    total: int

class Image(TypedDict):
    height: int
    width: int
    url: str

    
class Copyright(TypedDict):
    text: str
    type: str
    

class Paging(TypedDict, Generic[T]):
    href: str
    items: list[T]
    limit: int
    next: str | None
    offset: int
    previous: str | None
    total: int
    
class PublicUser(TypedDict):
    display_name: str
    external_urls: ExternalUrls
    followers: Followers
    href: str
    id: str
    images: list[Image]
    type: str
    uri: str
    
class PrivateUser(TypedDict):
    country: str
    display_name: str
    email: str
    explicit_content: dict[str, bool]
    external_urls: ExternalUrls
    followers: Followers
    href: str
    id: str
    images: list[Image]
    product: str
    type: str
    uri: str
    
class SpotifyArtist(TypedDict):
    external_urls: ExternalUrls
    followers: Followers
    genres: list[str]
    href: str
    id: str
    images: list[Image]
    name: str
    popularity: int
    type: str
    uri: str

class SpotifyAlbum(TypedDict):
    album_type: str
    artists: list[SpotifyArtist]
    available_markets: list[str]
    copyrights: list[Copyright]
    external_ids: dict[str, str]
    external_urls: dict[str, str]
    genres: list[str]
    href: str
    id: str
    images: list[Image]
    label: str
    name: str
    popularity: int
    release_date: str
    release_date_precision: str
    restrictions: dict[str, str]
    total_tracks: int
    tracks: Paging['SpotifyTrackObject']

class SpotifyTrackObject(TypedDict, total=False):
    id: str
    name: str
    artists: list[SpotifyArtist]
    album: SpotifyAlbum
    available_markets: list[str]
    disc_number: int
    duration_ms: int
    explicit: bool
    external_ids: dict[str, str]
    external_urls: dict[str, str]
    href: str
    is_local: bool
    is_playable: bool
    popularity: int
    preview_url: str | None
    track_number: int
    type: str
    uri: str

class PlaylistTrack(Generic[T]):
    def __init__(
        self,
        added_at: datetime.datetime | None,
        added_by: 'PublicUser',
        is_local: bool,
        track: T
    ):
        self.added_at: datetime.datetime | None = added_at
        self.added_by: PublicUser = added_by
        self.is_local: bool = is_local
        self.track: T = track
        

class SavedTrack(TypedDict):
    added_at: datetime.datetime | None
    added_by: PublicUser
    is_local: bool
    track: SpotifyTrackObject
