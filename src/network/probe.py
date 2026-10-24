"""Ask a co-op host whether it is reachable, without joining its room."""
import json
import secrets
import socket
import time

from src.network.coop_node import DEFAULT_COOP_UDP_PORT, MAX_PLAYERS, PROBE_PAD, PROTOCOL_VERSION
from src.network import rendezvous
from src.version import VERSION,mismatch

NO_HOST='L’hôte ne répond pas : vérifiez son code de salon, ou envoyez-lui votre code de réponse.'


def parse_address(text, default_port=DEFAULT_COOP_UDP_PORT):
    """'192.168.1.24', '192.168.1.24:45990' or '[2001:db8::1]:45990' -> (ip, port); None if invalid."""
    found = rendezvous.parse_target(text, default_port)
    return found[0] if found and not (text or "").strip().upper().startswith(rendezvous.PREFIX) else None


def _socket():
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", 0))
        return sock, True
    except (OSError, AttributeError):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return sock, False


def probe(host, port, tries=3, timeout=.8):
    """dict(ms, count, max, compatible) from the first answer, or None."""
    found = probe_any([(host, port)], tries, timeout)
    return found[0] if found else None


def probe_any(candidates, tries=3, timeout=.8):
    """(answer, endpoint) from the first address of a room code that answers, or None.

    A box that filters until it is punched does not answer here: the companion
    then tries anyway and shows the reply code for the host.
    """
    targets = []
    for value in candidates:
        try:targets.append(rendezvous.endpoint(*value))
        except (ValueError, TypeError):continue
    sock, dual = _socket()
    with sock:
        def address(target):
            host, port = target
            if ":" in host:return (host, port, 0, 0) if dual else None
            return ("::ffff:" + host, port, 0, 0) if dual else (host, port)
        for _ in range(tries):
            ident = secrets.token_hex(8)
            started = time.monotonic()
            request = json.dumps(dict(type="PROBE", id=ident, pad="." * PROBE_PAD, v=PROTOCOL_VERSION)).encode("utf-8")
            for target in targets:
                if address(target):
                    try:sock.sendto(request, address(target))
                    except OSError:continue
            deadline = started + timeout
            while (left := deadline - time.monotonic()) > 0:
                sock.settimeout(max(.01, left))
                try:data, sender = sock.recvfrom(2048)
                except (socket.timeout, ConnectionResetError):continue
                except OSError:return None
                try:
                    reply = json.loads(data.decode("utf-8"));sender = rendezvous.endpoint(*sender[:2])
                except (UnicodeError, ValueError):
                    continue
                if (sender not in targets or not isinstance(reply, dict)
                        or reply.get("type") != "PROBE_REPLY" or reply.get("id") != ident):
                    continue
                count = reply.get("count")
                return dict(ms=round((time.monotonic() - started) * 1000),
                            count=count if type(count) is int and 0 < count <= MAX_PLAYERS else None,
                            max=MAX_PLAYERS, version=reply.get('build'),
                            detail=mismatch(reply.get('build')),
                            compatible=reply.get("v") == PROTOCOL_VERSION and reply.get('build')==VERSION), sender
    return None
