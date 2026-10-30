import json
import os
from dotenv import load_dotenv
from client import PolarstepsAPIClient

def load_trip_data(filepath: str) -> dict:
    """Loads trip details from a local JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    # Load environment variables from a .env file
    load_dotenv()

    # 1. Initialize the Client
    client = PolarstepsAPIClient()

    # 2. Target Data Path
    data_file = "data.json"
    if not os.path.exists(data_file):
        print(f"Error: Data file '{data_file}' not found.")
        return

    try:
        # 3. Read payload data from external file
        trip_payload = load_trip_data(data_file)
        
        # 4. Invoke the method exposed by the client
        result = client.create_trip(trip_payload)
        
        print("Trip successfully created!")
        print(json.dumps(result, indent=2))
        
    except Exception as e:
        print(f"Application execution failed: {e}")

if __name__ == "__main__":
    main()