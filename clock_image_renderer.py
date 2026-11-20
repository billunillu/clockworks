"""Render prayer-time clock data into SVG and PNG files."""

from __future__ import annotations

import base64
import io
import math
import re
import struct
import shutil
import subprocess
import tempfile
from datetime import date, date as Date, datetime, timedelta, timezone
from pathlib import Path
from xml.dom import minidom
from zoneinfo import ZoneInfo

NIGHT_FULL_WIDTH_INCHES = 6.0527
SVG_UNITS_PER_INCH = 72.0
SEASON_MIN_WIDTH_INCHES = 2.4085
SEASON_MAX_WIDTH_INCHES = 5.5

import struct
SVG_NS = "http://www.w3.org/2000/svg"

def elements(node):
    return [c for c in node.childNodes if c.nodeType == c.ELEMENT_NODE]

def decode_name(value):
    # Illustrator escapes XML ID punctuation, e.g. _x5F_ => underscore.
    return re.sub(r"_x([0-9A-Fa-f]{2,6})_", lambda m: chr(int(m.group(1), 16)), value)

def label(node):
    return decode_name(node.getAttribute("data-name") or node.getAttribute("id"))

def locked(node):
    while node.nodeType == node.ELEMENT_NODE:
        if any("locked" in decode_name(node.getAttribute(a)).lower() for a in ("id", "data-name", "name", "label")):
            return True
        node = node.parentNode
    return False

def require_editable(node):
    if locked(node):
        raise ValueError(f"Protected element: {label(node)}")

def all_elements(document):
    return list(document.getElementsByTagName("*"))

def unique_named(document, names, tag=None):
    found = [e for e in (elements(document) if getattr(document, "localName", None) == "g" else all_elements(document))
             if any(decode_name(e.getAttribute(a)) in names for a in ("id", "data-name"))
             and (tag is None or e.localName == tag)]
    if len(found) != 1:
        raise ValueError(f"Expected one {names}; found {len(found)}")
    require_editable(found[0])
    return found[0]

def text_content(node):
    return "".join(c.data if c.nodeType in (c.TEXT_NODE, c.CDATA_SECTION_NODE)
                   else text_content(c) for c in node.childNodes)

def replace_text(node, value):
    if isinstance(node, list):
        for text_node in node:
            replace_text(text_node, value)
        return
    require_editable(node)
    spans = list(node.getElementsByTagNameNS("*", "tspan"))
    run = spans[0] if len(spans) == 1 else node
    texts = [c for c in run.childNodes if c.nodeType in (c.TEXT_NODE, c.CDATA_SECTION_NODE)]
    if len(spans) > 1 or len(texts) != 1 or elements(run):
        raise ValueError("Expected one text run; refusing to discard formatting.")
    value = str(value)
    if "ISLAM_MONTH" in identity_chain(node):
        # Normalize this field on every run, including user-entered double spaces.
        value = " ".join(value.split())
    texts[0].data = value

def identity_chain(node):
    names = []
    while node.nodeType == node.ELEMENT_NODE:
        if label(node): names.append(label(node))
        node = node.parentNode
    return names

def text_targets(document):
    # Illustrator may name either the text itself or a wrapper group. Some
    # exports split fill and stroke into two coincident text objects.
    result = {}
    def resolve(scope, name):
        candidates = [e for e in all_elements(scope)
                      if label(e) == name and not locked(e)]
        if len(candidates) != 1:
            raise ValueError(f"Expected one editable {name}, found {len(candidates)}")
        obj = candidates[0]
        texts = [obj] if obj.localName == "text" else list(obj.getElementsByTagNameNS("*", "text"))
        texts = [t for t in texts if not locked(t)]
        if not texts: raise ValueError(f"No text in {name}")
        if len({text_content(t) for t in texts}) != 1:
            raise ValueError(f"{name} contains distinct text fields; name them separately.")
        return texts[0] if len(texts) == 1 else texts
    for main_name, fields in {
        "TIMES": ["FAJR", "FAJR_AM/PM", "THUR", "THUR_AM/PM", "ASR", "ASR_AM/PM", "MAGH", "MAGH_AM/PM", "ISHA", "ISHA_AM/PM"],
        "Islamic_Date": ["ISLAM_YR", "ISLAM_MONTH"],
        "GeorgianDate": ["DAY", "MONTH"],
        "City": ["CITY_NAME"],
    }.items():
        scope = unique_named(document, {main_name}, "g")
        for field in fields:
            # Illustrator appends 1 to duplicate IDs after the fixed BASE icons.
            actual = field
            if field in ("ASR", "ISHA") and not any(label(e)==field for e in all_elements(scope)):
                actual += "1"
            result[main_name + "_" + re.sub(r"[^\w]+", "_", field)] = resolve(scope, actual)
    bottom = unique_named(document, {"BOTTOM_COUNTER"}, "g")
    season = unique_named(bottom, {"SEASON_COUNTER"}, "g")
    for prefix,scope,suffix in [("BOTTOM_COUNTER",bottom,""),("SEASON_COUNTER",season,"1")]:
        for field in ("DAYLIGHT_HRS", "NIGHTIME_HRS"):
            name = field + suffix
            if not any(label(e)==name for e in all_elements(scope)):
                name = field
            result[prefix+"_"+field] = resolve(scope,name)
    labels = [t for t in bottom.getElementsByTagNameNS("*", "text")
              if text_content(t).strip() == "SEASON" and not locked(t)]
    if len(labels) != 1: raise ValueError("Expected one unlocked season-name text.")
    result["SEASON_COUNTER_NAME"] = labels[0]
    return result

