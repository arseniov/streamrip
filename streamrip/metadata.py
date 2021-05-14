"""Manages the information that will be embeded in the audio file."""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from typing import Generator, Hashable, Iterable, Optional, Union

from .constants import (
    COPYRIGHT,
    COVER_SIZES,
    FLAC_KEY,
    MP3_KEY,
    MP4_KEY,
    PHON_COPYRIGHT,
    TIDAL_Q_MAP,
    TRACK_KEYS,
)
from .exceptions import InvalidContainerError, InvalidSourceError
from .utils import get_quality_id, safe_get, tidal_cover_url

logger = logging.getLogger("streamrip")


class TrackMetadata:
    """Contains all of the metadata needed to tag the file.

    Tags contained:
        * title
        * artist
        * album
        * albumartist
        * composer
        * year
        * comment
        * description
        * purchase_date
        * grouping
        * genre
        * lyrics
        * encoder
        * copyright
        * compilation
        * cover
        * tracknumber
        * discnumber
        * tracktotal
        * disctotal
    """

    def __init__(
        self,
        track: Optional[Union[TrackMetadata, dict]] = None,
        album: Optional[Union[TrackMetadata, dict]] = None,
        source="qobuz",
    ):
        """Create a TrackMetadata object.

        :param track: track dict from API
        :type track: Optional[dict]
        :param album: album dict from API
        :type album: Optional[dict]
        """
        # embedded information
        self.title: str
        self.album: str
        self.albumartist: str
        self.composer: Optional[str] = None
        self.comment: Optional[str] = None
        self.description: Optional[str] = None
        self.purchase_date: Optional[str] = None
        self.date: Optional[str] = None
        self.grouping: Optional[str] = None
        self.lyrics: Optional[str] = None
        self.encoder: Optional[str] = None
        self.compilation: Optional[str] = None
        self.cover: Optional[str] = None
        self.tracktotal: Optional[int] = None
        self.tracknumber: Optional[int] = None
        self.discnumber: Optional[int] = None
        self.disctotal: Optional[int] = None

        # not included in tags
        self.explicit: Optional[bool] = False
        self.quality: Optional[int] = None
        self.sampling_rate: Optional[int] = None
        self.bit_depth: Optional[int] = None
        self.booklets = None
        self.cover_urls = Optional[OrderedDict]
        self.work: Optional[str]
        self.id: Optional[str]

        # Internals
        self._artist: Optional[str] = None
        self._copyright: Optional[str] = None
        self._genres: Optional[Iterable] = None

        self.__source = source

        if isinstance(track, TrackMetadata):
            self.update(track)
        elif track is not None:
            self.add_track_meta(track)

        if isinstance(album, TrackMetadata):
            self.update(album)
        elif album is not None:
            self.add_album_meta(album)

    def update(self, meta: TrackMetadata):
        """Update the attributes from another TrackMetadata object.

        :param meta:
        :type meta: TrackMetadata
        """
        assert isinstance(meta, TrackMetadata)

        for k, v in meta.asdict().items():
            if v is not None:
                setattr(self, k, v)

    def add_album_meta(self, resp: dict):
        """Parse the metadata from an resp dict returned by the API.

        :param dict resp: from API
        """
        if self.__source == "qobuz":
            # Tags
            self.album = resp.get("title", "Unknown Album")
            self.tracktotal = resp.get("tracks_count", 1)
            self.genre = resp.get("genres_list") or resp.get("genre")
            self.date = resp.get("release_date_original") or resp.get(
                "release_date"
            )
            self.copyright = resp.get("copyright")
            self.albumartist = safe_get(resp, "artist", "name")
            self.composer = safe_get(resp, "composer", "name")
            self.label = resp.get("label")
            self.description = resp.get("description")
            self.disctotal = (
                max(
                    track.get("media_number", 1)
                    for track in safe_get(
                        resp, "tracks", "items", default=[{}]
                    )
                )
                or 1
            )
            self.explicit = resp.get("parental_warning", False)

            if isinstance(self.label, dict):
                self.label = self.label.get("name")

            # Non-embedded information
            self.version = resp.get("version")
            self.cover_urls = OrderedDict(resp["image"])
            self.cover_urls["original"] = self.cover_urls["large"].replace(
                "600", "org"
            )
            self.streamable = resp.get("streamable", False)
            self.bit_depth = resp.get("maximum_bit_depth")
            self.sampling_rate = resp.get("maximum_sampling_rate")
            self.quality = get_quality_id(self.bit_depth, self.sampling_rate)
            self.booklets = resp.get("goodies")

            if self.sampling_rate is not None:
                self.sampling_rate *= 1000

        elif self.__source == "tidal":
            self.album = resp.get("title", "Unknown Album")
            self.tracktotal = resp.get("numberOfTracks", 1)
            # genre not returned by API
            self.date = resp.get("releaseDate")

            self.copyright = resp.get("copyright")
            self.albumartist = safe_get(resp, "artist", "name")
            self.disctotal = resp.get("numberOfVolumes", 1)
            self.isrc = resp.get("isrc")
            # label not returned by API

            # non-embedded
            self.explicit = resp.get("explicit", False)
            # 80, 160, 320, 640, 1280
            uuid = resp.get("cover")
            self.cover_urls = OrderedDict(
                {
                    sk: tidal_cover_url(uuid, size)
                    for sk, size in zip(COVER_SIZES, (160, 320, 640, 1280))
                }
            )
            self.streamable = resp.get("allowStreaming", False)

            if q := resp.get(
                "audioQuality"
            ):  # for album entries in single tracks
                self._get_tidal_quality(q)

        elif self.__source == "deezer":
            self.album = resp.get("title", "Unknown Album")
            self.tracktotal = resp.get("track_total", 0) or resp.get(
                "nb_tracks", 0
            )
            self.disctotal = (
                max(
                    track.get("disk_number")
                    for track in resp.get("tracks", [{}])
                )
                or 1
            )
            self.genre = safe_get(resp, "genres", "data")
            self.date = resp.get("release_date")
            self.albumartist = safe_get(resp, "artist", "name")
            self.label = resp.get("label")
            self.url = resp.get("link")

            # not embedded
            self.explicit = bool(resp.get("parental_warning"))
            self.quality = 2
            self.bit_depth = 16
            self.cover_urls = OrderedDict(
                {
                    sk: resp.get(rk)  # size key, resp key
                    for sk, rk in zip(
                        COVER_SIZES,
                        ("cover", "cover_medium", "cover_large", "cover_xl"),
                    )
                }
            )
            self.sampling_rate = 44100
            self.streamable = True

        elif self.__source == "soundcloud":
            raise NotImplementedError
        else:
            raise InvalidSourceError(self.__source)

    def add_track_meta(self, track: dict):
        """Parse the metadata from a track dict returned by an API.

        :param track:
        """
        if self.__source == "qobuz":
            self.title = track["title"].strip()
            self._mod_title(track.get("version"), track.get("work"))
            self.composer = track.get("composer", {}).get("name")

            self.tracknumber = track.get("track_number", 1)
            self.discnumber = track.get("media_number", 1)
            self.artist = safe_get(track, "performer", "name")

        elif self.__source == "tidal":
            self.title = track["title"].strip()
            self._mod_title(track.get("version"), None)
            self.tracknumber = track.get("trackNumber", 1)
            self.discnumber = track.get("volumeNumber", 1)
            self.artist = track.get("artist", {}).get("name")
            self._get_tidal_quality(track["audioQuality"])

        elif self.__source == "deezer":
            self.title = track["title"].strip()
            self._mod_title(track.get("version"), None)
            self.tracknumber = track.get("track_position", 1)
            self.discnumber = track.get("disk_number", 1)
            self.artist = safe_get(track, "artist", "name")

        elif self.__source == "soundcloud":
            self.title = track["title"].strip()
            self.genre = track["genre"]
            self.artist = self.albumartist = track["user"]["username"]
            self.year = track["created_at"][:4]
            self.label = track["label_name"]
            self.description = track["description"]
            self.album = safe_get(track, "publisher_metadata", "album_title")
            self.copyright = safe_get(track, "publisher_metadata", "p_line")
            self.tracknumber = 0
            self.tracktotal = 0

        else:
            raise ValueError(self.__source)

        if track.get("album"):
            self.add_album_meta(track["album"])

    def _mod_title(self, version: Optional[str], work: Optional[str]):
        """Modify title using the version and work.

        :param version:
        :type version: str
        :param work:
        :type work: str
        """
        if version is not None:
            self.title = f"{self.title} ({version})"
        if work is not None:
            logger.debug("Work found: %s", work)
            self.title = f"{work}: {self.title}"

    def _get_tidal_quality(self, q: str):
        self.quality = TIDAL_Q_MAP[q]
        if self.quality >= 2:
            self.bit_depth = 24 if self.get("quality") == 3 else 16
            self.sampling_rate = 44100

    @property
    def album(self) -> str:
        """Return the album of the track.

        :rtype: str
        """
        assert hasattr(self, "_album"), "Must set album before accessing"

        album = self._album

        if self.get("version") and self["version"] not in album:
            album = f"{self._album} ({self.version})"

        if self.get("work") and self["work"] not in album:
            album = f"{self.work}: {album}"

        return album

    @album.setter
    def album(self, val):
        """Set the value of the album.

        :param val:
        """
        self._album = val

    @property
    def artist(self) -> Optional[str]:
        """Return the value to set for the artist tag.

        Defaults to `self.albumartist` if there is no track artist.

        :rtype: str
        """
        if self._artist is not None:
            return self._artist

        return None

    @artist.setter
    def artist(self, val: str):
        """Set the internal artist variable to val.

        :param val:
        :type val: str
        """
        self._artist = val

    @property
    def genre(self) -> Optional[str]:
        """Format the genre list returned by an API.

        It cleans up the Qobuz Response:
            >>> meta.genre = ['Pop/Rock', 'Pop/Rock→Rock', 'Pop/Rock→Rock→Alternatif et Indé']
            >>> meta.genre
            'Pop, Rock, Alternatif et Indé'

        :rtype: str
        """
        if not self.get("_genres"):
            return None

        if isinstance(self._genres, dict):
            self._genres = self._genres["name"]

        if isinstance(self._genres, list):
            if self.__source == "qobuz":
                genres: Iterable = re.findall(
                    r"([^\u2192\/]+)", "/".join(self._genres)
                )
                genres = set(genres)
            elif self.__source == "deezer":
                genres = ", ".join(g["name"] for g in self._genres)

            return ", ".join(genres)

        elif isinstance(self._genres, str):
            return self._genres

        raise TypeError(f"Genre must be list or str, not {type(self._genres)}")

    @genre.setter
    def genre(self, val: Union[Iterable, dict]):
        """Set the internal `genre` field to the given list.

        It is not formatted until it is requested with `meta.genre`.

        :param val:
        :type val: Union[str, list]
        """
        self._genres = val

    @property
    def copyright(self) -> Optional[str]:
        """Format the copyright string to use unicode characters.

        :rtype: str, None
        """
        if hasattr(self, "_copyright"):
            if self._copyright is None:
                return None
            copyright: str = re.sub(
                r"(?i)\(P\)", PHON_COPYRIGHT, self._copyright
            )
            copyright = re.sub(r"(?i)\(C\)", COPYRIGHT, copyright)
            return copyright

        logger.debug("Accessed copyright tag before setting, returning None")
        return None

    @copyright.setter
    def copyright(self, val: str):
        """Set the internal copyright variable to the given value.

        Only formatted when requested.

        :param val:
        :type val: str
        """
        self._copyright = val

    @property
    def year(self) -> Optional[str]:
        """Return the year published of the track.

        :rtype: str
        """
        if hasattr(self, "_year"):
            return self._year

        if hasattr(self, "date") and isinstance(self.date, str):
            return self.date[:4]

        return None

    @year.setter
    def year(self, val):
        """Set the internal year variable to val.

        :param val:
        """
        self._year = val

    def get_formatter(self) -> dict:
        """Return a dict that is used to apply values to file format strings.

        :rtype: dict
        """
        # the keys in the tuple are the possible keys for format strings
        return {k: getattr(self, k) for k in TRACK_KEYS}

    def tags(self, container: str = "flac") -> Generator:
        """Create a generator of key, value pairs for use with mutagen.

        The *_KEY dicts are organized in the format:

            >>> {attribute_name: key_to_use_for_metadata}

        They are then converted to the format

            >>> {key_to_use_for_metadata: value_of_attribute}

        so that they can be used like this:

            >>> audio = MP4(path)
            >>> for k, v in meta.tags(container='MP4'):
            ...     audio[k] = v
            >>> audio.save()

        :param container: the container format
        :type container: str
        :rtype: Generator
        """
        container = container.lower()
        if container in ("flac", "vorbis"):
            return self.__gen_flac_tags()
        if container in ("mp3", "id3"):
            return self.__gen_mp3_tags()
        if container in ("alac", "m4a", "mp4", "aac"):
            return self.__gen_mp4_tags()

        raise InvalidContainerError(f"Invalid container {container}")

    def __gen_flac_tags(self) -> Generator:
        """Generate key, value pairs to tag FLAC files.

        :rtype: Tuple[str, str]
        """
        for k, v in FLAC_KEY.items():
            tag = getattr(self, k)
            if tag:
                if k in (
                    "tracknumber",
                    "discnumber",
                    "tracktotal",
                    "disctotal",
                ):
                    tag = f"{int(tag):02}"

                logger.debug("Adding tag %s: %s", v, tag)
                yield (v, str(tag))

    def __gen_mp3_tags(self) -> Generator:
        """Generate key, value pairs to tag MP3 files.

        :rtype: Tuple[str, str]
        """
        for k, v in MP3_KEY.items():
            if k == "tracknumber":
                text = f"{self.tracknumber}/{self.tracktotal}"
            elif k == "discnumber":
                text = f"{self.discnumber}/{self.get('disctotal', 1)}"
            else:
                text = getattr(self, k)

            if text is not None and v is not None:
                yield (v.__name__, v(encoding=3, text=text))

    def __gen_mp4_tags(self) -> Generator:
        """Generate key, value pairs to tag ALAC or AAC files.

        :rtype: Tuple[str, str]
        """
        for k, v in MP4_KEY.items():
            if k == "tracknumber":
                text = [(self.tracknumber, self.tracktotal)]
            elif k == "discnumber":
                text = [(self.discnumber, self.get("disctotal", 1))]
            else:
                text = getattr(self, k)

            if v is not None and text is not None:
                yield (v, text)

    def asdict(self) -> dict:
        """Return a dict representation of self.

        :rtype: dict
        """
        ret = {}
        for attr in dir(self):
            if not attr.startswith("_") and not callable(getattr(self, attr)):
                ret[attr] = getattr(self, attr)

        return ret

    def __setitem__(self, key, val):
        """Dict-like access for tags.

        :param key:
        :param val:
        """
        setattr(self, key, val)

    def __getitem__(self, key):
        """Dict-like access for tags.

        :param key:
        """
        return getattr(self, key)

    def get(self, key, default=None):
        """Return the requested attribute of the object, with a default value.

        :param key:
        :param default:
        """
        if hasattr(self, key):
            res = self.__getitem__(key)
            if res is not None:
                return res

            return default

        return default

    def set(self, key, val) -> str:
        """Set an attribute.

        Equivalent to:
            >>> meta[key] = val

        :param key:
        :param val:
        :rtype: str
        """
        return self.__setitem__(key, val)

    def __hash__(self) -> int:
        """Get a hash of this.

        Warning: slow.

        :rtype: int
        """
        return sum(
            hash(v) for v in self.asdict().values() if isinstance(v, Hashable)
        )

    def __repr__(self) -> str:
        """Return the string representation of the metadata object.

        :rtype: str
        """
        # TODO: make a more readable repr
        return f"<TrackMetadata object {hex(hash(self))}>"
