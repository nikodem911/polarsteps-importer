#!/usr/bin/env python3
"""
prepare-import.py

Prepares and organizes pictures for Polarsteps import.
Supports subcommands for different preparation stages:
  1. group: Organizes pictures by EXIF date taken into YYYY-MM-DD subdirectories.
  2. generate: Generates import JSON file for main.py from the grouped directories.
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
    from PIL import Image, ExifTags
except ImportError:
    Image = None
    ExifTags = None

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "out"

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff",
    ".heic", ".heif", ".dng", ".cr2", ".nef", ".arw", ".raw"
}


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

            # 1. Try Exif SubIFD (tag 0x8769 / 34665)
            # DateTimeOriginal (36867), DateTimeDigitized (36868)
            try:
                sub_ifd = exif.get_ifd(0x8769)
                if sub_ifd:
                    date_str = (
                        sub_ifd.get(36867)  # DateTimeOriginal
                        or sub_ifd.get(36868)  # DateTimeDigitized
                        or sub_ifd.get(306)    # DateTime
                    )
            except Exception:
                pass

            # 2. Try root IFD tags if not found in SubIFD
            if not date_str:
                date_str = exif.get(36867) or exif.get(36868) or exif.get(306)

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
        gps_ifd = exif.get_ifd(0x8825)
        if not gps_ifd or 2 not in gps_ifd or 4 not in gps_ifd:
            return None

        lat_ref = gps_ifd.get(1, "N")
        lat_raw = gps_ifd[2]
        lon_ref = gps_ifd.get(3, "E")
        lon_raw = gps_ifd[4]

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
                    sub_ifd = exif.get_ifd(0x8769) or {}
                except Exception:
                    pass

                date_str = (
                    sub_ifd.get(36867)  # DateTimeOriginal
                    or sub_ifd.get(36868)  # DateTimeDigitized
                    or exif.get(36867)
                    or exif.get(306)  # DateTime
                )
                offset_str = sub_ifd.get(36880) or sub_ifd.get(36881)

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
    Reads pictures from input_dir, extracts EXIF date, and moves them to
    YYYY-MM-DD subdirectories in output_dir.
    """
    input_dir: Optional[Path] = args.input_dir or args.input_dir_opt
    if not input_dir:
        print("Error: Input directory is required.", file=sys.stderr)
        print("Usage: python prepare-import.py group <input_dir> [-o <output_dir>]", file=sys.stderr)
        sys.exit(1)

    input_path = input_dir.resolve()
    if not input_path.exists():
        print(f"Error: Input directory does not exist: '{input_path}'", file=sys.stderr)
        sys.exit(1)
    if not input_path.is_dir():
        print(f"Error: Input path is not a directory: '{input_path}'", file=sys.stderr)
        sys.exit(1)

    output_path = (args.output_dir.resolve() if args.output_dir else DEFAULT_OUTPUT_DIR.resolve())
    dry_run: bool = args.dry_run
    copy_mode: bool = args.copy
    fallback_mtime: bool = args.fallback_mtime
    recursive: bool = args.recursive

    action_verb = "Copying" if copy_mode else "Moving"
    action_done = "Copied" if copy_mode else "Moved"

    print("=" * 60)
    print("📸 Preparing pictures with command: 'group'")
    print(f"📂 Input directory:  {input_path}")
    print(f"📁 Output directory: {output_path}")
    print(f"⚙️  Mode:             {'Dry run (simulation)' if dry_run else ('Copy' if copy_mode else 'Move')}")
    print("=" * 60)

    # Collect files
    if recursive:
        file_iterator = input_path.rglob("*")
    else:
        file_iterator = input_path.glob("*")

    image_files = []
    for f in file_iterator:
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

        if not date_str and fallback_mtime:
            mtime = os.path.getmtime(file_path)
            date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")

        if not date_str:
            target_subdir_name = "no-date"
            print(f"⚠️  [No EXIF Date] {file_path.name} -> {target_subdir_name}/")
        else:
            target_subdir_name = date_str

        target_dir = output_path / target_subdir_name
        dest_file = target_dir / file_path.name

        counts_by_date[target_subdir_name] = counts_by_date.get(target_subdir_name, 0) + 1

        if dry_run:
            print(f"[DRY RUN] {action_verb} '{file_path.name}' -> '{dest_file.relative_to(output_path.parent)}'")
            success_count += 1
        else:
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                if copy_mode:
                    shutil.copy2(file_path, dest_file)
                else:
                    shutil.move(str(file_path), str(dest_file))
                print(f"✅ {action_done} '{file_path.name}' -> '{target_subdir_name}/{dest_file.name}'")
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
    and creates a Polarsteps import JSON file compatible with main.py import.
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

    print("=" * 60)
    print("📝 Generating Polarsteps import file with command: 'generate'")
    print(f"📂 Input directory:  {input_path}")
    print(f"📄 Output file:      {output_file}")
    print("=" * 60)

    # Subdirectories represent individual steps
    subdirs = [d for d in input_path.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not subdirs:
        print(f"⚠️  No subdirectories found in '{input_path}'.", file=sys.stderr)
        sys.exit(1)

    steps: List[Tuple[datetime, Dict[str, Any]]] = []

    for subdir in sorted(subdirs):
        # Collect pictures in this subdirectory
        pics = [f for f in subdir.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]
        if not pics:
            print(f"⚠️  Skipping '{subdir.name}': No supported picture files found.")
            continue

        # Extract metadata for all pictures
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

        # Media paths relative to output_dir so main.py import can locate them directly
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
            "type": 0,
            "media": media_paths
        }

        steps.append((oldest["dt"], step_dict))

    if not steps:
        print("❌ No steps were generated. Aborting.", file=sys.stderr)
        sys.exit(1)

    # Sort steps chronologically by start_time
    steps.sort(key=lambda s: s[0])
    sorted_steps = [s[1] for s in steps]

    earliest_date = sorted_steps[0]["start_time"][:10]
    latest_date = sorted_steps[-1]["start_time"][:10]

    trip_name = args.trip_name or input_path.name
    trip_description = args.trip_description or ""
    is_public = args.is_public

    import_data = {
        "name": trip_name,
        "description": trip_description,
        "start_date": earliest_date,
        "end_date": latest_date,
        "is_public": is_public,
        "steps": sorted_steps
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(import_data, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("📊 Generation Summary:")
    print(f"   Trip name:   {trip_name}")
    print(f"   Date range:  {earliest_date} to {latest_date}")
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
        help="Group pictures by date taken (from EXIF) into YYYY-MM-DD subdirectories"
    )
    group_parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        default=None,
        help="Directory containing the pictures"
    )
    group_parser.add_argument(
        "-i", "--input-dir",
        type=Path,
        dest="input_dir_opt",
        help="Directory containing the pictures (alternative to positional argument)"
    )
    group_parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: 'out' relative to script dir)"
    )
    group_parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy files instead of moving them"
    )
    group_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate actions without modifying files"
    )
    group_parser.add_argument(
        "--recursive", "-r",
        action="store_true",
        help="Scan input directory recursively"
    )
    group_parser.add_argument(
        "--fallback-mtime",
        action="store_true",
        help="Fallback to file modification date if EXIF date is missing"
    )
    group_parser.set_defaults(func=run_group)

    # 2. 'generate' subcommand
    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate import JSON file for main.py from grouped picture subdirectories"
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
        print("  python prepare-import.py generate out -o out/import_data.json --trip-name 'Barcelona Trip'")
        return

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
