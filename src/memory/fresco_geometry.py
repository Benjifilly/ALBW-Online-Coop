"""Read-only EUR Fresco surface telemetry; never sends pointers or GPU commands.

The ordinary FlatLink model does not contain this projected wall geometry.
This reader is opt-in research telemetry, not part of normal network snapshots.
"""
import struct

from src.memory.render_state import _heap, _number


FRESCO_VTABLE = 0x6DBB00
MAX_TRIANGLES = 90
GEOMETRY_FINGERPRINT = bytes.fromhex('70402de90040a0e1cc0190e55f5f84e2')


def _matrix(values):
    return len(values) == 12 and all(_number(v, -1e6 if i % 4 == 3 else -8,
        1e6 if i % 4 == 3 else 8) for i, v in enumerate(values))


class FrescoGeometryReader:
    def __init__(self, bridge):
        self.bridge = bridge
        self.identity = None
        self.supported = False

    def _read(self, pointer, size):
        if not _heap(pointer) or size < 0 or pointer + size > 0x1F000000:
            return None
        if not size:
            return b''
        data = self.bridge.read_memory(pointer, size)
        return data if data is not None and len(data) == size else None

    def read(self, player):
        b = self.bridge
        identity = b.attached_pid, b.attached_title_id
        if identity != self.identity:
            self.identity = identity
            from src.rpc.azahar_bridge import TITLE_ID_EUR
            self.supported = (b.attached_title_id == TITLE_ID_EUR
                and b.read_memory(0x2C1634, 16) == GEOMETRY_FINGERPRINT)
        if not self.supported or not _heap(player):
            return None
        controller = b.read_u32(player + 0x604)
        wall = b.read_u32(controller + 0x28) if _heap(controller) else None
        model = b.read_u32(wall + 8) if _heap(wall) else None
        if not _heap(model) or b.read_u32(model) != FRESCO_VTABLE:
            return None
        uv_transform=None
        materials=b.read_u32(model+0x20)
        table=b.read_u32(materials) if _heap(materials) else None
        material_count=b.read_u32(materials+4) if _heap(materials) else None
        if _heap(materials) and (not _heap(table) or type(material_count) is not int or not 1<=material_count<=8):return None
        if _heap(table) and b.read_u32(table+0x140):
            mirror=b.read_f32(table+0xEC);offset=b.read_f32(table+0xF8)
            if (mirror,offset) not in ((1.,0.),(-1.,1.)):return None
            uv_transform=[mirror,offset]
        state = self._read(model + 0x178, 0xA8)
        if state is None:
            return None
        wrapper = struct.unpack_from('<I', state)[0]
        slot_index = struct.unpack_from('<I', state, 0x54)[0]
        if slot_index not in (0, 1):
            return None
        header = self._read(wrapper, 0x24)
        if header is None:
            return None
        words = struct.unpack('<9I', header)
        counts = words[0::3]
        if len(set(counts)) != 1 or counts[0] > MAX_TRIANGLES:
            return None
        count = counts[0]
        if words[1::3] != (MAX_TRIANGLES,) * 3:
            return None
        slot = struct.unpack_from('<10I', state, 4 + slot_index * 0x28)
        if (slot[0], slot[2], slot[4]) != (MAX_TRIANGLES,) * 3:
            return None
        if (words[2], words[5], words[8]) != (slot[1], slot[5], slot[3]):
            return None
        world = self._read(model + 0x80, 48)
        projection = state[0x78:0xA8]  # The draw routine uploads G3D+1F0.
        if world is None:
            return None
        world_values = list(struct.unpack('<12f', world))
        projection_values = list(struct.unpack('<12f', projection))
        if not _matrix(world_values) or not _matrix(projection_values):
            return None
        buffers = [self._read(pointer, count * stride)
                   for pointer, stride in zip(words[2::3], (36, 36, 24))]
        if any(data is None for data in buffers):
            return None
        values = [list(struct.unpack('<' + 'f' * (len(data) // 4), data)) for data in buffers]
        if any(not _number(value, -1e6, 1e6) for group in values for value in group):
            return None
        # The engine double-buffers this surface. Confirm the binding, matrices
        # and bytes a second time; this is best-effort consistency, not a pause.
        state2 = self._read(model + 0x178, 0xA8)
        header2 = self._read(wrapper, 0x24)
        consistent = (state2 == state and header2 == header)
        if not consistent and state2 is not None and header2 is not None:
            slot_idx2 = struct.unpack_from('<I', state2, 0x54)[0]
            words2 = struct.unpack('<9I', header2)
            if (slot_idx2 in (0, 1) and state2[:0x54] == state[:0x54] and state2[0x58:] == state[0x58:]
                    and words2[0::3] == words[0::3] and words2[1::3] == words[1::3]):
                slot2 = struct.unpack_from('<10I', state2, 4 + slot_idx2 * 0x28)
                if (words2[2], words2[5], words2[8]) == (slot2[1], slot2[5], slot2[3]):
                    consistent = True
        if (not consistent
                or self._read(model + 0x80, 48) != world
                or any(self._read(pointer, len(data)) != data
                       for pointer, data in zip(words[2::3], buffers))
                or b.read_u32(player + 0x604) != controller
                or b.read_u32(controller + 0x28) != wall or b.read_u32(wall + 8) != model
                or b.read_u32(model) != FRESCO_VTABLE):
            return None
        result=dict(triangle_count=count, matrix=world_values, projection_matrix=projection_values,
                    positions=values[0], vectors=values[1], uv=values[2])
        if uv_transform is not None:
            if [b.read_f32(table+0xEC),b.read_f32(table+0xF8)]!=uv_transform:return None
            result['uv_transform']=uv_transform
        return result
