"""Targets must be on private, link-local or Tailscale space. Never the public Internet."""
from __future__ import annotations

import ipaddress
import socket

# Explicit allowlist: ipaddress.is_private also accepts documentation/benchmark ranges, so don't use it.
ALLOWED = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8",
    "100.64.0.0/10",  # Tailscale CGNAT
    "fc00::/7", "fe80::/10", "::1/128")]


class NetworkPolicyError(ValueError):
    pass


def _ok(ip: ipaddress._BaseAddress) -> bool:
    return any(ip.version == n.version and ip in n for n in ALLOWED)


def assert_private_address(host: str) -> None:
    """Reject any host that is, or resolves to, a publicly routable address."""
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [ipaddress.ip_address(i[4][0]) for i in socket.getaddrinfo(host, None)]
        except OSError as e:
            raise NetworkPolicyError(f"cannot resolve {host!r}") from e
    if not addrs:
        raise NetworkPolicyError("no address")
    for ip in addrs:
        if not _ok(ip):
            raise NetworkPolicyError(f"{ip} is a public address; only LAN, link-local (Ethernet) or Tailscale targets are allowed")


_OVERLAY = ipaddress.ip_network("100.64.0.0/10")


def classify_path(host: str) -> str:
    """How traffic to this host travels: loopback | lan | link-local | overlay (Tailscale/WireGuard) | blocked. Resolves names (e.g. MagicDNS)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.getaddrinfo(host, None)[0][4][0])
        except (OSError, IndexError):
            return "unresolvable"
    if not _ok(ip):
        return "blocked"
    if ip.is_loopback:
        return "loopback"
    if ip.version == 4 and ip in _OVERLAY:
        return "overlay"
    if ip.is_link_local:
        return "link-local"
    return "lan"
