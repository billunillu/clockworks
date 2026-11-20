"""Two-function interface for preparing clock data and rendering the clock image."""

from __future__ import annotations

from datetime import date as Date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_SVG = BASE_DIR / "exp.svg"

# Edit these values for the location and prayer-time settings used by default.
CITY_NAME = "CHICAGO"
LATITUDE = 41.8781
LONGITUDE = -87.6298
TIMEZONE = "America/Chicago"
CALCULATION_METHOD = "NORTH_AMERICA"  # ISNA
MADHAB = "standard"  # Use "hanafi" for Hanafi Asr.

try:
    from .muslim_history import get_muslim_history
except ImportError:  # Also support importing this file directly from its folder.
    from muslim_history import get_muslim_history

try:
    from .clock_image_renderer import render_clock
except ImportError:  # Also support importing this file directly from its folder.
    from clock_image_renderer import render_clock


def get_chart_info(
    latitude: float | None = None,
    longitude: float | None = None,
    date: str | Date | datetime | None = None,
    *,
    method: int | str | None = None,
    madhab: str | None = None,
    timezone: str | None = None,
    city: str | None = None,
) -> dict[str, Any]:
    """Calculate and format local prayer, Hijri, and history data for the clock.

    Date strings can be ISO (YYYY-MM-DD) or DD-MM-YYYY.
    Coordinates, city, timezone, calculation method, and madhab default to
    the editable constants at the top of this file. No API calls are made.
    """
    latitude = LATITUDE if latitude is None else latitude
    longitude = LONGITUDE if longitude is None else longitude
    timezone = TIMEZONE if timezone is None else timezone
    city = CITY_NAME if city is None else city
    method = CALCULATION_METHOD if method is None else method
    madhab = MADHAB if madhab is None else madhab
    madhab = madhab.strip().lower().replace("-", "_").replace(" ", "_")
    if madhab in ("shafi", "shafii", "standard_asr"):
        madhab = "standard"
    if madhab not in ("standard", "hanafi"):
        raise ValueError("madhab must be 'standard' (Shafi) or 'hanafi'")
    ZoneInfo(timezone)
    if date is None:
        selected_date = datetime.now(ZoneInfo(timezone)).date()
    elif isinstance(date, datetime):
        selected_date = date.date()
    elif isinstance(date, Date):
        selected_date = date
    elif isinstance(date, str):
        try:
            selected_date = Date.fromisoformat(date)
        except ValueError:
            selected_date = datetime.strptime(date, "%d-%m-%Y").date()
    else:
        raise ValueError("date must be a date, datetime, ISO string, or DD-MM-YYYY string")

    try:
        from hijridate import Gregorian
        from adhanpy.PrayerTimes import PrayerTimes
        from adhanpy.calculation import CalculationMethod, CalculationParameters
        from adhanpy.calculation.Madhab import Madhab as AdhanMadhab
    except ImportError as exc:
        raise RuntimeError(
            "Offline calculation packages are not installed. Run "
            "python -m pip install -r requirements.txt before going offline."
        ) from exc

    method_names = {
        "ISNA": "NORTH_AMERICA",
        "ISLAMIC_SOCIETY_OF_NORTH_AMERICA": "NORTH_AMERICA",
        "NORTH_AMERICA": "NORTH_AMERICA",
        "MWL": "MUSLIM_WORLD_LEAGUE",
        "MUSLIM_WORLD_LEAGUE": "MUSLIM_WORLD_LEAGUE",
        "KARACHI": "KARACHI",
        "UMM_AL_QURA": "UMM_AL_QURA",
        "EGYPTIAN": "EGYPTIAN",
        "DUBAI": "DUBAI",
        "MOON_SIGHTING_COMMITTEE": "MOON_SIGHTING_COMMITTEE",
        "SINGAPORE": "SINGAPORE",
    }
    if isinstance(method, int):
        method = {
            1: "KARACHI",
            2: "NORTH_AMERICA",
            3: "MUSLIM_WORLD_LEAGUE",
            4: "UMM_AL_QURA",
            5: "EGYPTIAN",
            11: "SINGAPORE",
        }.get(method)
        if method is None:
            raise ValueError("Unsupported numeric calculation method for offline mode")
    method_key = str(method).strip().upper().replace("-", "_").replace(" ", "_")
    method_name = method_names.get(method_key)
    if method_name is None or not hasattr(CalculationMethod, method_name):
        raise ValueError(f"Unsupported offline calculation method: {method!r}")
    calculation_method = getattr(CalculationMethod, method_name)
    calculation_parameters = CalculationParameters(method=calculation_method)
    calculation_parameters.madhab = (
        AdhanMadhab.HANAFI if madhab == "hanafi" else AdhanMadhab.SHAFI
    )
    computed = PrayerTimes(
        (float(latitude), float(longitude)),
        datetime.combine(selected_date, datetime.min.time()),
        calculation_parameters=calculation_parameters,
        time_zone=ZoneInfo(timezone),
    )
    times = {
        "Fajr": computed.fajr.strftime("%H:%M"),
        "Sunrise": computed.sunrise.strftime("%H:%M"),
        "Dhuhr": computed.dhuhr.strftime("%H:%M"),
        "Asr": computed.asr.strftime("%H:%M"),
        "Maghrib": computed.maghrib.strftime("%H:%M"),
        "Isha": computed.isha.strftime("%H:%M"),
    }

    hijri_date = Gregorian(
        selected_date.year, selected_date.month, selected_date.day
    ).to_hijri()
    hijri_info = {
        "date": hijri_date.dmyformat("-"),
        "day": hijri_date.day,
        "month": hijri_date.month,
        "month_name": hijri_date.month_name("en"),
        "year": hijri_date.year,
        "method": "Umm al-Qura",
        "authority": "Umm al-Qura",
        "gregorian_date": selected_date.isoformat(),
        "month_length": hijri_date.month_length(),
    }
    history_info = get_muslim_history(selected_date.month, selected_date.day)

    hijri_info = dict(hijri_info)
    hijri_day = int(hijri_info["day"])
    is_month_edge_day = (
        hijri_day <= 2 or hijri_day >= hijri_info["month_length"] - 1
    )
    hijri_info["is_marked_month_edge_day"] = is_month_edge_day

    if history_info is None:
        raise ValueError("No history fact is available for this date.")
    fun_fact = history_info["text"]
    qr_url = history_info["wikipedia_url"]

    def twelve_hour(value: str) -> tuple[str, str]:
        parsed = datetime.strptime(value, "%H:%M")
        return parsed.strftime("%I:%M"), parsed.strftime("%p")

    fajr, fajr_ampm = twelve_hour(times["Fajr"])
    dhuhr, dhuhr_ampm = twelve_hour(times["Dhuhr"])
    asr, asr_ampm = twelve_hour(times["Asr"])
    maghrib, maghrib_ampm = twelve_hour(times["Maghrib"])
    isha, isha_ampm = twelve_hour(times["Isha"])
    sunrise, sunrise_ampm = twelve_hour(times["Sunrise"])

    event_labels = ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha")
    event_minutes = [
        datetime.strptime(times[label], "%H:%M").hour * 60
        + datetime.strptime(times[label], "%H:%M").minute
        for label in event_labels
    ]
    pie_data = [
        {
            "label": label,
            "minutes": (event_minutes[(index + 1) % len(event_minutes)] - event_minutes[index]) % 1440,
        }
        for index, label in enumerate(event_labels)
    ]
    daylight_minutes = sum(
        row["minutes"] for row in pie_data
        if row["label"] in ("Sunrise", "Dhuhr", "Asr")
    )
    daylight_hours = daylight_minutes / 60
    night_hours = 24 - daylight_hours
    resolved_city = city.strip() if city and city.strip() else f"{latitude:.2f}, {longitude:.2f}"

    svg_fields = {
        "TIMES_FAJR": fajr,
        "TIMES_FAJR_AM_PM": fajr_ampm,
        "TIMES_THUR": dhuhr,
        "TIMES_THUR_AM_PM": dhuhr_ampm,
        "TIMES_ASR": asr,
        "TIMES_ASR_AM_PM": asr_ampm,
        "TIMES_MAGH": maghrib,
        "TIMES_MAGH_AM_PM": maghrib_ampm,
        "TIMES_ISHA": isha,
        "TIMES_ISHA_AM_PM": isha_ampm,
        "Islamic_Date_ISLAM_YR": str(hijri_info["year"]),
        "Islamic_Date_ISLAM_MONTH": (
            f"{hijri_info['day']} {hijri_info['month_name']}"
            f"{'*' if is_month_edge_day else ''}"
        ),
        "GeorgianDate_DAY": selected_date.strftime("%d"),
        "GeorgianDate_MONTH": selected_date.strftime("%B").upper(),
        "City_CITY_NAME": resolved_city.upper(),
        "BOTTOM_COUNTER_NIGHTIME_HRS": f"{night_hours:.1f} hrs",
        "BOTTOM_COUNTER_DAYLIGHT_HRS": f"{daylight_hours:.1f} hrs",
    }

    return {
        "data": svg_fields,
        "chart_sunrise_time": f"{sunrise} {sunrise_ampm}",
        "fun_fact": fun_fact,
        "qr_url": qr_url,
        "night_percent": round(night_hours / 24 * 100, 2),
        "current_date": selected_date.isoformat(),
        "season_timezone": timezone,
        "madhab": madhab,
        "calculation_method": method_name,
        "times": times,
        "prayer_info": {
            "times": times,
            "pie_data": pie_data,
            "daylight_percent": round(daylight_minutes / 1440 * 100, 2),
            "night_percent": round(night_hours / 24 * 100, 2),
        },
        "hijri_info": hijri_info,
        "history_info": history_info,
    }


