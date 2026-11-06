#!/usr/bin/env python3
"""
prepare-import.py

Prepares and organizes pictures for Polarsteps import.
Supports subcommands for different preparation stages:
  1. group: Organizes pictures by EXIF date taken into YYYY-MM-DD subdirectories.
  2. generate: Generates import JSON file for import.py from the grouped directories.
"""

import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Dict, List, Optional, Tuple, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Fallback: parse .env manually when python-dotenv is not installed
    _env_file = Path(__file__).resolve().parent / ".env"
    if _env_file.exists():
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

try:
    from PIL import Image, ExifTags
except ImportError:
    Image = None
    ExifTags = None

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "out"

from lib import STEP_TYPE_DRAFT, STEP_TYPE_PUBLISHED
from lib.exif_constants import (
    EXIF_IFD_EXIF,
    EXIF_IFD_GPS,
    EXIF_TAG_DATETIME,
    EXIF_TAG_DATETIME_ORIGINAL,
    EXIF_TAG_DATETIME_DIGITIZED,
    EXIF_TAG_OFFSET_TIME,
    EXIF_TAG_OFFSET_TIME_ORIG,
    GPS_TAG_LAT,
    GPS_TAG_LAT_REF,
    GPS_TAG_LON,
    GPS_TAG_LON_REF,
)

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff",
    ".heic", ".heif", ".dng", ".cr2", ".nef", ".arw", ".raw"
}


def parse_coordinates(coord_str: str) -> Tuple[float, float]:
    """
    Parses a string formatted as 'lat,lon' into a tuple of floats.
    """
    parts = [p.strip() for p in coord_str.split(",")]
    if len(parts) != 2:
        raise ValueError(f"Invalid coordinate format '{coord_str}'. Expected 'lat,lon'.")
    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except ValueError:
        raise ValueError(f"Invalid numeric values in coordinates: '{coord_str}'.")
    return (lat, lon)


def validate_date_string(date_str: str, arg_name: str) -> str:
    """
    Validates that date_str is in YYYY-MM-DD format.
    """
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        print(f"Error: Invalid date format for {arg_name}: '{date_str}'. Expected 'YYYY-MM-DD'.", file=sys.stderr)
        sys.exit(1)


def extract_date_from_exif(image_path: Path) -> Optional[str]:
    """
    Extracts the date taken from an image's EXIF data.
    Returns date in format 'YYYY-MM-DD', or None if not found.
    """
    if Image is None:
        raise ImportError(
            "The 'Pillow' library is required to read EXIF metadata.\n"
            "Please install it using: pip install Pillow"
        )

    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return None

            date_str = None

            # 1. Try Exif SubIFD (DateTimeOriginal, DateTimeDigitized, DateTime)
            try:
                sub_ifd = exif.get_ifd(EXIF_IFD_EXIF)
                if sub_ifd:
                    date_str = (
                        sub_ifd.get(EXIF_TAG_DATETIME_ORIGINAL)
                        or sub_ifd.get(EXIF_TAG_DATETIME_DIGITIZED)
                        or sub_ifd.get(EXIF_TAG_DATETIME)
                    )
            except Exception:
                pass

            # 2. Try root IFD tags if not found in SubIFD
            if not date_str:
                date_str = (
                    exif.get(EXIF_TAG_DATETIME_ORIGINAL)
                    or exif.get(EXIF_TAG_DATETIME_DIGITIZED)
                    or exif.get(EXIF_TAG_DATETIME)
                )

            if date_str:
                match = re.search(r"(\d{4})[:\-](\d{2})[:\-](\d{2})", str(date_str))
                if match:
                    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    except Exception:
        pass

    return None


