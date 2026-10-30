#!/usr/bin/env python3

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from typing import Any, Dict, List, Optional, Union
from dotenv import load_dotenv

from client import PolarstepsAPIClient
from geocoding_client import GeocodingClient

def load_json_file(filepath: str) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """Loads JSON data from a local file, returning a dict or list."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: '{filepath}'")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

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
    slug = trip_slug or raw_step.get("trip_slug") or "summer-alps-expedition"

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
        location_payload = {
            "country_code": raw_step.get("country_code") or geo_details.get("country_code", "ES"),
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
            print(f"⚠️ Could not fetch timezone from /api/timezone ({tz_err}). Using default.")

    timezone_id = timezone_id or raw_step.get("timezone_id", "Europe/Madrid")
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

    verbose = getattr(args, "verbose", False)
    geocoding_client = GeocodingClient(verbose=verbose)
    polarsteps_client = PolarstepsAPIClient(verbose=verbose)

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
    polarsteps_client = PolarstepsAPIClient(verbose=verbose)
    print(f"🚀 Creating trip from '{filepath}'...")
    try:
        result = polarsteps_client.create_trip(trip_payload)
        print("✅ Trip successfully created!")
        print(json.dumps(result, indent=2))
    except Exception as err:
        print(f"❌ Failed to create trip: {err}")

def main() -> None:
    load_dotenv()

    # Shared parser for global flags like -v/--verbose
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output logging all request and response payloads"
    )

    parser = argparse.ArgumentParser(
        description="Polarsteps API Importer - Programmatically create trips and steps on Polarsteps.",
        parents=[common_parser]
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
        help="Optional trip slug used for Referer header (e.g. 'summer-alps-expedition')"
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

    if len(sys.argv) == 1:
        parser.print_help()
        print("\nExamples:")
        print("  python main.py steps step_data.json         # Import steps from step_data.json")
        print("  python main.py steps step_data.json -v      # Import steps with verbose logging")
        print("  python main.py trip data.json               # Create trip from data.json")
        return

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
