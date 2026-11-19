"""Spanish HTML report for one graded submission.

Everything coming from the model or the rubric is escaped: the report never
runs scripts and doesn't load anything from the internet.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from html import escape
from pathlib import Path

from . import APP_NAME, COPYRIGHT
from .grading import STATUS_LABELS_ES, SubmissionResult
from .prompt import STATUSES
from .rubric import format_mmss

log = logging.getLogger(__name__)

STATUS_STYLE = {
    # background, text color, icon
    "cumple": ("#E6F4EA", "#1B6E2E", "check"),
    "cumple_parcialmente": ("#E5EEF9", "#1F4E8C", "half"),
    "se_muestra_pero_falla": ("#FCE8E6", "#A8231B", "cross"),
    "no_se_muestra": ("#EEF0F3", "#4A5160", "minus"),
    "requiere_revision": ("#FFF1DB", "#8A4B00", "warn"),
}

ICONS = {
    "check": '<path d="M5 12.5l4.5 4.5L19 7"/>',
    "cross": '<path d="M6 6l12 12M18 6L6 18"/>',
    "minus": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "warn": '<path d="M12 3l10 18H2z"/><path d="M12 10v4"/><path d="M12 17.5h.01"/>',
    "half": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 11v6"/><path d="M12 7.5h.01"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5"/>',
}

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#F2F4F7;color:#1B1F27;font-family:"Segoe UI",Inter,system-ui,-apple-system,sans-serif;line-height:1.45}
a{color:#0B5CAD}a:hover{color:#08427D}
.wrap{max-width:980px;margin:0 auto;padding:40px 20px 56px;display:flex;flex-direction:column;gap:16px}
.card{background:#fff;border:1px solid #D5D9E0;border-radius:10px}
header.card{padding:26px 30px;display:flex;flex-direction:column;gap:16px}
.eyebrow{font-size:13px;font-weight:600;color:#0B5CAD;letter-spacing:.4px;text-transform:uppercase}
h1{margin:0;font-size:26px;line-height:1.25;overflow-wrap:anywhere}
.source{font-size:14px;overflow-wrap:anywhere;display:inline-flex;gap:6px;align-items:center}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px 24px;font-size:14px;overflow-wrap:anywhere}
.meta div{min-width:0;display:flex;flex-direction:column;gap:2px}
.meta span:first-child{font-size:12px;color:#5C6370}
.meta span:last-child{font-weight:500}
.note{display:flex;gap:10px;align-items:flex-start;padding:11px 14px;border-radius:8px;background:#FFF1DB;border:1px solid #F0C98A;font-size:14px;color:#5C3400}
.note svg{flex-shrink:0;margin-top:2px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{display:flex;flex-direction:column;gap:2px;padding:12px 14px;border-radius:8px}
.tile b{font-size:26px}
.tile span{font-size:13px;font-weight:500}
.disclaimer{margin:0;display:flex;gap:10px;font-size:13px;color:#4A5160}
.disclaimer svg{flex-shrink:0;margin-top:2px}
h2{margin:10px 0 0;font-size:18px}
.req{padding:0;overflow:hidden}
.req-head{padding:16px 20px;border-bottom:1px solid #E6E9EE;display:flex;flex-wrap:wrap;gap:6px 16px;align-items:baseline;justify-content:space-between}
.req-head h3{margin:0;font-size:17px}
.req-head .profile{font-size:13px;color:#4A5160}
.req-head .subtotal{font-size:13px;font-weight:600;color:#33394A}
.item{padding:14px 20px;border-bottom:1px solid #EEF0F3;display:flex;flex-direction:column;gap:6px}
.item:last-child{border-bottom:0}
.item.review{background:#FFFBF3;box-shadow:inset 3px 0 0 #E8A33D}
.item-top{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center}
.item-top h4{margin:0;font-size:15px;font-weight:600;flex:1 1 360px}
.badge{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:13px;font-size:13px;font-weight:600;white-space:nowrap}
.item p{margin:0;font-size:14px;color:#33394A}
.ts{font-weight:600;font-variant-numeric:tabular-nums}
footer{display:flex;flex-direction:column;gap:4px;font-size:12px;color:#4A5160;text-align:center;padding-top:8px}
@media print{body{background:#fff}.wrap{padding:0}.card{break-inside:avoid}}
"""


def _icon(name: str, color: str, size: int = 14) -> str:
    return (
        f'<svg aria-hidden="true" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" '
        f'style="color:{color}">{ICONS[name]}</svg>'
    )


def _badge(status: str) -> str:
    bg, fg, icon = STATUS_STYLE[status]
    return f'<span class="badge" style="background:{bg};color:{fg}">{_icon(icon, fg)}{escape(STATUS_LABELS_ES[status])}</span>'


def _ts(result: SubmissionResult, seconds: int) -> str:
    label = format_mmss(seconds)
    url = result.submission.timestamp_url(seconds)
    if url:
        return f'<a class="ts" href="{escape(url)}" target="_blank" rel="noopener">{label}</a>'
    return f'<span class="ts">{label}</span>'


