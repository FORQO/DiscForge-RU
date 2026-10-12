#!/usr/bin/env python3
"""Smoke test a running DiscForge TV LAN server without exposing the pairing token.

Run from a trusted computer on the same LAN:
    python tv/tools/smoke_lan.py --url http://192.168.1.30:8098 --token-file ~/.discforge-tv/token

Only loopback and RFC1918 IPv4 addresses are accepted; redirects are NEVER followed.
This tool does not change the library or probe public internet hosts.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

TOKEN_HEADER = "X-DiscForge-Token"
MAX_CATALOG_BYTES = 2 * 1024 * 1024
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(v) for v in
                         ("127.0.0.0/8", "10.0.0.0/8",
                          "172.16.0.0/12", "192.168.0.0/16"))
ID_PATTERN = re.compile(r"^[a-f0-9]{24}$")


class SmokeFailure(RuntimeError):
    """The server did not meet a basic media playback/API invariant."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_base_url(raw: str) -> str:
    """Require literal private IPv4:port; never trust DNS redirects or public endpoints."""
    try:
        value = urlsplit(raw)
        if (value.scheme != "http" or value.username is not None or
                value.password is not None or value.path not in ("", "/") or
                value.query or value.fragment or value.hostname is None or
                value.port is None):
            raise ValueError("Expected http://PRIVATE_IPV4:PORT")
        ip = ipaddress.IPv4Address(value.hostname)
        if not any(ip in network for network in PRIVATE_NETWORKS):
            raise ValueError("Only private LAN or loopback IPv4 is accepted")
        if not 1 <= value.port <= 65535:
            raise ValueError("Bad TCP port")
    except (ValueError, AttributeError) as exc:
        raise SmokeFailure("Use a literal private IPv4 and port, e.g. http://192.168.1.30:8098") from exc
    return f"http://{ip}:{value.port}"


def fetch(base: str, token: str, path: str, *, method: str = "GET",
          headers: dict[str, str] | None = None, max_bytes: int = 4096):
    request_headers = {TOKEN_HEADER: token}
    request_headers.update(headers or {})
    req = Request(base + path, headers=request_headers, method=method)
    # Never leak the pairing token through HTTP(S)_PROXY from the shell environment.
    opener = build_opener(ProxyHandler({}), NoRedirects())
    try:
        with opener.open(req, timeout=5) as reply:
            return (reply.status, reply.headers,
                    reply.read(max_bytes + 1))
    except HTTPError as exc:
        # No redirect is followed, including 301/302/307/308 responses.
        return (exc.code, exc.headers, exc.read(min(1024, max_bytes + 1)))
    except (OSError, URLError, TimeoutError) as exc:
        raise SmokeFailure(f"LAN connection failed: {type(exc).__name__}") from exc


def expect(condition: bool, description: str) -> None:
    if not condition:
        raise SmokeFailure(description)


def run_smoke(base_url: str, token: str) -> list[str]:
    base = validate_base_url(base_url)
    if not (16 <= len(token) <= 256) or "\r" in token or "\n" in token:
        raise SmokeFailure("The token file contains an invalid pairing code")
    checks = []

    invalid_token = token[:-1] + ("X" if token[-1] != "X" else "Y")
    unauthorized, _, _ = fetch(base, invalid_token, "/api/v1/library")
    expect(unauthorized == 401, "Invalid pairing token must receive HTTP 401")
    checks.append("HTTP 401 rejects a wrong token")

    status, headers, payload = fetch(
        base, token, "/api/v1/library", max_bytes=MAX_CATALOG_BYTES)
    expect(status == 200, f"Expected authorized catalog HTTP 200, got {status}")
    expect(len(payload) <= MAX_CATALOG_BYTES, "Catalog exceeds smoke-test size limit (2 MiB)")
    try:
        catalog = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SmokeFailure("Catalog is not valid UTF-8 JSON") from exc
    expect(isinstance(catalog, dict) and isinstance(catalog.get("items"), list),
           "Catalog must contain an items array")
    checks.append(f"Authorized JSON catalog: {len(catalog['items'])} items")

    selected = None
    for item in catalog["items"]:
        if not isinstance(item, dict):
            continue
        media_id = item.get("id")
        size = item.get("size")
        if (isinstance(media_id, str) and ID_PATTERN.fullmatch(media_id) and
                type(size) is int and size > 0 and
                item.get("url") == f"/api/v1/media/{media_id}"):
            selected = (media_id, size)
            break
    if selected is None:
        checks.append("No nonempty MP4/MKV in catalog: Range/HEAD tests skipped")
        return checks

    media_id, size = selected
    media_path = f"/api/v1/media/{media_id}"
    status, headers, content = fetch(
        base, token, media_path, method="HEAD", max_bytes=0)
    expect(status == 200, f"HEAD media expected HTTP 200, got {status}")
    expect(len(content) == 0, "HEAD media returned a response body")
    expect(headers.get("Content-Length") == str(size),
           "HEAD media Content-Length does not match the catalog size")
    checks.append("HEAD media size agrees with catalog")

    status, headers, content = fetch(
        base, token, media_path, headers={"Range": "bytes=0-0"}, max_bytes=1)
    expect(status == 206, f"Range GET expected HTTP 206, got {status}")
    expect(headers.get("Content-Range") == f"bytes 0-0/{size}",
           "Invalid Content-Range response")
    expect(headers.get("Content-Length") == "1" and len(content) == 1,
           "Range GET should return exactly one byte")
    checks.append("HTTP 206: a single requested byte is returned")

    status, headers, _ = fetch(
        base, token, media_path,
        headers={"Range": f"bytes={size}-"}, max_bytes=0)
    expect(status == 416, f"Out-of-bounds Range must return HTTP 416, got {status}")
    expect(headers.get("Content-Range") == f"bytes */{size}",
           "HTTP 416 must report the current file size")
    checks.append("HTTP 416: unsatisfiable range is rejected")
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DiscForge TV: private LAN media smoke test")
    parser.add_argument("--url", required=True, help="http://PRIVATE_IPV4:8098")
    parser.add_argument("--token-file", required=True, type=Path,
                        help="Path to saved pairing secret; never pass the secret as an argument")
    args = parser.parse_args(argv)
    try:
        token = args.token_file.expanduser().read_text(encoding="utf-8").strip()
        checks = run_smoke(args.url, token)
    except (OSError, UnicodeError, SmokeFailure) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for check in checks:
        print("PASS:", check)
    print("OK:", len(checks), "LAN checks completed without printing credentials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
