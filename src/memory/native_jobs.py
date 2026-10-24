"""Local development queue for bounded, fingerprinted game-thread render calls.

Packets never select jobs, addresses or native arguments. Equipment rendering
selects only fingerprinted local Sword/Shield resources; network transforms are
validated separately and contain no pointers.
An installed RemoteModelTrial and a live, verified owner are prerequisites.
"""
from enum import IntEnum
from dataclasses import dataclass
import struct
import threading
import time
import math

from src.memory.remote_model import MAILBOX

JOBBOX=MAILBOX+0x110
JOB_MAGIC=0x4A4F4231
PRIVATE_SIZE=0x200000


class RenderJob(IntEnum):
    RESERVE=1
    RELEASE=2
    CREATE_HEAP=3
    DESTROY_HEAP=4
    ALLOCATE=5
    CREATE_MODEL=6
    CALCULATE=7
    REGISTER=8
    CREATE_ANIMATION=9
    ADD_RESOURCE=10
    BIND_ANIMATION=11
    SELECT_ANIMATION=12
    APPLY_ANIMATION=13
    DESTROY_MODEL=14
    FREE_SIZE=15
    MAX_BLOCK=16
    SHOW_MESH=17
    ADVANCE_ANIMATION=18
    CLEAR_RTT=19
    ADD_RTT_MODEL=20
    REMOVE_RTT=21
    FRESCO_SURFACE=22
    CREATE_RTT=23
    CONFIGURE_RTT=24
    DESTROY_RTT=25
    CREATE_EFFECT_BANK=26
    PREPARE_EFFECT_BANK=27
    CREATE_EFFECT=28
    KILL_EFFECT_BANK=29
    DETACH_EFFECT_BANK=30
    FREE=31
    STOP_EFFECT=32
    PARSE_EFFECT=33


JOB_FINGERPRINTS={
    0x2C1730:bytes.fromhex('f04f2de9028b2ded03db4de2f9df4de2'),
    0x2C16FC:bytes.fromhex('10402de90140a0e10410b0e502c0a0e1'),
    0x2C1270:bytes.fromhex('70402de920d04de20040a0e130009de5'),
    0x10CC7C:bytes.fromhex('f0412de90050a0e110d04de278009fe5'),
    0x49CAD0:bytes.fromhex('f0412de90050a0e15c009fe50060a0e1'),
    0x2C2CDC:bytes.fromhex('f8432de90040a0e1140090e5000050e3'),
    0x4CEAC8:bytes.fromhex('70402de90040a0e1bf0adfed016c80e2'),
    0x4CF450:bytes.fromhex('f0412de90040a0e10150a0e1d003d0e1'),
    0x16A2FC:bytes.fromhex('f0412de90160a0e10010e0e30070a0e3'),
    0x16A2A4:bytes.fromhex('04402de5083090e504c090e5000052e3'),
    0x16A02C:bytes.fromhex('ff5f2de90040a0e10150a0e10260a0e1'),
    0x16898C:bytes.fromhex('f0432de90040a0e10150a0e10080a0e3'),
    0x4D0A38:bytes.fromhex('f0472de90070a0e10090a0e34c0090e5'),
    0x580248:bytes.fromhex('70402de90040a0e1540080e2701094e5'),
    0x580334:bytes.fromhex('70402de90160a0e10050a0e1c60f21e0'),
    0x4CE9F0:bytes.fromhex('30002de95c3090e5020053e164c09085'),
    0x169A80:bytes.fromhex('f0472de90040a0e10080a0e3028b2ded'),
    0x300208:bytes.fromhex('70402de90060a0e3183080e2028b2ded'),
    0x2FF880:bytes.fromhex('ff4f2de90040a0e1070081e20780c0e3'),
    0x2FF598:bytes.fromhex('0010a0e104009fe5000090e56e0effea'),
    0x3001F4:bytes.fromhex('0010a0e104009fe5000090e5270cffea'),
    0x2FF718:bytes.fromhex('0010a0e3781180e5841180e51eff2fe1'),
    0x2FF728:bytes.fromhex('000052e330002de984118015784190e5'),
    0x300514:bytes.fromhex('70402de90040a0e13c009fe5000084e5'),
}