def _lang(result: SubmissionResult) -> str:
    n, u = result.narration_lang, result.ui_lang
    if n == u:
        return escape(n.capitalize())
    return f"Narración: {escape(n)}<br>Interfaz: {escape(u)}"


def safe_file_stem(name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)
    stem = unicodedata.normalize("NFKD", stem)
    stem = "".join(c for c in stem if not unicodedata.combining(c))
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return (stem or "entrega")[:80]


DATA_BLOCK_ID = "rubricvision-data"
DATA_VERSION = 1


def report_data(result: SubmissionResult, min_height: int = 720) -> dict:
    """Everything the report shows, as plain data (embedded in the HTML)."""
    sub = result.submission
    return {
        "version": DATA_VERSION,
        "video": sub.display_name,
        "source": sub.source if sub.is_youtube else Path(sub.source).name,
        "is_youtube": sub.is_youtube,
        "youtube_id": sub.youtube_id,
        "graded_at": result.graded_at.isoformat(timespec="seconds"),
        "model": result.model_label,
        "accuracy": result.accuracy,
        "duration_s": round(result.duration_s) if result.duration_s else None,
        "max_duration_s": result.rubric.max_duration_s,
        "over_limit": result.over_limit(),
        "resolution": None if sub.is_youtube else sub.resolution_label,
        "low_resolution": sub.is_low_resolution(min_height),
        "narration_lang": result.narration_lang,
        "ui_lang": result.ui_lang,
        "rubric_file": result.rubric.file_name,
        "cost_usd": round(result.cost_usd, 4),
        "illegible": [{"from_s": a, "to_s": b, "note": n} for a, b, n in result.illegible],
        "items": [
            {
                "requirement_id": r.requirement.id,
                "requirement": r.requirement.name,
                "profile": r.requirement.profile,
                "item_id": r.item.id,
                "item": r.item.text,
                "status": r.status,
                "evidences": [{"seconds": e.seconds, "text": e.text} for e in r.evidences],
                "missing": r.missing,
                "note": r.note,
            }
            for r in result.items
        ],
    }


def _data_block(data: dict) -> str:
    # Every "<" is written as \u003c so text from the model can never open or close a tag.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return f'<script type="application/json" id="{DATA_BLOCK_ID}">{payload}</script>'


