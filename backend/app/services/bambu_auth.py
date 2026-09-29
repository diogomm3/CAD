"""Local Bambu Cloud email-code authentication for MakerWorld downloads."""
import os

import httpx

from ..config import BAMBU_TOKEN_FILE

BAMBU_API = "https://api.bambulab.com"
TOKEN_FILE = BAMBU_TOKEN_FILE


def _response_error(response: httpx.Response, action: str) -> str:
    """Describe rejected API responses without logging response bodies or secrets."""
    content_type = response.headers.get("content-type", "unknown").split(";", 1)[0]
    body = response.content.lstrip()
    if "html" in content_type.lower() or body[:32].lower().startswith((b"<!doctype html", b"<html")):
        return (
            f"Bambu Cloud returned an HTML verification page while {action} "
            f"(HTTP {response.status_code}, {content_type}). The API request was "
            "challenged; this is not an invalid email code. Complete sign-in in "
            "Bambu's official app/site, then retry, or use a Bambu account with "
            "email-code login enabled."
        )
    if not body:
        return (
            f"Bambu Cloud returned an empty response while {action} "
            f"(HTTP {response.status_code}, {content_type})."
        )
    if "json" not in content_type.lower():
        return (
            f"Bambu Cloud returned an unexpected non-JSON response while {action} "
            f"(HTTP {response.status_code}, {content_type}, {len(body)} bytes)."
        )
    return f"Bambu Cloud rejected the request while {action} (HTTP {response.status_code}, {content_type})."


def load_bambu_token() -> str | None:
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return token or None


def save_bambu_token(token: str) -> None:
    """Persist the bearer token with owner-only permissions."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(TOKEN_FILE, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(token.strip() + "\n")
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def request_email_code(email: str) -> None:
    response = httpx.post(
        f"{BAMBU_API}/v1/user-service/user/sendemail/code",
        json={"email": email, "type": "codeLogin"},
        headers={"User-Agent": "LocalModelFinder/1.0", "Content-Type": "application/json"},
        timeout=20,
    )
    if response.is_error:
        raise RuntimeError(_response_error(response, "requesting a verification code"))
    # The endpoint may acknowledge a successfully queued email with HTTP 200
    # and an empty body. Treat that as accepted; requiring JSON here turns the
    # success response into a misleading login failure.
    if response.status_code in {200, 204} and not response.content.strip():
        return
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError(_response_error(response, "requesting a verification code")) from exc
    if not isinstance(result, dict):
        raise RuntimeError("Bambu Cloud returned an unexpected verification-code response format.")
    if result.get("success") is False:
        raise RuntimeError(result.get("message") or "Bambu Cloud could not send a verification code.")


def exchange_email_code(email: str, code: str) -> str:
    response = httpx.post(
        f"{BAMBU_API}/v1/user-service/user/login",
        json={"account": email, "code": code},
        headers={"User-Agent": "LocalModelFinder/1.0", "Content-Type": "application/json"},
        timeout=20,
    )
    if response.is_error:
        raise RuntimeError(_response_error(response, "exchanging the verification code"))
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError(_response_error(response, "exchanging the verification code")) from exc
    if not isinstance(result, dict):
        raise RuntimeError("Bambu Cloud returned an unexpected login response format.")
    token = result.get("accessToken")
    if not token:
        raise RuntimeError(result.get("message") or "Bambu Cloud did not return an access token.")
    return str(token)


def verify_bambu_token(token: str) -> None:
    response = httpx.get(
        f"{BAMBU_API}/v1/user-service/my/profile",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "LocalModelFinder/1.0"},
        timeout=20,
    )
    if response.is_error:
        raise RuntimeError(f"Bambu Cloud token verification failed (HTTP {response.status_code}).")
