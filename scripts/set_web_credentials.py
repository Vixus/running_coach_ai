"""CLI helper to set web dashboard credentials for an athlete.

Lookup is by --athlete-id (integer PK) or --email. Use this when you can't
reach the admin UI (e.g. forgot password and no admin account exists).
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from werkzeug.security import generate_password_hash

from running_coach_ai.database.models import Athlete
from running_coach_ai.database.session import get_session


def main():
    parser = argparse.ArgumentParser(description="Set web dashboard credentials for an athlete")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--athlete-id", type=int, help="Athlete primary key")
    group.add_argument("--email", help="Athlete email")
    parser.add_argument("--password", required=True, help="New password")
    parser.add_argument("--username", help="Optional web_username (defaults to email)")
    parser.add_argument("--admin", action="store_true", help="Grant admin privileges")
    args = parser.parse_args()

    with get_session() as db:
        if args.athlete_id is not None:
            athlete = db.get(Athlete, args.athlete_id)
        else:
            email = args.email.strip().lower()
            athlete = (
                db.query(Athlete)
                .filter((Athlete.email == email) | (Athlete.web_username == email))
                .first()
            )
        if not athlete:
            print("Error: athlete not found", file=sys.stderr)
            sys.exit(1)

        if args.username:
            athlete.web_username = args.username
        elif args.email and not athlete.web_username:
            athlete.web_username = args.email.strip().lower()
        athlete.web_password_hash = generate_password_hash(args.password)
        if args.admin:
            athlete.is_admin = True

        athlete_id = athlete.id
        athlete_name = athlete.name
        is_admin = athlete.is_admin
        web_username = athlete.web_username
        email = athlete.email

    print(f"Credentials set for athlete #{athlete_id}: {athlete_name or '(unnamed)'}")
    print(f"  Email       : {email}")
    print(f"  Username    : {web_username}")
    print(f"  Admin       : {is_admin}")


if __name__ == "__main__":
    main()
