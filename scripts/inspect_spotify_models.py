from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from typing import Any
 
import spotipy


# Reuse existing Spotify auth/client + cache
from services.spotify import Spotify as SpotifyService


def flatten_keys(obj: Any, prefix: str = "", max_depth: int | None = None) -> set[str]:
    """
    Recursively collect dotted key paths for all properties in a JSON-like object.

    Examples:
    {"images": [{"url": "..."}]} -> {"images", "images[].url"}
    """
    keys: set[str] = set()

    def _walk(value: Any, pfx: str, depth: int):
        if max_depth is not None and depth > max_depth:
            return
        if isinstance(value, dict):
            if pfx:
                keys.add(pfx)
            for k, v in value.items():
                child_prefix = f"{pfx}.{k}" if pfx else k
                keys.add(child_prefix)
                _walk(v, child_prefix, depth + 1)
        elif isinstance(value, list):
            # Mark the list itself and its element keys
            list_key = f"{pfx}[]" if pfx else "[]"
            keys.add(list_key)
            for item in value:
                _walk(item, f"{pfx}[]" if pfx else "[]", depth + 1)
        else:
            if pfx:
                keys.add(pfx)

    _walk(obj, prefix, 0)
    return keys


def merge_key_sets(items: Iterable[dict[str, Any]], max_depth: int | None = None) -> set[str]:
    merged: set[str] = set()
    for it in items:
        merged |= flatten_keys(it, max_depth=max_depth)
    return merged


def typename(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def collect_key_types(obj: Any, prefix: str = "", max_depth: int | None = None) -> dict[str, set[str]]:
    """
    Return mapping: dotted_key_path -> set of inferred type names across values.
    Includes entries for containers themselves (object/array) and their children.
    """
    acc: dict[str, set[str]] = {}

    def add_type(key: str, t: str):
        if not key:
            return
        acc.setdefault(key, set()).add(t)

    def _walk(value: Any, pfx: str, depth: int):
        if max_depth is not None and depth > max_depth:
            return
        t = typename(value)
        if pfx:
            add_type(pfx, t)
        if isinstance(value, dict):
            for k, v in value.items():
                child_prefix = f"{pfx}.{k}" if pfx else k
                add_type(child_prefix, typename(v))
                _walk(v, child_prefix, depth + 1)
        elif isinstance(value, list):
            list_key = f"{pfx}[]" if pfx else "[]"
            add_type(list_key, "array")
            for item in value:
                _walk(item, list_key, depth + 1)

    _walk(obj, prefix, 0)
    return acc


def merge_type_maps(items: Iterable[dict[str, Any]], max_depth: int | None = None) -> dict[str, list[str]]:
    merged: dict[str, set[str]] = {}
    for it in items:
        cur = collect_key_types(it, max_depth=max_depth)
        for k, vset in cur.items():
            merged.setdefault(k, set()).update(vset)
    # sort for stable output
    return {k: sorted(list(v)) for k, v in sorted(merged.items())}


def pick_artist(sp: spotipy.Spotify, artist_id: str | None, artist_name: str | None) -> dict[str, Any]:
    if artist_id:
        return sp.artist(artist_id)
    if not artist_name:
        raise SystemExit("Provide --artist-id or --artist-name")
    search = sp.search(q=f"artist:{artist_name}", type="artist", limit=1)
    items = search.get("artists", {}).get("items", [])
    if not items:
        raise SystemExit(f"No artist found for name: {artist_name}")
    return items[0]


def unique_preserve_order(seq: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for s in seq:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Inspect Spotify artist and album property keys (for typing/models)",
    )
    _ = parser.add_argument("--artist-id", help="Spotify Artist ID", default=None)
    _ = parser.add_argument("--artist-name", help="Artist name to search", default=None)
    _ = parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Limit recursion depth when flattening keys",
    )
    _ = parser.add_argument(
        "--include-groups",
        default="album,single,appears_on,compilation",
        help="Album include_groups for artist_albums",
    )
    _ = parser.add_argument(
        "--market",
        default=None,
        help="Override market when fetching album pages (default: user country)",
    )
    args = parser.parse_args()

    service = SpotifyService()
    sp = service.get_client()
    # Ensure user fetched for market
    user = service.get_user()
    if args.market:
        service.market = args.market

    # Artist
    artist_obj = pick_artist(sp, args.artist_id, args.artist_name)
    artist_id = artist_obj["id"]

    # Albums (summary list items)
    albums_summary: list[dict[str, Any]] = []
    results = sp.artist_albums(
        artist_id,
        include_groups=args.include_groups,
        limit=50,
        offset=0,
    )
    if not results:
        results = {"items": []}
    albums_summary.extend((results.get("items") or []))
    while results and results.get("next"):
        nxt = sp.next(results) or {}
        results = nxt
        albums_summary.extend((results.get("items") or []))

    # Full albums
    full_albums: list[dict[str, Any]] = []
    for alb in albums_summary:
        try:
            full_album = sp.album(alb.get("id"))
            if isinstance(full_album, dict):
                full_albums.append(full_album)
        except Exception:
            # skip fetch errors but continue
            pass

    # Collect keys
    artist_keys = flatten_keys(artist_obj, max_depth=args.max_depth)
    album_summary_keys = merge_key_sets(albums_summary, max_depth=args.max_depth)
    album_full_keys = merge_key_sets(full_albums, max_depth=args.max_depth)

    # Also collect union keys for embedded artist objects from albums
    embedded_artist_keys = merge_key_sets(
        (a for fa in full_albums for a in fa.get("artists", [])),
        max_depth=args.max_depth,
    )

    # Collect types
    artist_types = merge_type_maps([artist_obj], max_depth=args.max_depth)
    album_summary_types = merge_type_maps(albums_summary, max_depth=args.max_depth)
    album_full_types = merge_type_maps(full_albums, max_depth=args.max_depth)
    embedded_artist_types = merge_type_maps(
        (a for fa in full_albums for a in fa.get("artists", [])),
        max_depth=args.max_depth,
    )

    # Output
    sections = {
        "artist.id": artist_id,
        "artist.name": artist_obj.get("name"),
        "artist_keys": sorted(artist_keys),
        "album_summary_keys": sorted(album_summary_keys),
        "album_full_keys": sorted(album_full_keys),
        "embedded_artist_keys_from_albums": sorted(embedded_artist_keys),
        "artist_types": artist_types,
        "album_summary_types": album_summary_types,
        "album_full_types": album_full_types,
        "embedded_artist_types_from_albums": embedded_artist_types,
        "counts": {
            "albums_summary": len(albums_summary),
            "albums_full": len(full_albums),
        },
    }

    print(json.dumps(sections, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()


