# RubricVision AI

Grades software demo videos against a rubric using Gemini in the cloud. The
program runs on Windows; the interface is in English and the grading reports
are written in Spanish.

© 2026 Marcos Buydid. All Rights Reserved

---

## 1. Build `RubricVisionAI.exe` (one time)

1. Install **Python 3.11 or newer** from <https://www.python.org/downloads/>.
   In the installer, check **"Add python.exe to PATH"**.
2. Unzip this folder on your PC, for example to `C:\RubricVisionAI-src`.
3. Double-click **`build.bat`**. The first run takes a few minutes: it creates
   an isolated environment, installs the libraries and builds the executable.
4. When it finishes, the **`dist`** folder contains:
   - `RubricVisionAI.exe`: the program.
   - `config.json`: model and prices (editable with Notepad).
   - `plantilla_rubrica.xlsx`: rubric template.

The `dist` folder is what you share with other teachers. They don't need to
install Python.

> If Windows Defender or SmartScreen says the program is unrecognized, that's
> normal for new executables without a digital signature: "More info" →
> "Run anyway".

To try it without building the `.exe`, use `run_from_source.bat`.

### Automatic builds on GitHub

`.github/workflows/build.yml` builds the `.exe` on every push to `master` (or
manually from the **Actions** tab → **Run workflow**). It runs the tests
first, then uploads the `dist` folder as the artifact
`RubricVisionAI-windows-<commit>` (kept for 30 days). If your main branch is
`main`, change `branches: [master]` in that file.

## 2. Before using it: the Gemini API key

Each teacher uses their own key and pays only for what they grade:

1. Go to <https://aistudio.google.com> → **Dashboard → API keys** →
   **Create API key**.
2. In the same list, **Set up billing** → choose **Prepay**, add a small
   amount (for example US$10) and leave **auto-reload turned off**.
3. Recommended: in the project settings, set log retention to the minimum.

RubricVision AI asks for the key **every time it starts** and **never saves it**.

## 3. How to use it

1. **API key:** paste it and click *Verify and continue* (this costs nothing).
2. **Rubric:** *Load rubric…* and choose the Excel or YAML file.
3. **Submissions (up to 10):**
   - *Add videos…* for files on the PC (MP4, MOV, AVI, WEBM, WMV, MPEG, FLV,
     3GP). MKV isn't supported by Gemini.
   - *Add YouTube link…* for **public** YouTube videos (unlisted or private
     videos can't be read).
   - Videos longer than the rubric's maximum length or below 720p are flagged,
     but they're still graded.
4. **Accuracy:** *High* (recommended) reads small interface text more reliably.
5. **Grade:** all submissions are processed in parallel. You can minimize the
   window; don't close it until grading finishes.
6. **Results:** *Open report* opens the report in the browser. If something
   fails (video not public, no balance left, no connection), fix it and click
   *Retry*.

Reports are saved to `Documents\RubricVision AI\Reports\<date and time>\`: one
`.html` per submission plus a `.json` with the model's full answer.

## 4. Rubric format

The rubric content is written in Spanish (as in the course documents), so the
sheet and column names below are in Spanish.

### Excel (`.xlsx`)

Sheet **`Rubrica`** with the columns **Requerimiento | Perfil | Item**
(requirement, profile, item), one row per item (each bullet in the PDF). If a
Requerimiento or Perfil cell is empty, the value from the row above is used.

Sheet **`Configuracion`**: `Duracion maxima | 15` (maximum video length in
minutes; `15:00` is also accepted).

The template (`plantilla_rubrica.xlsx`, or the *Download template* link in the
program) already has this structure.

### YAML (`.yaml`)

```yaml
duracion_maxima: "15:00"
requerimientos:
  - nombre: Registro, ingreso y cambio de contraseña
    perfil: Usuario sin permisos; luego Administrador
    items:
      - registrar un usuario y validar DNI, nombre, apellido y formato y unicidad del correo
      - mostrar que DNI y correo no pueden repetirse
```

## 5. Item statuses

Shown in Spanish in the reports and in English in the results screen.

| Report (Spanish) | Results screen | When |
|---|---|---|
| Cumple | Meets | Everything the item asks for is shown, with evidence and a timestamp. |
| Cumple parcialmente | Partial | The item asks for several things and at least one, but not all, was shown (the report says what's missing). |
| Se muestra pero falla | Fails | It was attempted and the result contradicts what was asked. |
| No se muestra | Not shown | It doesn't appear in the video. |
| Requiere revisión | Review | It can't be decided with confidence: the teacher should check it. |

The program never accepts "Meets" without evidence: if the model returns it
that way, it's changed to "Review".

## 6. Privacy and video deletion

- Local files are uploaded to Google only to be analyzed and are **deleted as
  soon as each grading finishes**, including when there was an error or it was
  cancelled.
- If the program closed unexpectedly, it deletes that session's leftover
  uploads the next time it starts (Google also removes them after 48 hours).
- YouTube links upload nothing: Gemini reads the video directly.
- On the paid tier, Google doesn't use the videos to train its models.
- The original video on your PC is never modified.

## 7. `config.json`

| Key | Purpose |
|---|---|
| `model` | Gemini model (default `gemini-3.8-flash`). `gemini-4-hydrogen` and `gemini-4-neon` are also included; they cost more. |
| `models.<id>.prices_usd_per_million` | Prices per million tokens, with the date they take effect. Update them if Google changes its prices. |
| `max_submissions`, `max_parallel` | Maximum submissions per session and in parallel (10). |
| `min_recommended_height` | Minimum recommended resolution (720). |
| `reports_dir` | Reports folder (empty = Documents\RubricVision AI\Reports). |

The cost estimate uses: seconds of video × (280 tokens per frame at high
accuracy or 70 at normal + 25 for audio) × input price, plus the answer. "Cost
so far" uses the actual token counts returned by Gemini.

## 8. Troubleshooting

- The error log is at `%LOCALAPPDATA%\RubricVision AI\rubricvision.log`
  (it never stores the API key).
- "The configured model isn't available": check `model` in `config.json`.
- "isn't public": the YouTube video is unlisted or private.
- "balance has run out": top up your balance in Google AI Studio and click
  *Retry*.

## For developers

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt pytest
pytest
python RubricVisionAI.pyw
```

Code in `rubricvision/`: `rubric.py` (rubrics), `media.py` (videos and links),
`gemini_service.py` (API, upload, deletion), `grading.py` (parallel grading),
`report.py` (HTML report), `ui.py` (interface).
