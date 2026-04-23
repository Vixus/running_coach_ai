#!/usr/bin/env python3
"""Diagnostic script to check Garmin authentication setup."""

import os
import sys
sys.path.insert(0, '/app')

from running_coach_ai.config import settings
from running_coach_ai.database.session import get_session
from running_coach_ai.database.models import Athlete
from running_coach_ai.garmin.client import decrypt_password, get_garmin_client

def main():
    print("=== Garmin Authentication Diagnostics ===")
    print(f"ENCRYPTION_KEY set: {bool(settings.ENCRYPTION_KEY)}")
    print(f"ENCRYPTION_KEY length: {len(settings.ENCRYPTION_KEY) if settings.ENCRYPTION_KEY else 0}")
    print(f"Garmin timeout setting: {settings.GARMIN_TIMEOUT}s")
    print()

    if not settings.ENCRYPTION_KEY:
        print("ERROR: ENCRYPTION_KEY is not set!")
        return

    # Test encryption/decryption
    test_pw = "test_password_123"
    from running_coach_ai.garmin.client import encrypt_password
    encrypted = encrypt_password(test_pw)
    decrypted = decrypt_password(encrypted)
    print(f"Encryption test: {'PASS' if decrypted == test_pw else 'FAIL'}")

    # Check athletes in database
    with get_session() as db:
        athletes = db.query(Athlete).filter(Athlete.garmin_email.isnot(None)).all()
        print(f"Found {len(athletes)} athletes with Garmin credentials")

        for athlete in athletes:
            print(f"\nAthlete {athlete.id}: {athlete.garmin_email}")
            if athlete.garmin_password_encrypted:
                try:
                    # Try to decrypt password
                    password = decrypt_password(athlete.garmin_password_encrypted)
                    print(f"  Password decryption: SUCCESS (length: {len(password)})")

                    # Try to authenticate
                    print("  Testing authentication...")
                    try:
                        client = get_garmin_client(athlete.id, athlete.garmin_email, athlete.garmin_password_encrypted)
                        print("  Authentication: SUCCESS")
                        client.get_full_name()  # Test API call
                        print("  API call: SUCCESS")
                    except Exception as e:
                        print(f"  Authentication/API call: FAILED - {e}")
                        if "429" in str(e) or "rate limit" in str(e).lower() or "cloudflare" in str(e).lower():
                            print("  NOTE: This appears to be IP-based rate limiting from Garmin/Cloudflare")
                            print("  Solutions:")
                            print("  - Try using a VPN to route through a different IP address")
                            print("  - Wait several hours before retrying")
                            print("  - Consider using a residential IP instead of datacenter IP")

                except Exception as e:
                    print(f"  Password decryption: FAILED - {e}")
            else:
                print("  No encrypted password stored")

    print("\n=== Summary ===")
    print("If you're seeing 429/rate limit errors:")
    print("- This may be IP-based rate limiting from Garmin/Cloudflare")
    print("- Different IP addresses have different rate limit thresholds")
    print("- Try running this same diagnostic from your Windows machine to compare")
    print("- Solutions: VPN, wait longer between attempts, use residential IP")

if __name__ == "__main__":
    main()