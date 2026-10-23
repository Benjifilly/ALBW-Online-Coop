"""Azahar EUR world extension; all addresses are local, never wire values.

Single registry of the fixed patch area 7AFD00..7B0000 (verified post-BSS
padding) and of the offsets inside the private world reservation. Every fixed
address used by src/memory (Python or .arm) must be declared here, without
overlaps.

The renderer (RemoteModelTrial) writes world_entry.bin except the persistent
life_session slots: the code blocks below, zeros elsewhere. Services write their
8-byte trampolines ("ldr pc,[pc,#-4]; .word target") into the declared slots
and must clear them before the renderer closes. Use free_patch_ranges() to
find room for a new hook.
"""
from types import MappingProxyType

PATCH_AREA=(0x7AFD00,0x7B0000)

# name: (address, size). Code block sizes are the real built lengths; the
# builder keeps each block inside its own limit (see build_remote_model.py).
PATCH_SLOTS=MappingProxyType({
    'world_entry':(0x7AFD00,0x54),        # frame callback -> published services
    'party_generation':(0x7AFD54,4),     # reçus persistants de défaite collective
    'progress_flag_trampoline':(0x7AFD58,8),  # 4ADAF4 changed game flag
    'progress_grant_trampoline':(0x7AFD60,8), # 28EE18 item grant
    'progress_event_trampoline':(0x7AFD68,8), # 4CDF18 direct event flag
    'life_trampoline':(0x7AFD70,8),       # 1DE420 Player calc: collision, spectator
    'input_lock_trampoline':(0x7AFD78,8), # 19AE0C input wrappers frozen for a spectator
    'grass_entry':(0x7AFD80,0x30),        # 237288 capture entry
    'life_hide_trampoline':(0x7AFDB0,8),  # 1DCC34 bl 1DD530: spectator hidden after it
    'life_session_main':(0x7AFDB8,0x24), # compte les chargements, même sans compagnon
    'life_session_main_alt':(0x7AFDDC,0x24),
    'enemy_entry':(0x7AFE00,0x54),        # 4F73F8 damage observation
    'life_session_menu':(0x7AFE54,0x24),
    'life_session_state':(0x7AFE78,8),   # nonce du jeu, génération de partie
    'hit_entry':(0x7AFE80,0x50),          # 4F85F0 contact observation
    'loot_trampolines':(0x7AFED0,0x50),   # shared_loot.HOOKS, 10 x 8
    'impact_trampoline':(0x7AFF20,8),     # enemy_impact_replay
    'foliage_trampoline':(0x7AFF28,8),    # 236454 grass/lawn rustle
    'restore_trampolines':(0x7AFF30,0x18),# grass loader hooks, 3 x 8
    'loot_late_trampolines':(0x7AFF48,0x10),
    'shrub_motion_trampoline':(0x7AFF58,8),# 238F28 shrub rustle
    'shrub_cut_trampoline':(0x7AFF60,8),  # 23979C shrub transitions
    'sign_trampoline':(0x7AFF68,8),       # 1C2D50 signboard hit decision
    'loot_pointer':(0x7AFF70,4),
    'progress_pointer':(0x7AFF74,4),      # shared_progress reservation
    'restore_pointer':(0x7AFF78,4),
    'party_penalty_serial':(0x7AFF7C,4),
    'pose_entry':(0x7AFF80,0x2C),         # 4F48C8 enemy pose
    'party_warp_serial':(0x7AFFAC,4),
    'pot_break_trampoline':(0x7AFFB0,8),  # capture_hooks.MASS_SITES
    'pot_lift_trampoline':(0x7AFFB8,8),
    'rock_break_trampoline':(0x7AFFC0,8),
    'rock_lift_trampoline':(0x7AFFC8,8),
    'pot_carry_trampoline':(0x7AFFD0,8),  # capture_hooks.CARRY_SITES
    'grass_carry_trampoline':(0x7AFFD8,8),
    'rock_carry_trampoline':(0x7AFFE0,8),
    'nearest_trampoline':(0x7AFFE8,8),    # 520F98 player position for enemies
    'pose_trampoline':(0x7AFFF0,8),       # original integrator prologue
    'party_penalty_amount':(0x7AFFF8,4),
    'world_pointer':(0x7AFFFC,4),
})

