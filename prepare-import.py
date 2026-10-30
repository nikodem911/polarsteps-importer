#!/usr/bin/env python3
"""
prepare-import.py

Prepares and organizes pictures for Polarsteps import.
Supports subcommands for different preparation stages.
"""

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Optional

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
                # Match YYYY:MM:DD or YYYY-MM-DD at the start of the string
                match = re.search(r"(\d{4})[:\-](\d{2})[:\-](\d{2})", str(date_str))
                if match:
                    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    except Exception:
        pass

    return None


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
    print(f"📸 Preparing pictures with command: 'group'")
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
            # Skip if inside output directory to avoid self-processing
            try:
                if f.resolve().is_relative_to(output_path):
                    continue
            except AttributeError:
                # Python < 3.9 compatibility fallback
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

    # Note: Second command placeholder can be registered here in future

    if len(sys.argv) == 1:
        parser.print_help()
        print("\nExamples:")
        print("  python prepare-import.py group /path/to/photos")
        print("  python prepare-import.py group /path/to/photos -o /path/to/output")
        print("  python prepare-import.py group /path/to/photos --dry-run")
        print("  python prepare-import.py group /path/to/photos --copy")
        return

    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

