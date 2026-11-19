"""Excel summary built from the HTML reports in a folder.

Each report made by RubricVision AI carries its data in a JSON block
(<script type="application/json" id="rubricvision-data">). Reports made before
that block existed are read from their visible HTML instead.

The workbook has three sheets (in Spanish, like the reports):
  Resumen  one row per graded submission
  Detalle  one row per rubric item of each submission
  Matriz   rubric items down, submissions across, status in each cell
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from .grading import STATUS_LABELS_ES
from .prompt import STATUSES
from .rubric import format_mmss

log = logging.getLogger(__name__)

SUMMARY_NAME = "resumen_correcciones.xlsx"
DATA_BLOCK_ID = "rubricvision-data"

STATUS_FILL = {
    "cumple": "E6F4EA",
    "cumple_parcialmente": "E5EEF9",
    "se_muestra_pero_falla": "FCE8E6",
    "no_se_muestra": "EEF0F3",
    "requiere_revision": "FFF1DB",
}
STATUS_FONT = {
    "cumple": "1B6E2E",
    "cumple_parcialmente": "1F4E8C",
    "se_muestra_pero_falla": "A8231B",
    "no_se_muestra": "4A5160",
    "requiere_revision": "8A4B00",
}
LABEL_TO_STATUS = {v: k for k, v in STATUS_LABELS_ES.items()}


class SummaryError(Exception):
    """Shown to the teacher (for example, the Excel file is open)."""


# ------------------------------------------------------------------ reading HTML
class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs), [], parent

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else ("\n" if c.tag == "br" else c.text()))
        return "".join(out)

    def find_all(self, tag=None, cls=None):
        for c in self.children:
            if isinstance(c, _Node):
                if (tag is None or c.tag == tag) and (cls is None or cls in c.classes):
                    yield c
                yield from c.find_all(tag, cls)

    def find(self, tag=None, cls=None):
        return next(self.find_all(tag, cls), None)

    def child_elements(self):
        return [c for c in self.children if isinstance(c, _Node)]


class _TreeBuilder(HTMLParser):
    VOID = {"br", "meta", "link", "img", "input", "hr", "path", "circle", "rect", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in self.VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(_Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def _clean(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()


def _mmss_to_s(text: str) -> int:
    parts = [int(p) for p in text.strip().split(":") if p.isdigit()]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def _parse_legacy(html: str) -> dict:
    """Read a report from its visible content (reports without the data block)."""
    b = _TreeBuilder()
    b.feed(html)
    root = b.root
    h1 = root.find("h1")
    data: dict = {
        "version": 0,
        "video": _clean(h1.text()) if h1 else "",
        "items": [],
        "illegible": [],
    }
    src = root.find("a", "source")
    data["is_youtube"] = src is not None
    data["source"] = src.attrs.get("href", "") if src is not None else data["video"]

    meta = {}
    meta_div = root.find("div", "meta")
    if meta_div is not None:
        for pair in meta_div.child_elements():
            spans = [c for c in pair.child_elements() if c.tag == "span"]
            if len(spans) >= 2:
                meta[_clean(spans[0].text())] = _clean(spans[1].text())
    dur = meta.get("Duración", "")
    data["duration_s"] = _mmss_to_s(dur) if re.match(r"^\d", dur) else None
    res = meta.get("Resolución")
    data["resolution"] = res if res and res[0].isdigit() else None
    lang = meta.get("Idioma del video", "")
    if "Narración:" in lang:
        m = re.search(r"Narración:\s*(.+?)\s*(?:\n|Interfaz:)", lang + "\n")
        u = re.search(r"Interfaz:\s*(.+)", lang)
        data["narration_lang"] = m.group(1).strip() if m else ""
        data["ui_lang"] = u.group(1).strip() if u else ""
    else:
        data["narration_lang"] = data["ui_lang"] = lang.lower()
    data["rubric_file"] = meta.get("Rúbrica", "")
    model = meta.get("Modelo", "")
    data["model"] = model.split("·")[0].strip()
    data["accuracy"] = "high" if "alta" in model else "normal"
    try:
        data["graded_at"] = datetime.strptime(meta.get("Fecha", ""), "%d/%m/%Y %H:%M").isoformat(timespec="seconds")
    except ValueError:
        data["graded_at"] = ""

    notes_text = " ".join(_clean(n.text()) for n in root.find_all("div", "note"))
    data["over_limit"] = "supera el máximo" in notes_text
    m = re.search(r"máximo de (\d+:\d{2})", notes_text)
    data["max_duration_s"] = _mmss_to_s(m.group(1)) if m else None
    data["low_resolution"] = "Resolución baja" in notes_text
    data["cost_usd"] = None

    for n, section in enumerate(root.find_all("section", "req"), start=1):
        h3 = section.find("h3")
        prof = section.find("span", "profile")
        req_name = _clean(h3.text()) if h3 else ""
        profile = _clean(prof.text()).removeprefix("Perfil:").strip() if prof else ""
        for k, item in enumerate(section.find_all("div", "item"), start=1):
            h4 = item.find("h4")
            badge = item.find("span", "badge")
            title = _clean(h4.text()) if h4 else ""
            title = re.sub(r"^\d+\.\s*", "", title)
            status = LABEL_TO_STATUS.get(_clean(badge.text()) if badge else "", "requiere_revision")
            evidences, missing, note = [], "", ""
            for p in item.find_all("p"):
                line = _clean(p.text())
                ts = p.find(cls="ts")
                if ts is not None:
                    seconds = _mmss_to_s(_clean(ts.text()))
                    text = line.split("—", 1)[1].strip() if "—" in line else line
                    evidences.append({"seconds": seconds, "text": text})
                elif line.startswith("Falta:"):
                    missing = line.removeprefix("Falta:").strip()
                elif line.startswith("Observación:"):
                    note = line.removeprefix("Observación:").strip()
            data["items"].append(
                {
                    "requirement_id": f"R{n}",
                    "requirement": req_name,
                    "profile": profile,
                    "item_id": f"R{n}.{k}",
                    "item": title,
                    "status": status,
                    "evidences": evidences,
                    "missing": missing,
                    "note": note,
                }
            )
    return data


def load_report(path: Path) -> dict | None:
    """Data of one report, or None if the file isn't a RubricVision AI report."""
    try:
        html = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("Can't read %s: %s", path, exc)
        return None
    if "Reporte de corrección" not in html:
        return None
    m = re.search(
        r'<script type="application/json" id="' + DATA_BLOCK_ID + r'">(.*?)</script>', html, re.S
    )
    try:
        data = json.loads(m.group(1)) if m else _parse_legacy(html)
    except (ValueError, AttributeError) as exc:
        log.warning("Can't read the data of %s: %s", path, exc)
        return None
    if not data.get("items"):
        return None
    data["report_file"] = path.name
    return data