def fun_fact_node(document):
    explicit = [t for t in document.getElementsByTagNameNS("*", "text")
                if "FUN_FACT_TEXT" in identity_chain(t) or "FACT_TEXT" in identity_chain(t)]
    candidates = explicit or [t for t in document.getElementsByTagNameNS("*", "text")
                              if text_content(t).strip().startswith("FUN FACT.")]
    if len(candidates) != 1: raise ValueError("Name the fun-fact body FUN_FACT_TEXT before exporting.")
    require_editable(candidates[0])
    return candidates[0]

def update_fun_fact(document, fact, box=None):
    if not isinstance(fact,str) or not 18 <= len(fact.split()) <= 22:
        raise ValueError("Fun fact must contain 18–22 words (separated by whitespace).")
    node=fun_fact_node(document)
    spans=list(node.getElementsByTagNameNS("*","tspan"))
    if len(spans)!=1: raise ValueError("Expected a single source text run for the fun fact.")
    span=spans[0]
    transform=node.getAttribute("transform")
    match=re.fullmatch(r"translate\(\s*([-\d.]+)[ ,]+([-\d.]+)\s*\)",transform)
    if not match: raise ValueError("Fun fact needs a translation transform for box layout.")
    tx,ty=map(float,match.groups())
    original_size=float(inherited_svg_attribute(span,"font-size"))
    face=inherited_svg_attribute(span,"font-family").split(",")[0].strip().strip("\"'")
    font=exact_font_paths({face})[face]
    if box is None:
        # The current export has no named fact-box rectangle. Use the fact's
        # existing left/baseline, QR lower edge, and matching right artboard inset.
        qr=unique_named(document,{"QR_CODE_PLACEHOLDER","QR CODE_PLACEHOLDER"},"rect")
        viewbox=list(map(float,re.split(r"[ ,]+",document.documentElement.getAttribute("viewBox"))))
        right=viewbox[0]+viewbox[2]-float(qr.getAttribute("x"))
        bottom=float(qr.getAttribute("y"))+float(qr.getAttribute("height"))
        box=(tx,ty-original_size,right-tx,bottom-(ty-original_size))
    bx,by,bw,bh=map(float,box)
    if bw<=0 or bh<=0: raise ValueError("Fun-fact box must have positive dimensions.")
    words=fact.split()
    def wrap(size):
        lines=[];current=""
        for word in words:
            trial=(current+" "+word).strip()
            if original_text_width(trial,font,size)<=bw:
                current=trial
            else:
                if not current or original_text_width(word,font,size)>bw: return None
                lines.append(current);current=word
        if current:lines.append(current)
        return lines
    # Keep source font size whenever it fits. Shrink only if the full fact
    # would otherwise exceed the available height or contains an oversized word.
    size=original_size
    while size>=10:
        lines=wrap(size)
        if lines and size*1.3+(len(lines)-1)*size*1.2<=bh:break
        size-=0.5
    else:raise ValueError("Fun fact cannot fit its box at a readable size.")
    for child in list(node.childNodes):node.removeChild(child)
    node.setAttribute("font-size",format(size,".12g"))
    node.setAttribute("text-anchor","start")
    node.setAttribute("id","FUN_FACT_TEXT")
    baseline=by-ty+size
    for i,line in enumerate(lines):
        run=span.cloneNode(deep=False)
        run.setAttribute("x",format(bx-tx,".12g"))
        run.setAttribute("y",format(baseline+i*size*1.2,".12g"))
        run.setAttribute("text-anchor","start")
        run.setAttribute("font-size",format(size,".12g"))
        # Trailing whitespace keeps the complete text recoverable across runs.
        run.appendChild(document.createTextNode(line+(" " if i<len(lines)-1 else "")))
        node.appendChild(run)
        assert original_text_width(line,font,size)<=bw+1e-6
    assert baseline+ty+(len(lines)-1)*size*1.2+size*.3<=by+bh+1e-6
    defs=document.createElementNS(SVG_NS,"defs")
    clip=document.createElementNS(SVG_NS,"clipPath");clip.setAttribute("id","fun_fact_text_clip")
    clip.setAttribute("clipPathUnits","userSpaceOnUse")
    rect=document.createElementNS(SVG_NS,"rect")
    for key,value in {"x":bx-tx,"y":by-ty,"width":bw,"height":bh}.items():rect.setAttribute(key,format(value,".12g"))
    clip.appendChild(rect);defs.appendChild(clip);document.documentElement.insertBefore(defs,document.documentElement.firstChild)
    node.setAttribute("clip-path","url(#fun_fact_text_clip)")
    assert " ".join(text_content(node).split())==" ".join(words)
    return {"lines":len(lines),"font_size":size,"box":(bx,by,bw,bh)}


