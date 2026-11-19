"""Runs the grading of up to 10 submissions in parallel.

Each submission goes: upload (files only) -> Google processing -> analysis ->
report -> delete the uploaded video. The delete runs in a `finally`, so it
also happens when a step fails or the teacher cancels.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import Config
from .gemini_service import GeminiService, GradingError, Usage
from .media import Submission
from .pricing import actual_cost
from .prompt import STATUSES
from .rubric import Item, Requirement, Rubric

log = logging.getLogger(__name__)

STATUS_LABELS_ES = {
    "cumple": "Cumple",
    "cumple_parcialmente": "Cumple parcialmente",
    "se_muestra_pero_falla": "Se muestra pero falla",
    "no_se_muestra": "No se muestra",
    "requiere_revision": "Requiere revisión",
}
STATUS_LABELS_EN = {
    "cumple": "Meets",
    "cumple_parcialmente": "Partial",
    "se_muestra_pero_falla": "Fails",
    "no_se_muestra": "Not shown",
    "requiere_revision": "Review",
}


@dataclass
class Evidence:
    seconds: int
    text: str


@dataclass
class ItemResult:
    requirement: Requirement
    item: Item
    status: str
    evidences: list[Evidence]
    missing: str = ""
    note: str = ""


@dataclass
class SubmissionResult:
    submission: Submission
    rubric: Rubric
    items: list[ItemResult]
    duration_s: float | None
    narration_lang: str
    ui_lang: str
    illegible: list[tuple[int, int, str]]
    model_label: str
    accuracy: str
    graded_at: datetime
    usage: Usage
    cost_usd: float
    report_path: Path | None = None

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for r in self.items:
            out[r.status] += 1
        return out

    def over_limit(self) -> bool:
        lim = self.rubric.max_duration_s
        return bool(lim and self.duration_s and self.duration_s > lim + 0.5)


def _clean_text(value, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def normalize(
    raw: dict,
    sub: Submission,
    rubric: Rubric,
    config: Config,
    accuracy: str,
    usage: Usage,
) -> SubmissionResult:
    """Check Gemini's answer against the rubric and apply the safety rules."""
    model_duration = _to_int(raw.get("duracion_segundos"), 0) or None
    duration = sub.duration_s or model_duration
    max_second = int(duration) if duration else None

    by_id: dict[str, dict] = {}
    for entry in raw.get("items") or []:
        if isinstance(entry, dict) and entry.get("id") is not None:
            by_id.setdefault(str(entry["id"]).strip(), entry)

    items: list[ItemResult] = []
    for req, item in rubric.all_items():
        entry = by_id.get(item.id)
        if entry is None:
            items.append(
                ItemResult(req, item, "requiere_revision", [], note="El modelo no devolvió un resultado para este ítem.")
            )
            continue
        status = str(entry.get("estado", "")).strip()
        if status not in STATUSES:
            status = "requiere_revision"
        evidences = []
        for ev in entry.get("evidencias") or []:
            if not isinstance(ev, dict):
                continue
            text = _clean_text(ev.get("descripcion"))
            if not text:
                continue
            seconds = max(0, _to_int(ev.get("segundo"), 0))
            if max_second is not None:
                seconds = min(seconds, max_second)
            evidences.append(Evidence(seconds, text))
        evidences.sort(key=lambda e: e.seconds)
        missing = _clean_text(entry.get("faltante"))
        note = _clean_text(entry.get("observacion"))

        if status in ("cumple", "cumple_parcialmente") and not evidences:
            status = "requiere_revision"
            note = (note + " " if note else "") + "Se marcó como cumplido sin evidencia con minuto; conviene revisarlo."
        if status == "no_se_muestra":
            evidences = []
        items.append(ItemResult(req, item, status, evidences, missing, note))

    illegible = []
    for seg in raw.get("tramos_poco_legibles") or []:
        if not isinstance(seg, dict):
            continue
        start = max(0, _to_int(seg.get("desde_segundo")))
        end = max(start, _to_int(seg.get("hasta_segundo"), start))
        if max_second is not None:
            start, end = min(start, max_second), min(end, max_second)
        illegible.append((start, end, _clean_text(seg.get("nota"), 300)))

    return SubmissionResult(
        submission=sub,
        rubric=rubric,
        items=items,
        duration_s=duration,
        narration_lang=_clean_text(raw.get("idioma_narracion"), 40) or "no determinado",
        ui_lang=_clean_text(raw.get("idioma_interfaz"), 40) or "no determinado",
        illegible=illegible,
        model_label=config.model_label,
        accuracy=accuracy,
        graded_at=datetime.now(),
        usage=usage,
        cost_usd=actual_cost(config, usage.prompt_tokens, usage.output_tokens),
    )


