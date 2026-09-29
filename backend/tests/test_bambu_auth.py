import httpx
import pytest

from app.services import bambu_auth


def _response(status: int, content: bytes, headers: dict[str, str] | None = None):
    request = httpx.Request("POST", "https://api.bambulab.com/v1/user-service/user/sendemail/code")
    return httpx.Response(status, content=content, headers=headers, request=request)


def test_empty_http_200_is_accepted_as_email_code_request(monkeypatch):
    monkeypatch.setattr(bambu_auth.httpx, "post", lambda *args, **kwargs: _response(200, b""))

    assert bambu_auth.request_email_code("maker@example.com") is None


def test_html_http_200_reports_challenge_without_exposing_body(monkeypatch):
    challenge = b"<html>Cloudflare request challenge; sensitive content</html>"
    monkeypatch.setattr(bambu_auth.httpx, "post", lambda *args, **kwargs: _response(200, challenge))

    with pytest.raises(RuntimeError, match="HTML verification page") as error:
        bambu_auth.request_email_code("maker@example.com")

    assert "sensitive content" not in str(error.value)


def test_empty_error_response_is_described_as_empty():
    response = _response(200, b"")

    assert "empty response" in bambu_auth._response_error(response, "verifying a login")