def replace_circle_placeholder_with_chart(document, times):
    """Render one prayer wheel from 24-hour times and replace CIRCLE_PLACEHOLDER."""
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Wedge
    from matplotlib import font_manager as fm
    from matplotlib.font_manager import FontProperties

    placeholder = unique_named(document, {"CIRCLE_PLACEHOLDER"}, "circle")
    required = {"fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha", "noon"}
    missing = required - set(times)
    if missing:
        raise ValueError("Missing chart times: " + ", ".join(sorted(missing)))

    def minutes(value):
        parsed = datetime.strptime(value, "%H:%M")
        return parsed.hour * 60 + parsed.minute

    fajr = minutes(times["fajr"])
    sunrise = minutes(times["sunrise"])
    dhuhr = minutes(times["dhuhr"])
    asr = minutes(times["asr"])
    maghrib = minutes(times["maghrib"])
    isha = minutes(times["isha"])
    noon = minutes(times["noon"])

    start_angle = 90.0
    r_slice, r_outer = 1.0, 1.08
    r_time_inner, r_time_outer = 1.1, 1.2
    r_time_label = (r_time_inner + r_time_outer) / 2  # Centered, nudged outward from the prior position.

    def angle_for_time(value):
        # Anchor the wheel at noon rather than Fajr.
        return start_angle - ((value - noon) % 1440) / 1440.0 * 360.0

    def point(angle, radius):
        rad = math.radians(angle)
        return radius * math.cos(rad), radius * math.sin(rad)

    font_path = Path.home() / "Library" / "Fonts" / "Eurostile Extended #2 Regular.otf"
    if font_path.is_file():
        label_font = FontProperties(fname=str(font_path))
    else:
        label_font = FontProperties(family="Eurostile Extended #2")

    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_axes([0, 0, 1, 1])  # Fill the image canvas so the chart fills the placeholder.
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    # White halo separates the chart from the dotted artwork behind it.
    ax.add_patch(Circle((0, 0), r_time_outer, fill=False, edgecolor="white", lw=20.0, zorder=-1))
    ax.add_patch(Circle((0, 0), r_time_outer, facecolor="white", edgecolor="black", lw=1.0, zorder=0))
    ax.add_patch(Circle((0, 0), r_slice, fill=False, edgecolor="black", lw=1.0))
    ax.add_patch(Circle((0, 0), r_outer, fill=False, edgecolor="black", lw=1.0))
    ax.add_patch(Circle((0, 0), r_time_inner, fill=False, edgecolor="black", lw=0.4))
    ax.add_patch(Circle((0, 0), r_time_outer, fill=False, edgecolor="black", lw=0.6))

    for hour in range(24):
        if hour in (0, 12):
            continue
        angle = angle_for_time(hour * 60)
        x1, y1 = point(angle, r_time_inner + 0.01)
        x2, y2 = point(angle, r_time_outer - 0.01)
        ax.plot([x1, x2], [y1, y2], color="black", lw=0.2)

    def add_arc_label(text, angle, radius, fontsize=8, rotation=None):
        x, y = point(angle, radius)
        if rotation is None:
            rotation = angle - 90
        ax.text(x, y, text, ha="center", va="center", rotation=rotation,
                rotation_mode="anchor", fontsize=fontsize, fontproperties=label_font)

    slices = [
        ("FAJR", fajr, sunrise, "#d9d9d9", None),
        ("", sunrise, dhuhr, "white", None),
        ("DUHR", dhuhr, asr, "white", "..."),
        ("ASR", asr, maghrib, "white", "++"),
        ("MAGHRIB", maghrib, isha, "white", ".."),
        ("ISHA", isha, fajr, "white", "oo"),
    ]
    for segment_label, start, end, facecolor, hatch in slices:
        a0, a1 = angle_for_time(start), angle_for_time(end)
        span = (a0 - a1) % 360
        ax.add_patch(Wedge((0, 0), r_slice, theta1=a1, theta2=a0,
                           facecolor=facecolor, edgecolor="black", linewidth=1.5,
                           hatch=hatch))
        if segment_label:
            label_angle = a0 - span / 2
            if segment_label == "ISHA":
                label_angle += 2.0  # Nudge away from MIDNIGHT along the arc.
            rotation = label_angle - 90
            if segment_label == "ISHA":
                rotation += 180
            label_radius = 1.04 if segment_label == "ISHA" else 1.03
            add_arc_label(segment_label, label_angle, label_radius,
                          fontsize=8, rotation=rotation)

    # Noon marker: a straight dotted line from the center to the chart edge.
    noon_angle = math.radians(angle_for_time(noon))
    samples = 240
    along = [r_slice * i / samples for i in range(samples + 1)]
    offsets = [0.0 for _ in range(samples + 1)]
    noon_x = [d * math.cos(noon_angle) - off * math.sin(noon_angle)
              for d, off in zip(along, offsets)]
    noon_y = [d * math.sin(noon_angle) + off * math.cos(noon_angle)
              for d, off in zip(along, offsets)]
    ax.plot(noon_x, noon_y, color="black", lw=1.2, linestyle=(0, (1, 2)),
            dash_capstyle="round", zorder=4)

    sunrise_angle = angle_for_time(sunrise)
    dhuhr_angle = angle_for_time(dhuhr)
    sunrise_span = (sunrise_angle - dhuhr_angle) % 360
    sunrise_label_angle = sunrise_angle - sunrise_span * 0.026
    sx, sy = point(sunrise_label_angle, 0.84)
    sunrise_rotation = sunrise_label_angle
    if sunrise_rotation < -90:
        sunrise_rotation += 180
    if sunrise_rotation > 90:
        sunrise_rotation -= 180
    ax.text(sx, sy, "SUNRISE", ha="center", va="center",
            rotation=sunrise_rotation, rotation_mode="anchor", fontsize=7,
            fontproperties=label_font)

    for value, word in ((noon, "NOON"), (0, "MIDNIGHT")):
        angle = angle_for_time(value)
        rotation = angle - 90 + (180 if word == "MIDNIGHT" else 0)
        add_arc_label(word, angle, r_time_label, fontsize=8, rotation=rotation)

    ax.add_patch(Circle((0, 0), 0.06, facecolor="white", edgecolor="black", lw=1.2, zorder=5))
    ax.set_aspect("equal")
    ax.set_xlim(-1.26, 1.26)
    ax.set_ylim(-1.26, 1.26)
    ax.axis("off")

    png = io.BytesIO()
    fig.savefig(png, format="png", dpi=300, transparent=True, pad_inches=0)
    plt.close(fig)

    cx, cy, radius = (float(placeholder.getAttribute(key)) for key in ("cx", "cy", "r"))
    image_diameter = 2 * radius * (1.26 / r_time_outer)
    image = document.createElementNS(SVG_NS, "image")
    image.setAttribute("id", placeholder.getAttribute("id"))
    image.setAttribute("x", format(cx - image_diameter / 2, ".12g"))
    image.setAttribute("y", format(cy - image_diameter / 2, ".12g"))
    image.setAttribute("width", format(image_diameter, ".12g"))
    image.setAttribute("height", format(image_diameter, ".12g"))
    image.setAttribute("preserveAspectRatio", "xMidYMid meet")
    image.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href",
                         "data:image/png;base64," + base64.b64encode(png.getvalue()).decode("ascii"))
    placeholder.parentNode.replaceChild(image, placeholder)

    # Keep the chart above the top artwork and below the prayer-list artwork.
    base_top = [e for e in all_elements(document) if label(e) == "BASE_TOP"]
    base_salah = [e for e in all_elements(document) if label(e) == "BASE_SALAH"]
    if len(base_top) != 1 or len(base_salah) != 1:
        raise ValueError("Expected one BASE_TOP and one BASE_SALAH layer.")
    if base_top[0].parentNode is not base_salah[0].parentNode:
        raise ValueError("BASE_TOP and BASE_SALAH must share a parent layer.")
    parent = base_top[0].parentNode
    parent.insertBefore(image, base_top[0].nextSibling)
    children = elements(parent)
    if not (children.index(base_top[0]) < children.index(image) < children.index(base_salah[0])):
        raise ValueError("Could not place the circle above BASE_TOP and under BASE_SALAH.")
    return image


