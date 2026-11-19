"""Loading rubrics from Excel (.xlsx) or YAML, plus the Excel template.

Excel layout
------------
Sheet "Rubrica": a header row with the columns Requerimiento, Perfil and Item
(accents and case don't matter), then one row per item. An empty
Requerimiento or Perfil cell repeats the value from the row above, so a table
copied from the PDF can be pasted as is.

Sheet "Configuracion" (optional): a row whose first cell is
"Duracion maxima" and whose second cell is the limit, as minutes (15),
"MM:SS" text ("15:00") or an Excel time.

YAML layout
-----------
duracion_maxima: "15:00"
requerimientos:
  - nombre: ...
    perfil: ...
    items:
      - ...
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


class RubricError(Exception):
    """A rubric file that can't be used. The message is shown to the teacher."""


@dataclass
class Item:
    id: str
    text: str


@dataclass
class Requirement:
    id: str
    name: str
    profile: str
    items: list[Item] = field(default_factory=list)


@dataclass
class Rubric:
    file_name: str
    requirements: list[Requirement]
    max_duration_s: int | None = None

    @property
    def item_count(self) -> int:
        return sum(len(r.items) for r in self.requirements)

    def all_items(self) -> list[tuple[Requirement, Item]]:
        return [(r, i) for r in self.requirements for i in r.items]

    def summary(self) -> str:
        parts = [
            f"{len(self.requirements)} requirement{'s' if len(self.requirements) != 1 else ''}",
            f"{self.item_count} item{'s' if self.item_count != 1 else ''}",
        ]
        if self.max_duration_s:
            parts.append(f"max. {format_mmss(self.max_duration_s)}")
        return " · ".join(parts)


def format_mmss(seconds: float | int) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _norm(text: object) -> str:
    """Lowercase, no accents, single spaces: for matching headers and labels."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _clean(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"[ \t]+", " ", str(value)).strip()


def parse_duration(value: object) -> int | None:
    """Accepts 15, 15.5, "15", "15:00", "15 min", an Excel time or timedelta."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.timedelta):
        return int(value.total_seconds())
    if isinstance(value, dt.time):
        # Excel turns "15:00" into 15 hours; a demo limit is minutes:seconds.
        return value.hour * 60 + value.minute
    if isinstance(value, dt.datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, (int, float)):
        if value <= 0:
            raise RubricError("The maximum video length must be greater than zero.")
        return int(round(float(value) * 60))
    text = _norm(value).replace("minutos", "").replace("minuto", "").replace("min", "").strip()
    match = re.fullmatch(r"(\d{1,3}):(\d{2})", text)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)", text)
    if match:
        return int(round(float(match.group(1).replace(",", ".")) * 60))
    raise RubricError(
        f"Can't read the maximum video length '{value}'. Use minutes (15) or MM:SS (15:00)."
    )


def _build(file_name: str, rows: list[tuple[str, str, str]], max_duration_s: int | None) -> Rubric:
    requirements: list[Requirement] = []
    by_key: dict[tuple[str, str], Requirement] = {}
    for req_name, profile, item_text in rows:
        key = (req_name, profile)
        req = by_key.get(key)
        if req is None or requirements[-1] is not req:
            req = Requirement(id=f"R{len(requirements) + 1}", name=req_name, profile=profile)
            requirements.append(req)
            by_key[key] = req
        req.items.append(Item(id=f"{req.id}.{len(req.items) + 1}", text=item_text))
    if not requirements:
        raise RubricError("The rubric has no items. Add at least one row with an item.")
    return Rubric(file_name=file_name, requirements=requirements, max_duration_s=max_duration_s)


# ---------------------------------------------------------------- Excel
def _find_sheet(wb, *names: str):
    wanted = {_norm(n) for n in names}
    for ws in wb.worksheets:
        if _norm(ws.title) in wanted:
            return ws
    return None


