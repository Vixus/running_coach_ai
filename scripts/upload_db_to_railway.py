"""One-shot: upload a local SQLite file to a Railway-deployed instance.

Usage:
    python scripts/upload_db_to_railway.py \
        --url https://runningcoachai-production.up.railway.app \
        --token <DB_UPLOAD_TOKEN value> \
        --db-path data/coach.db

After upload, remove DB_UPLOAD_TOKEN from Railway env vars to disable the endpoint
and trigger a redeploy so the app picks up the new DB.
"""

import argparse
import os
import sys

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Public Railway URL, e.g. https://...up.railway.app")
    parser.add_argument("--token", required=True, help="Value of DB_UPLOAD_TOKEN env var on Railway")
    parser.add_argument("--db-path", required=True, help="Local path to your coach.db file")
    args = parser.parse_args()

    if not os.path.exists(args.db_path):
        print(f"Error: {args.db_path} not found", file=sys.stderr)
        sys.exit(1)

    size_mb = os.path.getsize(args.db_path) / (1024 * 1024)
    print(f"Uploading {args.db_path} ({size_mb:.1f} MB) to {args.url}...")

    with open(args.db_path, "rb") as f:
        response = requests.post(
            f"{args.url.rstrip('/')}/admin/upload-db",
            headers={"X-Upload-Token": args.token},
            files={"file": ("coach.db", f, "application/octet-stream")},
            timeout=300,
        )

    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
    if response.status_code != 200:
        sys.exit(1)

    # Upload Garmin session files if they exist alongside the DB
    sessions_dir = os.path.join(os.path.dirname(args.db_path), "garmin_sessions")
    if os.path.isdir(sessions_dir):
        print(f"\nUploading Garmin sessions from {sessions_dir}...")
        for athlete_id in os.listdir(sessions_dir):
            athlete_dir = os.path.join(sessions_dir, athlete_id)
            if not os.path.isdir(athlete_dir):
                continue
            for filename in ("oauth1_token.json", "oauth2_token.json"):
                filepath = os.path.join(athlete_dir, filename)
                if not os.path.exists(filepath):
                    continue
                with open(filepath, "rb") as f:
                    r = requests.post(
                        f"{args.url.rstrip('/')}/admin/upload-garmin-session/{athlete_id}/{filename}",
                        headers={"X-Upload-Token": args.token, "Content-Type": "application/octet-stream"},
                        data=f,
                        timeout=30,
                    )
                status = "OK" if r.status_code == 200 else f"FAILED ({r.status_code})"
                print(f"  {athlete_id}/{filename}: {status}")
    else:
        print("\nNo garmin_sessions directory found alongside DB — skipping.")


if __name__ == "__main__":
    main()
