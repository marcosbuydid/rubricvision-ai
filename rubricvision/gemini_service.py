"""Everything that talks to the Gemini API.

Uploaded videos are always deleted: right after each grading (success or
error), and leftovers from interrupted sessions are removed at startup.
YouTube links are read by Gemini directly, so nothing is uploaded for them.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .config import Config
from .media import Submission
from .prompt import SYSTEM_INSTRUCTION, build_user_prompt, response_schema
from .rubric import Rubric

log = logging.getLogger(__name__)

UPLOAD_PREFIX = "rubricvision-"


class GradingError(Exception):
    """An error with a kind the interface knows how to explain."""

    KEY_INVALID = "key_invalid"
    NO_BALANCE = "no_balance"
    QUOTA = "quota"
    NOT_PUBLIC = "not_public"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    SERVER = "server"
    FILE_FAILED = "file_failed"
    BAD_RESPONSE = "bad_response"
    MODEL_UNAVAILABLE = "model_unavailable"
    CANCELLED = "cancelled"
    OTHER = "other"

    RETRYABLE = {RATE_LIMITED, NETWORK, SERVER}

    def __init__(self, kind: str, message: str, detail: str = ""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.kind in self.RETRYABLE


def _error_text(exc: Exception) -> str:
    parts = [str(getattr(exc, "message", "") or ""), str(getattr(exc, "status", "") or "")]
    details = getattr(exc, "details", None)
    if details:
        try:
            parts.append(json.dumps(details))
        except (TypeError, ValueError):
            parts.append(str(details))
    parts.append(str(exc))
    return " ".join(parts).lower()


def classify(exc: Exception, is_youtube: bool = False) -> GradingError:
    """Turn an SDK or network exception into a GradingError."""
    if isinstance(exc, GradingError):
        return exc
    from google.genai import errors

    if isinstance(exc, errors.APIError):
        code = int(getattr(exc, "code", 0) or 0)
        status = str(getattr(exc, "status", "") or "")
        text = _error_text(exc)
        raw = (getattr(exc, "message", "") or str(exc))[:500]

        if code == 401 or status == "UNAUTHENTICATED" or "api key not valid" in text or "api_key_invalid" in text:
            return GradingError(GradingError.KEY_INVALID, "The API key isn't valid.", raw)
        if code == 402 or any(w in text for w in ("prepay", "prepaid", "credit balance", "insufficient balance", "billing account", "out of credits", "payment required")):
            return GradingError(
                GradingError.NO_BALANCE,
                "Your Gemini balance has run out. Top up your balance in Google AI Studio, then click Retry.",
                raw,
            )
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            if "quota" in text and ("exceeded" in text or "free" in text):
                return GradingError(
                    GradingError.QUOTA,
                    "Gemini rejected the request for lack of quota. On the free tier, set up billing; "
                    "on prepay, check your balance in Google AI Studio.",
                    raw,
                )
            return GradingError(GradingError.RATE_LIMITED, "Gemini is busy (too many requests).", raw)
        if code == 404 and "model" in text and not is_youtube:
            return GradingError(
                GradingError.MODEL_UNAVAILABLE,
                "The configured model isn't available for this key. Check the model in config.json.",
                raw,
            )
        if is_youtube and code in (400, 403, 404):
            return GradingError(
                GradingError.NOT_PUBLIC,
                "This YouTube video isn't public. Gemini can only read public videos, not unlisted or "
                "private ones. Ask the student to set its visibility to Public, then click Retry.",
                raw,
            )
        if code >= 500:
            return GradingError(GradingError.SERVER, "Google's servers had a temporary problem.", raw)
        return GradingError(GradingError.OTHER, f"Gemini returned an error ({code}): {raw}", raw)

    try:
        import httpx

        network_types: tuple = (httpx.TransportError, ConnectionError, TimeoutError)
    except ImportError:  # pragma: no cover
        network_types = (ConnectionError, TimeoutError)
    if isinstance(exc, network_types):
        return GradingError(GradingError.NETWORK, "Couldn't reach Google. Check your internet connection.", str(exc))
    return GradingError(GradingError.OTHER, f"Unexpected error: {exc}", repr(exc))


class _ProgressFile(io.FileIO):
    """A file that reports how many bytes were read and can be cancelled."""

    def __init__(self, path: str, on_progress: Callable[[int, int], None], cancel: threading.Event):
        super().__init__(path, "rb")
        self._total = self.seek(0, io.SEEK_END)
        self.seek(0)
        self._on_progress = on_progress
        self._cancel = cancel

    def _report(self) -> None:
        if self._cancel.is_set():
            raise GradingError(GradingError.CANCELLED, "Cancelled.")
        try:
            self._on_progress(self.tell(), self._total)
        except Exception:  # never break an upload because of the UI
            pass

    def read(self, size: int = -1) -> bytes:
        data = super().read(size)
        self._report()
        return data

    def readinto(self, buffer) -> int:
        n = super().readinto(buffer)
        self._report()
        return n


@dataclass
class Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0


class GeminiService:
    def __init__(self, api_key: str, config: Config):
        from google import genai
        from google.genai import types

        self._types = types
        self.config = config
        timeout_ms = int(config.get("request_timeout_seconds")) * 1000
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))

    # ------------------------------------------------------------ key check
    def verify_key(self) -> None:
        """Free call: lists one model. Raises GradingError on failure."""
        try:
            pager = self.client.models.list(config={"page_size": 1})
            next(iter(pager), None)
        except Exception as exc:
            err = classify(exc)
            if err.kind == GradingError.OTHER and getattr(exc, "code", 0) in (400, 403):
                err = GradingError(GradingError.KEY_INVALID, "The API key isn't valid.", err.detail)
            raise err from exc

    def check_model(self) -> bool:
        """True if the configured model exists for this key (free call)."""
        try:
            self.client.models.get(model=self.config.model_id)
            return True
        except Exception as exc:
            log.warning("Model check failed: %s", exc)
            return False

    # ------------------------------------------------------------ upload
    def upload(
        self,
        sub: Submission,
        cancel: threading.Event,
        on_progress: Callable[[int, int], None],
    ):
        types = self._types
        display = f"{UPLOAD_PREFIX}{uuid.uuid4().hex[:12]}"
        with _ProgressFile(sub.source, on_progress, cancel) as fh:
            try:
                return self.client.files.upload(
                    file=fh,
                    config=types.UploadFileConfig(mime_type=sub.mime_type, display_name=display),
                )
            except GradingError:
                raise
            except Exception as exc:
                cause = exc.__context__ or exc.__cause__
                if isinstance(cause, GradingError):
                    raise cause from exc
                raise classify(exc) from exc

    def wait_until_active(self, file_obj, cancel: threading.Event):
        """Google processes the upload before it can be analyzed."""
        types = self._types
        deadline = time.monotonic() + int(self.config.get("file_processing_timeout_seconds"))
        current = file_obj
        while True:
            state = getattr(current.state, "name", str(current.state))
            if state == types.FileState.ACTIVE.name:
                return current
            if state == types.FileState.FAILED.name:
                raise GradingError(
                    GradingError.FILE_FAILED,
                    "Google couldn't process this video file. Ask the student for an MP4 export.",
                    str(getattr(current, "error", "")),
                )
            if cancel.is_set():
                raise GradingError(GradingError.CANCELLED, "Cancelled.")
            if time.monotonic() > deadline:
                raise GradingError(GradingError.SERVER, "Google took too long to process the video.")
            time.sleep(3)
            try:
                current = self.client.files.get(name=file_obj.name)
            except Exception as exc:
                raise classify(exc) from exc

    # ------------------------------------------------------------ analysis
    def analyze(self, sub: Submission, file_obj, rubric: Rubric, accuracy: str) -> tuple[dict, Usage]:
        types = self._types
        if sub.is_youtube:
            video_part = types.Part(
                file_data=types.FileData(file_uri=sub.source),
                video_metadata=types.VideoMetadata(fps=1),
            )
        else:
            video_part = types.Part(
                file_data=types.FileData(file_uri=file_obj.uri, mime_type=file_obj.mime_type),
                video_metadata=types.VideoMetadata(fps=1),
            )
        resolution = (
            types.MediaResolution.MEDIA_RESOLUTION_HIGH
            if accuracy == "high"
            else types.MediaResolution.MEDIA_RESOLUTION_MEDIUM
        )
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_json_schema=response_schema(rubric),
            media_resolution=resolution,
        )
        try:
            response = self.client.models.generate_content(
                model=self.config.model_id,
                contents=[types.Content(role="user", parts=[video_part, types.Part(text=build_user_prompt(rubric))])],
                config=config,
            )
        except Exception as exc:
            raise classify(exc, is_youtube=sub.is_youtube) from exc

        meta = getattr(response, "usage_metadata", None)
        usage = Usage(
            prompt_tokens=int(getattr(meta, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(meta, "candidates_token_count", 0) or 0)
            + int(getattr(meta, "thoughts_token_count", 0) or 0),
        )
        text = getattr(response, "text", None) or ""
        if not text.strip():
            reason = ""
            try:
                reason = str(response.candidates[0].finish_reason)
            except (AttributeError, IndexError, TypeError):
                pass
            raise GradingError(GradingError.BAD_RESPONSE, "Gemini returned an empty answer.", reason)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise GradingError(GradingError.BAD_RESPONSE, "Gemini's answer wasn't valid JSON.", text[:500]) from exc
        if not isinstance(data, dict):
            raise GradingError(GradingError.BAD_RESPONSE, "Gemini's answer had an unexpected format.", text[:500])
        return data, usage

    # ------------------------------------------------------------ deletion
    def delete(self, name: str) -> bool:
        """Delete an uploaded video. Retries a few times; True when gone."""
        from google.genai import errors

        for attempt in range(3):
            try:
                self.client.files.delete(name=name)
                log.info("Deleted %s", name)
                return True
            except errors.APIError as exc:
                if getattr(exc, "code", 0) == 404:
                    return True
                log.warning("Delete %s failed (attempt %d): %s", name, attempt + 1, exc)
            except Exception as exc:
                log.warning("Delete %s failed (attempt %d): %s", name, attempt + 1, exc)
            time.sleep(2 * (attempt + 1))
        return False

    def cleanup_leftovers(self) -> int:
        """Delete RubricVision AI uploads left by a session that ended abruptly.

        Only files older than a few hours are touched, so a RubricVision AI open on
        another PC with the same key isn't disturbed.
        """
        min_age = timedelta(hours=float(self.config.get("leftover_upload_min_age_hours")))
        now = datetime.now(timezone.utc)
        deleted = 0
        try:
            for f in self.client.files.list(config={"page_size": 100}):
                if not (f.display_name or "").startswith(UPLOAD_PREFIX):
                    continue
                created = f.create_time
                if created and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created and now - created < min_age:
                    continue
                if self.delete(f.name):
                    deleted += 1
        except Exception as exc:
            log.warning("Leftover cleanup skipped: %s", exc)
        return deleted
