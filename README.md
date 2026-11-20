# Clock Image Generator

Generate a prayer-times clock image with local prayer calculations, a Hijri date, daylight and nighttime summaries, and a daily Muslim history fact. The project renders both an editable SVG and a PNG from the bundled `exp.svg` design.

## Requirements

- Python 3.10 or later
- Google Chrome or Microsoft Edge for PNG export
- The fonts used by the SVG design, installed on the computer that renders it

Python dependencies are listed in `requirements.txt`.

## Setup

From the project directory, create and activate a virtual environment, then install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows, activate the environment with `.venv\Scripts\activate`.

## Generate a clock

The generator is a Python module. Run these commands from the project directory:

```bash
python - <<'PY'
from clock_image_generator import get_chart_info, generate_image

chart_info = get_chart_info()  # Uses today's date in the configured timezone.
files = generate_image(chart_info)
print(files)
PY
```

The default location and calculation settings are Chicago, Illinois; America/Chicago; North America (ISNA); and standard (Shafi) Asr. Edit the constants near the top of `clock_image_generator.py` to change these defaults, or pass options directly to `get_chart_info`:

```python
chart_info = get_chart_info(
    latitude=40.7128,
    longitude=-74.0060,
    city="New York",
    timezone="America/New_York",
    date="2026-11-20",  # Also accepts DD-MM-YYYY, e.g. "20-11-2026".
    method="NORTH_AMERICA",
    madhab="hanafi",  # Or "standard" (Shafi).
)
files = generate_image(chart_info, output_dir="output")
```

The returned `files` dictionary contains absolute paths under `svg` and `png`. By default, both files are written to `output/clock.svg` and `output/clock.png`. The calculation and history lookup work offline after the Python dependencies are installed. PNG export needs Chrome or Edge available on the machine.

## Jupyter notebook

Open `generate_clock.ipynb` in Jupyter and run its cells to calculate the day's clock data and display the generated image. The notebook installs the listed dependencies into its active kernel if they are missing.

## Project files

- `clock_image_generator.py` — prepares prayer, Hijri, and history data and exposes `get_chart_info` and `generate_image`.
- `clock_image_renderer.py` — updates the SVG template and exports SVG and PNG files.
- `exp.svg` — source clock design.
- `muslim_history.py` and `muslim_history_daily.csv` — offline daily history facts.
- `generate_clock.ipynb` — interactive notebook workflow.
- `output/` — example/generated clock images.

## Notes

Prayer times depend on coordinates, timezone, calculation method, and Asr madhab. Confirm the settings are appropriate for your location and community. The Hijri date uses the Umm al-Qura calendar. History facts are assigned to Gregorian month/day entries in the bundled CSV; they are informational and link to Wikipedia for further reading.