# Offsets inside the world reservation (2 MiB, owned by NativeWorldEvents).
WORLD_RESERVE=MappingProxyType({
    'control':(0x0,0x100),
    'cut_ring':(0x100,0x800),             # 64 x 32-byte Mass events
    'payload':(0x1000,0x1800),            # world_payload.bin, incl. world_mass +0x2000, world_prop +0x2400
    'combat_ring':(0x10000,0x2000),
    'contact_ring':(0x20000,0x4000),
    'impact':(0x30000,0x2000),            # code + 0x120-byte control at +0x1000
    'pose':(0x50000,0x1000),
    'pose_buffers':(0x51000,0x4000),      # two 0x1020-byte buffers at +0/+0x2000
    'health':(0x56000,0x1000),
    'foliage':(0x58000,0x1000),
    'foliage_buffers':(0x59000,0x200),
    'shrub_motion':(0x5A000,0x1000),
    'shrub_capture':(0x5B000,0x1000),
    'sign_capture':(0x5C000,0x1000),
    'pot_break':(0x5D000,0x1000),
    'pot_lift':(0x5E000,0x1000),
    'rock_break':(0x5F000,0x1000),
    'rock_lift':(0x60000,0x1000),
    'pot_carry':(0x61000,0x1000),         # code, carried table at +0xF00 (read by shared_loot.arm)
    'carried_mirror':(0x62000,0x1000),    # code, mirror table at +0xF00; called each frame via +0xB0
    'grass_carry':(0x63000,0x1000),
    'rock_carry':(0x64000,0x1000),
    'nearest_player':(0x65000,0x1000),    # nearest_player.arm + enemy vtable list
    'item_visuals':(0x66000,0x1000),      # private held-item presenter and descriptor table
    'player_life':(0x67000,0x1000),       # player_life.arm, collision + spectator, control after the code
    'input_lock':(0x68000,0x1000),        # input_lock.arm, reads player_life's control
    'life_hide':(0x69000,0x1000),         # life_hide.arm, reads player_life's control
    'model_light':(0x6A000,0x1000),       # model_light.arm, called each frame via +0xB8
    'party_life':(0x6B000,0x1000),        # retour du groupe et pénalité confirmée
    'switch_hit':(0x6C000,0x1000),        # reaction_capture.arm for MojSwitchHit 1C4538
    'step_switch':(0x6D000,0x1000),       # reaction_capture.arm for MojSwitchStep 3AAAE4
    'machine_capture':(0x6E000,0x1000),   # machine_capture.arm, catalogued props' machines
})

# Second verified padding, after data+BSS (ends 7AFA44) and before the renderer
# mailbox 7AFB00: zero in both EUR games, never written by the renderer.
# Trampolines only (8 bytes each); a service clears its own when it closes.
TRAMPOLINE_AREA=(0x7AFA48,0x7AFB00)
TRAMPOLINE_SLOTS=MappingProxyType({
    'switch_hit_trampoline':(0x7AFA48,8), # 1C4538 crystal switch reaction
    'step_switch_trampoline':(0x7AFA50,8),# 3AAAE4 floor switch setState
    'machine_trampoline':(0x7AFA58,8),    # 10BEB4 every machine's state change
})

def slot(name):return (PATCH_SLOTS.get(name) or TRAMPOLINE_SLOTS[name])[0]
def reserve(name):return WORLD_RESERVE[name][0]

LIFE_SESSION_SLOTS=('life_session_main','life_session_main_alt','life_session_menu','life_session_state',
                    'party_generation','party_penalty_serial','party_warp_serial','party_penalty_amount')


def world_image_ranges():
    """Le repère de partie survit au retrait du rendu coop."""
    at=PATCH_AREA[0];ranges=[]
    for start,size in sorted(PATCH_SLOTS[n] for n in LIFE_SESSION_SLOTS):
        if start>at:ranges.append((at,start-at))
        at=start+size
    if at<PATCH_AREA[1]:ranges.append((at,PATCH_AREA[1]-at))
    return ranges

def free_patch_ranges(area=PATCH_AREA,slots=PATCH_SLOTS):
    """Unclaimed (address, size) ranges of the patch area, in address order."""
    free=[];at=area[0]
    for start,size in sorted(slots.values()):
        if start>at:free.append((at,start-at))
        at=max(at,start+size)
    if at<area[1]:free.append((at,area[1]-at))
    return free

def free_trampolines():return free_patch_ranges(TRAMPOLINE_AREA,TRAMPOLINE_SLOTS)

WORLD_CODE=PATCH_AREA[0]
WORLD_CODE_SIZE=PATCH_AREA[1]-PATCH_AREA[0]
WORLD_POINTER=slot('world_pointer')
WORLD_GRASS_ENTRY=slot('grass_entry')
WORLD_ENEMY_ENTRY=slot('enemy_entry')
WORLD_HIT_ENTRY=slot('hit_entry')
WORLD_PAYLOAD_OFFSET=reserve('payload')
WORLD_MASS_OFFSET=0x2000
WORLD_PROP_OFFSET=0x2400
ENEMY_HIT_HOOK=0x4F85F0
ENEMY_HIT_ORIGINAL=bytes.fromhex('b550f9eb')
ENEMY_HIT_FINGERPRINT=bytes.fromhex('b550f9eb000050e30100a0137080bde8')
ENEMY_DAMAGE_HOOK=0x4F73F8
ENEMY_DAMAGE_ORIGINAL=bytes.fromhex('b810d5e1')
ENEMY_DAMAGE_FINGERPRINT=bytes.fromhex('b810d5e1000041e07000bfe6400070e3')
WORLD_MAGIC=0x574F5231
WORLD_ABI=4
