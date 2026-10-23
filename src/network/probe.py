"""Ask a co-op host whether it is reachable, without joining its room."""
import ipaddress
import json
import secrets
import socket
import time

from src.network.coop_node import DEFAULT_COOP_UDP_PORT, MAX_PLAYERS, PROBE_PAD, PROTOCOL_VERSION


def parse_address(text, default_port=DEFAULT_COOP_UDP_PORT):
    """'192.168.1.24' or '192.168.1.24:45990' -> (ip, port); None if invalid."""
    host, _, port = (text or "").strip().partition(":")
    try:
        address = ipaddress.IPv4Address(host)
        port = int(port) if port else default_port
    except ValueError:
        return None
    if address.is_multicast or address.is_unspecified or str(address) == "255.255.255.255" or not 1 <= port <= 65535:
        return None
    return str(address), port


def probe(host, port, tries=3, timeout=.8):
    """dict(ms, count, max, compatible) from the first answer, or None."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        for _ in range(tries):
            ident = secrets.token_hex(8)
            started = time.monotonic()
            request = dict(type="PROBE", id=ident, pad="." * PROBE_PAD, v=PROTOCOL_VERSION)
            try:
                sock.sendto(json.dumps(request).encode("utf-8"), (host, port))
                deadline = started + timeout
                while time.monotonic() < deadline:
                    sock.settimeout(max(.01, deadline - time.monotonic()))
                    data, sender = sock.recvfrom(2048)
                    try:
                        reply = json.loads(data.decode("utf-8"))
                    except (UnicodeError, ValueError):
                        continue
                    if (sender != (host, port) or not isinstance(reply, dict)
                            or reply.get("type") != "PROBE_REPLY" or reply.get("id") != ident):
                        continue
                    count = reply.get("count")
                    return dict(ms=round((time.monotonic() - started) * 1000),
                                count=count if type(count) is int and 0 < count <= MAX_PLAYERS else None,
                                max=MAX_PLAYERS, compatible=reply.get("v") == PROTOCOL_VERSION)
            except (socket.timeout, ConnectionResetError):
                continue
            except OSError:
                return None
    return None