class NativeJobRejected(RuntimeError):
    """The game acknowledged the request without entering a native function."""


@dataclass
class _Request:
    channel: object
    sequence: int
    deadline: float
    start_frame: object = None
    frame_budget: int = 0
    result: object = None
    rejected: bool = False
    error: str = ''
    prepared: bytes = b''
    uncertain: bool = False
    recovering: bool = False
    recovery_until: float = 0
    next_probe: float = 0
    recovery_window: float = 15


class _JobChannel:
    def __init__(self):
        self.sequence=0;self.pending=None;self.lock=threading.RLock()


class NativeRenderJobs:
    def __init__(self,trial):
        if not trial.installed:raise ValueError('Le rendu doit être installé avant la file de test.')
        self.trial=trial;self.bridge=trial.bridge
        # Every private renderer uses the same native JOBBOX. Sharing the
        # sequence and request also lets a synchronous resource call finish a
        # particle request without stealing its eventual result.
        if not hasattr(trial,'_native_job_channel'):trial._native_job_channel=_JobChannel()
        self._channel=trial._native_job_channel
        for at,data in JOB_FINGERPRINTS.items():
            observed=self.bridge.read_memory(at,len(data))
            if observed is None:raise RuntimeError('Azahar ne répond pas pendant la vérification du rendu.')
            if observed!=data:raise ValueError('Les fonctions de rendu ne correspondent pas au programme vérifié.')

    @property
    def pending(self):
        return self._channel.pending is not None

    @property
    def sequence(self):
        return self._channel.sequence

    def _validate(self,job,args,heap,stack,timeout):
        if type(job) is not RenderJob or len(args)>4 or len(stack)>4:
            raise ValueError('Appel de rendu hors de la liste autorisée.')
        if self.bridge.attached_pid!=self.trial.pid or not self.trial.installed:
            raise RuntimeError('Le jeu a changé pendant le test.')
        if any(type(v) is not int or not 0<=v<=0xFFFFFFFF for v in (*args,heap,*stack)):
            raise ValueError('Arguments natifs invalides.')
        if not isinstance(timeout,(int,float)) or not 0<timeout<=60:
            raise ValueError('Délai de rendu invalide.')

    def _waiting_transport(self,request):
        if not request.recovering:request.recovery_until=time.monotonic()+request.recovery_window
        request.recovering=True
        if time.monotonic()>=request.recovery_until:
            request.error='Azahar ne répond plus pendant l’appel ; son résultat reste réservé.'
            raise RuntimeError(request.error)
        return False

    def _resolve(self,request):
        if request.error:raise RuntimeError(request.error)
        if not self.trial.installed:
            request.error='Le jeu a changé pendant un appel en attente.'
            raise RuntimeError(request.error)
        if self.bridge.attached_pid is None and callable(getattr(self.bridge,'auto_attach_albw',None)):
            self._waiting_transport(request)
            if time.monotonic()<request.next_probe:return False
            request.next_probe=time.monotonic()+.5
            if not self.bridge.auto_attach_albw(force=True):return self._waiting_transport(request)
        if self.bridge.attached_pid!=self.trial.pid:
            request.error='Le jeu a changé pendant un appel en attente.'
            raise RuntimeError(request.error)
        if request.result is not None:return True
        # Read the commit before judging its age. A transition can delay the
        # Python poll even though the exact native request already returned.
        raw=self.bridge.read_memory(JOBBOX,0x48)
        if not raw or len(raw)!=0x48:return self._waiting_transport(request)
        if request.uncertain or request.recovering:
            if raw[:4]!=request.prepared[:4] or raw[0xC:0x38]!=request.prepared[0xC:0x38]:
                request.error='La file du rendu a changé pendant la coupure ; aucun appel rejoué.'
                raise RuntimeError(request.error)
        reply=raw[8:]
        published,acked=struct.unpack_from('<2I',raw,4)
        if (acked==request.sequence and published==request.sequence
                and self.bridge.read_u32(JOBBOX+4)==request.sequence
                and struct.unpack_from('<I',reply,0x3C)[0]==0):
            request.result=struct.unpack_from('<2I',reply,0x30)
            request.rejected=bool(struct.unpack_from('<I',reply,0x38)[0])
            if self._channel.pending is request:self._channel.pending=None
            return True
        if self.bridge.attached_pid is None:return self._waiting_transport(request)
        if request.uncertain and published==0 and acked==0 and struct.unpack_from('<I',raw,0x44)[0]==0:
            # La préparation est intacte, mais pas publiée : même numéro, aucun ACK effacé.
            self._waiting_transport(request)
            frame=self.bridge.read_u32(MAILBOX+0x50)
            request.start_frame=frame if type(frame) is int and frame>0 else None
            if self.bridge.write_u32(JOBBOX+4,request.sequence):request.uncertain=False
            return False
        # A pause/scroll without owner callbacks must not spend the execution
        # budget. A genuinely expired, unacknowledged request stays uncertain;
        # even a later ACK cannot silently clear that recorded failure.
        frame=self.bridge.read_u32(MAILBOX+0x50) if request.start_frame is not None else None
        if self.bridge.attached_pid is None:return self._waiting_transport(request)
        age=((frame-request.start_frame)&0xFFFFFFFF) if type(frame) is int else None
        deadline=request.recovery_until if request.recovering or request.uncertain else request.deadline
        expired=(age>request.frame_budget) if age is not None else time.monotonic()>=deadline
        if expired:
            request.error='L’appel de rendu n’a pas terminé ; aucune relance automatique.'
            raise RuntimeError(request.error)
        return False

    def submit(self,job,*args,heap=0,s0=0.0,stack=(),timeout=1.0):
        """Publish once without waiting; None means the shared queue is busy."""
        self._validate(job,args,heap,stack,timeout)
        with self._channel.lock:
            previous=self._channel.pending
            if previous and not self._resolve(previous):return None
            self._channel.sequence=(self.sequence+1)&0xFFFFFFFF or 1
            sequence=self.sequence;data=bytearray(0x48)
            struct.pack_into('<5I',data,0,JOB_MAGIC,0,0,int(job),heap)
            struct.pack_into('<4If4I',data,0x14,*(list(args)+[0]*(4-len(args))),s0,
                             *(list(stack)+[0]*(4-len(stack))))
            if not self.bridge.write_memory(JOBBOX,data):raise RuntimeError('Préparation de l’appel impossible.')
            frame=self.bridge.read_u32(MAILBOX+0x50)
            request=_Request(self._channel,sequence,time.monotonic()+timeout,
                             frame if type(frame) is int and frame>0 else None,max(1,math.ceil(timeout*60)),
                             prepared=bytes(data),recovery_window=max(timeout,getattr(self.bridge,'native_recovery_timeout',15.0)))
            self._channel.pending=request
            if not self.bridge.write_u32(JOBBOX+4,sequence):
                request.uncertain=True
            return request

    def poll(self,request):
        """Return the preserved result, or None until a native frame replies."""
        if not isinstance(request,_Request) or request.channel is not self._channel:
            raise ValueError('Cet appel appartient à une autre file de rendu.')
        with self._channel.lock:
            if not self._resolve(request):return None
            if request.rejected:raise NativeJobRejected('Appel refusé par le rendu : le héros ou la scène a changé.')
            return request.result

    def call(self,job,*args,heap=0,s0=0.0,stack=(),timeout=1.0):
        self._validate(job,args,heap,stack,timeout)
        while True:
            request=self.submit(job,*args,heap=heap,s0=s0,stack=stack,timeout=timeout)
            if request is not None:break
            time.sleep(.002)
        while True:
            result=self.poll(request)
            if result is not None:return result
            time.sleep(.002)
