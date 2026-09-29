"""Sign in to Bambu Cloud by email code without opening MakerWorld in a browser."""
import getpass
import sys

from ..services.bambu_auth import (
    exchange_email_code,
    request_email_code,
    save_bambu_token,
    verify_bambu_token,
)


def main() -> int:
    email = input("Bambu account email: ").strip()
    if not email or "@" not in email:
        print("Enter a valid Bambu account email.", file=sys.stderr)
        return 2
    try:
        request_email_code(email)
        print("Verification code requested. Check your email.")
        code = getpass.getpass("Bambu verification code: ").strip()
        if not code:
            print("No code entered; token was not saved.", file=sys.stderr)
            return 2
        token = exchange_email_code(email, code)
        verify_bambu_token(token)
        save_bambu_token(token)
    except Exception as exc:
        print(f"Bambu Cloud login failed: {exc}", file=sys.stderr)
        return 1
    print("Bambu Cloud login verified. Token saved locally with owner-only permissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