def extract_gps_coordinates(exif) -> Optional[Tuple[float, float]]:
    """
    Extracts latitude and longitude in decimal degrees from Pillow EXIF object.
    Returns (lat, lon) or None.
    """
    try:
        gps_ifd = exif.get_ifd(EXIF_IFD_GPS)
        if not gps_ifd or GPS_TAG_LAT not in gps_ifd or GPS_TAG_LON not in gps_ifd:
            return None

        lat_ref = gps_ifd.get(GPS_TAG_LAT_REF, "N")
        lat_raw = gps_ifd[GPS_TAG_LAT]
        lon_ref = gps_ifd.get(GPS_TAG_LON_REF, "E")
        lon_raw = gps_ifd[GPS_TAG_LON]

        def dms_to_deg(val) -> float:
            d = float(val[0])
            m = float(val[1])
            s = float(val[2])
            return d + (m / 60.0) + (s / 3600.0)

        lat = dms_to_deg(lat_raw)
        if str(lat_ref).upper() == "S":
            lat = -lat

        lon = dms_to_deg(lon_raw)
        if str(lon_ref).upper() == "W":
            lon = -lon

        return (lat, lon)
    except Exception:
        return None


def extract_image_metadata(image_path: Path) -> Tuple[datetime, str, Optional[Tuple[float, float]]]:
    """
    Extracts datetime taken (as datetime object and ISO string) and GPS coordinates.
    Falls back to file mtime if EXIF datetime is missing.
    """
    if Image is None:
        raise ImportError(
            "The 'Pillow' library is required to read EXIF metadata.\n"
            "Please install it using: pip install Pillow"
        )

    dt: Optional[datetime] = None
    iso_str: Optional[str] = None
    gps: Optional[Tuple[float, float]] = None

    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if exif:
                gps = extract_gps_coordinates(exif)

                sub_ifd = {}
                try:
                    sub_ifd = exif.get_ifd(EXIF_IFD_EXIF) or {}
                except Exception:
                    pass

                date_str = (
                    sub_ifd.get(EXIF_TAG_DATETIME_ORIGINAL)
                    or sub_ifd.get(EXIF_TAG_DATETIME_DIGITIZED)
                    or exif.get(EXIF_TAG_DATETIME_ORIGINAL)
                    or exif.get(EXIF_TAG_DATETIME)
                )
                offset_str = sub_ifd.get(EXIF_TAG_OFFSET_TIME) or sub_ifd.get(EXIF_TAG_OFFSET_TIME_ORIG)

                if date_str:
                    match = re.search(r"(\d{4})[:\-](\d{2})[:\-](\d{2})\s+(\d{2}):(\d{2}):(\d{2})", str(date_str))
                    if match:
                        y, m, d, H, M, S = [int(x) for x in match.groups()]
                        tz = None
                        if offset_str:
                            m_off = re.match(r"([+-])(\d{2}):(\d{2})", str(offset_str).strip())
                            if m_off:
                                sign, oh, om = m_off.groups()
                                delta = timedelta(hours=int(oh), minutes=int(om))
                                if sign == "-":
                                    delta = -delta
                                tz = timezone(delta)

                        if tz:
                            dt = datetime(y, m, d, H, M, S, tzinfo=tz).astimezone(timezone.utc)
                        else:
                            dt = datetime(y, m, d, H, M, S, tzinfo=timezone.utc)
                        iso_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        pass

    if dt is None:
        mtime = os.path.getmtime(image_path)
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        iso_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    return dt, iso_str, gps


