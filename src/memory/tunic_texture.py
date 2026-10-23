"""A cosmetic violet variant of the game's blue ETC1A4 clothing texture.

Only blue base colours are changed. The alpha, detail selectors and all other
colour blocks remain intact. No Nintendo texture is included in the project.
The 3DS ETC1A4 layout is documented by the Azahar texture decoder.
"""
import struct
import hashlib

from src.rpc.azahar_bridge import TITLE_ID_EUR

BLUE_TEXTURE_SHA256 = '42a131e66607bb0d9433df44e3c0c01e32a0ed6a65f3cb0da9227e85d9fecc7a'


def _signed3(value):
    return value - 8 if value & 4 else value


def etc1_bases(word):
    if word & (1 << 33):
        first = [(word >> shift) & 31 for shift in (59, 51, 43)]
        second = [v + _signed3((word >> shift) & 7)
                  for v, shift in zip(first, (56, 48, 40))]
        return [tuple((v << 3) | (v >> 2) for v in rgb) for rgb in (first, second)]
    return [tuple(((word >> shift) & 15) * 17 for shift in shifts)
            for shifts in ((60, 52, 44), (56, 48, 40))]


def violet_texture(data):
    """Transform a complete ETC1A4 mip chain, retaining its byte length."""
    if not data or len(data) % 16:
        raise ValueError('Invalid ETC1A4 texture length')
    result = bytearray(data)
    for offset in range(8, len(data), 16):
        word = struct.unpack_from('<Q', data, offset)[0]
        original = etc1_bases(word)
        colours = []
        for r, g, b in original:
            colours.append((round(b * .82), round(g * .70), round(b * .96))
                           if b > r * 1.2 + 10 and b > g * 1.1 + 8 else (r, g, b))
        if colours == original:
            continue
        # Prefer the original differential precision where the pair still fits.
        q5 = [tuple(round(v * 31 / 255) for v in rgb) for rgb in colours]
        deltas = [b - a for a, b in zip(*q5)]
        word &= ~(((1 << 24) - 1) << 40)
        if word & (1 << 33) and all(-4 <= d <= 3 for d in deltas):
            for base, delta, shift in zip(q5[0], deltas, (56, 48, 40)):
                word |= ((base << 3) | (delta & 7)) << shift
        else:
            word &= ~(1 << 33)
            for rgb, shifts in zip(colours, ((60, 52, 44), (56, 48, 40))):
                for v, shift in zip(rgb, shifts):
                    word |= round(v / 17) << shift
        struct.pack_into('<Q', result, offset, word)
    return bytes(result)


def _heap(pointer):
    return pointer is not None and 0x08000000 <= pointer < 0x1F000000


def locate_blue_texture(bridge, resource=None):
    """Follow the live Link_Change texture table, never a fixed VRAM address."""
    if bridge.attached_title_id != TITLE_ID_EUR:
        return None
    pointer = resource
    if pointer is None:
        pointer = bridge.read_u32(0x70FB60)
        for offset in (0x10, 0x600, 0x18, 0x14, 4):
            if not _heap(pointer):
                return None
            pointer = bridge.read_u32(pointer + offset)
    if not _heap(pointer):
        return None
    resource = bridge.read_u32(pointer)
    if not _heap(resource):
        return None
    name = bridge.read_u32(resource)
    if not _heap(name) or bridge.read_memory(name, 12) != b'Link_Change\0':
        return None
    if bridge.read_u32(pointer + 0x14) != 3:
        return None
    table = bridge.read_u32(pointer + 0x10)
    if not _heap(table):
        return None
    texture = bridge.read_u32(table + 4)
    if not _heap(texture):
        return None
    name = bridge.read_u32(texture + 0x1C)
    if not _heap(name) or bridge.read_memory(name, 7) != b'main.1\0':
        return None
    command = bridge.read_u32(texture)
    if command is None or not 0x1F000000 <= command < 0x1F600000 - 48:
        return None
    blob = bridge.read_memory(command, 32)
    if not blob or len(blob) != 32:
        return None
    dim, h0, lod, h1, address, h2, fmt, h3 = struct.unpack('<8I', blob)
    if (h0, h1, h2, h3, fmt, dim, lod) != (
            0xF0082, 0x40084, 0xF0085, 0xF008E, 13, 0x800100, 0x40000):
        return None
    # PICA stores physical addresses divided by eight. Map only video RAM.
    physical = address << 3
    length = sum((256 >> level) * (128 >> level) for level in range(5))
    if not 0x18000000 <= physical <= 0x18600000 - length:
        return None
    return physical + 0x07000000, length


class TunicTexturePatch:
    def __init__(self, bridge):
        self.bridge = bridge
        self.saved = None

    def apply(self):
        location = locate_blue_texture(self.bridge)
        if location is None:
            return False
        address, length = location
        if self.saved:
            if (self.saved[0], self.saved[1], len(self.saved[2])) == (
                    self.bridge.attached_pid, address, length):
                return self.bridge.read_memory(address, length) == self.saved[3]
            # Old resources may have been released. Never write into a new owner.
            self.saved = None
        original = self.bridge.read_memory(address, length)
        if original is None or hashlib.sha256(original).hexdigest() != BLUE_TEXTURE_SHA256:
            return False
        replacement = violet_texture(original)
        self.saved = self.bridge.attached_pid, address, original, replacement
        return (self.bridge.write_memory(address, replacement)
                and self.bridge.read_memory(address, length) == replacement)

    def restore(self):
        if self.saved is None:
            return True
        pid, address, original, replacement = self.saved
        if self.bridge.attached_pid != pid or locate_blue_texture(self.bridge) != (address, len(original)):
            return False
        # Transport writes use chunks. A lost acknowledgement can leave a mix of
        # complete old/new chunks; restore those while preserving unknown changes.
        chunk_size = self.bridge.max_data_size
        blocks = []
        for offset in range(0, len(original), chunk_size):
            old, new = original[offset:offset+chunk_size], replacement[offset:offset+chunk_size]
            current = self.bridge.read_memory(address + offset, len(old))
            if current not in (old, new):
                return False
            if current != old:
                blocks.append((address + offset, old))
        for target, old in blocks:
            if not self.bridge.write_memory(target, old):
                return False
        if self.bridge.read_memory(address, len(original)) != original:
            return False
        self.saved = None
        return True


class TunicAppearance:
    """Combine the native cosmetic selector with a reversible violet texture."""
    def __init__(self, bridge):
        from src.memory.model_colors import ModelColorPatch
        self.code = ModelColorPatch(bridge)
        self.texture = TunicTexturePatch(bridge)

    def apply(self, colour):
        if colour == 'purple':
            if not self.texture.apply():
                self.restore()
                return False
        elif not self.texture.restore():
            return False
        if not self.code.apply(colour):
            self.restore()
            return False
        return True

    def restore(self):
        code_ok = self.code.restore()
        texture_ok = self.texture.restore()
        return code_ok and texture_ok