def collect(folder: Path) -> list[dict]:
    reports = []
    for path in sorted(folder.glob("*.html")):
        data = load_report(path)
        if data:
            reports.append(data)
    reports.sort(key=lambda d: (d.get("graded_at") or "", d.get("video") or ""))
    return reports


# ------------------------------------------------------------------ writing Excel
def _counts(report: dict) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for it in report["items"]:
        out[it["status"] if it["status"] in out else "requiere_revision"] += 1
    return out


def _evidence_text(item: dict) -> tuple[str, str]:
    times = ", ".join(format_mmss(e["seconds"]) for e in item["evidences"])
    lines = "\n".join(f"{format_mmss(e['seconds'])} — {e['text']}" for e in item["evidences"])
    return times, lines


def build_workbook(reports: list[dict]):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    font = "Arial"
    head_font = Font(name=font, bold=True, color="FFFFFF", size=10)
    head_fill = PatternFill("solid", fgColor="0B5CAD")
    body = Font(name=font, size=10)
    thin = Side(style="thin", color="D5D9E0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    top_wrap = Alignment(vertical="top", wrap_text=True)

    def header(ws, titles, widths):
        ws.append(titles)
        for i, (cell, width) in enumerate(zip(ws[1], widths), start=1):
            cell.font, cell.fill, cell.border = head_font, head_fill, border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.row_dimensions[1].height = 30
        ws.freeze_panes = "A2"

    def style_row(ws, row, count):
        for col in range(1, count + 1):
            c = ws.cell(row, col)
            c.font = body
            c.border = border
            c.alignment = top_wrap

    def status_cell(cell, status):
        cell.value = STATUS_LABELS_ES.get(status, status)
        cell.fill = PatternFill("solid", fgColor=STATUS_FILL.get(status, "FFFFFF"))
        cell.font = Font(name=font, size=10, bold=True, color=STATUS_FONT.get(status, "1B1F27"))

    wb = Workbook()

    # ---------------- Resumen
    ws = wb.active
    ws.title = "Resumen"
    titles = [
        "Video", "Fuente", "Fecha", "Modelo", "Precisión", "Duración", "Supera duración máx.",
        "Resolución", "Cumple", "Cumple parcialmente", "Se muestra pero falla", "No se muestra",
        "Requiere revisión", "Total ítems", "% Cumple", "Costo (US$)", "Reporte",
    ]
    header(ws, titles, [34, 34, 17, 18, 11, 10, 12, 11, 9, 12, 12, 10, 11, 9, 10, 10, 30])
    for r, rep in enumerate(reports, start=2):
        counts = _counts(rep)
        try:
            when = datetime.fromisoformat(rep.get("graded_at") or "")
        except ValueError:
            when = None
        dur = rep.get("duration_s")
        values = [
            rep.get("video", ""),
            rep.get("source", ""),
            when,
            rep.get("model", ""),
            "Alta" if rep.get("accuracy") == "high" else "Normal",
            format_mmss(dur) if dur else "",
            "Sí" if rep.get("over_limit") else "No",
            rep.get("resolution") or ("YouTube" if rep.get("is_youtube") else ""),
            *[counts[s] for s in STATUSES],
            f"=SUM(I{r}:M{r})",
            f'=IF(N{r}=0,"",I{r}/N{r})',
            rep.get("cost_usd"),
            rep.get("report_file", ""),
        ]
        ws.append(values)
        style_row(ws, r, len(titles))
        ws.cell(r, 3).number_format = "dd/mm/yyyy hh:mm"
        ws.cell(r, 15).number_format = "0%"
        ws.cell(r, 16).number_format = "0.00"
        for col, status in zip(range(9, 14), STATUSES):
            c = ws.cell(r, col)
            if c.value:
                c.font = Font(name=font, size=10, bold=True, color=STATUS_FONT[status])
            c.alignment = Alignment(horizontal="center", vertical="top")
        for col in (7, 14, 15):
            ws.cell(r, col).alignment = Alignment(horizontal="center", vertical="top")
        if rep.get("over_limit"):
            ws.cell(r, 7).font = Font(name=font, size=10, bold=True, color="8A4B00")
        link = ws.cell(r, 17)
        link.hyperlink = rep.get("report_file", "")
        link.font = Font(name=font, size=10, color="0B5CAD", underline="single")
        src = ws.cell(r, 2)
        if rep.get("is_youtube") and str(src.value).startswith("http"):
            src.hyperlink = src.value
            src.font = Font(name=font, size=10, color="0B5CAD", underline="single")
    if reports:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(titles))}{len(reports) + 1}"

    # ---------------- Detalle
    wd = wb.create_sheet("Detalle")
    titles_d = ["Video", "Requerimiento", "Perfil", "N°", "Ítem", "Estado", "Minutos", "Evidencia", "Falta", "Observación"]
    header(wd, titles_d, [28, 30, 26, 7, 60, 20, 14, 70, 40, 40])
    row = 2
    for rep in reports:
        for it in rep["items"]:
            times, lines = _evidence_text(it)
            wd.append([
                rep.get("video", ""), it["requirement"], it["profile"], it["item_id"].lstrip("R"),
                it["item"], "", times, lines, it.get("missing", ""), it.get("note", ""),
            ])
            style_row(wd, row, len(titles_d))
            status_cell(wd.cell(row, 6), it["status"])
            row += 1
    if row > 2:
        wd.auto_filter.ref = f"A1:{get_column_letter(len(titles_d))}{row - 1}"

    # ---------------- Matriz
    wm = wb.create_sheet("Matriz")
    order: list[tuple[str, str, str]] = []
    seen = set()
    for rep in reports:
        for it in rep["items"]:
            key = (it["requirement"], it["item"])
            if key not in seen:
                seen.add(key)
                order.append((it["requirement"], it["item_id"].lstrip("R"), it["item"]))
    videos = [rep.get("video", "") for rep in reports]
    header(wm, ["Requerimiento", "N°", "Ítem", *videos], [30, 7, 60, *[22] * len(videos)])
    lookup = {
        (i, (it["requirement"], it["item"])): it["status"]
        for i, rep in enumerate(reports)
        for it in rep["items"]
    }
    for r, (req, num, text) in enumerate(order, start=2):
        wm.append([req, num, text])
        style_row(wm, r, 3)
        for i in range(len(videos)):
            c = wm.cell(r, 4 + i)
            status = lookup.get((i, (req, text)))
            if status:
                status_cell(c, status)
            c.border = border
            c.alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)
    wm.freeze_panes = "D2"

    note_row = len(order) + 3
    if videos:
        wm.cell(note_row, 1, "Ítems que cumplen por video:").font = Font(name=font, size=10, bold=True)
    for i in range(len(videos)):
        col = get_column_letter(4 + i)
        c = wm.cell(note_row, 4 + i, f'=COUNTIF({col}2:{col}{len(order) + 1},"Cumple")')
        c.font = Font(name=font, size=10, bold=True)
        c.alignment = Alignment(horizontal="center")

    for sheet in (ws, wd, wm):
        sheet.sheet_view.zoomScale = 100
        # Printing: landscape, all columns on one page width.
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.print_title_rows = "1:1"
    return wb


def write_empty_summary(folder: str | Path) -> Path | None:
    """Create the summary with only the column headers, if it doesn't exist yet.

    Called when the teacher chooses the reports folder; it's filled in after
    each grading. An existing summary is never touched.
    """
    target = Path(folder) / SUMMARY_NAME
    if target.exists():
        return None
    try:
        build_workbook([]).save(target)
    except OSError as exc:
        log.warning("Couldn't create the empty summary in %s: %s", folder, exc)
        return None
    return target


def write_summary(folder: str | Path) -> Path | None:
    """(Re)build the summary in the folder. None when there are no reports yet."""
    folder = Path(folder)
    reports = collect(folder)
    if not reports:
        return None
    wb = build_workbook(reports)
    target = folder / SUMMARY_NAME
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".resumen_", suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(tmp)
        try:
            os.replace(tmp, target)
        except PermissionError as exc:
            raise SummaryError(
                f"Couldn't update {SUMMARY_NAME} because it's open in Excel. Close it and try again."
            ) from exc
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return target
