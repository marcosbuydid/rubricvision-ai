import datetime as dt
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

from rubricvision import rubric as rb
from rubricvision.config import DEFAULT_CONFIG, Config
from rubricvision.gemini_service import GradingError, Usage
from rubricvision.grading import GradingSession, normalize
from rubricvision.media import Submission, parse_youtube_id, submission_from_file, submission_from_youtube, SubmissionError
from rubricvision.pricing import estimate
from rubricvision.report import build_html, write_report

CONFIG = Config(DEFAULT_CONFIG)


# ------------------------------------------------------------------ rubric
def test_template_roundtrip(tmp_path):
    path = tmp_path / "plantilla.xlsx"
    rb.write_template(path)
    r = rb.load(path)
    assert [q.name for q in r.requirements] == ["Registro e ingreso", "Gestión de catálogos"]
    assert r.item_count == 5
    assert r.requirements[0].profile == "Usuario sin permisos"
    assert r.requirements[1].items[1].id == "R2.2"
    assert r.max_duration_s == 15 * 60


def test_excel_carry_forward_and_time(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Rúbrica"
    ws.append(["Requerimiento funcional", "Usuario o perfil que muestra la funcionalidad", "Ítems a mostrar y validar"])
    ws.append(["Registro", "Usuario sin permisos; luego Administrador", "registrar un usuario"])
    ws.append([None, None, "mostrar que DNI no se repite"])
    ws.append([None, None, None])
    ws.append(["Catálogos", "Administrador o Supervisor", "crear catálogos"])
    cfg = wb.create_sheet("Configuración")
    cfg.append(["Duración máxima", dt.time(15, 0)])
    p = tmp_path / "r.xlsx"
    wb.save(p)
    r = rb.load(p)
    assert len(r.requirements) == 2
    assert [i.text for i in r.requirements[0].items] == ["registrar un usuario", "mostrar que DNI no se repite"]
    assert r.requirements[1].profile == "Administrador o Supervisor"
    assert r.max_duration_s == 900


def test_excel_missing_item(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Requerimiento", "Perfil", "Item"])
    ws.append(["Registro", "Admin", None])
    p = tmp_path / "bad.xlsx"
    wb.save(p)
    with pytest.raises(rb.RubricError, match="Row 2"):
        rb.load(p)


def test_yaml(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(
        'duracion_maxima: "15:00"\nrequerimientos:\n  - nombre: Registro\n    perfil: Admin\n    items:\n      - uno\n      - dos\n',
        encoding="utf-8",
    )
    r = rb.load(p)
    assert r.item_count == 2 and r.max_duration_s == 900


@pytest.mark.parametrize("value,expected", [(15, 900), ("15:00", 900), ("15 min", 900), ("12:30", 750), ("7,5", 450)])
def test_parse_duration(value, expected):
    assert rb.parse_duration(value) == expected


# ------------------------------------------------------------------ media
@pytest.mark.parametrize(
    "url,vid",
    [
        ("https://www.youtube.com/watch?v=1nsFdWVB_H0", "1nsFdWVB_H0"),
        ("youtube.com/watch?v=1nsFdWVB_H0&t=30s", "1nsFdWVB_H0"),
        ("https://youtu.be/1nsFdWVB_H0?si=abc", "1nsFdWVB_H0"),
        ("https://m.youtube.com/shorts/1nsFdWVB_H0", "1nsFdWVB_H0"),
        ("https://vimeo.com/123", None),
        ("hola", None),
    ],
)
def test_youtube_ids(url, vid):
    assert parse_youtube_id(url) == vid


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed to build sample videos")
def test_local_metadata(tmp_path):
    out = tmp_path / "group-3_demo.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=854x480:rate=5", "-t", "3", "-c:v", "libx264", str(out)],
        check=True,
    )
    sub = submission_from_file(out)
    assert sub.resolution_label == "480p"
    assert sub.is_low_resolution(720)
    assert 2.5 <= sub.duration_s <= 3.5
    mkv = tmp_path / "x.mkv"
    mkv.write_bytes(b"x")
    with pytest.raises(SubmissionError, match="MKV"):
        submission_from_file(mkv)


# ------------------------------------------------------------------ pricing
def _rubric():
    return rb.Rubric(
        file_name="rubrica.xlsx",
        requirements=[
            rb.Requirement("R1", "Registro", "Admin", [rb.Item("R1.1", "registrar"), rb.Item("R1.2", "validar")]),
        ],
        max_duration_s=900,
    )


def test_estimate_uses_rubric_limit_for_youtube():
    r = _rubric()
    local = Submission("file", "a.mp4", "a.mp4", duration_s=600, size_bytes=10)
    yt = submission_from_youtube("https://youtu.be/1nsFdWVB_H0")
    est = estimate([local, yt], r, CONFIG, "high")
    assert est.assumed_count == 1
    price_in, price_out = CONFIG.prices()
    expected = (600 + 900) * 305 / 1e6 * price_in + 2 * (3000 + 220 * 2) / 1e6 * price_out
    assert est.usd == pytest.approx(expected)


def test_price_change_2027():
    assert CONFIG.prices(dt.date(2026, 12, 31)) == (0.75, 3.75)
    assert CONFIG.prices(dt.date(2027, 1, 1)) == (1.5, 7.5)


# ------------------------------------------------------------------ normalize + report
RAW = {
    "duracion_segundos": 1000,
    "idioma_narracion": "español",
    "idioma_interfaz": "inglés",
    "tramos_poco_legibles": [{"desde_segundo": 615, "hasta_segundo": 640, "nota": ""}],
    "items": [
        {"id": "R1.1", "estado": "cumple", "evidencias": [], "faltante": "", "observacion": ""},
        {
            "id": "R1.2",
            "estado": "cumple_parcialmente",
            "evidencias": [
                {"segundo": 151, "descripcion": "Prueba con contraseña incorrecta y aparece «Wrong password»."},
                {"segundo": 125, "descripcion": "<script>alert(1)</script> correo inexistente"},
            ],
            "faltante": "No se muestra el usuario desactivado.",
            "observacion": "",
        },
    ],
}


def test_normalize_rules():
    sub = submission_from_youtube("https://youtu.be/1nsFdWVB_H0")
    res = normalize(RAW, sub, _rubric(), CONFIG, "high", Usage(300000, 9000))
    assert res.items[0].status == "requiere_revision"  # "cumple" without evidence
    assert [e.seconds for e in res.items[1].evidences] == [125, 151]
    assert res.over_limit()
    assert res.cost_usd > 0


def test_report_escapes_and_links(tmp_path):
    sub = submission_from_youtube("https://youtu.be/1nsFdWVB_H0")
    res = normalize(RAW, sub, _rubric(), CONFIG, "high", Usage(1, 1))
    html = build_html(res)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "watch?v=1nsFdWVB_H0&amp;t=151s" in html
    assert "supera el máximo de 15:00" in html
    assert "Legibilidad" in html
    p1 = write_report(res, tmp_path)
    p2 = write_report(res, tmp_path)
    assert p1 != p2 and p1.exists() and p2.exists()


# ------------------------------------------------------------------ session with a fake Gemini
class FakeService:
    def __init__(self, fail_for=(), delay=0.05):
        self.fail_for = set(fail_for)
        self.delay = delay
        self.uploaded, self.deleted = [], []
        self.lock = threading.Lock()

    def upload(self, sub, cancel, on_progress):
        on_progress(50, 100)
        time.sleep(self.delay)
        on_progress(100, 100)
        name = f"files/{sub.uid}"
        with self.lock:
            self.uploaded.append(name)
        return SimpleNamespace(name=name, uri="u", mime_type="video/mp4", state=SimpleNamespace(name="ACTIVE"))

    def wait_until_active(self, f, cancel):
        return f

    def analyze(self, sub, f, rubric, accuracy):
        time.sleep(self.delay)
        if sub.display_name in self.fail_for:
            raise GradingError(GradingError.NOT_PUBLIC if sub.is_youtube else GradingError.FILE_FAILED, "boom")
        return RAW, Usage(100000, 5000)

    def delete(self, name):
        with self.lock:
            self.deleted.append(name)
        return True


def _run(session):
    done = threading.Event()
    events = []

    def emit(e):
        events.append(e)
        if e.kind == "finished":
            done.set()

    session.emit = emit
    session.start()
    assert done.wait(10)
    session.shutdown(wait=True)
    return events


def test_session_deletes_every_upload(tmp_path):
    svc = FakeService(fail_for={"b.mp4", "youtube.com/watch?v=1nsFdWVB_H0"})
    subs = [
        Submission("file", "a.mp4", "a.mp4", duration_s=60),
        Submission("file", "b.mp4", "b.mp4", duration_s=60),
        submission_from_youtube("https://youtu.be/1nsFdWVB_H0"),
    ]
    s = GradingSession(svc, _rubric(), subs, "high", CONFIG, tmp_path, emit=lambda e: None)
    events = _run(s)
    assert sorted(svc.uploaded) == sorted(svc.deleted) and len(svc.uploaded) == 2
    assert len(s.results) == 1 and len(s.errors) == 2
    assert s.errors[subs[2].uid].kind == GradingError.NOT_PUBLIC
    assert (tmp_path / "a.html").exists() and (tmp_path / "a.json").exists()
    assert any(e.kind == "cost" for e in events)


def test_session_cancel_still_deletes(tmp_path):
    svc = FakeService(delay=0.3)
    subs = [Submission("file", f"{i}.mp4", f"{i}.mp4", duration_s=60) for i in range(4)]
    s = GradingSession(svc, _rubric(), subs, "high", CONFIG, tmp_path, emit=lambda e: None)
    done = threading.Event()
    s.emit = lambda e: done.set() if e.kind == "finished" else None
    s.start()
    time.sleep(0.1)
    s.cancel()
    assert done.wait(10)
    s.shutdown(wait=True)
    assert sorted(svc.uploaded) == sorted(svc.deleted)
    assert all(err.kind == GradingError.CANCELLED for err in s.errors.values())


# ------------------------------------------------------------------ error mapping
from google.genai import errors as genai_errors  # noqa: E402

from rubricvision.gemini_service import _ProgressFile, classify  # noqa: E402


def _api(code, status, message):
    return genai_errors.APIError(code, {"error": {"code": code, "status": status, "message": message}})


@pytest.mark.parametrize(
    "exc,youtube,kind",
    [
        (_api(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."), False, GradingError.KEY_INVALID),
        (_api(429, "RESOURCE_EXHAUSTED", "Your prepay credit balance is depleted."), False, GradingError.NO_BALANCE),
        (_api(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota, please check your plan."), False, GradingError.QUOTA),
        (_api(429, "RESOURCE_EXHAUSTED", "Too many requests"), False, GradingError.RATE_LIMITED),
        (_api(403, "PERMISSION_DENIED", "The caller does not have permission"), True, GradingError.NOT_PUBLIC),
        (_api(503, "UNAVAILABLE", "overloaded"), False, GradingError.SERVER),
        (_api(404, "NOT_FOUND", "models/gemini-x is not found"), False, GradingError.MODEL_UNAVAILABLE),
        (ConnectionError("down"), False, GradingError.NETWORK),
    ],
)
def test_classify(exc, youtube, kind):
    assert classify(exc, youtube).kind == kind


def test_progress_file_reports_and_cancels(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x" * 1000)
    seen = []
    cancel = threading.Event()
    with _ProgressFile(str(p), lambda d, t: seen.append((d, t)), cancel) as fh:
        fh.read(400)
        assert seen[-1] == (400, 1000)
        cancel.set()
        with pytest.raises(GradingError):
            fh.read(100)