def replace_square_with_qr(document, url):
    import qrcode
    import qrcode.image.svg
    square = unique_named(document, {"QR_CODE_PLACEHOLDER", "QR CODE_PLACEHOLDER"}, "rect")
    if not url or not isinstance(url, str):
        raise ValueError("Provide a QR destination string.")
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=10)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buf)
    # White background preserves contrast and the four-module quiet zone.
    qr_doc = minidom.parseString(buf.getvalue())
    root = qr_doc.documentElement
    background = qr_doc.createElementNS(SVG_NS, "rect")
    for k,v in {"width":"100%", "height":"100%", "fill":"white"}.items(): background.setAttribute(k,v)
    root.insertBefore(background,root.firstChild)
    image = document.createElementNS(SVG_NS, "image")
    for k in ("x", "y", "width", "height", "transform", "opacity"):
        if square.hasAttribute(k): image.setAttribute(k, square.getAttribute(k))
    image.setAttribute("id", square.getAttribute("id"))
    image.setAttribute("preserveAspectRatio", "xMidYMid meet")
    uri = "data:image/svg+xml;base64," + base64.b64encode(qr_doc.toxml(encoding="utf-8")).decode()
    image.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", uri)
    square.parentNode.replaceChild(image, square)
    return qr.modules_count

def resize_night(document, percentage, full_width_inches=6.0527, units_per_inch=72.0):
    percentage = float(percentage)
    if not math.isfinite(percentage) or not 0 <= percentage <= 100:
        raise ValueError("Night percentage must be between 0 and 100.")
    if full_width_inches <= 0 or units_per_inch <= 0:
        raise ValueError("Width and unit scale must be positive.")
    group = unique_named(document, {"BOTTOM_COUNTER"}, "g")
    shape = unique_named(group, {"NIGHT_%_EDIT_SIZE", "NIGHT___EDIT_SIZE", "EDIT_SIZE"}, "rect")
    right = float(shape.getAttribute("x")) + float(shape.getAttribute("width"))
    width = full_width_inches * units_per_inch * percentage / 100
    shape.setAttribute("width", format(width, ".12g"))
    shape.setAttribute("x", format(right - width, ".12g"))
    assert math.isclose(float(shape.getAttribute("x")) + float(shape.getAttribute("width")), right, abs_tol=1e-8)
    return width / units_per_inch, right