def load_excel(path: Path) -> Rubric:
    import openpyxl  # imported here so YAML users don't pay for it at startup

    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:  # openpyxl raises many kinds of errors
        raise RubricError(f"Can't open the Excel file: {exc}") from exc

    try:
        ws = _find_sheet(wb, "Rubrica", "Rubric") or wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))

        header_idx = None
        cols: dict[str, int] = {}
        for idx, row in enumerate(rows[:20]):
            normalized = [_norm(c) for c in row]
            if any(c.startswith("requerimiento") for c in normalized) and any(
                c in ("item", "items") or c.startswith("item") for c in normalized
            ):
                header_idx = idx
                for col, name in enumerate(normalized):
                    if name.startswith("requerimiento"):
                        cols.setdefault("req", col)
                    elif name.startswith("perfil") or name.startswith("usuario"):
                        cols.setdefault("profile", col)
                    elif name.startswith("item"):
                        cols.setdefault("item", col)
                break
        if header_idx is None:
            raise RubricError(
                f"Sheet '{ws.title}' needs a header row with the columns "
                "Requerimiento, Perfil and Item."
            )

        parsed: list[tuple[str, str, str]] = []
        current_req, current_profile = "", ""
        for offset, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
            def cell(key: str) -> str:
                col = cols.get(key)
                return _clean(row[col]) if col is not None and col < len(row) else ""

            req, profile, item = cell("req"), cell("profile"), cell("item")
            if not (req or profile or item):
                continue
            if req and req != current_req:
                current_req = req
                current_profile = profile  # a new requirement starts a new profile
            elif profile:
                current_profile = profile
            if not item:
                raise RubricError(f"Row {offset} of sheet '{ws.title}' has no item.")
            if not current_req:
                raise RubricError(f"Row {offset} of sheet '{ws.title}' has no requirement.")
            parsed.append((current_req, current_profile, item))

        max_duration = None
        cfg = _find_sheet(wb, "Configuracion", "Configuración", "Config")
        if cfg is not None:
            for row in cfg.iter_rows(values_only=True):
                if row and _norm(row[0]).startswith("duracion maxima"):
                    max_duration = parse_duration(row[1] if len(row) > 1 else None)
                    break
        return _build(path.name, parsed, max_duration)
    finally:
        wb.close()


# ---------------------------------------------------------------- YAML
def load_yaml(path: Path) -> Rubric:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RubricError(f"Can't read the YAML file: {exc}") from exc
    if not isinstance(data, dict):
        raise RubricError("The YAML file must contain 'duracion_maxima' and 'requerimientos'.")

    lowered = {_norm(k).replace(" ", "_"): v for k, v in data.items()}
    reqs = lowered.get("requerimientos")
    if not isinstance(reqs, list) or not reqs:
        raise RubricError("The YAML file needs a non-empty 'requerimientos' list.")

    parsed: list[tuple[str, str, str]] = []
    for n, req in enumerate(reqs, start=1):
        if not isinstance(req, dict):
            raise RubricError(f"Requirement #{n} must have 'nombre', 'perfil' and 'items'.")
        fields = {_norm(k): v for k, v in req.items()}
        name = _clean(fields.get("nombre"))
        profile = _clean(fields.get("perfil"))
        items = fields.get("items") or fields.get("itemes")
        if not name:
            raise RubricError(f"Requirement #{n} has no 'nombre'.")
        if not isinstance(items, list) or not items:
            raise RubricError(f"Requirement '{name}' has no items.")
        for item in items:
            text = _clean(item)
            if text:
                parsed.append((name, profile, text))

    return _build(path.name, parsed, parse_duration(lowered.get("duracion_maxima")))


def load(path: str | Path) -> Rubric:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return load_excel(path)
    if suffix in (".yaml", ".yml"):
        return load_yaml(path)
    raise RubricError("Use an Excel (.xlsx) or YAML (.yaml) rubric file.")


# ---------------------------------------------------------------- template
def write_template(path: str | Path) -> None:
    """Excel template with the expected sheets and two example requirements."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Rubrica"
    ws.append(["Requerimiento", "Perfil", "Item"])
    examples = [
        ("Registro e ingreso", "Usuario sin permisos", "Registrar un usuario y validar nombre, apellido y formato del correo"),
        ("", "", "Mostrar que el correo no puede repetirse"),
        ("", "", "Probar ingreso con correo inexistente, contraseña incorrecta y usuario desactivado"),
        ("Gestión de catálogos", "Administrador", "Crear un catálogo y validar que el nombre tenga entre 3 y 80 caracteres"),
        ("", "", "Modificar los datos del catálogo"),
    ]
    for row in examples:
        ws.append(row)
    header_fill = PatternFill("solid", fgColor="0B5CAD")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 90
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"

    cfg = wb.create_sheet("Configuracion")
    cfg.append(["Duracion maxima", 15])
    cfg.append(["(minutos; también se acepta 15:00 como texto)"])
    cfg["A1"].font = Font(bold=True)
    cfg["A2"].font = Font(italic=True, color="5C6370")
    cfg.column_dimensions["A"].width = 44

    help_ws = wb.create_sheet("Ayuda")
    lines = [
        "Cómo completar esta rúbrica para RubricVision AI",
        "",
        "• Una fila por cada ítem a mostrar y validar (cada viñeta del PDF).",
        "• Si la celda Requerimiento o Perfil queda vacía, se usa la de la fila de arriba.",
        "• La duración máxima va en la hoja Configuracion, en minutos.",
        "• No cambies los nombres de las hojas ni de las columnas.",
    ]
    for line in lines:
        help_ws.append([line])
    help_ws["A1"].font = Font(bold=True, size=13)
    help_ws.column_dimensions["A"].width = 90
    wb.save(path)
