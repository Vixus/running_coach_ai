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


if __name__ == "__main__":
    main()
