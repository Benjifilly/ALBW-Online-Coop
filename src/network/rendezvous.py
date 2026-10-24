"""Se rejoindre sans serveur : adresses candidates, STUN public et codes à copier.

Chaque PC demande à un serveur STUN public l'adresse qu'Internet voit pour le
socket même du salon. Le code de salon de l'hôte liste ses chemins possibles
(IPv6, IPv4 publique, UPnP, réseau local) ; l'ami les essaie tous à la fois.
Si la box de l'hôte filtre, l'ami lui renvoie un code de réponse et les deux
PC s'envoient des paquets en même temps : chaque box croit à une réponse et
laisse passer (perçage de NAT). Sans relais, deux NAT symétriques (certains
réseaux mobiles en IPv4) restent injoignables ; l'IPv6 évite le plus souvent
ce cas, car il n'y a pas de traduction d'adresse.
"""
import base64
import ipaddress
import os
import socket
import struct
import zlib

STUN_SERVERS = (("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478))
STUN_MAGIC = 0x2112A442
STUN_RETRY = 1.5          # until the first answers
STUN_REFRESH = 20         # keeps the box's mapping alive while nobody plays yet
HOST, REPLY = "H", "R"
PREFIX = "ALBW-"
MAX_CANDIDATES = 6


def endpoint(host, port):
    """(ip, port) as the room compares them: IPv4-mapped back to IPv4, no IPv6 scope."""
    ip = ipaddress.ip_address(str(host).split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip), int(port)


def format_endpoint(value):
    host, port = value
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def shareable(host):
    """An address worth putting in a code: not loopback, link-local or multicast."""
    ip = ipaddress.ip_address(host)
    return not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or ip.version == 4 and str(ip) == "255.255.255.255")


def encode(kind, candidates):
    """Code to copy: kind, then each distinct address and port, then a checksum."""
    body = bytearray(kind.encode("ascii"));seen = []
    for value in candidates:
        try:
            key = endpoint(*value)
            if key in seen or not shareable(key[0]) or not 1 <= key[1] <= 65535:continue
        except (ValueError, TypeError):
            continue
        ip = ipaddress.ip_address(key[0])
        body += bytes([ip.version]) + ip.packed + struct.pack(">H", key[1]);seen.append(key)
        if len(seen) == MAX_CANDIDATES:break
    if not seen:return ""
    body += struct.pack(">I", zlib.crc32(body))[:3]
    return PREFIX + base64.b32encode(bytes(body)).decode("ascii").rstrip("=")


def decode(text, kind=None):
    """[(ip, port), …] of a code; ValueError with a readable reason otherwise."""
    raw = "".join(str(text or "").split()).upper()
    if not raw.startswith(PREFIX):raise ValueError("Ce n’est pas un code ALBW.")
    raw = raw[len(PREFIX):]
    try:data = base64.b32decode(raw + "=" * (-len(raw) % 8))
    except (ValueError, TypeError):raise ValueError("Code incomplet : recopiez-le en entier.") from None
    if len(data) < 4 or struct.pack(">I", zlib.crc32(data[:-3]))[:3] != data[-3:]:
        raise ValueError("Code incomplet : recopiez-le en entier.")
    found, body, at = chr(data[0]), data[:-3], 1
    if kind and found != kind:
        raise ValueError("C’est un code de réponse : l’hôte le colle dans son menu Salon."
                         if found == REPLY else "C’est un code de salon : collez-le dans « Rejoindre ».")
    result = []
    while at < len(body):
        size = {4: 4, 6: 16}.get(body[at])
        if size is None or at + 3 + size > len(body):raise ValueError("Code endommagé.")
        host = str(ipaddress.ip_address(body[at + 1:at + 1 + size]))
        result.append((host, struct.unpack_from(">H", body, at + 1 + size)[0]));at += 3 + size
    if not result or len(result) > MAX_CANDIDATES:raise ValueError("Code endommagé.")
    return result


def parse_target(text, default_port):
    """A code, '[v6]:port', 'v6', 'v4:port' or 'v4' -> [(ip, port), …]; None if invalid."""
    text = (text or "").strip()
    if text.upper().startswith(PREFIX):
        try:return decode(text, HOST)
        except ValueError:return None
    host, port = text, default_port
    if text.startswith("["):
        if "]" not in text:return None
        host, _, rest = text[1:].partition("]")
        if rest:
            if not rest.startswith(":"):return None
            port = rest[1:]
    elif text.count(":") == 1:
        host, _, port = text.partition(":")
    try:
        key = endpoint(host, int(port))
        ip = ipaddress.ip_address(key[0])
    except (ValueError, TypeError):
        return None
    if ip.is_multicast or ip.is_unspecified or str(ip) == "255.255.255.255" or not 1 <= key[1] <= 65535:
        return None
    return [key]


def stun_request():
    ident = os.urandom(12)
    return ident, struct.pack(">HHI12s", 0x0001, 0, STUN_MAGIC, ident)


def is_stun(data):
    return len(data) >= 20 and data[0] & 0xC0 == 0 and struct.unpack_from(">I", data, 4)[0] == STUN_MAGIC


def stun_response(data, ident):
    """(ip, port) mapped by a Binding success response to this transaction, else None."""
    if not is_stun(data):return None
    kind, length, _, found = struct.unpack_from(">HHI12s", data)
    if kind != 0x0101 or found != ident or 20 + length > len(data):return None
    at, plain = 20, None
    while at + 4 <= 20 + length:
        atype, alen = struct.unpack_from(">HH", data, at);value = data[at + 4:at + 4 + alen]
        if len(value) >= 8 and value[1] in (1, 2):
            port = struct.unpack_from(">H", value, 2)[0]
            raw = value[4:8] if value[1] == 1 else value[4:20]
            if atype in (0x0020, 0x8020) and len(raw) in (4, 16):
                key = struct.pack(">I", STUN_MAGIC) + ident
                return str(ipaddress.ip_address(bytes(a ^ b for a, b in zip(raw, key)))), port ^ (STUN_MAGIC >> 16)
            if atype == 0x0001 and len(raw) in (4, 16):
                plain = str(ipaddress.ip_address(raw)), port
        at += 4 + alen + (-alen % 4)
    return plain


def resolve_servers(servers=STUN_SERVERS):
    """One IPv4 and one IPv6 address per public STUN server (DNS, may block)."""
    targets = []
    for host, port in servers:
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                info = socket.getaddrinfo(host, port, family, socket.SOCK_DGRAM)
            except OSError:
                continue
            if info:targets.append((f"{host} (IPv{4 if family == socket.AF_INET else 6})", endpoint(*info[0][4][:2])))
    return targets


class PublicAddress:
    """STUN through the room's own socket: the mapping a friend must reach."""

    def __init__(self):
        self.targets = None;self.pending = {};self.mapped = {};self.next = 0;self.log_note = ""

    def tick(self, now, send):
        if not self.targets or now < self.next:return
        self.next = now + (STUN_REFRESH if self.mapped else STUN_RETRY)
        self.pending = {}
        for name, target in self.targets:
            ident, request = stun_request();self.pending[ident] = name
            send(request, target)

    def receive(self, data):
        if len(data) < 20:return False
        ident = data[8:20];name = self.pending.get(ident)
        found = name and stun_response(data, ident)
        if not found:return False
        self.pending.pop(ident, None);self.mapped[name] = endpoint(*found)
        return True

    def candidates(self):
        # IPv6 first, then a stable order: the room code changes only when an address does.
        return sorted(set(self.mapped.values()), key=lambda value: (":" not in value[0], value))

    def symmetric(self):
        """Two IPv4 servers saw different ports: punching cannot predict this box."""
        seen = {value for name, value in self.mapped.items() if "IPv4" in name}
        return len({value[0] for value in seen}) == 1 and len(seen) > 1
