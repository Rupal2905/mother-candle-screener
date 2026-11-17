"""Local-only Upstox OAuth helpers.  No password, PIN, OTP, or trading API."""
from __future__ import annotations

import os
import secrets
import webbrowser
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

AUTH_DIALOG = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


class AuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpstoxCredentials:
    access_token: str | None
    api_key: str | None
    api_secret: str | None
    redirect_uri: str | None

    @classmethod
    def from_environment(cls) -> "UpstoxCredentials":
        load_dotenv()
        return cls(
            os.getenv("UPSTOX_ACCESS_TOKEN") or None,
            os.getenv("UPSTOX_API_KEY") or None,
            os.getenv("UPSTOX_API_SECRET") or None,
            os.getenv("UPSTOX_REDIRECT_URI") or None,
        )


def authorisation_url(credentials: UpstoxCredentials, state: str) -> str:
    if not credentials.api_key or not credentials.redirect_uri:
        raise AuthenticationError("UPSTOX_API_KEY and UPSTOX_REDIRECT_URI are required for OAuth")
    return AUTH_DIALOG + "?" + urlencode(
        {"response_type": "code", "client_id": credentials.api_key, "redirect_uri": credentials.redirect_uri, "state": state}
    )


def extract_authorisation_code(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme and parsed.query:
        code = parse_qs(parsed.query).get("code", [""])[0]
        if code:
            return code
    if value.strip():
        return value.strip()
    raise AuthenticationError("No authorisation code supplied")


def exchange_code(credentials: UpstoxCredentials, code: str) -> str:
    if not all((credentials.api_key, credentials.api_secret, credentials.redirect_uri)):
        raise AuthenticationError("OAuth exchange needs UPSTOX_API_KEY, UPSTOX_API_SECRET, and UPSTOX_REDIRECT_URI")
    response = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": credentials.api_key,
            "client_secret": credentials.api_secret,
            "redirect_uri": credentials.redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
        timeout=20,
    )
    if response.status_code >= 400:
        raise AuthenticationError(f"Upstox token exchange failed ({response.status_code}): {response.text[:300]}")
    token = response.json().get("access_token")
    if not token:
        raise AuthenticationError("Upstox token response did not contain access_token")
    return str(token)


def obtain_token_interactively() -> str:
    credentials = UpstoxCredentials.from_environment()
    if credentials.access_token:
        return credentials.access_token
    print("No UPSTOX_ACCESS_TOKEN found in .env or environment.")
    choice = input("Choose [1] paste current access token, [2] OAuth browser flow, [q] quit: ").strip().lower()
    if choice == "1":
        token = input("Paste access token (used only for this run): ").strip()
        if token:
            return token
        raise AuthenticationError("No access token supplied")
    if choice != "2":
        raise AuthenticationError("Upstox access token is required")
    state = secrets.token_urlsafe(16)
    url = authorisation_url(credentials, state)
    print("Opening Upstox login in your browser. Complete login there; this program never asks for credentials, PIN, or OTP.")
    print(url)
    webbrowser.open(url)
    returned = input("Paste the full redirect URL (or just its code parameter): ")
    return exchange_code(credentials, extract_authorisation_code(returned))
