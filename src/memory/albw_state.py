from dataclasses import dataclass, field
import struct
import math
from typing import Dict
from ..rpc.azahar_bridge import AzaharBridge, TITLE_ID_EUR

COURSE_NAMES: Dict[int, str] = {
    0: "Hyrule (Surface)",
    1: "Lorule (Surface)",
    2: "Intérieur (Hyrule)",
    3: "Intérieur (Lorule)",
    4: "Grotte / Sanctuaire (Hyrule)",
    5: "Grotte / Souterrain (Lorule)",
    6: "Palais de l'Est",
    7: "Dememeure des Vents",
    8: "Tour d'Héra",
    9: "Château d'Hyrule",
    10: "Palais des Ténèbres",
    11: "Palais des Marais",
    12: "Bois des Squelettes",
    13: "Repaire des Bandits",
    14: "Ruines de Glace",
    15: "Palais du Désert",
    16: "Rocher de la Tortue",
    17: "Château de Lorule",
    18: "Tour de l'Épreuve",
}


@dataclass
class RegionMemoryProfile:
    name: str
    saves_location: int
    events_location: int
    courses_location: int
    minigame_location: int
    game_location: int
    player_singleton_location: int
    fcram_rupees_addr: int
    fcram_hearts_addr: int
    actor_pos_offset: int = 0x28
    actor_yaw_offset: int = 0x38
    player_maiamai_offset: int = 0x508
    # Current and maximum HP are adjacent bytes (598/599), not 597/598.
    player_max_health_offset: int = 0x599
    player_health_offset: int = 0x598
    actor_position_verified: bool = False
    pose_read_supported: bool = False
    actor_yaw_binary: bool = False


PROFILE_USA = RegionMemoryProfile(
    name="USA (00040000000EC300)",
    saves_location=0x00711DE8,
    events_location=0x0070B728,
    courses_location=0x0070C8E0,
    minigame_location=0x0070D858,
    game_location=0x00709DF8,
    player_singleton_location=0x0070FB60,
    fcram_rupees_addr=0x168B5FA8,
    fcram_hearts_addr=0x168B6044,
)

PROFILE_EUR = RegionMemoryProfile(
    name="EUR (00040000000EC400)",
    saves_location=0x00711DE8,
    events_location=0x0070B728,
    courses_location=0x0070C8E0,
    minigame_location=0x0070D858,
    game_location=0x00709DF8,
    player_singleton_location=0x0070FB60,
    fcram_rupees_addr=0x168B73A8,
    fcram_hearts_addr=0x168B7444,
    actor_pos_offset=0x3D0,
    actor_yaw_offset=0x3E8,
    pose_read_supported=True,
    actor_yaw_binary=True,
)


@dataclass
class PlayerSnapshot:
    player_id: str = "local"
    name: str = "Link"
    tunic_color: str = "green"
    in_game: bool = False
    course: int = 0
    stage: int = 0
    # Entrance of the current scene (game +0x20): where a warp can land.
    entrance: int = 0
    # 'alive', 'dead' (game over shown) or 'ghost' (spectating, see life_sync).
    life: str = "alive"
    # Weathervanes this Link activated: a growing value revives spectators.
    revive_serial: int = 0
    # Identité de la partie chargée et fin de groupe, conservées aux reconnexions.
    life_id: str = ""
    release_serial: int = 0
    life_party: dict = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    pose_verified: bool = False
    animation_id: int = 65535
    upper_animation_id: int = 65535
    render_state: dict = field(default_factory=dict)
    health: int = 12
    max_health: int = 12
    rupees: int = 0
    maiamai: int = 0


# Every Player field a snapshot reads, in one request (EUR): +368 other
# position .. +650 Shooter wrapper. A field outside it is read on its own.
PLAYER_BLOCK = (0x360, 0x300)


class _Block:
    """Fields of one RPC read; a failed read or a field outside falls back to its own read."""

    def __init__(self, bridge, base, offset, size):
        self.bridge, self.base, self.offset = bridge, base, offset
        data = bridge.read_memory(base + offset, size)
        self.data = data if data is not None and len(data) == size else None

    def _field(self, fmt, offset, read):
        at = offset - self.offset
        if self.data is not None and 0 <= at <= len(self.data) - struct.calcsize(fmt):
            value = struct.unpack_from(fmt, self.data, at)
            return value if len(value) > 1 else value[0]
        return read(self.base + offset)

    def u8(self, offset):return self._field('<B', offset, self.bridge.read_u8)
    def u16(self, offset):return self._field('<H', offset, self.bridge.read_u16)
    def u32(self, offset):return self._field('<I', offset, self.bridge.read_u32)
    def f32(self, offset):return self._field('<f', offset, self.bridge.read_f32)
    def vec3(self, offset):return self._field('<3f', offset, self.bridge.read_vec3)


