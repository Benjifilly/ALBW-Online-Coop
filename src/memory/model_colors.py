"""Reversible cosmetic clothing animation override for the verified EUR build.

This changes only the visual animation frame. Inventory and armor resistance are
not touched. Applying this executable patch requires an explicitly approved test.
"""
import struct
from src.rpc.azahar_bridge import TITLE_ID_EUR

COLOR_ROUTINE = 0x001DCA48
ORIGINAL_COLOR_CODE = bytes.fromhex(
    "b00494e5020050e30400000a030050e3480ab00e0100000a040050e3b30a9f0d")
COLOR_FRAMES = {"green": 0.0, "blue": 1.0, "red": 2.0, "purple": 1.0}


def color_instructions(color):
    if color not in COLOR_FRAMES:
        raise ValueError("Couleur inconnue")
    # VFPv2 literal load and branch; the frame constant is skipped as data.
    return struct.pack("<7If", 0xED9F0A05, 0xEA000005,
        *([0xE320F000] * 5), COLOR_FRAMES[color])


class ModelColorPatch:
    def __init__(self, bridge):
        self.bridge = bridge
        self.applied = None
        self.pid = None

    def apply(self, color):
        if self.bridge.attached_title_id != TITLE_ID_EUR:
            return False
        replacement = color_instructions(color)
        pid = self.bridge.attached_pid
        if pid is None:
            return False
        current = self.bridge.read_memory(COLOR_ROUTINE, len(ORIGINAL_COLOR_CODE))
        # A restarted game must pass the original fingerprint again.
        expected = self.applied if pid == self.pid and self.applied else ORIGINAL_COLOR_CODE
        if current != expected:
            return False
        if current == replacement:
            return True
        # Remember the intended patch before sending: a lost ACK may still mean
        # the write happened, so restore() must handle this case too.
        self.pid, self.applied = pid, replacement
        return (self.bridge.write_memory(COLOR_ROUTINE, replacement)
                and self.bridge.read_memory(COLOR_ROUTINE, len(replacement)) == replacement)

    def restore(self):
        if self.applied is None:
            return True
        if self.bridge.attached_title_id != TITLE_ID_EUR or self.bridge.attached_pid != self.pid:
            return False
        current = self.bridge.read_memory(COLOR_ROUTINE, len(ORIGINAL_COLOR_CODE))
        if current == ORIGINAL_COLOR_CODE:
            self.applied = None
            return True
        if current != self.applied:
            return False
        if (self.bridge.write_memory(COLOR_ROUTINE, ORIGINAL_COLOR_CODE)
                and self.bridge.read_memory(COLOR_ROUTINE, len(ORIGINAL_COLOR_CODE)) == ORIGINAL_COLOR_CODE):
            self.applied = None
            return True
        return False