def run_group(args: argparse.Namespace) -> None:
    """
    Executes the 'group' command:
    Reads pictures recursively from input_dir, extracts EXIF date, and moves
    them to YYYY-MM-DD subdirectories in output_dir.
    """
    input_path = args.input_dir.resolve()
    if not input_path.exists():
        print(f"Error: Input directory does not exist: '{input_path}'", file=sys.stderr)
        sys.exit(1)
    if not input_path.is_dir():
        print(f"Error: Input path is not a directory: '{input_path}'", file=sys.stderr)
        sys.exit(1)

    output_path = args.output_dir.resolve() if args.output_dir else DEFAULT_OUTPUT_DIR.resolve()

    print("=" * 60)
    print("📸 Preparing pictures with command: 'group'")
    print(f"📂 Input directory:  {input_path}")
    print(f"📁 Output directory: {output_path}")
    print("=" * 60)

    image_files = []
    for f in input_path.rglob("*"):
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
            try:
                if f.resolve().is_relative_to(output_path):
                    continue
            except AttributeError:
                if str(output_path) in str(f.resolve()):
                    continue
            image_files.append(f)

    if not image_files:
        print(f"⚠️  No pictures found in '{input_path}' matching supported extensions: {', '.join(sorted(IMAGE_EXTENSIONS))}")
        return

    print(f"🔍 Found {len(image_files)} picture(s) to process.\n")

    counts_by_date = {}
    skipped_count = 0
    success_count = 0

    for file_path in image_files:
        date_str = extract_date_from_exif(file_path)

        if not date_str:
            target_subdir_name = "no-date"
            print(f"⚠️  [No EXIF Date] {file_path.name} -> {target_subdir_name}/")
        else:
            target_subdir_name = date_str

        target_dir = output_path / target_subdir_name
        dest_file = target_dir / file_path.name

        counts_by_date[target_subdir_name] = counts_by_date.get(target_subdir_name, 0) + 1

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(file_path), str(dest_file))
            print(f"✅ Moved '{file_path.name}' -> '{target_subdir_name}/{dest_file.name}'")
            success_count += 1
        except Exception as e:
            print(f"❌ Error processing '{file_path.name}': {e}", file=sys.stderr)
            skipped_count += 1

    print("\n" + "=" * 60)
    print("📊 Grouping Summary:")
    print(f"   Total processed: {success_count} file(s)")
    if skipped_count > 0:
        print(f"   Errors/Skipped:  {skipped_count} file(s)")
    print(f"   Target folders created ({len(counts_by_date)}):")
    for date_key in sorted(counts_by_date.keys()):
        print(f"     📁 {date_key}: {counts_by_date[date_key]} picture(s)")
    print("=" * 60)