# Astronomical season boundary estimates (Meeus JDE polynomial, 2000–2100).
# Boundaries are converted to local calendar dates; the start date is day 1.
def season_boundary(year, quarter, tz):
    coefficients = (
        (2451623.80984,365242.37404,.05169,-.00411,-.00057),
        (2451716.56767,365241.62603,.00325,.00888,-.00030),
        (2451810.21715,365242.01767,-.11575,.00337,.00078),
        (2451900.05952,365242.74049,-.06223,-.00823,.00032),
    )
    y = (year - 2000) / 1000
    jde = sum(c * y**i for i,c in enumerate(coefficients[quarter]))
    # Approximate TT to UTC; sufficient for a calendar-day counter except
    # boundaries extremely close to local midnight.
    instant = datetime(2000,1,1,12,tzinfo=timezone.utc) + timedelta(days=jde-2451545, seconds=-69)
    return instant.astimezone(ZoneInfo(tz)).date()

def season_info(current_date, tz="America/Chicago"):
    if isinstance(current_date, datetime):
        current_date = current_date.astimezone(ZoneInfo(tz)).date() if current_date.tzinfo else current_date.date()
    if isinstance(current_date, str):
        current_date = date.fromisoformat(current_date)
    if not isinstance(current_date, date) or not 2001 <= current_date.year <= 2099:
        raise ValueError("Use a date or YYYY-MM-DD from 2001 through 2099.")
    names = ("SPRING", "SUMMER", "AUTUMN", "WINTER")
    boundaries = [(season_boundary(y,q,tz), names[q])
                  for y in range(current_date.year-1,current_date.year+2) for q in range(4)]
    boundaries.sort()
    for (start,name),(end,_) in zip(boundaries,boundaries[1:]):
        if start <= current_date < end:
            return {"name": name, "day": (current_date-start).days+1,
                    "total": (end-start).days, "start": start, "end": end}
    raise ValueError("Could not locate season.")

def resize_season(document, day, total, min_inches=2.4085, max_inches=5.5):
    if not (1 <= day <= total) or total < 2 or not (0 < min_inches <= max_inches):
        raise ValueError("Invalid season days or width bounds.")
    group = unique_named(document, {"SEASON_COUNTER"}, "g")
    shape = unique_named(group, {"NIGHT_%_EDIT_SIZE", "NIGHT_%_EDIT_SIZE1", "NIGHT___EDIT_SIZE", "EDIT_SIZE"}, "rect")
    right = float(shape.getAttribute("x")) + float(shape.getAttribute("width"))
    inches = max_inches - (max_inches-min_inches) * (day-1)/(total-1)
    shape.setAttribute("width", format(inches*SVG_UNITS_PER_INCH, ".12g"))
    shape.setAttribute("x", format(right-inches*SVG_UNITS_PER_INCH, ".12g"))
    assert math.isclose(float(shape.getAttribute("x"))+float(shape.getAttribute("width")),right,abs_tol=1e-8)
    return inches

# Alignment direction rules only; positions and widths come from SOURCE each run.
# These cover Illustrator exports that omit text-anchor entirely.
ALIGNMENT_MODES = {
    "ISLAM_MONTH": "end", "MONTH": "end", "CITY_NAME": "end",
    "DAYLIGHT_HRS": "middle", "DAYLIGHT_HRS1": "middle",
    "NIGHTIME_HRS": "middle", "NIGHTIME_HRS1": "middle",
}

def font_postscript_names(path):
    # Read OpenType/TrueType name tables without an additional dependency.
    raw = Path(path).read_bytes()
    if raw[:4] not in (b"OTTO", b"\x00\x01\x00\x00", b"true"):
        return set()
    count = struct.unpack_from(">H", raw, 4)[0]
    for i in range(count):
        tag, _, offset, length = struct.unpack_from(">4sIII", raw, 12+16*i)
        if tag == b"name":
            break
    else:
        return set()
    count, storage = struct.unpack_from(">HH", raw, offset+2)
    names = set()
    for i in range(count):
        platform, encoding, language, name_id, size, start = struct.unpack_from(">6H",raw,offset+6+12*i)
        if name_id == 6:
            value = raw[offset+storage+start:offset+storage+start+size]
            names.add(value.decode("utf-16-be" if platform in (0,3) else "mac_roman"))
    return names


def inherited_svg_attribute(node, key):
    while node.nodeType == node.ELEMENT_NODE:
        style = dict(item.split(":",1) for item in node.getAttribute("style").split(";") if ":" in item)
        style = {k.strip():v.strip() for k,v in style.items()}
        if key in style: return style[key]
        if node.hasAttribute(key): return node.getAttribute(key)
        node = node.parentNode
    return None

