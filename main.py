#!/usr/bin/env python3

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from typing import Any, Dict, List, Optional, Union
from dotenv import load_dotenv

from lib import PolarstepsAPIClient, GeocodingClient, process_and_upload_image

def load_json_file(filepath: str) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """Loads JSON data from a local file, returning a dict or list."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: '{filepath}'")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def _make_client(args: argparse.Namespace) -> PolarstepsAPIClient:
    """Constructs a PolarstepsAPIClient from parsed CLI args."""
    return PolarstepsAPIClient(
        verbose=getattr(args, "verbose", False),
        dump=getattr(args, "dump", None),
    )

def process_step(
    raw_step: Dict[str, Any],
    geocoding_client: GeocodingClient,
    polarsteps_client: PolarstepsAPIClient,
    trip_slug: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes the step creation process:
      1. Fetch external reverse geocoding details from coordinates
      2. Register location via POST /api/locations (polarsteps-api-version: 13)
      3. Retrieve timezone if needed via GET /api/timezone
      4. Create step via POST /api/steps (polarsteps-api-version: 68)
    """
    lat = raw_step.get("location_lat")
    lon = raw_step.get("location_lon")
    trip_id = raw_step.get("trip_id")
    slug = trip_slug or raw_step.get("trip_slug")

    if lat is None or lon is None:
        raise ValueError("Missing 'location_lat' or 'location_lon' in step data.")

    # Stage 1: External reverse geocoding
    geo_details = geocoding_client.get_location_details(lat, lon)
    print(f"✅ Geocoding resolved: {geo_details.get('resolved_name')}, {geo_details.get('full_detail')}")

    # Stage 2: Register location with Polarsteps via POST /api/locations
    if raw_step.get("location_id"):
        location_id = raw_step.get("location_id")
        location_result = {"id": location_id}
        print(f"ℹ️ Using pre-existing location_id: {location_id}")
    else:
        country_code = raw_step.get("country_code") or geo_details.get("country_code")
        if not country_code:
            raise ValueError(f"Could not determine 'country_code' for coordinates ({lat}, {lon}). Please specify 'country_code' in step data.")

        location_payload = {
            "country_code": country_code,
            "lat": lat,
            "lon": lon,
            "detail": raw_step.get("detail") or geo_details.get("detail") or geo_details.get("country", ""),
            "full_detail": raw_step.get("full_detail") or geo_details.get("full_detail", ""),
            "name": raw_step.get("location_name") or geo_details.get("name") or raw_step.get("name", "")
        }
        location_result = polarsteps_client.create_location_object(location_payload, trip_id=trip_id, trip_slug=slug)
        location_id = (
            location_result.get("id")
            or location_result.get("location_id")
            or (location_result.get("location", {}).get("id") if isinstance(location_result.get("location"), dict) else None)
        )

    if location_id is None:
        raise ValueError(
            f"Failed to obtain a valid location ID from Polarsteps POST /api/locations.\n"
            f"Response received was: {json.dumps(location_result, indent=2)}\n"
            f"Run with -v/--verbose to inspect request and response headers."
        )

    # Stage 3: Fetch timezone
    timezone_id = location_result.get("timezone_id") or location_result.get("timezone")
    if not timezone_id:
        try:
            tz_data = polarsteps_client.get_timezone(lat, lon)
            timezone_id = tz_data.get("timezone_id") or tz_data.get("timezone")
        except Exception as tz_err:
            print(f"⚠️ Could not fetch timezone from /api/timezone ({tz_err}).")

    timezone_id = timezone_id or raw_step.get("timezone_id")
    if not timezone_id:
        raise ValueError(f"Could not determine 'timezone_id' for coordinates ({lat}, {lon}). Please specify 'timezone_id' in step data.")

    print(f"✅ Location registered in Polarsteps -> ID: {location_id}, Timezone: {timezone_id}")

    # Stage 4: Create step on Polarsteps
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    step_name = raw_step.get("name") or geo_details.get("name") or "New Step"

    step_payload = {
        "trip_id": trip_id,
        "location_id": location_id,
        "name": step_name,
        "description": raw_step.get("description", ""),
        "start_time": raw_step.get("start_time"),
        "creation_time": raw_step.get("creation_time") or now_iso,
        "timezone_id": timezone_id,
        "type": raw_step.get("type", 0)
    }

    step_result = polarsteps_client.create_step(step_payload, trip_slug=slug)
    return step_result

