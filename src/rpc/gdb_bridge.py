"""Memory transport for isolated local Azahar instances on distinct GDB ports.

Only use with the launcher-provided title ID and executable name from the ROM.
Azahar's GDB process list does not include the title ID, unlike RPC.
"""
import socket
import time
import xml.etree.ElementTree as ET
from src.rpc.azahar_bridge import AzaharBridge, SUPPORTED_TITLE_IDS, TITLE_ID_EUR


class GDBBridge(AzaharBridge):
    def __init__(self, port, title_id, process_name, address="127.0.0.1", timeout=.8):
        if title_id not in SUPPORTED_TITLE_IDS or not process_name or len(process_name) > 8:
            raise ValueError("Un jeu ALBW reconnu est nécessaire pour la liaison locale.")
        super().__init__(address, port, timeout, retries=0)
        self.socket.close()
        self.socket = None
        self.expected_title_id, self.process_name = title_id, process_name
        self._buffer = bytearray()
        self.max_data_size = 2048

    @staticmethod
    def _packet(command):
        body = command.encode("ascii")
        return b"$" + body + b"#" + f"{sum(body) & 255:02x}".encode("ascii")

    def _disconnect(self):
        if self.socket:
            self.socket.close()
        self.socket = None
        self._buffer.clear()
        self.rpc_connected = False
        self.detection_status = "rpc_unavailable"
        self.detach()

    def _exchange(self, command, reply=True):
        with self._lock:
            try:
                if self.socket is None:
                    return None
                self.socket.sendall(self._packet(command))
                if not reply:
                    return b""
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    # ACKs and any asynchronous stop notifications are framed separately.
                    start = self._buffer.find(b"$")
                    if start >= 0:
                        end = self._buffer.find(b"#", start + 1)
                        if end >= 0 and len(self._buffer) >= end + 3:
                            body = bytes(self._buffer[start + 1:end])
                            checksum = bytes(self._buffer[end + 1:end + 3])
                            del self._buffer[:end + 3]
                            if (sum(body) & 255) != int(checksum, 16):
                                self.socket.sendall(b"-")
                                continue
                            self.socket.sendall(b"+")
                            decoded = bytearray()
                            index = 0
                            while index < len(body):
                                if body[index] == 125:
                                    index += 1
                                    decoded.append(body[index] ^ 32)
                                else:
                                    decoded.append(body[index])
                                index += 1
                            return bytes(decoded)
                    elif self._buffer:
                        if b"-" in self._buffer:
                            self.socket.sendall(self._packet(command))
                        self._buffer.clear()
                    if len(self._buffer) > 65536:
                        raise ValueError("Réponse GDB trop longue")
                    self.socket.settimeout(max(.01, deadline - time.monotonic()))
                    data = self.socket.recv(16384)
                    if not data:
                        raise ConnectionError("Azahar fermé")
                    self._buffer.extend(data)
            except (OSError, ValueError, IndexError):
                pass
            self._disconnect()
            return None

    def auto_attach_albw(self, force=False):
        with self._lock:
            now = time.monotonic()
            if not force and now < self._next_probe:
                return self.attached_pid is not None
            self._next_probe = now + 1
            try:
                if self.socket is None:
                    self.socket = socket.create_connection((self.address, self.port), self.timeout)
                    self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    if self._exchange("!") != b"OK":
                        self._disconnect()
                        return False
                payload = self._exchange("qXfer:osdata:read:processes:0,4000")
                if not payload or payload[:1] != b"l":
                    self._disconnect()
                    return False
                root = ET.fromstring(payload[1:])
                processes = []
                for item in root.findall("item"):
                    columns = {c.get("name"): c.text for c in item.findall("column")}
                    if columns.get("command") == self.process_name:
                        processes.append(int(columns["pid"]))
                self.rpc_connected = True
                if len(processes) != 1:
                    self.detach()
                    self.detection_status = "other_game" if root.findall("item") else "waiting_game"
                    return False
                pid = processes[0]
                if self.attached_pid != pid:
                    answer = self._exchange(f"vAttach;{pid:x}")
                    if not answer or answer[:1] not in (b"T", b"S"):
                        self._disconnect()
                        return False
                    # Attaching stops the selected process. Resume it immediately;
                    # later memory queries do not interrupt gameplay.
                    self._exchange("c", reply=False)
                    self.attached_pid, self.attached_title_id = pid, self.expected_title_id
                    self.region = "EUR" if self.expected_title_id == TITLE_ID_EUR else "USA"
                self.detection_status = "albw_detected"
                return True
            except (OSError, ValueError, ET.ParseError):
                self._disconnect()
                return False

    def read_memory(self, address, size):
        if self.attached_pid is None or size < 0:
            return None
        result = bytearray()
        for offset in range(0, size, self.max_data_size):
            length = min(self.max_data_size, size - offset)
            reply = self._exchange(f"m{address + offset:x},{length:x}")
            if reply is None or len(reply) != length * 2:
                return None
            try:
                result.extend(bytes.fromhex(reply.decode("ascii")))
            except (ValueError, UnicodeError):
                return None
        return bytes(result)

    def write_memory(self, address, data):
        if self.attached_pid is None:
            return False
        for offset in range(0, len(data), self.max_data_size):
            chunk = data[offset:offset + self.max_data_size]
            if self._exchange(f"M{address + offset:x},{len(chunk):x}:{chunk.hex()}") != b"OK":
                return False
        return True

    def close(self):
        with self._lock:
            if self.socket:
                self._exchange("D")  # Detach, never terminate the emulator.
            self._disconnect()