def exact_font_paths(faces):
    roots = [Path.cwd()/"fonts",Path.home()/"Library/Fonts",Path("/Library/Fonts"),
             Path.home()/"Library/Application Support/Adobe/CoreSync/plugins/livetype"]
    result = {}
    for root in roots:
        if not root.exists(): continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in (".ttf", ".otf"): continue
            try:
                for face in font_postscript_names(path) & faces:
                    result.setdefault(face,path)
            except (OSError, ValueError, struct.error, UnicodeError):
                continue
    missing = faces - result.keys()
    if missing: raise RuntimeError("Cannot measure exact source fonts: " + ", ".join(sorted(missing)))
    return result

def original_text_width(text, font_path, size):
    # CoreText measures the exact font file, including kerning and trailing spaces.
    import ctypes as C
    import sys
    if sys.platform != "darwin":
        raise RuntimeError("Dynamic source alignment measurement currently requires macOS/CoreText.")
    cf=C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ct=C.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
    P=C.c_void_p; L=C.c_long; D=C.c_double
    def bind(lib,name,result,args):
        f=getattr(lib,name); f.restype=result; f.argtypes=args; return f
    string=bind(cf,"CFStringCreateWithCString",P,[P,C.c_char_p,C.c_uint32])
    url=bind(cf,"CFURLCreateFromFileSystemRepresentation",P,[P,C.c_char_p,L,C.c_bool])
    arr=bind(cf,"CFArrayGetValueAtIndex",P,[P,L])
    desc=bind(ct,"CTFontManagerCreateFontDescriptorsFromURL",P,[P])
    font=bind(ct,"CTFontCreateWithFontDescriptor",P,[P,D,P])
    dictionary=bind(cf,"CFDictionaryCreate",P,[P,C.POINTER(P),C.POINTER(P),L,P,P])
    attributed=bind(cf,"CFAttributedStringCreate",P,[P,P,P])
    line=bind(ct,"CTLineCreateWithAttributedString",P,[P])
    width=bind(ct,"CTLineGetTypographicBounds",D,[P,P,P,P])
    release=bind(cf,"CFRelease",None,[P])
    allocated=[]
    def keep(obj):
        if not obj: raise RuntimeError("CoreText could not measure source text.")
        allocated.append(obj); return obj
    try:
        path=str(font_path).encode()
        u=keep(url(None,path,len(path),False)); descriptors=keep(desc(u))
        f=keep(font(arr(descriptors,0),float(size),None))
        key=P.in_dll(ct,"kCTFontAttributeName")
        attrs=keep(dictionary(None,(P*1)(key),(P*1)(f),1,None,None))
        txt=keep(string(None,text.encode(),0x08000100))
        value=keep(attributed(None,txt,attrs)); text_line=keep(line(value))
        return width(text_line,None,None,None)
    finally:
        for obj in reversed(allocated): release(obj)

def restore_alignment(document):
    restored=[]; pending=[]
    for node in document.getElementsByTagNameNS("*","text"):
        names=identity_chain(node)
        if locked(node) or any(name.upper().startswith("BASE") for name in names): continue
        spans=list(node.getElementsByTagNameNS("*","tspan"))
        if len(spans)!=1: raise ValueError("Expected one positioned text run for source alignment.")
        span=spans[0]
        # Explicit source anchors (including left/start) take precedence over rules.
        explicit=inherited_svg_attribute(span,"text-anchor")
        if explicit is not None:
            if explicit not in ("start","middle","end"): raise ValueError(f"Unsupported source anchor: {explicit}")
            continue
        mode=next((ALIGNMENT_MODES[name] for name in names if name in ALIGNMENT_MODES),None)
        if mode is None and any(name.endswith("AM/PM") for name in names): mode="middle"
        if mode is None and text_content(node).strip()=="SEASON": mode="middle"
        if mode is None: continue
        family=inherited_svg_attribute(span,"font-family")
        if not family: raise ValueError("Missing source font-family.")
        face=family.split(",")[0].strip().strip("\"'")
        size=float(inherited_svg_attribute(span,"font-size"))
        original=text_content(node)
        if inherited_svg_attribute(span,"xml:space")!="preserve":
            original=" ".join(original.split())
        if not span.hasAttribute("x"): raise ValueError("Source tspan needs an x coordinate.")
        pending.append((node,span,mode,face,size,original))
    fonts=exact_font_paths({item[3] for item in pending})
    for node,span,mode,face,size,original in pending:
        advance=original_text_width(original,fonts[face],size)
        spacing=inherited_svg_attribute(span,"letter-spacing")
        if spacing and spacing!="normal": advance+=len(original)*float(spacing)
        # Calculate in the source's local coordinates; retain its transform/baseline.
        anchor_x=float(span.getAttribute("x"))+advance*(0.5 if mode=="middle" else 1.0)
        node.setAttribute("text-anchor",mode); span.setAttribute("text-anchor",mode)
        span.setAttribute("x",format(anchor_x,".12g"))
        restored.append((node,span,mode,anchor_x))
    return restored