class ALBWMemoryController:
    """
    Manages validated snapshots, shared health and same-room teleportation for
    A Link Between Worlds RAM through AzaharBridge.
    """

    def __init__(self, bridge: AzaharBridge):
        self.bridge = bridge
        self.profile: RegionMemoryProfile = PROFILE_EUR
        self.bss_delta: int = 0
        self.save_ptr: int = 0
        self.event_flags_ptr: int = 0
        self.course_flags_ptr: int = 0
        self.game_ptr: int = 0
        self.player_ptr: int = 0
        self.last_snapshot = PlayerSnapshot()
        self._pose_fingerprint = None
        self._pose_code_matches = False
        from src.memory.gameplay_state import GameplayStateReader
        self._scene_reader = GameplayStateReader(bridge)

    def select_profile_for_region(self) -> None:
        if self.bridge.attached_title_id == TITLE_ID_EUR:
            self.profile = PROFILE_EUR
        else:
            self.profile = PROFILE_USA

    def resolve_pointers(self) -> bool:
        """Resolves dynamic heap pointers from ALBW's BSS segment."""
        self.select_profile_for_region()
        p = self.profile
        d = self.bss_delta

        all_saves = self.bridge.read_u32(p.saves_location + d) or 0
        self.save_ptr = (self.bridge.read_u32(all_saves + 0x14) or 0) if self._valid_pointer(all_saves) else 0
        if not self._valid_pointer(self.save_ptr):
            self.save_ptr = 0
        self.event_flags_ptr = self.bridge.read_u32(p.events_location + d) or 0
        self.course_flags_ptr = self.bridge.read_u32(p.courses_location + d) or 0
        self.game_ptr = self.bridge.read_u32(p.game_location + d) or 0
        singleton = self.bridge.read_u32(p.player_singleton_location + d) or 0
        self.player_ptr = (self.bridge.read_u32(singleton + 0x10) or 0) if self._valid_pointer(singleton) else 0

        return all(
            self._valid_pointer(ptr)
            for ptr in (self.event_flags_ptr, self.course_flags_ptr, self.game_ptr, self.player_ptr)
        )

    @staticmethod
    def _valid_pointer(pointer):
        # The existing profiles use the process heap / linear heap, never VRAM.
        return 0x08000000 <= pointer < 0x1F000000

    def _hookshot_out(self, player):
        """This Link's own Shooter (wrapper slot 8, owner Player+40C) is drawn."""
        wrapper = player.u32(0x630 + 8 * 4)
        raw = self.bridge.read_memory(wrapper, 12) if self._valid_pointer(wrapper or 0) else None
        if not raw or len(raw) != 12:
            return False
        vtable, root, owner = struct.unpack('<3I', raw)
        return (vtable == 0x6E8F7C and owner == self.player_ptr + 0x40C and self._valid_pointer(root)
                and self.bridge.read_u8(root + 0x30) == 1)

    def read_snapshot(self, player_id: str, name: str, tunic_color: str) -> PlayerSnapshot:
        snap = PlayerSnapshot(player_id=player_id, name=name, tunic_color=tunic_color)
        self.last_snapshot = snap
        if not self.bridge.auto_attach_albw():
            self.save_ptr = self.event_flags_ptr = self.course_flags_ptr = self.game_ptr = self.player_ptr = 0
            return snap

        if not self.resolve_pointers():
            # Fixed RAM remnants are not proof that a save is currently playable.
            self.player_ptr = 0
            return snap

        scene_ready = self._scene_reader.playable(self.bss_delta)
        if scene_ready is False:
            self.player_ptr = 0
            return snap

        p = self.profile

        # Course, stage and entrance in one request (game +18/+1C/+20).
        scene = self.bridge.read_memory(self.game_ptr + 0x18, 10)
        course, stage, entrance = struct.unpack('<B3xIH', scene) if scene and len(scene) == 10 else (None,) * 3
        snap.course = course if course is not None else 0
        snap.stage = stage if stage is not None else 0
        snap.entrance = entrance if entrance is not None else 0

        player = _Block(self.bridge, self.player_ptr, *PLAYER_BLOCK)
        vec = player.vec3(p.actor_pos_offset)
        if vec:
            snap.x, snap.y, snap.z = vec
        if p.actor_yaw_binary:
            rotation = player.u32(p.actor_yaw_offset)
            yaw = rotation * (math.tau / (1 << 32)) if rotation is not None else None
        else:
            yaw = player.f32(p.actor_yaw_offset)
        if yaw is not None:
            snap.yaw = yaw

        hp = player.u8(p.player_health_offset)
        max_hp = player.u8(p.player_max_health_offset)
        if (hp is None or max_hp is None or not 12 <= max_hp <= 80
                or max_hp % 4 != 0 or not 0 <= hp <= max_hp
                or course not in COURSE_NAMES or stage is None or vec is None
                or not all(math.isfinite(v) for v in vec)):
            self.player_ptr = 0
            return snap
        snap.health, snap.max_health = hp, max_hp
        snap.in_game = True

        identity = self.bridge.attached_pid, self.bridge.attached_title_id, self.bss_delta
        if identity != self._pose_fingerprint:
            self._pose_fingerprint = identity
            self._pose_code_matches = p.pose_read_supported and self.bss_delta == 0 and (
                self.bridge.read_memory(0x1DE378, 20) == bytes.fromhex(
                    'f0412de90040a0e1da0f80e2281091e8281080e8'))
        if self._pose_code_matches and scene_ready is True and yaw is not None and all(abs(v) <= 1e6 for v in vec):
            # The native setPos routine maintains these two world-space vectors.
            other = player.vec3(0x368)
            model = player.u32(0x600)
            # Body type and both animation indices (+52/+72) in one request.
            body = _Block(self.bridge, model, 0, 0x74) if self._valid_pointer(model or 0) else None
            body_valid = bool(body) and body.u32(0) == 0x6DA424
            ids=tuple(body.u16(offset) for offset in (0x52,0x72)) if body_valid else ()
            action=player.u32(0x614)
            jumping=(body_valid and ids in ((17,17),(18,18)) and self._valid_pointer(action or 0)
                     and self.bridge.read_u16(action+0x42)==2)
            # The EUR jump capture shows a vertical integration-step difference
            # up to .4655. Keep the horizontal guard and admit that bounded Y
            # difference only for the verified native jump state and animations.
            tolerance=(.25,.6 if jumping else .25,.25)
            # The hookshot slide moves Link faster than the .25 gap between
            # these vectors (22/10: the companion vanished for the whole slide).
            # Admit a bounded 1-unit gap only while his own Shooter is out.
            if self._hookshot_out(player):tolerance=(1.,.6,1.)
            snap.pose_verified=bool(body_valid and other and all(math.isfinite(a) and abs(a-b)<limit
                for a,b,limit in zip(other,vec,tolerance)))
            if snap.pose_verified:
                for name, index in zip(('animation_id','upper_animation_id'), ids):
                    if index is not None and 0<=index<176:
                        setattr(snap,name,index)
                from src.memory.render_state import RenderStateReader
                if not hasattr(self,'_render_reader'):
                    self._render_reader=RenderStateReader(self.bridge)
                snap.render_state=self._render_reader.read(self.player_ptr,(snap.animation_id,snap.upper_animation_id))
                for name,layer in zip(('animation_id','upper_animation_id'),snap.render_state.get('layers',())):
                    if getattr(snap,name)==65535 and layer is not None:
                        setattr(snap,name,layer[0])
                if self.bridge.read_u32(self.player_ptr+0x600)!=model:
                    snap.pose_verified=False
                    snap.render_state={}

        maiamai = player.u8(p.player_maiamai_offset)
        if maiamai is not None:
            snap.maiamai = maiamai

        rupees = player.u16(0x4FC) if self._pose_code_matches else None
        if rupees is None:rupees=self.bridge.read_u16(p.fcram_rupees_addr)
        if rupees is not None and rupees <= 9999:
            snap.rupees = rupees

        # Inventory and story flags are no longer read or sent each frame: no
        # service used them (progress travels through shared_progress).
        if not self.bridge.rpc_connected or self._scene_reader.playable(self.bss_delta) is False:
            snap.in_game = False
            snap.pose_verified = False
            snap.render_state = {}
            self.player_ptr = 0
        self.last_snapshot = snap
        return snap

    def teleport_to_coordinates(self, target_x: float, target_y: float, target_z: float) -> bool:
        """Teleports local Link to target coordinates when in the same course/stage."""
        if not self.profile.actor_position_verified or not self.player_ptr or not self.last_snapshot.in_game or not all(
                math.isfinite(v) and abs(v) <= 1e7 for v in (target_x, target_y, target_z)):
            return False
        return self.bridge.write_vec3(self.player_ptr + self.profile.actor_pos_offset, target_x, target_y, target_z)

    def set_current_health(self, health):
        if (type(health) is not int or not self.last_snapshot.in_game or not self.player_ptr
                or not 0 <= health <= self.last_snapshot.max_health):
            return False
        address = self.player_ptr + self.profile.player_health_offset
        if not self.bridge.write_u8(address, health):
            return False
        # An acknowledged write establishes the new baseline. A second read could
        # already include fresh damage and incorrectly classify this write as failed.
        self.last_snapshot.health = health
        return True
