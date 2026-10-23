"""Reversible EUR rendering hook, guarded by actor identity and short leases.

The ranges are padding inside the EUR executable/data mappings, as established
from its NCCH extended header (text ends 6920F4; even page-aligned data+BSS ends
7AFA44, its Azahar mapping ends 7B0000). They must still
be entirely zero in the live process before installation. No guessed BSS is used.
"""
import math
from pathlib import Path
import struct
import time
from src.rpc.azahar_bridge import TITLE_ID_EUR
from src.memory.world_layout import WORLD_CODE,WORLD_CODE_SIZE,WORLD_POINTER,world_image_ranges

CODE = 0x692200
EFFECT_CODE = 0x692100
EFFECT_UNLOAD_HOOK=0x4A2680
EFFECT_UNLOAD_ENTRY=0x6921E0
ORIGINAL_EFFECT_UNLOAD=bytes.fromhex('2c0090e5')
MAILBOX = 0x7AFB00
RETURN_HOOK = 0x1DCD5C
ORIGINAL_RETURN = bytes.fromhex('f080bde8')
COLOR_HOOK = 0x1DCA48
COLOR_ENTRY = CODE+0x600
ORIGINAL_COLOR_LOAD = bytes.fromhex('b00494e5')
FRESCO_HEAP_HOOK = 0x2C17F0
FRESCO_HEAP_ENTRY = CODE+0x440
ORIGINAL_FRESCO_HEAP = bytes.fromhex('00005ae3')
MAGIC = 0x434F4F50
MAILBOX_SIZE=0x200
PALETTE_SIZE=0x80000
FINGERPRINTS = {
    0x53F604:bytes.fromhex('f214d4e198209fe50122d2e7000052e3'),
    0x5C9570:bytes.fromhex('240a1fe5ca1f8fe2f845a0e5041080e5'),
    0x28D848:bytes.fromhex('f74f2de90040a0e1028b2dedd0d04de2'),
    0x1692F8:bytes.fromhex('70402de90040a0e1006091e50150a0e1'),
    EFFECT_UNLOAD_HOOK:bytes.fromhex('2c0090e50000a0e1000051e30020a0e3'),
    RETURN_HOOK: ORIGINAL_RETURN,
    COLOR_HOOK: ORIGINAL_COLOR_LOAD,
    FRESCO_HEAP_HOOK: ORIGINAL_FRESCO_HEAP,
    0x2C151C:bytes.fromhex('201090e50020a0e3041091e5000051e3'),
    0x2C15A8:bytes.fromhex('201090e50020a0e3041091e5000051e3'),
    0x2C1550:bytes.fromhex('f03091e53b0a81edf03081e5483191e5'),
    0x2C15DC:bytes.fromhex('fc3091e53e0a81edfc3081e5483191e5'),
    0x28C97C: bytes.fromhex('f04f2de90320a0e30010a0e3028b2ded'),
    0x28C090: bytes.fromhex('f0402de90040a0e10350a0e10260a0e1'),
    0x28C038: bytes.fromhex('10402de90040a0e1100090e59f0a09eb'),
    0x28C054: bytes.fromhex('70402de90040a0e1100090e50150a0e1'),
    0x10BAB4: bytes.fromhex('10402de90040a0e128009fe5000090e5'),
    0x10983C: bytes.fromhex('20009fe510402de90140a0e1000090e5'),
    0x28C794: bytes.fromhex('101090e5030a9fed4d0a81ed400a80ed'),
    0x28C484: bytes.fromhex('70402de90040a0e1030051e31c0094e5'),
    0x4CE99C: bytes.fromhex('102a00ee70402de90040a0e1200090e5'),
    0x28C434: bytes.fromhex('020051e30e0a9fed0e0a9f0d0100000a'),
    0x169A40: bytes.fromhex('000051e30120a0130020a003102a00ee'),
    0x168E9C: bytes.fromhex('f04f2de90040a0e10170a0e10290a0e1'),
}


def branch(source, destination):
    offset = destination - source - 8
    if offset % 4 or not -(1 << 25) <= offset < (1 << 25):
        raise ValueError('ARM branch out of range')
    return struct.pack('<I', 0xEA000000 | ((offset >> 2) & 0xFFFFFF))


