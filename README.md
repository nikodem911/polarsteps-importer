# Polarsteps Importer

A command-line tool to programmatically import trips, steps, and media into [Polarsteps](https://www.polarsteps.com) from local JSON files and photo collections.

---

> [!CAUTION]
> **Personal use only.** This tool relies on Polarsteps' internal, undocumented REST API, which is reverse-engineered from browser traffic. It may break without notice at any time if Polarsteps changes their API. It is not affiliated with or endorsed by Polarsteps B.V. Use at your own risk.

---

## Table of Contents

- [Requirements](#requirements)
- [Setup](#setup)
- [Authentication](#authentication)
- [Typical Workflow](#typical-workflow)
- [prepare-import.py — Prepare Pictures](#prepare-importpy--prepare-pictures)
  - [group — Organise by Date](#group--organise-by-date)
  - [generate — Build the Import File](#generate--build-the-import-file)
- [import.py — Import to Polarsteps](#importpy--import-to-polarsteps)
  - [import — Full Import (Recommended)](#import--full-import-recommended)
  - [trip — Create a Trip Only](#trip--create-a-trip-only)
  - [steps — Import Steps Only](#steps--import-steps-only)
  - [media — Upload Media Only](#media--upload-media-only)
- [JSON File Formats](#json-file-formats)
- [Global Flags](#global-flags)
---

## Requirements

- Python 3.10+
- A Polarsteps account with valid session cookies (see [Authentication](#authentication))

## Setup

```bash
# 1. Clone the repository
git clone https://github.com/yourname/polarsteps-importer.git
cd polarsteps-importer

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

`requirements.txt` includes:
- `requests` — HTTP client for the Polarsteps and Nominatim APIs
- `python-dotenv` — loads credentials from a `.env` file
- `Pillow` — reads EXIF metadata and resizes images for upload

---

## Authentication

The tool authenticates as your browser would, using two session cookies. You need to extract them from your browser after logging in to Polarsteps.

**Steps:**

1. Log in to [polarsteps.com](https://www.polarsteps.com) in your browser.
2. Open DevTools → **Application** (Chrome) or **Storage** (Firefox) → Cookies → `www.polarsteps.com`.
3. Copy the values of `remember_token` and `session`.

Create a `.env` file in the project root (it is gitignored by default):

```ini
POLARSTEPS_REMEMBER_TOKEN=your_remember_token_here
POLARSTEPS_SESSION=your_session_cookie_here
POLARSTEPS_USERNAME=your_polarsteps_username

# Optional: home location used by prepare-import.py --default-home
POLARSTEPS_HOME_LOCATION=52.3676,4.9041
```

> [!NOTE]
> Credentials can also be passed as environment variables directly. The `.env` file is just a convenience. **Never commit it to version control.**

---

## Typical Workflow

The recommended end-to-end flow for importing a photo album (e.g. a Google Photos album downloaded as a folder) is:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  1. Export / collect photos                                              │
│     Download your album (e.g. Google Takeout) into a local folder.      │
│     Each photo should carry EXIF metadata (date + ideally GPS).         │
├──────────────────────────────────────────────────────────────────────────┤
│  2. Group photos by date  (prepare-import.py group)                      │
│     The script reads each photo's EXIF date and moves it into a         │
│     YYYY-MM-DD subfolder inside an output directory.                    │
├──────────────────────────────────────────────────────────────────────────┤
│  3. Review & adjust folders manually                                     │
│     Each subfolder becomes one Polarsteps step.                         │
│     • Rename folders to meaningful place names (e.g. "Amsterdam").      │
│     • Split a single day into multiple steps by creating extra folders  │
│       (e.g. "Morning hike", "Evening canal tour").                      │
│     • Delete photos you don't want to upload.                           │
│     • Re-order photos by renaming them if order matters.                │
├──────────────────────────────────────────────────────────────────────────┤
│  4. Generate the import file  (prepare-import.py generate)               │
│     Scans the adjusted folders, reads GPS from photos, resolves         │
│     coordinates via OpenStreetMap, and writes import_data.json.         │
│     Optionally synthesises a "Home" step at the start and end.          │
├──────────────────────────────────────────────────────────────────────────┤
│  5. Review import_data.json                                              │
│     Open the generated JSON and verify trip name, dates, step names,    │
│     coordinates, and media paths. Edit as needed.                       │
├──────────────────────────────────────────────────────────────────────────┤
│  6. Import to Polarsteps  (import.py import)                             │
│     Creates the trip, registers each step (with reverse geocoding),     │
│     uploads all photos to S3, and links them to the step.               │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## `prepare-import.py` — Prepare Pictures

### `group` — Organise by Date

Recursively scans the input directory, reads each photo's EXIF date, and moves it into a `YYYY-MM-DD` subdirectory. Photos without an EXIF date go into `no-date/`.

```bash
python3 prepare-import.py group <input_dir> [options]
```

| Option | Description |
|--------|-------------|
| `<input_dir>` | Directory containing photos to scan recursively |
| `-o, --output-dir DIR` | Output directory (default: `out/` next to `prepare-import.py`) |

**Example:**

```bash
# Move photos from a Google Photos export into dated folders under ./out
python3 prepare-import.py group ~/Downloads/MyTrip -o out
```

After running, `out/` will look like:

```
out/
├── 2026-07-01/
│   ├── IMG_0001.jpg
│   └── IMG_0002.jpg
├── 2026-07-03/
│   └── IMG_0010.jpg
└── no-date/          ← photos with no EXIF date
    └── scan.jpg
```

**Manual adjustments to make before step 4:**

- Rename folders to place names: `2026-07-01` → `Barcelona`
- Split one day into multiple steps by duplicating and renaming the folder:
  ```
  out/
  ├── Morning hike/
  │   └── IMG_0001.jpg
  └── Evening canal tour/
      └── IMG_0002.jpg
  ```
- Delete unwanted photos, add extras.

### `generate` — Build the Import File

Scans the (adjusted) output directory, reads the oldest photo's timestamp and GPS per folder, and produces an `import_data.json` ready for `import.py`.

```bash
python3 prepare-import.py generate <input_dir> [options]
```

| Option | Description |
|--------|-------------|
| `<input_dir>` | Directory of step subdirectories (positional or `-i`) |
| `-o, --output FILE` | Output JSON file (default: `<input_dir>/import_data.json`) |
| `--trip-name NAME` | Trip name (default: input directory name) |
| `--trip-description TEXT` | Trip description |
| `--start-date YYYY-MM-DD` | Override trip start date |
| `--end-date YYYY-MM-DD` | Override trip end date |
| `--home lat,lon` | Add synthetic "Home" steps at start and end of the trip |
| `--default-home` | Use `POLARSTEPS_HOME_LOCATION` from `.env` for home steps |
| `--is-public` | Mark the trip as public (default: private) |

**Example:**

```bash
# Basic generation — trip name taken from folder name
python3 prepare-import.py generate out -o out/import_data.json --trip-name "Alps 2026"

# With a home step injected at start and end
python3 prepare-import.py generate out --default-home --trip-name "Alps 2026" --start-date 2026-07-01 --end-date 2026-07-15
```

---

## `import.py` — Import to Polarsteps

### `import` — Full Import (Recommended)

Creates the trip, then each step (with reverse geocoding via OpenStreetMap), then uploads and links all media. This is the single command that does everything.

```bash
python3 import.py import <import_file.json>
```

**Example:**

```bash
python3 import.py import out/import_data.json
```

---

### `trip` — Create a Trip Only

Creates a new trip on Polarsteps from a JSON object. Useful if you want to create the trip separately before adding steps.

```bash
python3 import.py trip <trip_file.json>
```

**Example `examples/data.json`:**

```json
{
  "name": "Summer Alps Expedition",
  "description": "Two weeks of trekking across the European Alps.",
  "start_date": "2026-07-01",
  "end_date": "2026-07-15",
  "is_public": false
}
```

```bash
python3 import.py trip examples/data.json
```

---

### `steps` — Import Steps Only

Imports an array of steps into an existing trip. Each step must include `trip_id`.

```bash
python3 import.py steps <steps_file.json> [--trip-slug SLUG]
```

**Example `examples/step_data.json`:**

```json
[
  {
    "trip_id": 29889790,
    "location_lat": 41.3927674,
    "location_lon": 2.0577881,
    "name": "Barcelona",
    "description": "Visiting the beautiful city",
    "start_time": "2026-07-01T16:00:00.000Z",
    "type": 0
  }
]
```

```bash
python3 import.py steps examples/step_data.json
```

---

### `media` — Upload Media Only

Uploads image files and links them to an existing step. Useful for adding photos to a step that was already created.

```bash
python3 import.py media <media_file.json>
# or using the shorthand flag from the root command:
python3 import.py -m <media_file.json>
```

**Example `examples/media_data.json`:**

```json
{
  "trip_id": 29889790,
  "step_id": 279040273,
  "files": [
    "barcelona1.jpg",
    "barcelona2.jpg"
  ]
}
```

File paths in `files` are resolved relative to the JSON file's directory.

```bash
python3 import.py media examples/media_data.json
```

---

## JSON File Formats

### Full Import File (`import_data.json`)

Top-level fields are passed as the trip payload; `steps` is processed separately.

```json
{
  "name": "Trip name",
  "description": "Optional description",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "is_public": false,
  "steps": [
    {
      "location_lat": 41.3927,
      "location_lon": 2.0577,
      "name": "Step name",
      "description": "Optional step description",
      "start_time": "2026-07-01T12:00:00.000Z",
      "type": 0,
      "media": [
        "relative/path/to/photo1.jpg",
        "relative/path/to/photo2.jpg"
      ]
    }
  ]
}
```

### Step fields

| Field | Required | Description |
|-------|----------|-------------|
| `location_lat` | ✅ | Latitude in decimal degrees |
| `location_lon` | ✅ | Longitude in decimal degrees |
| `name` | — | Step name. Falls back to geocoded place name |
| `description` | — | Step body text |
| `start_time` | — | ISO 8601 UTC timestamp (`YYYY-MM-DDTHH:MM:SS.000Z`) |
| `type` | — | `0` = draft, `1` = published (default: `1`) |
| `media` | — | List of image paths (relative to the JSON file) |
| `trip_id` | steps-only | Required when using the `steps` subcommand |
| `location_id` | — | Skip geocoding and use an existing Polarsteps location ID |
| `country_code` | — | Override the 2-letter country code (e.g. `"ES"`) |

---

## Global Flags

These flags work with every subcommand:

| Flag | Description |
|------|-------------|
| `-v, --verbose` | Print all HTTP request and response payloads |
| `-d, --dump FILE` | Write all traffic to a `.har` file for debugging in browser DevTools |

**Example:**

```bash
# Debug a failing import by inspecting the full HTTP traffic
python3 import.py -v -d debug.har import out/import_data.json
```


