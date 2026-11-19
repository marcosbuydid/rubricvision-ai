"""RubricVision AI interface (English). Tkinter, fixed-size centered windows."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from . import APP_NAME, COPYRIGHT
from .config import Config
from .gemini_service import GeminiService, GradingError
from .grading import Event, GradingSession, SubmissionResult
from .media import (
    SUPPORTED_VIDEO_TYPES,
    Submission,
    SubmissionError,
    fetch_youtube_title,
    submission_from_file,
    submission_from_youtube,
)
from .pricing import estimate, estimate_minutes_range, format_usd
from .prompt import STATUSES
from .report import write_report
from .rubric import Rubric, RubricError, format_mmss
from .rubric import load as load_rubric
from .rubric import write_template

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ look
BLUE = "#0B5CAD"
BLUE_DARK = "#08427D"
BLUE_SOFT = "#EAF2FB"
TEXT = "#1B1F27"
MUTED = "#4A5160"
FAINT = "#5C6370"
BORDER = "#D5D9E0"
BORDER_STRONG = "#8A919C"
SURFACE = "#F7F8FA"
HEADER_BG = "#F2F4F7"
FOOTER_BG = "#EEF0F3"
WHITE = "#FFFFFF"
AMBER = "#8A4B00"
AMBER_BG = "#FFF1DB"
AMBER_ROW = "#FFF8EC"
AMBER_BORDER = "#F0C98A"
RED = "#A8231B"
GREEN = "#1B6E2E"
PARTIAL = "#1F4E8C"

KEY_SIZE = (480, 420)
MAIN_SIZE = (880, 600)
STATUS_COLORS = {
    "cumple": GREEN,
    "cumple_parcialmente": PARTIAL,
    "se_muestra_pero_falla": RED,
    "no_se_muestra": MUTED,
    "requiere_revision": AMBER,
}
API_KEY_HELP_URL = "https://aistudio.google.com/apikey"


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / name


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def open_path(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        messagebox.showerror(APP_NAME, f"Couldn't open {path}:\n{exc}")


class Theme:
    def __init__(self, root: tk.Tk):
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        family = "Segoe UI" if sys.platform.startswith("win") else "DejaVu Sans"
        if family not in tkfont.families(root):
            family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.family = family
        self.base = tkfont.Font(root, family=family, size=10)
        self.small = tkfont.Font(root, family=family, size=9)
        self.small_bold = tkfont.Font(root, family=family, size=9, weight="bold")
        self.bold = tkfont.Font(root, family=family, size=10, weight="bold")
        self.h1 = tkfont.Font(root, family=family, size=14, weight="bold")
        self.h2 = tkfont.Font(root, family=family, size=11, weight="bold")
        self.link = tkfont.Font(root, family=family, size=9, underline=True)
        self.tiny = tkfont.Font(root, family=family, size=8)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=self.base, background=WHITE, foreground=TEXT)
        pad_btn = (self.px(18), self.px(8))
        pad_small = (self.px(12), self.px(5))

        def button(name, bg, fg, border, active_bg, padding, font):
            style.configure(
                name,
                background=bg,
                foreground=fg,
                bordercolor=border,
                lightcolor=bg,
                darkcolor=bg,
                focuscolor=bg,
                focusthickness=0,
                padding=padding,
                font=font,
                relief="flat" if border == bg else "solid",
                borderwidth=1,
            )
            style.map(
                name,
                background=[("disabled", "#E3E7ED" if bg == WHITE else "#A9C0DD"), ("pressed", active_bg), ("active", active_bg)],
                lightcolor=[("pressed", active_bg), ("active", active_bg)],
                darkcolor=[("pressed", active_bg), ("active", active_bg)],
                foreground=[("disabled", "#8A919C" if bg == WHITE else "#F2F5FA")],
                bordercolor=[("disabled", BORDER)],
            )

        button("Primary.TButton", BLUE, WHITE, BLUE, BLUE_DARK, pad_btn, self.bold)
        button("Secondary.TButton", WHITE, TEXT, BORDER_STRONG, HEADER_BG, pad_btn, self.base)
        button("Outline.TButton", WHITE, BLUE, BLUE, BLUE_SOFT, pad_small, self.small_bold)
        button("SmallPrimary.TButton", BLUE, WHITE, BLUE, BLUE_DARK, pad_small, self.small_bold)
        button("SmallSecondary.TButton", WHITE, TEXT, BORDER_STRONG, HEADER_BG, pad_small, self.small)
        button("ToggleOn.TButton", BLUE, WHITE, BLUE, BLUE_DARK, (self.px(16), self.px(5)), self.small_bold)
        button("ToggleOff.TButton", WHITE, TEXT, BORDER_STRONG, HEADER_BG, (self.px(16), self.px(5)), self.small)
        button("Icon.TButton", WHITE, FAINT, WHITE, HEADER_BG, (self.px(6), self.px(2)), self.base)
        style.configure(
            "TEntry",
            fieldbackground=WHITE,
            bordercolor=BORDER_STRONG,
            lightcolor=BORDER_STRONG,
            darkcolor=BORDER_STRONG,
            padding=(self.px(8), self.px(7)),
        )
        style.map("TEntry", bordercolor=[("focus", BLUE)], lightcolor=[("focus", BLUE)])
        style.configure("Error.TEntry", bordercolor=RED, lightcolor=RED, darkcolor=RED)
        style.configure(
            "Blue.Horizontal.TProgressbar",
            troughcolor="#E3E7ED",
            background=BLUE,
            bordercolor="#E3E7ED",
            lightcolor=BLUE,
            darkcolor=BLUE,
            thickness=self.px(8),
        )
        style.configure(
            "Thin.Horizontal.TProgressbar",
            troughcolor="#E3E7ED",
            background=BLUE,
            bordercolor="#E3E7ED",
            lightcolor=BLUE,
            darkcolor=BLUE,
            thickness=self.px(6),
        )
        style.configure("Vertical.TScrollbar", background=HEADER_BG, troughcolor=WHITE, bordercolor=WHITE, arrowcolor=FAINT)

    def px(self, n: float) -> int:
        return int(round(n * self.scale))


# ------------------------------------------------------------------ widgets
class Link(tk.Label):
    def __init__(self, parent, text, command, theme: Theme, bg=WHITE, **kw):
        super().__init__(parent, text=text, fg=BLUE, bg=bg, cursor="hand2", font=theme.link, **kw)
        self.bind("<Button-1>", lambda _e: command())


class Box(tk.Frame):
    """A frame with a 1 px border and an inner area."""

    def __init__(self, parent, bg=WHITE, border=BORDER, **kw):
        super().__init__(parent, bg=border, padx=1, pady=1)
        self.inner = tk.Frame(self, bg=bg, **kw)
        self.inner.pack(fill="both", expand=True)


class ScrollList(tk.Frame):
    """Table with a fixed header and scrollable rows."""

    def __init__(self, parent, theme: Theme, columns: list[tuple[str, int]], height: int):
        super().__init__(parent, bg=WHITE)
        self.theme = theme
        self.columns = columns
        box = Box(self)
        box.pack(side="left", fill="both", expand=True)
        self.header = tk.Frame(box.inner, bg=HEADER_BG, height=theme.px(32))
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        head_grid = tk.Frame(self.header, bg=HEADER_BG)
        head_grid.pack(fill="both", expand=True, padx=(theme.px(16), theme.px(8)))
        self._configure(head_grid)
        for i, (title, _w) in enumerate(columns):
            tk.Label(head_grid, text=title, bg=HEADER_BG, fg=MUTED, font=theme.small_bold, anchor="w").grid(
                row=0, column=i, sticky="w"
            )
        head_grid.grid_rowconfigure(0, weight=1)
        tk.Frame(box.inner, bg=BORDER, height=1).pack(fill="x")

        self.canvas = tk.Canvas(box.inner, bg=WHITE, highlightthickness=0, height=theme.px(height))
        self.canvas.pack(fill="both", expand=True)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.body = tk.Frame(self.canvas, bg=WHITE)
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        for widget in (self.canvas, self.body):
            widget.bind("<Enter>", lambda _e: self._wheel(True))
            widget.bind("<Leave>", lambda _e: self._wheel(False))
        self.empty_label: tk.Label | None = None

    def _on_scroll(self, first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scroll.pack_forget()
        else:
            self.scroll.pack(side="right", fill="y", padx=(self.theme.px(4), 0))
        self.scroll.set(first, last)

    def _wheel(self, on: bool):
        if on:
            self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))
            self.canvas.bind_all("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"))
            self.canvas.bind_all("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"))
        else:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.canvas.unbind_all(seq)

    def _configure(self, frame):
        for i, (_t, width) in enumerate(self.columns):
            if width:
                frame.grid_columnconfigure(i, minsize=self.theme.px(width), weight=0)
            else:
                frame.grid_columnconfigure(i, weight=1)

    def clear(self, empty_text: str = ""):
        for child in self.body.winfo_children():
            child.destroy()
        if empty_text:
            tk.Label(
                self.body, text=empty_text, bg=WHITE, fg=FAINT, font=self.theme.base, justify="center"
            ).pack(fill="x", pady=self.theme.px(36))
        self.canvas.yview_moveto(0)

    def add_row(self, bg=WHITE, height=40) -> tk.Frame:
        outer = tk.Frame(self.body, bg=bg, height=self.theme.px(height))
        outer.pack(fill="x")
        outer.pack_propagate(False)
        grid = tk.Frame(outer, bg=bg)
        grid.pack(fill="both", expand=True, padx=(self.theme.px(16), self.theme.px(8)))
        self._configure(grid)
        grid.grid_rowconfigure(0, weight=1)
        tk.Frame(self.body, bg="#E6E9EE", height=1).pack(fill="x")
        for w in (outer, grid):
            w.bind("<Enter>", lambda _e: self._wheel(True))
        return grid


# ------------------------------------------------------------------ app
class App(tk.Tk):
    def __init__(self, config: Config):
        super().__init__()
        self.withdraw()
        self.cfg = config
        self.theme = Theme(self)
        self.title(APP_NAME)
        self.configure(bg=WHITE)
        self.resizable(False, False)  # no maximize, no resize; minimize stays
        self._set_icon()

        self.service: GeminiService | None = None
        self.rubric: Rubric | None = None
        self.submissions: list[Submission] = []
        self.accuracy = config.get("default_accuracy") if config.get("default_accuracy") in ("normal", "high") else "high"
        self.session: GradingSession | None = None
        self.session_dir: Path | None = None
        self.events: queue.Queue = queue.Queue()
        self.notice = ""
        self._closing = False

        self.footer = tk.Frame(self, bg=FOOTER_BG, height=self.theme.px(28))
        self.footer.pack(side="bottom", fill="x")
        self.footer.pack_propagate(False)
        tk.Frame(self.footer, bg=BORDER, height=1).pack(side="top", fill="x")
        tk.Label(self.footer, text=COPYRIGHT, bg=FOOTER_BG, fg=MUTED, font=self.theme.tiny).pack(expand=True)

        self.container = tk.Frame(self, bg=WHITE)
        self.container.pack(side="top", fill="both", expand=True)
        self.screen: tk.Frame | None = None

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._poll_events)
        self.show_key_screen()
        self.deiconify()

    # ------------------------------------------------------------ helpers
    def _set_icon(self):
        try:
            if sys.platform.startswith("win"):
                self.iconbitmap(default=str(resource_path("rubricvision.ico")))
            else:
                self._icon_img = tk.PhotoImage(file=str(resource_path("rubricvision.png")))
                self.iconphoto(True, self._icon_img)
        except (tk.TclError, OSError) as exc:
            log.info("Icon not set: %s", exc)

    def px(self, n: float) -> int:
        return self.theme.px(n)

    def set_size(self, size: tuple[int, int]):
        """Fixed size, centered on the screen."""
        w, h = self.px(size[0]), self.px(size[1] + 28)
        self.update_idletasks()
        x = max(0, (self.winfo_screenwidth() - w) // 2)
        y = max(0, (self.winfo_screenheight() - h) // 2 - self.px(20))
        self.minsize(w, h)
        self.maxsize(w, h)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def switch(self, frame_cls, size, *args):
        if self.screen is not None:
            self.screen.destroy()
        self.screen = frame_cls(self.container, self, *args)
        self.screen.pack(fill="both", expand=True)
        self.set_size(size)

    def show_key_screen(self):
        self.switch(KeyScreen, KEY_SIZE)

    def show_submissions(self):
        self.title(APP_NAME)
        self.switch(SubmissionsScreen, MAIN_SIZE)

    def show_progress(self):
        self.switch(ProgressScreen, MAIN_SIZE)

    def show_results(self):
        self.title(APP_NAME)
        self.switch(ResultsScreen, MAIN_SIZE)

    def run_in_thread(self, work, done):
        """Run work() in a thread and done(result, error) back on the UI thread."""

        def target():
            try:
                result, error = work(), None
            except Exception as exc:  # reported to the UI
                result, error = None, exc
            self.events.put(("call", lambda: done(result, error)))

        threading.Thread(target=target, daemon=True).start()

    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "call":
                    payload()
                elif kind == "grading" and self.screen is not None:
                    handler = getattr(self.screen, "on_grading_event", None)
                    if handler:
                        handler(payload)
        except queue.Empty:
            pass
        if not self._closing:
            self.after(100, self._poll_events)

    # ------------------------------------------------------------ grading
    def start_grading(self):
        assert self.service and self.rubric and self.submissions
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_dir = self.cfg.reports_dir / stamp
        min_h = int(self.cfg.get("min_recommended_height"))
        self.session = GradingSession(
            self.service,
            self.rubric,
            list(self.submissions),
            self.accuracy,
            self.cfg,
            self.session_dir,
            emit=lambda e: self.events.put(("grading", e)),
            report_writer=lambda result, out: write_report(result, out, min_h),
        )
        self.show_progress()
        self.session.start()

    def new_session(self):
        if self.session:
            self.session.shutdown(wait=False)
        self.session = None
        self.submissions = []
        self.show_submissions()

    def on_close(self):
        if self.session and self.session.busy:
            if not messagebox.askyesno(
                APP_NAME,
                "Grading is in progress. If you close RubricVision AI now, unfinished submissions are lost.\n\n"
                "Close anyway?",
                icon="warning",
                parent=self,
            ):
                return
            self.session.cancel()
            self._closing_screen()
            session = self.session

            def cleanup():
                session.delete_remaining_uploads()
                return True

            self.run_in_thread(cleanup, lambda *_: self._destroy())
            self.after(30000, self._destroy)  # never hang on close
            return
        self._destroy()

    def _closing_screen(self):
        if self.screen is not None:
            self.screen.destroy()
        self.screen = tk.Frame(self.container, bg=WHITE)
        self.screen.pack(fill="both", expand=True)
        tk.Label(
            self.screen,
            text="Deleting uploaded videos from Google…",
            bg=WHITE,
            fg=TEXT,
            font=self.theme.h2,
        ).pack(expand=True)

    def _destroy(self):
        if self._closing:
            return
        self._closing = True
        if self.session:
            self.session.shutdown(wait=False)
        self.destroy()


# ------------------------------------------------------------------ screens
class Screen(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=WHITE)
        self.app = app
        self.t = app.theme

    def px(self, n):
        return self.t.px(n)

    def bottom_bar(self) -> tk.Frame:
        bar = tk.Frame(self, bg=SURFACE, height=self.px(72))
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=BORDER, height=1).pack(side="top", fill="x")
        inner = tk.Frame(bar, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=self.px(28))
        return inner

    def notice_box(self, parent, text: str, kind: str = "info") -> tk.Frame:
        colors = {
            "info": (BLUE_SOFT, BLUE_SOFT, "#1B3A5C"),
            "warn": (AMBER_BG, AMBER_BORDER, "#5C3400"),
        }[kind]
        box = Box(parent, bg=colors[0], border=colors[1])
        tk.Label(
            box.inner,
            text=text,
            bg=colors[0],
            fg=colors[2],
            font=self.t.small,
            justify="left",
            anchor="w",
            wraplength=self.px(790),
        ).pack(fill="x", padx=self.px(14), pady=self.px(9))
        return box


class KeyScreen(Screen):
    def __init__(self, parent, app: App):
        super().__init__(parent, app)
        body = tk.Frame(self, bg=WHITE)
        body.pack(fill="both", expand=True, padx=self.px(32), pady=(self.px(26), self.px(24)))

        tk.Label(body, text="Enter your API key", bg=WHITE, fg=TEXT, font=self.t.h1, anchor="w").pack(fill="x")
        tk.Label(
            body,
            text="RubricVision AI uses Gemini to analyze the videos. Your key is never saved: "
            "you'll be asked for it every time you open the program.",
            bg=WHITE,
            fg=MUTED,
            font=self.t.base,
            justify="left",
            anchor="w",
            wraplength=self.px(410),
        ).pack(fill="x", pady=(self.px(6), self.px(18)))

        tk.Label(body, text="Gemini API key", bg=WHITE, fg=TEXT, font=self.t.small_bold, anchor="w").pack(fill="x")
        row = tk.Frame(body, bg=WHITE)
        row.pack(fill="x", pady=(self.px(4), self.px(4)))
        self.key = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.key, show="•", font=self.t.base)
        self.entry.pack(side="left", fill="x", expand=True)
        self.toggle = ttk.Button(row, text="Show", style="SmallSecondary.TButton", command=self._toggle, width=6)
        self.toggle.pack(side="left", padx=(self.px(6), 0), ipady=self.px(3))

        self.error = tk.Label(body, text="", bg=WHITE, fg=RED, font=self.t.small, justify="left", anchor="w", wraplength=self.px(410))
        self.error.pack(fill="x")
        Link(body, "How do I get my key?", lambda: webbrowser.open(API_KEY_HELP_URL), self.t).pack(anchor="w", pady=(self.px(2), 0))

        tk.Frame(body, bg=WHITE).pack(fill="both", expand=True)
        self.button = ttk.Button(body, text="Verify and continue", style="Primary.TButton", command=self.verify)
        self.button.pack(fill="x")
        tk.Label(
            body, text="Verifying your key doesn't use any of your balance.", bg=WHITE, fg=FAINT, font=self.t.small
        ).pack(pady=(self.px(8), 0))

        self.entry.bind("<Return>", lambda _e: self.verify())
        self.after(50, self.entry.focus_set)

    def _toggle(self):
        hidden = self.entry.cget("show") == "•"
        self.entry.configure(show="" if hidden else "•")
        self.toggle.configure(text="Hide" if hidden else "Show")

    def verify(self):
        key = self.key.get().strip()
        if not key:
            self._fail("Paste your Gemini API key first.")
            return
        self.error.configure(text="")
        self.entry.configure(style="TEntry")
        self.button.configure(text="Verifying…", state="disabled")
        cfg = self.app.cfg

        def work():
            service = GeminiService(key, cfg)
            service.verify_key()
            model_ok = service.check_model()
            removed = service.cleanup_leftovers()
            return service, model_ok, removed

        self.app.run_in_thread(work, self._verified)

    def _verified(self, result, error):
        if not self.winfo_exists():
            return
        self.button.configure(text="Verify and continue", state="normal")
        if error is not None:
            if isinstance(error, GradingError) and error.kind == GradingError.KEY_INVALID:
                self._fail("This key isn't valid. Check that you copied all of it, with no spaces at the beginning or end.")
            elif isinstance(error, GradingError):
                self._fail(error.message)
            else:
                log.exception("Key check failed", exc_info=error)
                self._fail(f"Couldn't verify the key: {error}")
            return
        service, model_ok, removed = result
        self.app.service = service
        self.key.set("")  # don't keep it in the widget
        if removed:
            self.app.notice = f"Removed {removed} leftover video upload{'s' if removed != 1 else ''} from Google."
        if not model_ok:
            messagebox.showwarning(
                APP_NAME,
                f"The model '{self.app.cfg.model_id}' doesn't seem to be available for this key. "
                "Grading may fail; check the model in config.json.",
                parent=self.app,
            )
        self.app.show_submissions()

    def _fail(self, text: str):
        self.error.configure(text=text)
        self.entry.configure(style="Error.TEntry")
        self.entry.focus_set()


class SubmissionsScreen(Screen):
    COLUMNS = [("Video", 0), ("Duration", 170), ("Resolution", 160), ("Size", 80), ("", 40)]

    def __init__(self, parent, app: App):
        super().__init__(parent, app)
        self.max_subs = int(app.cfg.get("max_submissions"))
        bar = self.bottom_bar()
        left = tk.Frame(bar, bg=SURFACE)
        left.pack(side="left", fill="y")
        self.cost = tk.Label(left, text="", bg=SURFACE, fg=TEXT, font=self.t.bold, anchor="w")
        self.cost.pack(anchor="w", pady=(self.px(15), 0))
        self.cost_note = tk.Label(left, text="", bg=SURFACE, fg=FAINT, font=self.t.small, anchor="w")
        self.cost_note.pack(anchor="w")
        self.grade_btn = ttk.Button(bar, text="Grade", style="Primary.TButton", command=self.grade)
        self.grade_btn.pack(side="right", pady=self.px(14))

        body = tk.Frame(self, bg=WHITE)
        body.pack(fill="both", expand=True, padx=self.px(28), pady=(self.px(18), self.px(12)))

        # rubric card
        card = Box(body, bg=SURFACE)
        card.pack(fill="x")
        inner = tk.Frame(card.inner, bg=SURFACE)
        inner.pack(fill="x", padx=self.px(16), pady=self.px(10))
        self.rubric_btn = ttk.Button(inner, text="Load rubric…", style="SmallSecondary.TButton", command=self.load_rubric)
        self.rubric_btn.pack(side="right")
        Link(inner, "Download template", self.save_template, self.t, bg=SURFACE).pack(side="right", padx=self.px(14))
        info = tk.Frame(inner, bg=SURFACE)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text="Rubric", bg=SURFACE, fg=FAINT, font=self.t.small_bold, anchor="w").pack(anchor="w")
        self.rubric_label = tk.Label(info, text="", bg=SURFACE, fg=TEXT, font=self.t.bold, anchor="w")
        self.rubric_label.pack(anchor="w")

        # list header
        head = tk.Frame(body, bg=WHITE)
        head.pack(fill="x", pady=(self.px(14), self.px(8)))
        self.count_label = tk.Label(head, text="", bg=WHITE, fg=TEXT, font=self.t.h2, anchor="w")
        self.count_label.pack(side="left")
        self.add_files_btn = ttk.Button(head, text="+ Add videos…", style="Outline.TButton", command=self.add_files)
        self.add_files_btn.pack(side="right")
        self.add_yt_btn = ttk.Button(head, text="Add YouTube link…", style="Outline.TButton", command=self.add_youtube)
        self.add_yt_btn.pack(side="right", padx=(0, self.px(8)))

        self.list = ScrollList(body, self.t, self.COLUMNS, height=290)
        self.list.pack(fill="x")

        # accuracy + model
        opts = tk.Frame(body, bg=WHITE)
        opts.pack(fill="x", pady=(self.px(14), 0))
        acc = tk.Frame(opts, bg=WHITE)
        acc.pack(side="left")
        tk.Label(acc, text="Accuracy", bg=WHITE, fg=FAINT, font=self.t.small_bold).pack(anchor="w")
        toggles = tk.Frame(acc, bg=WHITE)
        toggles.pack(anchor="w", pady=(self.px(3), 0))
        self.normal_btn = ttk.Button(toggles, text="Normal", command=lambda: self.set_accuracy("normal"))
        self.normal_btn.pack(side="left")
        self.high_btn = ttk.Button(toggles, text="High", command=lambda: self.set_accuracy("high"))
        self.high_btn.pack(side="left")
        tk.Label(
            opts,
            text="High reads small interface text more reliably.\nRecommended for grading.",
            bg=WHITE,
            fg=MUTED,
            font=self.t.small,
            justify="left",
        ).pack(side="left", padx=self.px(22), pady=(self.px(14), 0))
        model = tk.Frame(opts, bg=WHITE)
        model.pack(side="right")
        tk.Label(model, text="Model", bg=WHITE, fg=FAINT, font=self.t.small_bold).pack(anchor="e")
        tk.Label(model, text=app.cfg.model_label, bg=WHITE, fg=TEXT, font=self.t.base).pack(anchor="e")

        self.refresh()
        if app.notice:
            self.after(300, lambda: messagebox.showinfo(APP_NAME, app.notice, parent=app))
            app.notice = ""

    # ------------------------------------------------------------ actions
    def set_accuracy(self, value: str):
        self.app.accuracy = value
        self.refresh()

    def save_template(self):
        path = filedialog.asksaveasfilename(
            parent=self.app,
            title="Save rubric template",
            defaultextension=".xlsx",
            initialfile="plantilla_rubrica.xlsx",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not path:
            return
        try:
            write_template(path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Couldn't save the template:\n{exc}", parent=self.app)
            return
        if messagebox.askyesno(APP_NAME, "Template saved. Open it now?", parent=self.app):
            open_path(Path(path))

    def load_rubric(self):
        path = filedialog.askopenfilename(
            parent=self.app,
            title="Choose the rubric",
            filetypes=[("Rubric (Excel or YAML)", "*.xlsx *.xlsm *.yaml *.yml"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.app.rubric = load_rubric(path)
        except RubricError as exc:
            messagebox.showerror(APP_NAME, f"This rubric can't be used:\n\n{exc}", parent=self.app)
            return
        self.refresh()

    def add_files(self):
        room = self.max_subs - len(self.app.submissions)
        exts = " ".join(f"*{e}" for e in SUPPORTED_VIDEO_TYPES)
        paths = filedialog.askopenfilenames(
            parent=self.app,
            title="Choose demo videos",
            filetypes=[("Videos", exts), ("All files", "*.*")],
        )
        if not paths:
            return
        problems = []
        existing = {s.source for s in self.app.submissions}
        self.app.configure(cursor="watch")
        self.app.update_idletasks()
        try:
            for p in paths:
                if room <= 0:
                    problems.append(f"Only {self.max_subs} submissions per session; the rest were skipped.")
                    break
                try:
                    sub = submission_from_file(p)
                except SubmissionError as exc:
                    problems.append(str(exc))
                    continue
                if sub.source in existing:
                    continue
                self.app.submissions.append(sub)
                existing.add(sub.source)
                room -= 1
        finally:
            self.app.configure(cursor="")
        self.refresh()
        if problems:
            messagebox.showwarning(APP_NAME, "\n\n".join(problems), parent=self.app)

    def add_youtube(self):
        YouTubeDialog(self.app, self._youtube_added)

    def _youtube_added(self, sub: Submission):
        if any(s.youtube_id == sub.youtube_id for s in self.app.submissions):
            messagebox.showinfo(APP_NAME, "That video is already in the list.", parent=self.app)
            return
        self.app.submissions.append(sub)
        self.refresh()

        def work():
            return fetch_youtube_title(sub.youtube_id)

        def done(title, _err):
            if title:
                sub.display_name = title
                if self.winfo_exists():
                    self.refresh()

        self.app.run_in_thread(work, done)

    def remove(self, sub: Submission):
        self.app.submissions.remove(sub)
        self.refresh()

    def grade(self):
        self.app.start_grading()

    # ------------------------------------------------------------ view
    def refresh(self):
        app = self.app
        r = app.rubric
        if r:
            self.rubric_label.configure(text=f"{shorten(r.file_name, 30)}  ·  {r.summary()}")
            self.rubric_btn.configure(text="Change…")
        else:
            self.rubric_label.configure(text="No rubric loaded yet (Excel or YAML)")
            self.rubric_btn.configure(text="Load rubric…")

        n = len(app.submissions)
        self.count_label.configure(text=f"Submissions ({n} of {self.max_subs} max.)")
        full = n >= self.max_subs
        self.add_files_btn.configure(state="disabled" if full else "normal")
        self.add_yt_btn.configure(state="disabled" if full else "normal")
        self.normal_btn.configure(style="ToggleOn.TButton" if app.accuracy == "normal" else "ToggleOff.TButton")
        self.high_btn.configure(style="ToggleOn.TButton" if app.accuracy == "high" else "ToggleOff.TButton")

        min_h = int(app.cfg.get("min_recommended_height"))
        self.list.clear("" if n else "Add local videos or public YouTube links (up to 10).")
        for sub in app.submissions:
            over = bool(r and sub.is_over_limit(r.max_duration_s))
            low = sub.is_low_resolution(min_h)
            bg = AMBER_ROW if (over or low) else WHITE
            row = self.list.add_row(bg=bg, height=38)
            kind = "▶ " if sub.is_youtube else ""
            name = tk.Frame(row, bg=bg)
            name.grid(row=0, column=0, sticky="w")
            tk.Label(name, text=kind + shorten(sub.display_name, 32 if sub.is_youtube else 44), bg=bg, fg=TEXT, font=self.t.base).pack(side="left")
            if sub.is_youtube:
                tk.Label(name, text="  · YouTube", bg=bg, fg=FAINT, font=self.t.small).pack(side="left")
                tk.Label(row, text="Checked when graded", bg=bg, fg=FAINT, font=self.t.small).grid(row=0, column=1, sticky="w")
                tk.Label(row, text="Checked when graded", bg=bg, fg=FAINT, font=self.t.small).grid(row=0, column=2, sticky="w")
                tk.Label(row, text="Link", bg=bg, fg=FAINT, font=self.t.small).grid(row=0, column=3, sticky="w")
            else:
                if sub.duration_s:
                    dur = format_mmss(sub.duration_s)
                    if over:
                        dur = f"⚠ {dur} · over {format_mmss(r.max_duration_s)}"
                else:
                    dur = "Unknown"
                tk.Label(row, text=dur, bg=bg, fg=AMBER if over else MUTED, font=self.t.small_bold if over else self.t.base).grid(
                    row=0, column=1, sticky="w"
                )
                res = sub.resolution_label or "Unknown"
                if low:
                    res = f"⚠ {res} · below {min_h}p"
                tk.Label(row, text=res, bg=bg, fg=AMBER if low else MUTED, font=self.t.small_bold if low else self.t.base).grid(
                    row=0, column=2, sticky="w"
                )
                size_mb = (sub.size_bytes or 0) / 1024**2
                tk.Label(row, text=f"{size_mb:,.0f} MB", bg=bg, fg=MUTED, font=self.t.base).grid(row=0, column=3, sticky="w")
            remove = tk.Label(row, text="✕", bg=bg, fg=FAINT, font=self.t.base, cursor="hand2", padx=self.px(8))
            remove.grid(row=0, column=4, sticky="e")
            remove.bind("<Button-1>", lambda _e, s=sub: self.remove(s))

        ready = bool(r and n)
        self.grade_btn.configure(
            text=f"Grade {n} submission{'s' if n != 1 else ''}" if n else "Grade",
            state="normal" if ready else "disabled",
        )
        if not r:
            self.cost.configure(text="Estimated cost: —")
            self.cost_note.configure(text="Load a rubric to see the estimate")
        elif not n:
            self.cost.configure(text="Estimated cost: —")
            self.cost_note.configure(text="Add submissions to see the estimate")
        else:
            est = estimate(app.submissions, r, app.cfg, app.accuracy)
            self.cost.configure(text=f"Estimated cost: ~{format_usd(est.usd)}")
            notes = []
            if est.assumed_count:
                notes.append(f"YouTube links estimated at {format_mmss(est.assumed_minutes * 60)} each")
            if any(not s.is_youtube for s in app.submissions):
                lo, hi = estimate_minutes_range(app.submissions)
                notes.append(f"Time: {lo}–{hi} min (depends on your connection)")
            else:
                notes.append("Time: a few minutes")
            self.cost_note.configure(text="  ·  ".join(notes))


class YouTubeDialog(tk.Toplevel):
    def __init__(self, app: App, on_add):
        super().__init__(app)
        self.app = app
        self.on_add = on_add
        t = app.theme
        self.title("Add YouTube link")
        self.configure(bg=WHITE)
        self.resizable(False, False)
        self.transient(app)
        body = tk.Frame(self, bg=WHITE)
        body.pack(fill="both", expand=True, padx=t.px(24), pady=t.px(20))
        tk.Label(body, text="YouTube link", bg=WHITE, fg=TEXT, font=t.small_bold, anchor="w").pack(fill="x")
        self.url = tk.StringVar()
        self.entry = ttk.Entry(body, textvariable=self.url, font=t.base)
        self.entry.pack(fill="x", pady=(t.px(4), t.px(4)))
        self.error = tk.Label(body, text="", bg=WHITE, fg=RED, font=t.small, anchor="w")
        self.error.pack(fill="x")
        tk.Label(
            body,
            text="The video must be Public. Gemini can't read unlisted or private videos.",
            bg=WHITE,
            fg=FAINT,
            font=t.small,
            anchor="w",
        ).pack(fill="x")
        buttons = tk.Frame(body, bg=WHITE)
        buttons.pack(fill="x", pady=(t.px(16), 0))
        ttk.Button(buttons, text="Add", style="SmallPrimary.TButton", command=self.add).pack(side="right")
        ttk.Button(buttons, text="Cancel", style="SmallSecondary.TButton", command=self.destroy).pack(
            side="right", padx=(0, t.px(8))
        )
        self.entry.bind("<Return>", lambda _e: self.add())
        self.bind("<Escape>", lambda _e: self.destroy())

        w, h = t.px(480), t.px(190)
        app.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - w) // 2
        y = app.winfo_rooty() + (app.winfo_height() - h) // 3
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.after(50, self._focus)

    def _focus(self):
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.entry.focus_set()

    def add(self):
        try:
            sub = submission_from_youtube(self.url.get())
        except SubmissionError as exc:
            self.error.configure(text=str(exc))
            return
        self.destroy()
        self.on_add(sub)


class ProgressScreen(Screen):
    COLUMNS = [("Video", 0), ("Status", 200), ("Details", 250)]
    STATE_TEXT = {
        "waiting": ("Waiting", FAINT),
        "uploading": ("Uploading…", BLUE),
        "processing": ("Preparing…", BLUE),
        "analyzing": ("Analyzing…", BLUE),
        "done": ("✓ Done", GREEN),
        "error": ("Not graded", AMBER),
        "cancelled": ("Cancelled", FAINT),
    }

    def __init__(self, parent, app: App):
        super().__init__(parent, app)
        self.session = app.session
        self.total = len(app.submissions)
        self.rows: dict[str, dict] = {}

        bar = self.bottom_bar()
        self.cost = tk.Label(bar, text="Cost so far: US$0.00", bg=SURFACE, fg=MUTED, font=self.t.base)
        self.cost.pack(side="left")
        self.cancel_btn = ttk.Button(bar, text="Cancel", style="Secondary.TButton", command=self.cancel)
        self.cancel_btn.pack(side="right", pady=self.px(14))

        body = tk.Frame(self, bg=WHITE)
        body.pack(fill="both", expand=True, padx=self.px(28), pady=(self.px(18), self.px(12)))
        top = tk.Frame(body, bg=WHITE)
        top.pack(fill="x")
        tk.Label(
            top, text=f"Grading {self.total} submission{'s' if self.total != 1 else ''}", bg=WHITE, fg=TEXT, font=self.t.h1
        ).pack(side="left")
        self.done_label = tk.Label(top, text="", bg=WHITE, fg=MUTED, font=self.t.base)
        self.done_label.pack(side="right")
        self.overall = ttk.Progressbar(body, style="Blue.Horizontal.TProgressbar", maximum=self.total, value=0)
        self.overall.pack(fill="x", pady=(self.px(10), self.px(6)))
        acc = "High accuracy" if app.accuracy == "high" else "Normal accuracy"
        tk.Label(
            body,
            text=f"Rubric: {shorten(app.rubric.file_name, 40)} · {app.cfg.model_label} · {acc} · "
            f"up to {app.cfg.get('max_parallel')} in parallel",
            bg=WHITE,
            fg=MUTED,
            font=self.t.small,
            anchor="w",
        ).pack(fill="x")

        self.list = ScrollList(body, self.t, self.COLUMNS, height=280)
        self.list.pack(fill="x", pady=(self.px(14), self.px(12)))
        self.list.clear()
        for sub in app.submissions:
            row = self.list.add_row(height=42)
            name = ("▶ " if sub.is_youtube else "") + shorten(sub.display_name, 40)
            tk.Label(row, text=name, bg=WHITE, fg=TEXT, font=self.t.base).grid(row=0, column=0, sticky="w")
            status = tk.Label(row, text="Waiting", bg=WHITE, fg=FAINT, font=self.t.bold)
            status.grid(row=0, column=1, sticky="w")
            detail = tk.Frame(row, bg=WHITE)
            detail.grid(row=0, column=2, sticky="ew")
            label = tk.Label(detail, text="", bg=WHITE, fg=MUTED, font=self.t.small, anchor="w")
            label.pack(fill="x")
            bar_widget = ttk.Progressbar(detail, style="Thin.Horizontal.TProgressbar", maximum=1.0)
            self.rows[sub.uid] = {"status": status, "label": label, "bar": bar_widget}

        self.notice_box(
            body,
            "You can minimize this window and keep using your PC. Don't close RubricVision AI until grading finishes.",
        ).pack(fill="x")
        self._update_counts()

    def _update_counts(self):
        s = self.session
        done = len(s.results) + len(s.errors) if s else 0
        self.done_label.configure(text=f"{done} of {self.total} done")
        self.overall.configure(value=done)
        self.app.title(f"{APP_NAME} — Grading ({done} of {self.total})")

    def on_grading_event(self, e: Event):
        if e.kind == "cost":
            self.cost.configure(text=f"Cost so far: {format_usd(e.total_cost)}")
            return
        if e.kind == "finished":
            self.app.show_results()
            return
        row = self.rows.get(e.uid)
        if not row:
            return
        text, color = self.STATE_TEXT.get(e.state, (e.state, MUTED))
        row["status"].configure(text=text, fg=color)
        if e.state == "uploading" and e.progress is not None:
            row["label"].pack_forget()
            row["bar"].pack(fill="x", pady=self.px(2))
            row["bar"].configure(value=e.progress)
            row["status"].configure(text=f"Uploading… {int(e.progress * 100)}%")
        else:
            row["bar"].pack_forget()
            row["label"].pack(fill="x")
            row["label"].configure(text=shorten(e.detail, 34))
        if e.kind in ("done", "error"):
            self._update_counts()

    def cancel(self):
        if not messagebox.askyesno(
            APP_NAME,
            "Stop grading? Submissions already graded keep their reports; the rest are cancelled "
            "and their uploads are deleted from Google.",
            parent=self.app,
        ):
            return
        self.cancel_btn.configure(text="Cancelling…", state="disabled")
        if self.session:
            self.session.cancel()


class ResultsScreen(Screen):
    COLUMNS = [("Video", 0), ("Meets", 58), ("Partial", 60), ("Fails", 52), ("Not shown", 80), ("Review", 62), ("", 128)]

    def __init__(self, parent, app: App):
        super().__init__(parent, app)
        self.session = app.session
        bar = self.bottom_bar()
        ttk.Button(bar, text="Open reports folder", style="Secondary.TButton", command=self.open_folder).pack(
            side="left", pady=self.px(14)
        )
        ttk.Button(bar, text="New grading session", style="Primary.TButton", command=self.new_session).pack(
            side="right", pady=self.px(14)
        )

        body = tk.Frame(self, bg=WHITE)
        body.pack(fill="both", expand=True, padx=self.px(28), pady=(self.px(18), self.px(12)))
        self.title_label = tk.Label(body, text="", bg=WHITE, fg=TEXT, font=self.t.h1, anchor="w")
        self.title_label.pack(fill="x")
        self.summary = tk.Label(body, text="", bg=WHITE, fg=MUTED, font=self.t.base, anchor="w")
        self.summary.pack(fill="x", pady=(self.px(2), self.px(10)))
        self.alert_holder = tk.Frame(body, bg=WHITE)
        self.alert_holder.pack(fill="x")
        self.list = ScrollList(body, self.t, self.COLUMNS, height=300)
        self.list.pack(fill="x", pady=(self.px(10), 0))
        self.render()

    def render(self):
        s = self.session
        subs = list(s.submissions.values()) if s else []
        graded = len(s.results) if s else 0
        failed = [u for u in s.order if u in s.errors] if s else []
        cancelled = s and all(s.errors[u].kind == GradingError.CANCELLED for u in failed) and failed
        self.title_label.configure(text="Grading stopped" if cancelled and not graded else "Grading complete")
        parts = [f"{graded} of {len(subs)} submissions graded"]
        if failed:
            parts.append(f"{len(failed)} not graded")
        if s:
            parts.append(f"Total cost: {format_usd(s.total_cost)}")
        if any(not x.is_youtube for x in subs):
            parts.append("Uploaded videos deleted from Google")
        self.summary.configure(text=" · ".join(parts))

        for child in self.alert_holder.winfo_children():
            child.destroy()
        kinds = {s.errors[u].kind for u in failed} if s else set()
        kinds.discard(GradingError.CANCELLED)
        if len(kinds) == 1:
            uid = next(u for u in failed if s.errors[u].kind in kinds)
            self.notice_box(self.alert_holder, s.errors[uid].message, "warn").pack(fill="x")
        elif kinds:
            self.notice_box(
                self.alert_holder, "Some submissions weren't graded. The reason is shown on each row; fix it and click Retry.", "warn"
            ).pack(fill="x")

        min_h = int(self.app.cfg.get("min_recommended_height"))
        self.list.clear()
        for uid in (s.order if s else []):
            sub = s.submissions[uid]
            result = s.results.get(uid)
            err = s.errors.get(uid)
            flags = []
            duration = result.duration_s if result else sub.duration_s
            lim = self.app.rubric.max_duration_s if self.app.rubric else None
            if lim and duration and duration > lim + 0.5:
                flags.append(f"⚠ {format_mmss(duration)} (max {format_mmss(lim)})")
            if sub.is_low_resolution(min_h):
                flags.append(f"⚠ {sub.resolution_label} (low res.)")
            bg = AMBER_ROW if err and err.kind != GradingError.CANCELLED else WHITE
            row = self.list.add_row(bg=bg, height=42)
            name = tk.Frame(row, bg=bg)
            name.grid(row=0, column=0, sticky="w")
            tk.Label(
                name, text=("▶ " if sub.is_youtube else "") + shorten(sub.display_name, 24 if flags else 42), bg=bg, fg=TEXT, font=self.t.base
            ).pack(side="left")
            if flags:
                tk.Label(name, text="  " + "  ".join(flags), bg=bg, fg=AMBER, font=self.t.tiny).pack(side="left")
            if result:
                counts = result.counts()
                for col, status in enumerate(STATUSES, start=1):
                    value = counts[status]
                    color = STATUS_COLORS[status] if value else FAINT
                    tk.Label(row, text=str(value), bg=bg, fg=color, font=self.t.bold).grid(row=0, column=col, sticky="w")
                ttk.Button(
                    row, text="Open report", style="Outline.TButton", command=lambda r=result: self.open_report(r)
                ).grid(row=0, column=6, sticky="e")
            elif err:
                short = {
                    GradingError.NOT_PUBLIC: "Not graded: video isn't public",
                    GradingError.NO_BALANCE: "Not graded: no balance left",
                    GradingError.QUOTA: "Not graded: no quota left",
                    GradingError.NETWORK: "Not graded: no connection",
                    GradingError.CANCELLED: "Cancelled",
                    GradingError.FILE_FAILED: "Not graded: Google couldn't read the file",
                    GradingError.KEY_INVALID: "Not graded: API key rejected",
                }.get(err.kind, "Not graded: " + shorten(err.message, 40))
                lbl = tk.Label(row, text=short, bg=bg, fg=AMBER if err.kind != GradingError.CANCELLED else FAINT, font=self.t.bold)
                lbl.grid(row=0, column=1, columnspan=5, sticky="w")
                if err.detail:
                    lbl.bind("<Button-1>", lambda _e, d=err.detail: messagebox.showinfo(APP_NAME, d, parent=self.app))
                    lbl.configure(cursor="question_arrow")
                ttk.Button(row, text="Retry", style="SmallPrimary.TButton", command=lambda u=uid: self.retry(u)).grid(
                    row=0, column=6, sticky="e"
                )
            else:
                tk.Label(row, text="Grading…", bg=bg, fg=BLUE, font=self.t.bold).grid(row=0, column=1, columnspan=5, sticky="w")

    def on_grading_event(self, e: Event):
        if e.kind in ("done", "error", "cost", "finished"):
            self.render()

    def open_report(self, result: SubmissionResult):
        if result.report_path and result.report_path.exists():
            webbrowser.open(result.report_path.resolve().as_uri())
        else:
            messagebox.showerror(APP_NAME, "The report file wasn't found.", parent=self.app)

    def open_folder(self):
        folder = self.app.session_dir
        if folder and folder.exists():
            open_path(folder)
        else:
            messagebox.showinfo(APP_NAME, "No reports were created in this session.", parent=self.app)

    def retry(self, uid: str):
        if self.session:
            self.session.retry(uid)
            self.render()

    def new_session(self):
        if self.session and self.session.busy:
            messagebox.showinfo(APP_NAME, "Wait until the retries finish.", parent=self.app)
            return
        self.app.new_session()


def run(config: Config) -> None:
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
        except (AttributeError, OSError):
            pass
    app = App(config)
    app.mainloop()