class RemoteModelTrial:
    def _world_matches(self,image):
        image=image.ljust(WORLD_CODE_SIZE,b'\0')
        return all(self.bridge.read_memory(at,size)==image[at-WORLD_CODE:at-WORLD_CODE+size]
                   for at,size in world_image_ranges())

    def _write_world(self,image):
        image=image.ljust(WORLD_CODE_SIZE,b'\0')
        return all(self.bridge.write_memory(at,image[at-WORLD_CODE:at-WORLD_CODE+size])
                   for at,size in world_image_ranges())

    def __init__(self, bridge):
        self.bridge = bridge
        self.machine = Path(__file__).with_name('remote_model.bin').read_bytes()
        self.effect_machine = Path(__file__).with_name('attack_effects.bin').read_bytes()
        self.world_machine = Path(__file__).with_name('world_entry.bin').read_bytes()
        self.effect_unload_jump=branch(EFFECT_UNLOAD_HOOK,EFFECT_UNLOAD_ENTRY)
        self.jump = branch(RETURN_HOOK, CODE)
        self.colour_jump=branch(COLOR_HOOK,COLOR_ENTRY)
        self.fresco_heap_jump=branch(FRESCO_HEAP_HOOK,FRESCO_HEAP_ENTRY)
        self.installed = False
        self.pid = None
        self.owner = 0
        self.room = None
        self._enabled = False
        self.body = 0
        self.handle = None
        self._published = False
        self.sequence=0
        self._merged=False
        self._visual_address=MAILBOX+0x80
        self._last_phases=None
        self.final_model=0

    def install(self, player, course, stage, reuse_model=0, palette=0, request_palette=False):
        if (self.bridge.attached_title_id != TITLE_ID_EUR
                or self.bridge.attached_pid is None
                or not 0x08000000 <= player < 0x1F000000):
            return False
        if any(self.bridge.read_memory(at, len(data)) != data for at,data in FINGERPRINTS.items()):
            return False
        if (self.bridge.read_memory(CODE, len(self.machine)) != bytes(len(self.machine))
                or self.bridge.read_memory(EFFECT_CODE,0x100)!=bytes(0x100)
                or not self._world_matches(bytes(WORLD_CODE_SIZE))
                or self.bridge.read_memory(MAILBOX, MAILBOX_SIZE) != bytes(MAILBOX_SIZE)):
            return False
        self.pid = self.bridge.attached_pid
        self.owner, self.room = player, (course, stage)
        self.body = self.bridge.read_u32(player+0x600)
        self.handle = self.bridge.read_u32(player+0x2CC)
        if not self.body or self.handle is None or self.bridge.read_u32(self.body)!=0x6DA424:
            return False
        self._enabled = False
        if reuse_model:
            local_model = self.bridge.read_u32(player+0x600)
            if (not local_model or self.bridge.read_u32(reuse_model)!=0x6DA424
                    or self.bridge.read_u32(reuse_model+4)!=self.bridge.read_u32(local_model+4)):
                return False
        initial = bytearray(MAILBOX_SIZE)
        struct.pack_into('<2I', initial, 0, MAGIC, 1)
        struct.pack_into('<I', initial, 0x10, reuse_model)
        struct.pack_into('<2I',initial,0x60,int(request_palette),palette)
        struct.pack_into('<3I', initial, 0x18, player, course, stage)
        struct.pack_into('<3f', initial, 0x40, 1, 1, 1)
        struct.pack_into('<2I',initial,0x6C,self.handle,self.body)
        struct.pack_into('<I',initial,0x58,255)
        struct.pack_into('<I',initial,0x168,MAILBOX+0x80)
        self.installed = True
        # Publish the entry jump last. The zeroed mailbox initially hides it.
        prepared = (self.bridge.write_memory(EFFECT_CODE,self.effect_machine)
            and self.bridge.read_memory(EFFECT_CODE,len(self.effect_machine))==self.effect_machine
            and self.bridge.write_memory(CODE, self.machine)
            and self.bridge.read_memory(CODE, len(self.machine)) == self.machine
            and self.bridge.write_memory(MAILBOX, initial)
            and self._write_world(self.world_machine)
            and self._world_matches(self.world_machine))
        if not prepared:
            return False
        self._published = True  # A lost acknowledgement may still publish it.
        return (self.bridge.write_memory(EFFECT_UNLOAD_HOOK,self.effect_unload_jump)
                and self.bridge.write_memory(FRESCO_HEAP_HOOK,self.fresco_heap_jump)
                and self.bridge.write_memory(COLOR_HOOK,self.colour_jump)
                and self.bridge.write_memory(RETURN_HOOK,self.jump)
                and self.bridge.read_memory(RETURN_HOOK,4)==self.jump)

    def set_local_colour(self,colour,lease=60):
        names=('green','blue','red','purple')
        if colour not in names or not self.installed or not 1<=lease<=120:
            return False
        return self.bridge.write_memory(MAILBOX+0x58,struct.pack('<2I',names.index(colour),lease))

    def bind_palette(self,original):
        buffer=self.bridge.read_u32(MAILBOX+0x64)
        if (not buffer or self.bridge.read_memory(buffer+0x180,8)!=b'COOPPAL1'
                or self.bridge.read_u32(buffer+0x188)!=original):
            return False
        return self.bridge.write_u32(MAILBOX+0x74,original)

    def set_room(self,course,stage):
        if (self.bridge.read_u32(self.owner+0x2CC)!=self.handle
                or self.bridge.read_u32(self.owner+0x600)!=self.body):
            return False
        if (course,stage)!=self.room:
            if not (self.hide() and self.bridge.write_memory(MAILBOX+0x1C,struct.pack('<2I',course,stage))):
                return False
            self.room=(course,stage)
        return True

    def hide(self):
        self._enabled=False
        return not self.installed or self.bridge.write_u32(MAILBOX+8,0)

    def keep_visible(self,lease=60):
        # Reuse the existing pose/objects only. Native owner, course and live
        # scroll-state checks still decide whether this model may be drawn.
        return (self.installed and self._enabled
                and self.bridge.attached_pid==self.pid
                and type(lease) is int and 1<=lease<=60
                and self.bridge.write_u32(MAILBOX+0x4C,lease))

    def update(self, pose, frame=1.0, lease=60):
        if not self.installed or self.bridge.attached_pid != self.pid:
            return False
        if (not pose.get('pose_verified') or not pose.get('in_game')
                or (pose.get('course'), pose.get('stage')) != self.room):
            self.hide()
            return False
        xyz = tuple(pose.get(k) for k in ('x','y','z'))
        yaw = pose.get('yaw')
        if any(type(v) not in (float,int) or not math.isfinite(v) or abs(v)>1e6 for v in (*xyz,yaw)):
            return False
        if frame not in (0.0,1.0,2.0) or type(lease) is not int or not 1<=lease<=1200:
            return False
        rotation = round((yaw % math.tau) / math.tau * (1 << 32)) & 0xFFFFFFFF
        # A 60-update lease hides the duplicate if the sender disappears.
        animations = tuple(pose.get(key,65535) for key in ('animation_id','upper_animation_id'))
        if any(type(v) is not int or v not in range(176) and v!=65535 for v in animations):
            return False
        from src.memory.render_state import encode_render_state
        visual=encode_render_state(pose.get('render_state'))
        transitions=pose['render_state'].get('transition_frames',[0,0]) if visual else (0,0)
        # The old unused float at +3C is two bounded local blend durations.
        block=struct.pack('<3fIf4H3fI',*xyz,rotation,frame,*animations,*transitions,1,1,1,lease)
        if not visual and self._merged:
            # No valid visual sample is not a wall-exit event. Leave the last
            # merged marker intact for native equipment suppression and hide
            # the body until a complete sample explicitly changes its form.
            return self.hide()
        if visual:self._merged=pose['render_state']['merged']
        if visual:
            phases=visual[0x60:0x88]
            if phases!=self._last_phases:
                self.sequence=(self.sequence+1)&0xFFFFFFFF
                self._last_phases=phases
            visual+=struct.pack('<I',self.sequence)
        else:
            visual=bytes(4)
        address=MAILBOX+(0x16C if self._visual_address==MAILBOX+0x80 else 0x80)
        if not self.bridge.write_memory(address,visual):
            return False
        if not self.bridge.write_u32(MAILBOX+0x168,address):
            return False
        self._visual_address=address
        index=('green','blue','red','purple').index(pose.get('tunic_color','blue'))
        if not self.bridge.write_u32(MAILBOX+0xC,index):
            return False
        if not self.bridge.write_memory(MAILBOX+0x24,block):
            return False
        if not self.bridge.write_u32(MAILBOX+8,1):
            return False
        self._enabled=True
        return True

    def status(self):
        data = self.bridge.read_memory(MAILBOX, 0x6C)
        if not data or len(data)!=0x6C:
            return None
        return dict(model=struct.unpack_from('<I',data,0x10)[0],
                    state=struct.unpack_from('<I',data,0x14)[0],
                    updates=struct.unpack_from('<I',data,0x50)[0],
                    busy=struct.unpack_from('<I',data,0x54)[0],
                    palette=struct.unpack_from('<I',data,0x64)[0],
                    allocation_result=struct.unpack_from('<I',data,0x68)[0])

    def restore(self, free_palette=True):
        if not self.installed:
            return True
        if self.bridge.attached_title_id != TITLE_ID_EUR or self.bridge.attached_pid != self.pid:
            return False
        # Particle banks must complete their native retirement before either
        # their presenter or the only game-thread cleanup queue is removed.
        if self.bridge.read_u32(MAILBOX+0x1FC):return False
        if self.bridge.read_u32(WORLD_POINTER):return False
        if self.bridge.read_u32(0x7AFF78):return False
        if self.bridge.read_u32(0x7AFF70):return False
        if self.bridge.read_u32(0x7AFF74):return False
        if not self._published:
            # No entry jump was sent: nothing can be executing this storage.
            if self.bridge.read_memory(RETURN_HOOK,4)!=ORIGINAL_RETURN:
                return False
            ok=(self.bridge.write_memory(CODE,bytes(len(self.machine)))
                and self.bridge.write_memory(EFFECT_CODE,bytes(len(self.effect_machine)))
                and self._write_world(bytes(WORLD_CODE_SIZE))
                and self.bridge.write_memory(MAILBOX,bytes(MAILBOX_SIZE)))
            if ok:self.installed=False
            return ok
        before=self.status()
        if before is None:return False
        initial_updates=before['updates']
        self.bridge.write_u32(MAILBOX+0x1F8,0)
        self.bridge.write_u32(MAILBOX+0x5C,0)
        self.bridge.write_u32(MAILBOX+8,0)
        if free_palette and self.bridge.read_u32(MAILBOX+0x64):
            self.bridge.write_u32(MAILBOX+0x60,2)
        deadline = time.monotonic()+2
        hidden = False
        while time.monotonic()<deadline:
            status = self.status()
            if (status and status['updates']!=initial_updates
                    and status['state'] in (2,4) and not status['busy']
                    and (not free_palette or not status['palette'])):
                hidden=True
                self.final_model=status.get('model',0)
                break
            time.sleep(.02)
        # A paused game has not acknowledged removal. Keep its entry points;
        # never resume the debugger or strand queued work just to erase a hook.
        if not hidden:return False
        current = self.bridge.read_memory(RETURN_HOOK,4)
        if current not in (self.jump,ORIGINAL_RETURN):
            return False
        colour_current=self.bridge.read_memory(COLOR_HOOK,4)
        if colour_current not in (self.colour_jump,ORIGINAL_COLOR_LOAD):
            return False
        heap_current=self.bridge.read_memory(FRESCO_HEAP_HOOK,4)
        if heap_current not in (self.fresco_heap_jump,ORIGINAL_FRESCO_HEAP):
            return False
        if self.bridge.read_memory(EFFECT_UNLOAD_HOOK,4) not in (self.effect_unload_jump,ORIGINAL_EFFECT_UNLOAD):return False
        if not self.bridge.write_memory(EFFECT_UNLOAD_HOOK,ORIGINAL_EFFECT_UNLOAD):return False
        if not self.bridge.write_memory(FRESCO_HEAP_HOOK,ORIGINAL_FRESCO_HEAP):
            return False
        if not self.bridge.write_memory(COLOR_HOOK,ORIGINAL_COLOR_LOAD):
            return False
        if not self.bridge.write_memory(RETURN_HOOK,ORIGINAL_RETURN):
            return False
        # Stop at the next entry, after every previous invocation has returned.
        # This makes it safe to erase the formerly executing padding as well.
        breakpoint='1,1dc9c8,4'
        paused=False
        try:
            if not hidden or self.bridge._exchange('Z'+breakpoint)!=b'OK':
                return False
            answer=self.bridge._exchange('c')
            paused=bool(answer and answer[:1] in (b'T',b'S'))
            if not paused:
                return False
            pc=self.bridge._exchange('pf')
            if not pc or bytes.fromhex(pc.decode())!=struct.pack('<I',0x1DC9C8):
                return False
            if self.bridge.read_memory(CODE,len(self.machine))!=self.machine:
                return False
            if self.bridge.read_memory(EFFECT_CODE,len(self.effect_machine))!=self.effect_machine:return False
            if not self._world_matches(self.world_machine):return False
            if not (self.bridge.write_memory(CODE,bytes(len(self.machine)))
                    and self.bridge.write_memory(EFFECT_CODE,bytes(len(self.effect_machine)))
                    and self._write_world(bytes(WORLD_CODE_SIZE))
                    and self.bridge.write_memory(MAILBOX,bytes(MAILBOX_SIZE))):
                return False
            self.installed=False
            self._published=False
            self._enabled=False
            return free_palette
        finally:
            self.bridge._exchange('z'+breakpoint)
            self.bridge._exchange('c',reply=False)
