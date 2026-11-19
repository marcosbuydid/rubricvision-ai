"""Submissions: local video files and YouTube links.

Only metadata is read locally (duration and resolution, from the file
header). The video itself is never decoded or converted on this PC.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Formats the Gemini Files API accepts for video, by extension.
SUPPORTED_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpg",
    ".webm": "video/webm",
    ".wmv": "video/wmv",
    ".flv": "video/x-flv",
    ".3gp": "video/3gpp",
}
MAX_UPLOAD_BYTES = 2 * 1024**3  # Files API limit per file

YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class SubmissionError(Exception):
    """A file or link that can't be added. The message is shown to the teacher."""


@dataclass
class Submission:
    kind: str  # "file" or "youtube"
    source: str  # file path or canonical YouTube URL
    display_name: str
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    size_bytes: int | None = None
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    youtube_id: str | None = None

    @property
    def is_youtube(self) -> bool:
        return self.kind == "youtube"

    @property
    def resolution_label(self) -> str | None:
        if not self.width or not self.height:
            return None
        return f"{min(self.width, self.height)}p"

    def is_low_resolution(self, min_height: int) -> bool:
        if not self.width or not self.height:
            return False
        return min(self.width, self.height) < min_height

    def is_over_limit(self, max_duration_s: int | None) -> bool:
        return bool(max_duration_s and self.duration_s and self.duration_s > max_duration_s + 0.5)

    def timestamp_url(self, seconds: int) -> str | None:
        if not self.youtube_id:
            return None
        return f"https://www.youtube.com/watch?v={self.youtube_id}&t={max(0, int(seconds))}s"


def read_video_metadata(path: Path) -> tuple[float | None, int | None, int | None]:
    """(duration in seconds, width, height). Any of them may be None."""
    try:
        from hachoir.metadata import extractMetadata
        from hachoir.parser import createParser
    except ImportError:  # pragma: no cover - packaging problem
        log.warning("hachoir not available; video metadata disabled")
        return None, None, None
    try:
        parser = createParser(str(path))
        if parser is None:
            return None, None, None
        with parser:
            meta = extractMetadata(parser)
    except Exception as exc:  # hachoir raises many kinds of errors on odd files
        log.info("Could not read metadata of %s: %s", path, exc)
        return None, None, None
    if meta is None:
        return None, None, None

    def value(key):
        try:
            return meta.get(key) if meta.has(key) else None
        except (ValueError, KeyError):
            return None

    duration = value("duration")
    return (
        duration.total_seconds() if duration is not None else None,
        value("width"),
        value("height"),
    )


def submission_from_file(path: str | Path) -> Submission:
    path = Path(path)
    if not path.is_file():
        raise SubmissionError(f"File not found: {path}")
    ext = path.suffix.lower()
    if ext == ".mkv":
        raise SubmissionError(
            f"{path.name}: MKV videos can't be analyzed by Gemini. "
            "Ask for an MP4 (OBS can remux MKV to MP4 from File > Remux Recordings)."
        )
    if ext not in SUPPORTED_VIDEO_TYPES:
        raise SubmissionError(
            f"{path.name}: unsupported format. Use MP4, MOV, AVI, WEBM, WMV, MPEG, FLV or 3GP."
        )
    size = path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise SubmissionError(f"{path.name} is larger than 2 GB, the maximum Gemini accepts.")
    if size == 0:
        raise SubmissionError(f"{path.name} is empty.")
    duration, width, height = read_video_metadata(path)
    return Submission(
        kind="file",
        source=str(path),
        display_name=path.name,
        size_bytes=size,
        duration_s=duration,
        width=width,
        height=height,
        mime_type=SUPPORTED_VIDEO_TYPES[ext],
    )


def parse_youtube_id(url: str) -> str | None:
    url = url.strip()
    if not url:
        return None
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    candidate = None
    if host == "youtu.be":
        candidate = parts.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        if parts.path == "/watch":
            candidate = urllib.parse.parse_qs(parts.query).get("v", [None])[0]
        else:
            segs = [s for s in parts.path.split("/") if s]
            if len(segs) >= 2 and segs[0] in ("shorts", "live", "embed", "v"):
                candidate = segs[1]
    if candidate and YOUTUBE_ID.match(candidate):
        return candidate
    return None


def fetch_youtube_title(video_id: str, timeout: float = 6.0) -> str | None:
    """Public title through YouTube's oEmbed endpoint (no download, no key)."""
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(
        f"https://www.youtube.com/watch?v={video_id}", safe=""
    )
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        title = str(data.get("title") or "").strip()
        return title or None
    except Exception as exc:  # unlisted/private videos answer 401/404; offline raises
        log.info("No oEmbed title for %s: %s", video_id, exc)
        return None


def submission_from_youtube(url: str) -> Submission:
    video_id = parse_youtube_id(url)
    if not video_id:
        raise SubmissionError("That doesn't look like a YouTube video link.")
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    return Submission(
        kind="youtube",
        source=canonical,
        display_name=f"youtube.com/watch?v={video_id}",
        youtube_id=video_id,
    )
