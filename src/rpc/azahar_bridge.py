import enum
import random
import socket
import struct
import threading
import time
from typing import Dict, Optional, Tuple

CURRENT_REQUEST_VERSION = 2
MAX_REQUEST_DATA_SIZE = 32 * 1024
HEADER_SIZE = 0x10
MAX_PACKET_SIZE = MAX_REQUEST_DATA_SIZE + HEADER_SIZE
DEFAULT_AZAHAR_PORT = 45987

TITLE_ID_USA = 0x00040000000EC300
TITLE_ID_EUR = 0x00040000000EC400
SUPPORTED_TITLE_IDS = {
    TITLE_ID_USA: "USA (00040000000EC300)",
    TITLE_ID_EUR: "EUR (00040000000EC400)",
}


class RequestType(enum.IntEnum):
    ReadMemory = 1
    WriteMemory = 2
    ProcessList = 3
    SetGetProcess = 4
    TakeScreenshot = 5
    ReadScreenshot = 6
    GetPerfStats = 7


class AzaharBridge:
    """
    UDP RPC bridge using Azahar's version 2 process-selection protocol.
    Provides typed memory reads/writes and automatic attachment to A Link Between Worlds.
    """

    def __init__(self, address: str = "127.0.0.1", port: int = DEFAULT_AZAHAR_PORT, timeout: float = 0.25, retries: int = 2):
        self.address = address
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.retry_backoff = 1.0
        self.native_recovery_timeout = 15.0
        self.version = CURRENT_REQUEST_VERSION
        self.max_data_size = MAX_REQUEST_DATA_SIZE
        self.attached_pid: Optional[int] = None
        self.attached_title_id: Optional[int] = None
        self.region: Optional[str] = None
        self.rpc_connected = False
        self.detection_status = "rpc_unavailable"
        self.last_failure = ""
        self._next_probe = 0.0
        self._lock = threading.RLock()

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for option in (socket.SO_SNDBUF, socket.SO_RCVBUF):
            try:
                if self.socket.getsockopt(socket.SOL_SOCKET, option) < MAX_PACKET_SIZE * 2:
                    self.socket.setsockopt(socket.SOL_SOCKET, option, MAX_PACKET_SIZE * 2)
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            self.socket.close()
            self.rpc_connected = False
            self.detach()

    def detach(self):
        self.attached_pid = self.attached_title_id = self.region = None

    def _generate_header(self, request_type: int, data_size: int) -> Tuple[bytes, int]:
        request_id = random.getrandbits(32)
        header = struct.pack("<IIII", self.version, request_id, int(request_type), data_size)
        return header, request_id

    def _validate_reply(self, raw_reply: bytes, expected_id: int, expected_type: int) -> Optional[bytes]:
        if len(raw_reply) < HEADER_SIZE:
            return None
        reply_version, reply_id, reply_type, reply_data_size = struct.unpack("<IIII", raw_reply[:HEADER_SIZE])
        if (reply_version == self.version and expected_id == reply_id
                and expected_type == reply_type and reply_data_size == len(raw_reply) - HEADER_SIZE):
            return raw_reply[HEADER_SIZE:]
        return None

    def _request(self, request_type: RequestType, request_data: bytes) -> Optional[bytes]:
        # One request owns the socket until completion: UI/network workers cannot
        # consume each other's replies. Irrelevant packets cannot extend the deadline.
        with self._lock:
            header, request_id = self._generate_header(request_type, len(request_data))
            for attempt in range(self.retries + 1):
                try:
                    # Une ancienne préparation rejouée peut effacer un résultat natif.
                    if attempt==0 or request_type!=RequestType.WriteMemory:
                        self.socket.sendto(header + request_data, (self.address, self.port))
                    deadline = time.monotonic() + self.timeout*self.retry_backoff**attempt
                    while (remaining := deadline - time.monotonic()) > 0:
                        self.socket.settimeout(remaining)
                        raw_reply, sender = self.socket.recvfrom(MAX_PACKET_SIZE)
                        if sender != (self.address, self.port):
                            continue
                        payload = self._validate_reply(raw_reply, request_id, request_type)
                        if payload is not None:
                            self.rpc_connected = True
                            return payload
                except (socket.timeout, ConnectionResetError, OSError):
                    continue
            failure=(f"{RequestType(request_type).name} sans réponse du port {self.port} "
                     f"({self.retries + 1} attentes de {self.timeout:g} s"
                     + (' à délai progressif)' if self.retry_backoff!=1 else ')'))
            # Une écriture peut être faite sans que son ACK nous parvienne.
            # Vérifier ses octets, jamais réécrire le paquet de préparation.
            if request_type==RequestType.WriteMemory and self.attached_pid is not None and len(request_data)>=8:
                address,size=struct.unpack_from('<II',request_data);pid=self.attached_pid
                if size==len(request_data)-8:
                    observed=self._request(RequestType.ReadMemory,struct.pack('<II',address,size))
                    if self.attached_pid==pid and observed==request_data[8:]:return b''
            self.rpc_connected = False
            self.detection_status = "rpc_unavailable"
            self.last_failure = failure
            self.detach()
            return None

    def process_list(self) -> Dict[int, Tuple[int, str]]:
        processes: Dict[int, Tuple[int, str]] = {}
        reply = self._request(RequestType.ProcessList, struct.pack("<II", 0, 64))
        if not reply or len(reply) < 4:
            if reply is not None:
                self.detection_status = "rpc_old" if reply == b"" and self._speaks_version_1() else "rpc_incompatible"
            return processes
        count = struct.unpack("<I", reply[:4])[0]
        body = reply[4:]
        if len(body) != count * 0x14:
            self.detection_status = "rpc_incompatible"
            return processes
        for i in range(count):
            chunk = body[i * 0x14 : (i + 1) * 0x14]
            if len(chunk) < 0x14:
                break
            proc_id, title_id, proc_name_raw = struct.unpack("<IQ8s", chunk)
            proc_name = proc_name_raw.rstrip(b"\x00").decode("ascii", errors="replace")
            processes[proc_id] = (title_id, proc_name)
        return processes

    def _speaks_version_1(self) -> bool:
        """Azahar 2125 and older speak RPC version 1 and answer every version 2
        request with an empty packet (25/10: « Serveur RPC incompatible » on a
        friend's PC). A well-formed version 1 list proves it; the companion
        itself keeps version 2, the protocol it was verified with."""
        self.version = 1
        try:
            reply = self._request(RequestType.ProcessList, struct.pack("<II", 0, 64))
        finally:
            self.version = CURRENT_REQUEST_VERSION
        return (reply is not None and len(reply) >= 4
                and len(reply) - 4 == struct.unpack_from("<I", reply)[0] * 0x14)

    def set_process(self, process_id: int) -> bool:
        reply = self._request(RequestType.SetGetProcess, struct.pack("<II", 1, process_id))
        return reply is not None

    def auto_attach_albw(self, force=False) -> bool:
        """Finds A Link Between Worlds (EUR or USA) in Azahar's running processes and binds to it."""
        now = time.monotonic()
        if not force and now < self._next_probe:
            return self.attached_pid is not None
        self._next_probe = now + 1.0
        self.detection_status = "rpc_unavailable"
        procs = self.process_list()
        if not procs:
            self.detach()
            if self.rpc_connected and self.detection_status not in ("rpc_incompatible", "rpc_old"):
                self.detection_status = "waiting_game"
            return False

        for pid, (title_id, name) in procs.items():
            if title_id in SUPPORTED_TITLE_IDS:
                if self.set_process(pid):
                    self.attached_pid = pid
                    self.attached_title_id = title_id
                    self.region = "EUR" if title_id == TITLE_ID_EUR else "USA"
                    self.detection_status = "albw_detected"
                    return True
        self.detach()
        if self.rpc_connected:
            self.detection_status = "other_game"
        return False

    def read_memory(self, address: int, size: int) -> Optional[bytes]:
        if self.attached_pid is None:
            return None
        result = bytearray()
        cur_addr = address
        remaining = size
        while remaining > 0:
            chunk_size = min(remaining, self.max_data_size)
            reply = self._request(RequestType.ReadMemory, struct.pack("<II", cur_addr, chunk_size))
            if reply is None or len(reply) != chunk_size:
                return None
            result.extend(reply)
            read_len = len(reply)
            remaining -= read_len
            cur_addr += read_len
        return bytes(result)

    def write_memory(self, address: int, data: bytes) -> bool:
        if self.attached_pid is None:
            return False
        cur_addr = address
        remaining_data = data
        while len(remaining_data) > 0:
            chunk = remaining_data[: self.max_data_size - 8]
            req = struct.pack("<II", cur_addr, len(chunk)) + chunk
            reply = self._request(RequestType.WriteMemory, req)
            if reply is None:
                return False
            cur_addr += len(chunk)
            remaining_data = remaining_data[len(chunk) :]
        return True

    def read_u8(self, address: int) -> Optional[int]:
        raw = self.read_memory(address, 1)
        return raw[0] if raw else None

    def read_u16(self, address: int) -> Optional[int]:
        raw = self.read_memory(address, 2)
        return struct.unpack("<H", raw)[0] if raw and len(raw) == 2 else None

    def read_u32(self, address: int) -> Optional[int]:
        raw = self.read_memory(address, 4)
        return struct.unpack("<I", raw)[0] if raw and len(raw) == 4 else None

    def read_f32(self, address: int) -> Optional[float]:
        raw = self.read_memory(address, 4)
        return struct.unpack("<f", raw)[0] if raw and len(raw) == 4 else None

    def read_vec3(self, address: int) -> Optional[Tuple[float, float, float]]:
        raw = self.read_memory(address, 12)
        return struct.unpack("<fff", raw) if raw and len(raw) == 12 else None

    def write_u8(self, address: int, value: int) -> bool:
        return self.write_memory(address, struct.pack("<B", value & 0xFF))

    def write_u16(self, address: int, value: int) -> bool:
        return self.write_memory(address, struct.pack("<H", value & 0xFFFF))

    def write_u32(self, address: int, value: int) -> bool:
        return self.write_memory(address, struct.pack("<I", value & 0xFFFFFFFF))

    def write_vec3(self, address: int, x: float, y: float, z: float) -> bool:
        return self.write_memory(address, struct.pack("<fff", x, y, z))
