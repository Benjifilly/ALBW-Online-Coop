"""Read the EUR outdoor camera scroll, without timers or game-state writes."""
from src.rpc.azahar_bridge import TITLE_ID_EUR
from src.memory.render_state import _heap

SCROLL_STATE=0x5F
SCROLL_FINGERPRINTS={
    # Action controller +42 indexes the native Player-state table.
    0x53F604:bytes.fromhex('f214d4e198209fe50122d2e7000052e3'),
    # State_ZeldaScroll is registered at table index95 (16-byte records).
    0x5C9570:bytes.fromhex('240a1fe5ca1f8fe2f845a0e5041080e5'),
}


class RoomScrollReader:
    def __init__(self,bridge):
        self.bridge=bridge;self.identity=None;self.supported=False

    def active(self,player):
        b=self.bridge;identity=(b.attached_pid,b.attached_title_id)
        if identity!=self.identity:
            self.identity=identity
            self.supported=(b.attached_title_id==TITLE_ID_EUR and all(
                b.read_memory(at,len(code))==code for at,code in SCROLL_FINGERPRINTS.items()))
        if not self.supported or not _heap(player):return False
        action=b.read_u32(player+0x614)
        if not _heap(action) or b.read_u32(action+0x10)!=player:return False
        state=b.read_u16(action+0x42)
        return (state==SCROLL_STATE and b.read_u32(player+0x614)==action
                and b.read_u32(action+0x10)==player)
