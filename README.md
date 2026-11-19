# RubricVision AI

Grades software demo videos against a rubric using Gemini in the cloud. The
program runs on Windows; the interface is in English and the grading reports
are written in Spanish.

© 2026 Marcos Buydid. All Rights Reserved

---

### Automatic builds on GitHub

`.github/workflows/build.yml` builds the `.exe` on every push to `master` (or
manually from the **Actions** tab → **Run workflow**). It runs the tests
first, then uploads the `dist` folder as the artifact
`RubricVisionAI-windows-<commit>` (kept for 30 days). If your main branch is
`main`, change `branches: [master]` in that file.

## 1. Before using it: the Gemini API key

Each teacher uses their own key and pays only for what they grade:

1. Go to <https://aistudio.google.com> → **Dashboard → API keys** →
   **Create API key**.
2. In the same list, **Set up billing** → choose **Prepay**, add a small
   amount (for example US$10) and leave **auto-reload turned off**.
3. Recommended: in the project settings, set log retention to the minimum.

RubricVision AI asks for the key **every time it starts** and **never saves it**.

## 2. How to use it

1. **API key:** paste it and click *Verify and continue* (this costs nothing).
2. **Rubric:** *Load rubric…* and choose the Excel or YAML file.
3. **Submissions (up to 10):**
   - *Add videos…* for files on the PC (MP4, MOV, AVI, WEBM, WMV, MPEG, FLV,
     3GP). MKV isn't supported by Gemini.
   - *Add YouTube link…* for **public** YouTube videos (unlisted or private
     videos can't be read).
   - Videos longer than the rubric's maximum length or below 720p are flagged,
     but they're still graded.
4. **Reports folder:** Choose folder… and pick where the reports will be saved.
   Nothing is saved anywhere else, and Grade stays disabled until a folder is chosen.
   The folder is kept for the rest of the session (also after New grading session),
   but you choose it again each time you open the program.
5. **Accuracy:** *High* (recommended) reads small interface text more reliably.
6. **Grade:** all submissions are processed in parallel. You can minimize the
   window; don't close it until grading finishes.
7. **Results:** *Open report* opens the report in the browser. If something
   fails (video not public, no balance left, no connection), fix it and click
   *Retry*.
Each submission produces, in the folder you chose, one `.html` report plus a
`.json` with the model's full answer. If the same video is graded again in the
same folder (same file name, or same YouTube video), its previous `.html` and
`.json` are **replaced**, so the folder and the Excel summary always hold the
latest grading of each submission. A different video that happens to get the
same report name is kept, with a number added (`group-1_demo_2.html`).

### Excel summary

When you choose the reports folder, the program creates
**`resumen_correcciones.xlsx`** there with only the column headers (if the file
doesn't exist yet). Every time a grading session finishes (and after each
*Retry*), it rebuilds the file from **all** the `.html` reports in that folder,
including ones from earlier sessions. The
*Excel summary* button on the results screen rebuilds it and opens it.

| Sheet | Contents |
|---|---|
| `Resumen` | One row per submission: video, source, date, model, accuracy, duration, over the limit, resolution, count per status, % Cumple, cost and a link to the report. |
| `Detalle` | One row per rubric item of each submission: requirement, profile, item, status, timestamps, evidence, what's missing and notes. Has filters. |
| `Matriz` | Rubric items down, submissions across, the status in each cell, and how many items each submission meets. |

Each report carries its data in a hidden JSON block, so the Excel is built
from the HTML files themselves. Reports made before this feature are read
from their visible content. If the Excel is open when it needs updating, the
program asks you to close it and try again.

## 3. Rubric format

The rubric content is written in Spanish (as in the course documents), so the
sheet and column names below are in Spanish.

### Excel (`.xlsx`)

Sheet **`Rubrica`** with the columns **Requerimiento | Perfil | Item**
(requirement, profile, item), one row per item. If a
Requerimiento or Perfil cell is empty, the value from the row above is used.

Sheet **`Configuracion`**: `Duracion maxima | 15` (maximum video length in
minutes; `15:00` is also accepted).

### YAML (`.yaml`)

```yaml
duracion_maxima: "15:00"
requerimientos:
```

## 4. Item statuses

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


## 5. Privacy and video deletion

- Local files are uploaded to Google only to be analyzed and are **deleted as
  soon as each grading finishes**, including when there was an error or it was
  cancelled.
- If the program closed unexpectedly, it deletes that session's leftover
  uploads the next time it starts (Google also removes them after 48 hours).
- YouTube links upload nothing: Gemini reads the video directly.
- On the paid tier, Google doesn't use the videos to train its models.
- The original video on your PC is never modified.


## 6. `config.json`

| Key | Purpose |
|---|---|
| `model` | Default model when the program opens (`gemini-3.8-flash`). The others listed under `models` can be picked in the *Model* list. |
| `models.<id>.prices_usd_per_million` | Prices per million tokens, with the date they take effect. Update them if Google changes its prices. |
| `max_submissions`, `max_parallel` | Maximum submissions per session and in parallel (10). |
| `min_recommended_height` | Minimum recommended resolution (720). |

Gemini 4 prices included (USD per million tokens, input / output): Hydrogen
1.50 / 7.50; Argon and Carbon 2 / 10 until November 30, 2026 and 4 / 20 from
December 1; Neon 3 / 15. The Gemini 4 models are assumed to count video tokens
like Gemini 3 (280 per frame at high accuracy), so their estimates are
approximate; "Cost so far" always uses the real token counts.

The cost estimate uses: seconds of video × (280 tokens per frame at high
accuracy or 70 at normal + 25 for audio) × input price, plus the answer. "Cost
so far" uses the actual token counts returned by Gemini.

## 7. Troubleshooting

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
