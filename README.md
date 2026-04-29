# Papercraft AI

Turn any 2D image into a printable papercraft template, end-to-end, for free.

```
[ image.png ]
     │
     ▼  Hugging Face Space  (free, no GPU needed locally)
[ AI 3D mesh ]
     │
     ▼  trimesh quadric decimation
[ low-poly OBJ ]
     │
     ▼  Blender headless + "Export Paper Model" addon
[ template.pdf ]   <-- print, cut, fold, glue
```

A small FastAPI web app you run locally. Drop an image, get a printable
PDF with cut + fold lines and glue tabs.

## Requirements

- **Python 3.10+** (tested on 3.11)
- **Blender 4.x** with the *Export Paper Model* addon enabled.
  - In Blender: `Edit → Preferences → Get Extensions → search "Export Paper Model" → Install`
  - (Or in older Blender: `Add-ons → Import-Export: Export Paper Model → enable`)
- An internet connection (for the Hugging Face Space call)
- *Optional:* a free Hugging Face account + token to raise rate limits

## Setup

```powershell
# from the project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# create your .env from the template, then edit it
copy .env.example .env
notepad .env
```

The most important variable is `BLENDER_PATH`. It must point to your
`blender.exe` (or `blender` on macOS/Linux). Examples:

```
BLENDER_PATH=C:\Program Files\Blender Foundation\Blender 4.2\blender.exe
BLENDER_PATH=D:\PortableApps\Blender\blender.exe
```

## Run

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>, drop an image, click **Generate template**.
First run takes a bit longer because the Hugging Face Space spins up.

## How the pieces fit together

| Stage | Module | What it does |
|---|---|---|
| Upload | `app/main.py` | Accepts image, creates a Job, kicks off background task |
| Image → 3D | `app/stages/image_to_3d.py` | Calls a public HF Space (TripoSR / Hunyuan3D / TRELLIS), returns a GLB |
| Decimate | `app/stages/decimate.py` | Simplifies to ~80 faces, normalises scale |
| Unfold | `app/stages/unfold.py` + `blender_scripts/unfold.py` | Runs Blender headless, marks seams on sharp edges, exports a printable PDF |
| UI | `static/` | Drag-and-drop, progress bar, downloads |

## Tweak knobs

- **`TARGET_FACES`** in `.env` – lower (40–60) = blockier, easier to assemble; higher (120–200) = more detail, harder to cut.
- **`HF_SPACE_ID`** – change the Space if the default is rate-limited or its API drifted. Other adapters are pre-wired in `image_to_3d.py`.
- **`PAGE_SIZE`** – `A4` or `LETTER`.
- **Sticker (glue tab) width** – edit `sticker_width` in `blender_scripts/unfold.py`.

## Troubleshooting

**"Could not locate Blender"** – set `BLENDER_PATH` in `.env`.

**"Export Paper Model addon is not available"** – open Blender, install the addon (see Requirements), then close Blender and rerun. The addon enable persists in your user preferences, so headless Blender will pick it up.

**"All image-to-3D Spaces failed"** – the public Spaces are sometimes overloaded. Try again in a minute, or set `HF_TOKEN` in `.env`, or change `HF_SPACE_ID` to another preset.

**Output template looks like one giant blob** – the unfold heuristic didn't find good cut seams. Try lowering `TARGET_FACES` to 50–60, or open the OBJ in Blender, mark seams manually, and rerun the export.

## Roadmap

- [ ] 3D preview on the result page
- [ ] Per-job tweaks (face count, page size) in the UI
- [ ] Texture-baked output (full colour template)
- [ ] Self-hosted GPU mode (skip HF Spaces) for unlimited free generations
- [ ] Stripe paywall + watermarked previews for selling templates online