def font_postscript_names(path):
    # Read OpenType/TrueType name tables without an additional dependency.
    raw = Path(path).read_bytes()
    if raw[:4] not in (b"OTTO", b"\x00\x01\x00\x00", b"true"):
        return set()
    count = struct.unpack_from(">H", raw, 4)[0]
    for i in range(count):
        tag, _, offset, length = struct.unpack_from(">4sIII", raw, 12+16*i)
        if tag == b"name":
            break
    else:
        return set()
    count, storage = struct.unpack_from(">HH", raw, offset+2)
    names = set()
    for i in range(count):
        platform, encoding, language, name_id, size, start = struct.unpack_from(">6H",raw,offset+6+12*i)
        if name_id == 6:
            value = raw[offset+storage+start:offset+storage+start+size]
            names.add(value.decode("utf-16-be" if platform in (0,3) else "mac_roman"))
    return names

def svg_with_embedded_fonts(svg_path):
    rendered = minidom.parse(str(svg_path))
    required = {}
    for node in rendered.getElementsByTagName("*"):
        family = node.getAttribute("font-family")
        if family:
            face = family.split(",")[0].strip().strip("\"'")
            required.setdefault(face, set()).add(node.getAttribute("font-weight") or "400")
    roots = [Path.cwd()/"fonts", Path.home()/"Library/Fonts", Path("/Library/Fonts"),
             Path.home()/"Library/Application Support/Adobe/CoreSync/plugins/livetype",
             Path("/usr/share/fonts"), Path.home()/".local/share/fonts"]
    found = {}
    for root in roots:
        if not root.exists(): continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in (".otf", ".ttf"): continue
            try:
                for face in font_postscript_names(path) & required.keys():
                    found.setdefault(face,path)
            except (OSError, ValueError, struct.error, UnicodeError):
                continue
    missing = required.keys() - found.keys()
    if missing:
        raise RuntimeError("Exact fonts missing; PNG export stopped to prevent substitution: "
                           + ", ".join(sorted(missing)) + ". Put the font files in TEST/fonts.")
    rules = []
    for face, weights in required.items():
        path = found[face]
        encoded = base64.b64encode(path.read_bytes()).decode()
        kind = "opentype" if path.suffix.lower() == ".otf" else "truetype"
        mime = "font/otf" if kind == "opentype" else "font/ttf"
        for weight in weights:
            rules.append(f"@font-face{{font-family:'{face}';src:url(data:{mime};base64,{encoded}) format('{kind}');font-weight:{weight};font-style:normal;font-display:block;}}")
    style = rendered.createElementNS("http://www.w3.org/2000/svg", "style")
    style.appendChild(rendered.createTextNode("\n".join(rules)))
    rendered.documentElement.insertBefore(style,rendered.documentElement.firstChild)
    print("Embedded exact faces: " + ", ".join(sorted(found)))
    return rendered.documentElement.toxml(), sorted(required)