def run_generate(args: argparse.Namespace) -> None:
    """
    Executes the 'generate' command:
    Scans subdirectories in input_dir, extracts oldest picture start_time and GPS,
    synthesizes Home steps if requested, and creates a Polarsteps import JSON file.
    """
    input_dir: Optional[Path] = args.input_dir or args.input_dir_opt
    if not input_dir:
        print("Error: Input directory is required.", file=sys.stderr)
        print("Usage: python prepare-import.py generate <input_dir> [-o <output_file>]", file=sys.stderr)
        sys.exit(1)

    input_path = input_dir.resolve()
    if not input_path.exists():
        print(f"Error: Input directory does not exist: '{input_path}'", file=sys.stderr)
        sys.exit(1)
    if not input_path.is_dir():
        print(f"Error: Input path is not a directory: '{input_path}'", file=sys.stderr)
        sys.exit(1)

    output_file: Path = (args.output.resolve() if args.output else (input_path / "import_data.json"))
    output_dir = output_file.parent

    # Validate custom start/end dates if provided
    custom_start_date = validate_date_string(args.start_date, "--start-date") if args.start_date else None
    custom_end_date = validate_date_string(args.end_date, "--end-date") if args.end_date else None

    if custom_start_date and custom_end_date and custom_start_date > custom_end_date:
        print(f"Error: --start-date ({custom_start_date}) cannot be after --end-date ({custom_end_date}).", file=sys.stderr)
        sys.exit(1)

    # Resolve Home location
    home_coords: Optional[Tuple[float, float]] = None
    if args.home:
        try:
            home_coords = parse_coordinates(args.home)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.default_home:
        env_home = os.environ.get("POLARSTEPS_HOME_LOCATION")
        if not env_home:
            print("Error: --default-home was specified, but POLARSTEPS_HOME_LOCATION is not set in environment or .env.", file=sys.stderr)
            sys.exit(1)
        try:
            home_coords = parse_coordinates(env_home)
        except ValueError as e:
            print(f"Error in POLARSTEPS_HOME_LOCATION: {e}", file=sys.stderr)
            sys.exit(1)

    print("=" * 60)
    print("📝 Generating Polarsteps import file with command: 'generate'")
    print(f"📂 Input directory:  {input_path}")
    print(f"📄 Output file:      {output_file}")
    if home_coords:
        print(f"🏠 Home location:    {home_coords[0]}, {home_coords[1]}")
    if custom_start_date or custom_end_date:
        print(f"📅 Custom dates:     {custom_start_date or '(from pictures)'} to {custom_end_date or '(from pictures)'}")
    print("=" * 60)

    # Subdirectories represent individual steps
    subdirs = [d for d in input_path.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not subdirs:
        print(f"⚠️  No subdirectories found in '{input_path}'.", file=sys.stderr)
        sys.exit(1)

    steps: List[Tuple[datetime, Dict[str, Any]]] = []

    for subdir in sorted(subdirs):
        pics = [f for f in subdir.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]
        if not pics:
            print(f"⚠️  Skipping '{subdir.name}': No supported picture files found.")
            continue

        pic_meta_list = []
        for p in pics:
            dt, iso_str, gps = extract_image_metadata(p)
            pic_meta_list.append({
                "path": p,
                "dt": dt,
                "iso_str": iso_str,
                "gps": gps
            })

        # Sort pictures chronologically (oldest first)
        pic_meta_list.sort(key=lambda item: item["dt"])

        oldest = pic_meta_list[0]
        step_start_time = oldest["iso_str"]

        # Determine GPS coordinates
        step_lat: Optional[float] = None
        step_lon: Optional[float] = None

        if oldest["gps"]:
            step_lat, step_lon = oldest["gps"]
        else:
            # Check subsequent pictures as fallback
            fallback_found = False
            for other in pic_meta_list[1:]:
                if other["gps"]:
                    step_lat, step_lon = other["gps"]
                    fallback_found = True
                    print(f"⚠️  Oldest picture '{oldest['path'].name}' in '{subdir.name}' has no GPS.")
                    print(f"    ↳ Using fallback coordinates from '{other['path'].name}'.")
                    break

            if not fallback_found:
                step_lat, step_lon = 0.0, 0.0
                print(f"⚠️  No GPS coordinates found in '{subdir.name}'. Setting coordinates to 0.0, 0.0.")

        # Media paths relative to output_dir so import.py import can locate them directly
        media_paths = []
        for item in pic_meta_list:
            rel_p = Path(os.path.relpath(item["path"], output_dir)).as_posix()
            media_paths.append(rel_p)

        step_dict = {
            "location_lat": round(step_lat, 7) if step_lat is not None else 0.0,
            "location_lon": round(step_lon, 7) if step_lon is not None else 0.0,
            "name": subdir.name,
            "description": "",
            "start_time": step_start_time,
            "type": STEP_TYPE_PUBLISHED,
            "media": media_paths
        }

        steps.append((oldest["dt"], step_dict))

    if not steps:
        print("❌ No steps were generated from pictures. Aborting.", file=sys.stderr)
        sys.exit(1)

    # Sort picture steps chronologically
    steps.sort(key=lambda s: s[0])

    earliest_pic_dt = steps[0][0]
    latest_pic_dt = steps[-1][0]
    earliest_pic_date = earliest_pic_dt.strftime("%Y-%m-%d")
    latest_pic_date = latest_pic_dt.strftime("%Y-%m-%d")

    final_start_date = custom_start_date or earliest_pic_date
    final_end_date = custom_end_date or latest_pic_date

    # If home location is provided, synthesize start and end "Home" steps
    if home_coords:
        home_lat = round(home_coords[0], 7)
        home_lon = round(home_coords[1], 7)

        # 1. Synthesize Start Home step
        start_day_dt = datetime.strptime(final_start_date, "%Y-%m-%d").replace(hour=8, minute=0, second=0, tzinfo=timezone.utc)
        if start_day_dt >= earliest_pic_dt:
            home_start_dt = earliest_pic_dt - timedelta(hours=1)
        else:
            home_start_dt = start_day_dt

        start_home_step = {
            "location_lat": home_lat,
            "location_lon": home_lon,
            "name": "Home",
            "description": "",
            "start_time": home_start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "type": STEP_TYPE_PUBLISHED,
            "media": []
        }
        steps.append((home_start_dt, start_home_step))

        # 2. Synthesize Finish Home step
        end_day_dt = datetime.strptime(final_end_date, "%Y-%m-%d").replace(hour=20, minute=0, second=0, tzinfo=timezone.utc)
        if end_day_dt <= latest_pic_dt:
            home_end_dt = latest_pic_dt + timedelta(hours=1)
        else:
            home_end_dt = end_day_dt

        end_home_step = {
            "location_lat": home_lat,
            "location_lon": home_lon,
            "name": "Home",
            "description": "",
            "start_time": home_end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "type": STEP_TYPE_PUBLISHED,
            "media": []
        }
        steps.append((home_end_dt, end_home_step))

    # Sort all steps chronologically
    steps.sort(key=lambda s: s[0])
    sorted_steps = [s[1] for s in steps]

    trip_name = args.trip_name or input_path.name
    trip_description = args.trip_description or ""
    is_public = args.is_public

    import_data = {
        "name": trip_name,
        "description": trip_description,
        "start_date": final_start_date,
        "end_date": final_end_date,
        "is_public": is_public,
        "steps": sorted_steps
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(import_data, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("📊 Generation Summary:")
    print(f"   Trip name:   {trip_name}")
    print(f"   Date range:  {final_start_date} to {final_end_date}")
    print(f"   Steps count: {len(sorted_steps)}")
    for s in sorted_steps:
        print(f"     📍 {s['name']}: {len(s['media'])} picture(s), coords: ({s['location_lat']}, {s['location_lon']}), start: {s['start_time']}")
    print(f"\n✅ Successfully generated import file: {output_file}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="prepare-import.py",
        description="Helper utility to organize and prepare pictures for Polarsteps import."
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="Commands",
        description="Supported preparation commands"
    )

    # 1. 'group' subcommand
    group_parser = subparsers.add_parser(
        "group",
        help="Recursively move pictures into subdirectories by EXIF date"
    )
    group_parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing the pictures to scan recursively"
    )
    group_parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: 'out' next to this script)"
    )
    group_parser.set_defaults(func=run_group)

    # 2. 'generate' subcommand
    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate import JSON file for import.py from grouped picture subdirectories"
    )
    generate_parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        default=None,
        help="Directory containing the picture subdirectories (e.g. 'out')"
    )
    generate_parser.add_argument(
        "-i", "--input-dir",
        type=Path,
        dest="input_dir_opt",
        help="Directory containing the picture subdirectories (alternative to positional argument)"
    )
    generate_parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output JSON file path (default: '<input_dir>/import_data.json')"
    )
    generate_parser.add_argument(
        "--trip-name",
        default=None,
        help="Name of the trip (default: name of the input directory)"
    )
    generate_parser.add_argument(
        "--trip-description",
        default="",
        help="Description of the trip (default: empty)"
    )
    generate_parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Trip start date (YYYY-MM-DD). If omitted, taken from pictures."
    )
    generate_parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Trip end date (YYYY-MM-DD). If omitted, taken from pictures."
    )
    generate_parser.add_argument(
        "--home",
        type=str,
        default=None,
        help="Home location as 'lat,lon' (e.g. '52.5200,13.4050'). Synthesizes start/end Home steps."
    )
    generate_parser.add_argument(
        "--default-home",
        action="store_true",
        default=False,
        help="Use home location from POLARSTEPS_HOME_LOCATION environment variable (or .env file)."
    )
    generate_parser.add_argument(
        "--is-public",
        action="store_true",
        default=False,
        help="Mark the trip as public (default: private)"
    )
    generate_parser.set_defaults(func=run_generate)

    if len(sys.argv) == 1:
        parser.print_help()
        print("\nExamples:")
        print("  python prepare-import.py group /path/to/photos")
        print("  python prepare-import.py group /path/to/photos -o out")
        print("  python prepare-import.py generate out")
        print("  python prepare-import.py generate out --home 52.5200,13.4050 --start-date 2026-10-16 --end-date 2026-10-20")
        print("  python prepare-import.py generate out --default-home")
        return

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