def run_step_import(args: argparse.Namespace) -> None:
    """Handles importing an array of steps from a JSON file."""
    filepath = args.file
    try:
        data = load_json_file(filepath)
    except Exception as e:
        print(f"❌ Error loading step data: {e}")
        return

    if not isinstance(data, list):
        print(f"❌ Error: Expected a JSON array of steps in '{filepath}', but found {type(data).__name__}.")
        return

    steps: List[Dict[str, Any]] = data
    total_steps = len(steps)
    if total_steps == 0:
        print(f"⚠️ Warning: '{filepath}' contains an empty array of steps. Nothing to import.")
        return

    print(f"🚀 Starting step import: {total_steps} step(s) to process from '{filepath}'.")

    geocoding_client = GeocodingClient(verbose=getattr(args, "verbose", False))
    polarsteps_client = _make_client(args)

    successful = 0
    for idx, raw_step in enumerate(steps, start=1):
        step_name = raw_step.get("name", f"Step #{idx}")
        print(f"\n--- [{idx}/{total_steps}] Processing: {step_name} ---")
        try:
            result = process_step(raw_step, geocoding_client, polarsteps_client, trip_slug=args.trip_slug)
            print(f"✅ Successfully created step '{step_name}'!")
            print(json.dumps(result, indent=2))
            successful += 1
        except Exception as err:
            print(f"❌ Failed to create step '{step_name}': {err}")

    polarsteps_client.flush_har()
    print(f"\n✨ Import finished: {successful}/{total_steps} step(s) created successfully.")

def run_trip_import(args: argparse.Namespace) -> None:
    """Handles creating a new trip from JSON file."""
    filepath = args.file
    try:
        trip_payload = load_json_file(filepath)
    except Exception as e:
        print(f"❌ Error loading trip data: {e}")
        return

    if not isinstance(trip_payload, dict):
        print("❌ Error: Trip data must be a JSON object.")
        return

    verbose = getattr(args, "verbose", False)
    polarsteps_client = _make_client(args)
    print(f"🚀 Creating trip from '{filepath}'...")
    try:
        result = polarsteps_client.create_trip(trip_payload)
        print(f"✅ Trip {result.get('id')} successfully created!")
    except Exception as err:
        print(f"❌ Failed to create trip: {err}")
    finally:
        polarsteps_client.flush_har()

def run_media_import(args: argparse.Namespace) -> None:
    """Handles uploading media files and attaching them to a step."""
    filepath = getattr(args, "file", None) or getattr(args, "media_flag", None)
    if not filepath:
        print("❌ Error: Missing media JSON file path.")
        return

    try:
        data = load_json_file(filepath)
    except Exception as e:
        print(f"❌ Error loading media JSON file: {e}")
        return

    entries: List[Dict[str, Any]] = data if isinstance(data, list) else [data]
    base_dir = os.path.dirname(os.path.abspath(filepath))

    verbose = getattr(args, "verbose", False)
    polarsteps_client = _make_client(args)

    for entry_idx, entry in enumerate(entries, start=1):
        trip_id = entry.get("trip_id")
        step_id = entry.get("step_id")
        files = entry.get("files", [])

        if not trip_id or not step_id:
            print(f"❌ Error in entry #{entry_idx}: Both 'trip_id' and 'step_id' are required.")
            continue

        if not files:
            print(f"⚠️ Warning in entry #{entry_idx}: 'files' list is empty. Nothing to upload.")
            continue

        _upload_step_media(files, base_dir, trip_id, step_id, polarsteps_client)

    polarsteps_client.flush_har()

def _upload_step_media(
    media_files: List[str],
    base_dir: str,
    trip_id: int,
    step_id: int,
    polarsteps_client: PolarstepsAPIClient,
) -> None:
    """Resolves, uploads, and links media files to a step."""
    resolved_files = []
    missing_files = []
    for f in media_files:
        full_path = f if os.path.isabs(f) else os.path.normpath(os.path.join(base_dir, f))
        if os.path.exists(full_path):
            resolved_files.append(full_path)
        else:
            missing_files.append(full_path)

    if missing_files:
        print(f"❌ Error: The following image file(s) do not exist:")
        for mf in missing_files:
            print(f"   - {mf}")
        print("Skipping media upload for this step.")
        return

    print(f"🖼️  Uploading {len(resolved_files)} image(s) to Step {step_id}...")

    media_infos = []
    for order, img_path in enumerate(resolved_files):
        try:
            media_info = process_and_upload_image(img_path, order, polarsteps_client, trip_id=trip_id)
            media_infos.append(media_info)
        except Exception as err:
            print(f"❌ Failed to process/upload '{img_path}': {err}")

    if not media_infos:
        print("❌ No images were uploaded successfully.")
        return

    try:
        polarsteps_client.add_media_to_step(trip_id, step_id, media_infos)
        print(f"✅ Linked {len(media_infos)} media item(s) to step {step_id}.")
    except Exception as err:
        print(f"❌ Failed to link media to step {step_id}: {err}")


