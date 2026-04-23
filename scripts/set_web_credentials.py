"""CLI helper to set web dashboard credentials for an athlete."""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from werkzeug.security import generate_password_hash

from running_coach_ai.database.models import Athlete
from running_coach_ai.database.session import get_session


def main():
    parser = argparse.ArgumentParser(description="Set web dashboard credentials for an athlete")
    parser.add_argument("--slack-id", required=True, help="Athlete's Slack user ID (e.g. U12345678)")
    parser.add_argument("--username", required=True, help="Web dashboard username")
    parser.add_argument("--password", required=True, help="Web dashboard password")
    parser.add_argument("--admin", action="store_true", help="Grant admin privileges")
    args = parser.parse_args()

    with get_session() as db:
        athlete = db.query(Athlete).filter(Athlete.slack_user_id == args.slack_id).first()
        if not athlete:
            print(f"Error: No athlete found with Slack ID {args.slack_id}", file=sys.stderr)
            sys.exit(1)

        athlete.web_username = args.username
        athlete.web_password_hash = generate_password_hash(args.password)
        if args.admin:
            athlete.is_admin = True

        # Store values before session closes
        athlete_name = athlete.name
        is_admin = athlete.is_admin

    print(f"Credentials set for athlete: {athlete_name or args.slack_id}")
    print(f"  Username : {args.username}")
    print(f"  Admin    : {args.admin or is_admin}")


if __name__ == "__main__":
    main()