def export_png(svg_path, png_path, scale=2):
    svg_path, png_path = Path(svg_path).resolve(), Path(png_path).resolve()
    root = minidom.parse(str(svg_path)).documentElement
    viewbox = [float(v) for v in re.split(r"[ ,]+", root.getAttribute("viewBox").strip())]
    if len(viewbox) != 4 or scale <= 0:
        raise ValueError("SVG needs a viewBox, and scale must be positive.")
    width, height = math.ceil(viewbox[2]), math.ceil(viewbox[3])
    browsers = [shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser", "msedge")]
    browsers += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    browser = next((b for b in browsers if b and Path(b).is_file()), None)
    if browser is None:
        raise RuntimeError("Install Google Chrome or Microsoft Edge to export PNG.")
    embedded_svg, font_faces = svg_with_embedded_fonts(svg_path)
    with tempfile.TemporaryDirectory(prefix="clock_png_") as temporary:
        temporary = Path(temporary)
        page = temporary / "render.html"
        # Inline SVG shares the document font loader. Wait for exact faces before paint.
        page.write_text(f'''<!doctype html><html><head><style>
html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;background:white}}
body>svg{{display:block;width:{width}px;height:{height}px;font-synthesis:none}}
</style></head><body>{embedded_svg}<script>
document.fonts.ready.then(() => {{ document.body.dataset.fontsReady = "true"; }});
</script></body></html>''', encoding="utf-8")
        result = subprocess.run([
            browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={temporary / 'profile'}",
            f"--window-size={width},{height}", f"--force-device-scale-factor={scale}",
            "--virtual-time-budget=3000", "--run-all-compositor-stages-before-draw",
            f"--screenshot={png_path}", page.as_uri(),
        ], capture_output=True, text=True, timeout=60)
        if result.returncode or not png_path.exists():
            raise RuntimeError("PNG export failed: " + result.stderr[-1200:])
    raw = png_path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("The exported file is not a valid PNG.")
    pixels = struct.unpack(">II", raw[16:24])
    print(f"Saved {png_path.name}: {pixels[0]} × {pixels[1]} pixels.")
    return png_path

def render_clock(chart_info: dict, source_svg: str | Path, output_svg: str | Path, output_png: str | Path) -> dict[str, str]:
    """Render calculated clock data using the base SVG and return output paths."""
    SOURCE = Path(source_svg).resolve()
    OUTPUT = Path(output_svg).resolve()
    PNG_OUTPUT = Path(output_png).resolve()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if PNG_OUTPUT.parent != OUTPUT.parent:
        PNG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    FUN_FACT = chart_info["fun_fact"]
    FUN_FACT_BOX = None
    QR_URL = chart_info["qr_url"]
    NIGHT_PERCENT = chart_info["night_percent"]
    CURRENT_DATE = Date.fromisoformat(chart_info["current_date"])
    SEASON_TIMEZONE = chart_info["season_timezone"]
    CHART_SUNRISE_TIME = chart_info["chart_sunrise_time"]
    data = dict(chart_info["data"])
    PNG_SCALE = 2

    if SOURCE.resolve() == OUTPUT.resolve():
        raise ValueError("Output must differ from the original.")
    document = minidom.parse(str(SOURCE))
    protected_before = [e.toxml() for e in all_elements(document) if locked(e)]
    alignment_checks = restore_alignment(document)
    targets = text_targets(document)
    season = season_info(CURRENT_DATE, SEASON_TIMEZONE)
    data.update({
        "SEASON_COUNTER_NAME": season["name"],
        "SEASON_COUNTER_DAYLIGHT_HRS": str(season["day"]),
        "SEASON_COUNTER_NIGHTIME_HRS": f"of {season['total']} Days",
    })
    fact_element = fun_fact_node(document)
    fact_elements = [fact_element] + list(fact_element.getElementsByTagName("*"))
    # Retain every other text element and tspan attribute, including anchors, transforms,
    # font weight, spacing and style. Only character data may change.
    text_attributes_before = [(e, dict(e.attributes.items())) for e in all_elements(document)
                              if e.localName in ("text", "tspan") and e not in fact_elements]
    unknown = set(data) - set(targets)
    if unknown:
        raise KeyError(f"Unknown or protected text keys: {sorted(unknown)}")
    for key, value in data.items():
        replace_text(targets[key], value)
    fact_layout = update_fun_fact(document, FUN_FACT, FUN_FACT_BOX)

    def to_24_hour(clock_value, am_pm):
        return datetime.strptime(f"{clock_value} {am_pm}", "%I:%M %p").strftime("%H:%M")

    chart_times = {
        "fajr": to_24_hour(data["TIMES_FAJR"], data["TIMES_FAJR_AM_PM"]),
        "sunrise": to_24_hour(*CHART_SUNRISE_TIME.rsplit(" ", 1)),
        "dhuhr": to_24_hour(data["TIMES_THUR"], data["TIMES_THUR_AM_PM"]),
        "asr": to_24_hour(data["TIMES_ASR"], data["TIMES_ASR_AM_PM"]),
        "maghrib": to_24_hour(data["TIMES_MAGH"], data["TIMES_MAGH_AM_PM"]),
        "isha": to_24_hour(data["TIMES_ISHA"], data["TIMES_ISHA_AM_PM"]),
        "noon": "12:00",
    }
    replace_circle_placeholder_with_chart(document, chart_times)
    # Verify complete strings, including both fill/outline copies.
    for key,value in data.items():
        nodes = targets[key] if isinstance(targets[key],list) else [targets[key]]
        expected = " ".join(str(value).split()) if key == "Islamic_Date_ISLAM_MONTH" else str(value)
        assert all(text_content(t) == expected for t in nodes), f"Incomplete update: {key}"
    fact_nodes = [t for t in document.getElementsByTagNameNS("*","text") if " ".join(text_content(t).split()) == " ".join(FUN_FACT.split())]
    assert len(fact_nodes) == 1, "Full fun fact was not written."
    qr_modules = replace_square_with_qr(document, QR_URL)
    night_inches, fixed_right = resize_night(document, NIGHT_PERCENT, NIGHT_FULL_WIDTH_INCHES, SVG_UNITS_PER_INCH)
    season_inches = resize_season(document, season["day"], season["total"],
                                  SEASON_MIN_WIDTH_INCHES, SEASON_MAX_WIDTH_INCHES)
    assert all(dict(e.attributes.items()) == attrs for e,attrs in text_attributes_before), "Text formatting changed."
    assert protected_before == [e.toxml() for e in all_elements(document) if locked(e)], "Locked artwork changed."
    for node,span,anchor,anchor_x in alignment_checks:
        assert node.getAttribute("text-anchor") == anchor
        assert math.isclose(float(span.getAttribute("x")), anchor_x, abs_tol=1e-8)
    OUTPUT.write_bytes(document.toxml(encoding="utf-8"))
    print(f"Saved {OUTPUT}: {len(data)} text fields; fact {len(FUN_FACT.split())} words; QR {qr_modules} modules.")
    print(f"Night width: {night_inches:.6f} inches; right edge fixed at {fixed_right:.9f}.")

    print(f"{season['name']}: day {season['day']} of {season['total']}; season bar {season_inches:.6f} inches, right edge fixed.")

    print(f"Restored and verified {len(alignment_checks)} center/right text anchors measured from the current base SVG.")

    print(f"Fun fact: {fact_layout['lines']} wrapped lines, font size {fact_layout['font_size']}, confined to {fact_layout['box']}.")

    export_png(OUTPUT, PNG_OUTPUT, PNG_SCALE)
    return {"svg": str(OUTPUT), "png": str(PNG_OUTPUT)}