def run_import(args: argparse.Namespace) -> None:
    """Handles a full import: creates the trip, then each step, then uploads media."""
    filepath = args.file
    try:
        data = load_json_file(filepath)
    except Exception as e:
        print(f"❌ Error loading import file: {e}")
        return

    if not isinstance(data, dict):
        print(f"❌ Error: Import file must be a JSON object (not an array).")
        return

    steps_data: List[Dict[str, Any]] = data.get("steps", [])
    base_dir = os.path.dirname(os.path.abspath(filepath))

    verbose = getattr(args, "verbose", False)
    geocoding_client = GeocodingClient(verbose=verbose)
    polarsteps_client = _make_client(args)

    # ── Step 1: Create the trip ───────────────────────────────────────────────
    trip_payload = {k: v for k, v in data.items() if k != "steps"}
    print(f"🚀 Creating trip '{data.get('name', '(unnamed)')}'...")
    try:
        trip_result = polarsteps_client.create_trip(trip_payload)
    except Exception as err:
        print(f"❌ Failed to create trip: {err}")
        return

    trip_id = trip_result.get("id")
    trip_slug = trip_result.get("slug")
    if not trip_id:
        print(f"❌ Trip creation succeeded but no 'id' was returned. Cannot continue.")
        print(json.dumps(trip_result, indent=2))
        return

    print(f"✅ Trip created! ID: {trip_id}, slug: '{trip_slug}'")

    # ── Step 2: Create each step ──────────────────────────────────────────────
    if not steps_data:
        print("⚠️  No steps defined in import file. Done.")
        return

    total = len(steps_data)
    print(f"\n📍 Processing {total} step(s)...")

    successful_steps = 0
    for idx, raw_step in enumerate(steps_data, start=1):
        step_name = raw_step.get("name", f"Step #{idx}")
        media_files: List[str] = raw_step.get("media", [])

        print(f"\n--- [{idx}/{total}] {step_name} ---")

        # Inject trip_id so process_step can use it
        raw_step_with_trip = {**raw_step, "trip_id": trip_id}

        try:
            step_result = process_step(raw_step_with_trip, geocoding_client, polarsteps_client, trip_slug=trip_slug)
        except Exception as err:
            print(f"❌ Failed to create step '{step_name}': {err}")
            continue

        step_id = step_result.get("id")
        if not step_id:
            print(f"❌ Step created but no 'id' returned. Skipping media for '{step_name}'.")
            print(json.dumps(step_result, indent=2))
            successful_steps += 1
            continue

        print(f"✅ Step created! ID: {step_id}")
        successful_steps += 1

        # ── Step 3: Upload media for this step ────────────────────────────────
        if media_files:
            _upload_step_media(media_files, base_dir, trip_id, step_id, polarsteps_client)

    polarsteps_client.flush_har()
    print(f"\n✨ Import complete: {successful_steps}/{total} step(s) created successfully.")


def main() -> None:
    load_dotenv()

    # Shared parser for global flags like -v/--verbose
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output logging all request and response payloads"
    )
    common_parser.add_argument(
        "-d", "--dump",
        metavar="FILE",
        default=None,
        help="Dump all requests and responses to a HAR file for debugging"
    )

    parser = argparse.ArgumentParser(
        description="Polarsteps API Importer - Programmatically create trips, steps, and upload media on Polarsteps.",
        parents=[common_parser]
    )
    parser.add_argument(
        "-m", "--media",
        dest="media_flag",
        metavar="FILE",
        help="Upload and attach media (pictures) to a step from a JSON file"
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Action to perform")

    # 'steps' subcommand
    steps_parser = subparsers.add_parser(
        "steps",
        aliases=["step"],
        parents=[common_parser],
        help="Import an array of steps from a JSON file"
    )
    steps_parser.add_argument(
        "file",
        help="Path to JSON file containing an array of steps"
    )
    steps_parser.add_argument(
        "--trip-slug",
        default=None,
        help="Optional trip slug used for Referer header"
    )
    steps_parser.set_defaults(func=run_step_import)

    # 'trip' subcommand
    trip_parser = subparsers.add_parser(
        "trip",
        parents=[common_parser],
        help="Create a trip from a JSON file"
    )
    trip_parser.add_argument(
        "file",
        nargs="?",
        default="data.json",
        help="Path to trip JSON file (default: data.json)"
    )
    trip_parser.set_defaults(func=run_trip_import)

    # 'media' subcommand
    media_parser = subparsers.add_parser(
        "media",
        parents=[common_parser],
        help="Upload and attach media (pictures) to a step from a JSON file"
    )
    media_parser.add_argument(
        "file",
        help="Path to media JSON file specifying relative image locations, trip_id, and step_id"
    )
    media_parser.set_defaults(func=run_media_import)

    # 'import' subcommand
    import_parser = subparsers.add_parser(
        "import",
        parents=[common_parser],
        help="Full import: create trip, steps, and upload media from a single JSON file"
    )
    import_parser.add_argument(
        "file",
        help="Path to import JSON file containing trip, steps, and optional media"
    )
    import_parser.set_defaults(func=run_import)

    if len(sys.argv) == 1:
        parser.print_help()
        print("\nExamples:")
        print("  python main.py steps examples/step_data.json       # Import steps from step_data.json")
        print("  python main.py trip examples/data.json             # Create trip from data.json")
        print("  python main.py media examples/media_data.json      # Upload media from media_data.json")
        print("  python main.py -m examples/media_data.json         # Shortcut for media upload")
        print("  python main.py import examples/import_data.json    # Full import: trip + steps + media")
        return

    args = parser.parse_args()

    # If -m/--media flag was provided at top-level
    if getattr(args, "media_flag", None):
        run_media_import(args)
        return

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
