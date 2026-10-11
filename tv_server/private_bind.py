"""Validate an explicit RFC1918 IPv4 listener for host-side LAN services.

This applies to host port publishing and the macOS launch agent. Docker's
internal socket still listens on 0.0.0.0 *inside* the container; only the
host-published address is restricted.
"""
from __future__ import annotations

import ipaddress
import sys

RFC1918 = tuple(map(ipaddress.IPv4Network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
)))


def private_bind_ip(value: str) -> str:
    """Return a canonical private IPv4 address, or fail closed."""
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError("Specify the Mac/NAS RFC1918 LAN IPv4 address") from exc
    if not any(address in network for network in RFC1918):
        raise ValueError("Only RFC1918 LAN IPv4 is permitted (no public, wildcard or loopback IP)")
    return str(address)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1:
            raise ValueError("Usage: python -m tv_server.private_bind 192.168.x.x")
        print(private_bind_ip(args[0]))
    except ValueError as exc:
        print(f"DiscForge LAN bind: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
