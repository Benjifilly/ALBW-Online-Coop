"""Read the verified EUR task mode; title demos can also own a live Player."""
from src.rpc.azahar_bridge import TITLE_ID_EUR


# TaskMainGame stores 0; TaskFileSelect stores 2 before its stage/demo starts.
# The manager starts at FE and TaskStaffroll stores 3. These instructions and
# the manager type belong to the inspected EUR executable, not guessed offsets.
SCENE_FINGERPRINTS = {
    0x1E31FC: bytes.fromhex('001080e51c109fe5001091e5'),
    0x29D5E4: bytes.fromhex('000098e50210a0e3'),
}
SCENE_SINGLETON = 0x712468
SCENE_VTABLE = 0x6EF5D8


class GameplayStateReader:
    def __init__(self, bridge):
        self.bridge = bridge
        self.identity = None
        self.supported = False

    def playable(self, bss_delta=0):
        b = self.bridge
        identity = b.attached_pid, b.attached_title_id, bss_delta
        if identity != self.identity:
            self.identity = identity
            self.supported = (b.attached_title_id == TITLE_ID_EUR and bss_delta == 0
                and all(b.read_memory(at, len(data)) == data
                        for at, data in SCENE_FINGERPRINTS.items()))
        if not self.supported:
            return None
        manager = b.read_u32(SCENE_SINGLETON)
        if type(manager) is not int or manager % 4 or not 0x08000000 <= manager < 0x1F000000:
            return False
        return (b.read_u32(manager) == SCENE_VTABLE
                and b.read_u8(manager + 0x25) == 0
                and b.read_u32(SCENE_SINGLETON) == manager)