def generate_image(
    chart_info: dict[str, Any],
    output_dir: str | Path | None = None,
) -> dict[str, str]:
    """Render chart_info into a clock SVG and PNG; return both file paths."""
    required = {
        "data", "chart_sunrise_time", "fun_fact", "qr_url", "night_percent",
        "current_date", "season_timezone",
    }
    missing = required - chart_info.keys()
    if missing:
        raise ValueError(f"chart_info is missing fields: {', '.join(sorted(missing))}")
    if not TEMPLATE_SVG.is_file():
        raise FileNotFoundError("The bundled exp.svg design is missing.")

    target_dir = Path(output_dir) if output_dir is not None else BASE_DIR / "output"
    target_dir.mkdir(parents=True, exist_ok=True)
    svg_path = target_dir / "clock.svg"
    png_path = target_dir / "clock.png"

    try:
        import matplotlib  # noqa: F401
        import qrcode  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Image dependencies are not installed. Run "
            "python -m pip install -r requirements.txt while online."
        ) from exc

    render_clock(chart_info, TEMPLATE_SVG, svg_path, png_path)

    if not svg_path.is_file() or not png_path.is_file():
        raise RuntimeError("Rendering finished without producing both image files.")
    return {"svg": str(svg_path.resolve()), "png": str(png_path.resolve())}
