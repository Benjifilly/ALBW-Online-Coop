"""A private RPC port for each verified portable Azahar 2126.2 copy.

Upstream hard-codes 45987 in UDPServer::Impl. This changes only its immediate
argument to WS2_32!htons (ordinal 9), identified in this exact Windows build.
The installed emulator is never patched; an original portable copy is retained.
"""
import hashlib
import json
from pathlib import Path
import struct

BUILD_SHA256='e6d76f0bdf35a935e128265100a6db39ff559f1e14c5af6520175be65f89e84e'
PORT_OFFSET=0x636149
ORIGINAL_PORT=45987
CALL_SIGNATURE=bytes.fromhex('ff15c571b6006689442442')
LAB=Path(__file__).resolve().parents[2]/'.local/coop-lab'


def patch_bytes(original,port):
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError('Port RPC local invalide')
    end=PORT_OFFSET+4+len(CALL_SIGNATURE)
    if (len(original)<end or original[PORT_OFFSET-1]!=0xB9
            or original[PORT_OFFSET+4:end]!=CALL_SIGNATURE):
        raise ValueError('Version Azahar non reconnue : liaison GDB conservée')
    normalized=bytearray(original)
    struct.pack_into('<I',normalized,PORT_OFFSET,ORIGINAL_PORT)
    if hashlib.sha256(normalized).hexdigest()!=BUILD_SHA256:
        raise ValueError('Le programme Azahar ne correspond pas à la version vérifiée')
    struct.pack_into('<I',normalized,PORT_OFFSET,port)
    return bytes(normalized)


def prepare_port(executable,port,managed_root=LAB):
    executable=Path(executable).resolve()
    if executable.name.lower()!='azahar.exe' or not executable.is_relative_to(Path(managed_root).resolve()):
        raise ValueError('Seules les copies portables gérées peuvent recevoir un port privé')
    original=executable.read_bytes()
    patched=patch_bytes(original,port)
    backup=executable.with_name('azahar.rpc-original.exe')
    if not backup.exists():
        source=patch_bytes(original,ORIGINAL_PORT)
        backup.write_bytes(source)
    if patched!=original:
        temporary=executable.with_name('azahar.rpc-patch.tmp')
        temporary.write_bytes(patched)
        temporary.replace(executable)
    (executable.parent/'rpc-port.json').write_text(json.dumps(dict(port=port,
        source_sha256=BUILD_SHA256,patched_sha256=hashlib.sha256(patched).hexdigest()),indent=2))


def configured_port(executable):
    try:
        path=Path(executable)
        data=json.loads((path.parent/'rpc-port.json').read_text())
        port=data['port']
        binary=path.read_bytes()
        return port if patch_bytes(binary,port)==binary else None
    except (OSError,ValueError,TypeError,KeyError):
        return None