# ------------------------------------------------------------------ events
@dataclass
class Event:
    kind: str  # "status", "done", "error", "cost", "finished"
    uid: str = ""
    state: str = ""  # waiting, uploading, processing, analyzing, done, error, cancelled
    detail: str = ""
    progress: float | None = None
    result: SubmissionResult | None = None
    error: GradingError | None = None
    total_cost: float = 0.0


class GradingSession:
    def __init__(
        self,
        service: GeminiService,
        rubric: Rubric,
        submissions: list[Submission],
        accuracy: str,
        config: Config,
        out_dir: Path,
        emit: Callable[[Event], None],
        report_writer: Callable[[SubmissionResult, Path], Path] | None = None,
        retry_delays: tuple[float, ...] = (5, 15, 30),
    ):
        self.service = service
        self.rubric = rubric
        self.submissions = {s.uid: s for s in submissions}
        self.order = [s.uid for s in submissions]
        self.accuracy = accuracy
        self.config = config
        self.out_dir = out_dir
        self.emit = emit
        self.retry_delays = retry_delays
        if report_writer is None:
            from .report import write_report

            report_writer = write_report
        self.report_writer = report_writer

        self.cancel_event = threading.Event()
        self.results: dict[str, SubmissionResult] = {}
        self.errors: dict[str, GradingError] = {}
        self.total_cost = 0.0
        self._lock = threading.Lock()
        self._uploaded: dict[str, str] = {}  # uid -> Gemini file name, while it exists
        self._pending = 0
        workers = max(1, min(int(config.get("max_parallel")), len(submissions) or 1))
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="grade")

    # ------------------------------------------------------------ control
    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for uid in self.order:
            self.emit(Event("status", uid, "waiting", "Starts as soon as a slot frees up"))
        for uid in self.order:
            self._schedule(uid)

    def retry(self, uid: str) -> None:
        self.cancel_event.clear()
        self.errors.pop(uid, None)
        self.emit(Event("status", uid, "waiting", "Waiting"))
        self._schedule(uid)

    def cancel(self) -> None:
        self.cancel_event.set()

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    def delete_remaining_uploads(self) -> int:
        """Used when the app closes in the middle of a grading."""
        with self._lock:
            names = list(self._uploaded.values())
        return sum(1 for name in names if self.service.delete(name))

    def _schedule(self, uid: str) -> None:
        with self._lock:
            self._pending += 1
        self._pool.submit(self._run_one, uid)

    # ------------------------------------------------------------ work
    def _with_retries(self, fn, uid: str, label: str):
        attempt = 0
        while True:
            try:
                return fn()
            except GradingError as err:
                if not err.retryable or attempt >= len(self.retry_delays) or self.cancel_event.is_set():
                    raise
                wait = self.retry_delays[attempt]
                attempt += 1
                log.info("%s for %s failed (%s); retrying in %ss", label, uid, err.kind, wait)
                self.emit(Event("status", uid, "waiting", f"{err.message} Retrying in {int(wait)} s…"))
                if self.cancel_event.wait(wait):
                    raise GradingError(GradingError.CANCELLED, "Cancelled.") from err

    def _run_one(self, uid: str) -> None:
        sub = self.submissions[uid]
        file_obj = None
        try:
            if self.cancel_event.is_set():
                raise GradingError(GradingError.CANCELLED, "Cancelled.")

            if not sub.is_youtube:
                last = [0.0]

                def progress(done: int, total: int) -> None:
                    now = time.monotonic()
                    if now - last[0] < 0.25 and done < total:
                        return
                    last[0] = now
                    frac = done / total if total else 0
                    self.emit(Event("status", uid, "uploading", f"Uploading… {int(frac * 100)}%", frac))

                self.emit(Event("status", uid, "uploading", "Uploading… 0%", 0.0))
                file_obj = self._with_retries(
                    lambda: self.service.upload(sub, self.cancel_event, progress), uid, "upload"
                )
                with self._lock:
                    self._uploaded[uid] = file_obj.name
                self.emit(Event("status", uid, "processing", "Google is preparing the video"))
                file_obj = self.service.wait_until_active(file_obj, self.cancel_event)

            if self.cancel_event.is_set():
                raise GradingError(GradingError.CANCELLED, "Cancelled.")
            detail = "Read directly from YouTube" if sub.is_youtube else "Checking the rubric"
            self.emit(Event("status", uid, "analyzing", detail))
            raw, usage = self._with_retries(
                lambda: self.service.analyze(sub, file_obj, self.rubric, self.accuracy), uid, "analysis"
            )
            if self.cancel_event.is_set():
                raise GradingError(GradingError.CANCELLED, "Cancelled.")

            result = normalize(raw, sub, self.rubric, self.config, self.accuracy, usage)
            result.report_path = self.report_writer(result, self.out_dir)
            self._save_json(result, raw)
            with self._lock:
                self.results[uid] = result
                self.total_cost += result.cost_usd
                total = self.total_cost
            self.emit(Event("cost", total_cost=total))
            done_detail = "Video deleted from Google" if file_obj is not None else "Report ready"
            self._finish(file_obj, uid)
            file_obj = None
            self.emit(Event("done", uid, "done", done_detail, result=result))
        except Exception as exc:
            from .gemini_service import classify

            err = classify(exc, sub.is_youtube)
            if err.kind == GradingError.OTHER:
                log.exception("Grading %s failed", sub.display_name)
            with self._lock:
                self.errors[uid] = err
            state = "cancelled" if err.kind == GradingError.CANCELLED else "error"
            self._finish(file_obj, uid)
            file_obj = None
            self.emit(Event("error", uid, state, err.message, error=err))
        finally:
            if file_obj is not None:
                self._finish(file_obj, uid)
            with self._lock:
                self._pending -= 1
                finished = self._pending == 0
            if finished:
                self.emit(Event("finished", total_cost=self.total_cost))

    def _finish(self, file_obj, uid: str) -> None:
        """Delete the uploaded video from Google (if any)."""
        if file_obj is None:
            with self._lock:
                name = self._uploaded.pop(uid, None)
        else:
            name = file_obj.name
            with self._lock:
                self._uploaded.pop(uid, None)
        if name:
            if not self.service.delete(name):
                log.error("Could not delete %s; it will be removed at next startup or in 48 h", name)

    def _save_json(self, result: SubmissionResult, raw: dict) -> None:
        if not result.report_path:
            return
        data = {
            "video": result.submission.display_name,
            "fuente": result.submission.source,
            "modelo": self.config.model_id,
            "precision": self.accuracy,
            "fecha": result.graded_at.isoformat(timespec="seconds"),
            "tokens_entrada": result.usage.prompt_tokens,
            "tokens_salida": result.usage.output_tokens,
            "costo_usd": round(result.cost_usd, 4),
            "respuesta_modelo": raw,
        }
        path = result.report_path.with_suffix(".json")
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not save %s: %s", path, exc)