def build_html(result: SubmissionResult, min_height: int = 720) -> str:
    sub = result.submission
    rubric = result.rubric
    title = sub.display_name
    counts = result.counts()

    meta = []
    duration = format_mmss(result.duration_s) if result.duration_s else "no determinada"
    meta.append(("Duración", escape(duration)))
    if not sub.is_youtube:
        res = sub.resolution_label
        meta.append(("Resolución", escape(res) if res else "no determinada"))
    meta.append(("Idioma del video", _lang(result)))
    meta.append(("Rúbrica", escape(rubric.file_name)))
    precision = "precisión alta" if result.accuracy == "high" else "precisión normal"
    meta.append(("Modelo", f"{escape(result.model_label)} · {precision}"))
    meta.append(("Fecha", result.graded_at.strftime("%d/%m/%Y %H:%M")))
    meta_html = "".join(f"<div><span>{k}</span><span>{v}</span></div>" for k, v in meta)

    notes = []
    if result.over_limit():
        notes.append(
            ("warn", f"<strong>El video dura {format_mmss(result.duration_s)} y supera el máximo de "
             f"{format_mmss(rubric.max_duration_s)} indicado en la rúbrica.</strong> "
             "Se corrigió igual; este aviso no se suma a los criterios.")
        )
    if sub.is_low_resolution(min_height):
        notes.append(
            ("warn", f"<strong>Resolución baja: {escape(sub.resolution_label or '')}</strong>, menor a la "
             f"recomendada ({min_height}p). Puede que el texto chico de la pantalla no se lea bien.")
        )
    for start, end, note in result.illegible:
        span = _ts(result, start) if start == end else f"{_ts(result, start)} y {_ts(result, end)}"
        between = "en" if start == end else "entre"
        extra = f" {escape(note)}" if note else ""
        notes.append(
            ("eye", f"<strong>Legibilidad:</strong> {between} {span} el texto de la pantalla se ve poco nítido.{extra} "
             "Los ítems de ese tramo pueden requerir revisión.")
        )
    notes_html = "".join(
        f'<div class="note" role="note">{_icon(icon, "#8A4B00", 16)}<span>{text}</span></div>' for icon, text in notes
    )

    tiles = []
    for status in STATUSES:
        bg, fg, _ = STATUS_STYLE[status]
        tiles.append(
            f'<div class="tile" style="background:{bg};color:{fg}"><b>{counts[status]}</b>'
            f"<span>{escape(STATUS_LABELS_ES[status])}</span></div>"
        )

    tip = (
        "Tocá cualquier minuto para abrir el video justo en ese momento; conviene arrancar un par de segundos antes. "
        if sub.is_youtube
        else "Los minutos indican dónde mirar en el video; conviene arrancar un par de segundos antes. "
    )

    blocks = []
    by_req: dict[str, list] = {}
    for r in result.items:
        by_req.setdefault(r.requirement.id, []).append(r)
    for req in rubric.requirements:
        rows = by_req.get(req.id, [])
        met = sum(1 for r in rows if r.status == "cumple")
        items_html = []
        for r in rows:
            lines = [f"<p>{_ts(result, ev.seconds)} — {escape(ev.text)}</p>" for ev in r.evidences]
            if r.missing:
                lines.append(f"<p><strong>Falta:</strong> {escape(r.missing)}</p>")
            if r.note:
                lines.append(f"<p><strong>Observación:</strong> {escape(r.note)}</p>")
            if not lines and r.status == "no_se_muestra":
                lines.append("<p>No aparece en ningún momento del video.</p>")
            cls = "item review" if r.status == "requiere_revision" else "item"
            items_html.append(
                f'<div class="{cls}"><div class="item-top"><h4>{escape(r.item.id.split(".", 1)[-1])}. '
                f"{escape(r.item.text)}</h4>{_badge(r.status)}</div>{''.join(lines)}</div>"
            )
        profile = f'<span class="profile">Perfil: {escape(req.profile)}</span>' if req.profile else ""
        blocks.append(
            f'<section class="card req"><div class="req-head"><div><h3>{escape(req.name)}</h3>{profile}</div>'
            f'<span class="subtotal">{met} de {len(rows)} ítem{"s" if len(rows) != 1 else ""} cumple{"n" if met != 1 else ""}</span></div>'
            f"{''.join(items_html)}</section>"
        )

    source = ""
    if sub.is_youtube:
        source = (
            f'<a class="source" href="{escape(sub.source)}" target="_blank" rel="noopener">'
            f'{_icon("link", "#0B5CAD")}{escape(sub.source.replace("https://www.", ""))}</a>'
        )
    upload_note = (
        "El video se leyó directamente desde YouTube; no se subió ningún archivo."
        if sub.is_youtube
        else "El video fue borrado de los servidores de Google después del análisis."
    )

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>Reporte de corrección — {escape(title)}</title>
<style>{CSS}</style>
{_data_block(report_data(result, min_height))}
</head>
<body>
<div class="wrap">
<header class="card">
<div style="display:flex;flex-direction:column;gap:6px">
<span class="eyebrow">{APP_NAME} · Reporte de corrección</span>
<h1>{escape(title)}</h1>
{source}
</div>
<div class="meta">{meta_html}</div>
{notes_html}
<div class="tiles">{''.join(tiles)}</div>
<p class="disclaimer">{_icon("info", "#4A5160", 16)}<span>Corrección generada con IA a partir del video. {tip}Revisá los ítems marcados como «Requiere revisión» antes de poner la nota.</span></p>
</header>
<h2>Criterios de la rúbrica</h2>
{''.join(blocks)}
<footer><span>Generado por {APP_NAME} · {upload_note}</span><span>{escape(COPYRIGHT)}</span></footer>
</div>
</body>
</html>
"""


_write_lock = threading.Lock()


def video_identity(data: dict) -> tuple[str, str] | None:
    """What makes two reports "the same video": file name, or YouTube video id."""
    if data.get("is_youtube"):
        vid = data.get("youtube_id")
        if not vid:
            from .media import parse_youtube_id

            vid = parse_youtube_id(str(data.get("source") or ""))
        return ("youtube", vid) if vid else None
    name = str(data.get("source") or data.get("video") or "").strip()
    return ("file", Path(name).name.lower()) if name else None


def _identity_of(result: SubmissionResult) -> tuple[str, str] | None:
    sub = result.submission
    if sub.is_youtube:
        return ("youtube", sub.youtube_id) if sub.youtube_id else None
    return ("file", Path(sub.source).name.lower())


def _remove_previous(out_dir: Path, identity: tuple[str, str] | None) -> None:
    """Delete earlier reports (.html + .json) of the same video in this folder."""
    if identity is None:
        return
    from .summary_excel import load_report  # reads both new and older reports

    for old in out_dir.glob("*.html"):
        data = load_report(old)
        if not data or video_identity(data) != identity:
            continue
        for path in (old, old.with_suffix(".json")):
            try:
                path.unlink(missing_ok=True)
                log.info("Replaced previous report %s", path.name)
            except OSError as exc:
                log.warning("Couldn't delete previous report %s: %s", path, exc)


def write_report(result: SubmissionResult, out_dir: Path, min_height: int = 720) -> Path:
    """Write the report. A previous report of the same video in the folder is replaced."""
    html = build_html(result, min_height)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_file_stem(result.submission.display_name)
    with _write_lock:  # several submissions finish at the same time
        _remove_previous(out_dir, _identity_of(result))
        path = out_dir / f"{stem}.html"
        n = 2
        while path.exists():  # a different video with the same name
            path = out_dir / f"{stem}_{n}.html"
            n += 1
        path.write_text(html, encoding="utf-8")
    return path
