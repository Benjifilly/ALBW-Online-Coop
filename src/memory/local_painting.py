"""Copied pigments for the local native painting, restored with the colour lease."""
import struct
from src.memory.painting_palette import PrivatePaintingPalette,COLOURS
from src.memory.render_state import _heap
from src.memory.remote_model import MAILBOX,PALETTE_SIZE


class LocalPaintingPalette(PrivatePaintingPalette):
    def __init__(self,bridge,buffer,player):
        self.bridge=bridge;self.buffer=buffer;self.player=player
        self.next_address=buffer+0xB100
        self.pool=self
        self.originals={};self.textures={};self.commands={};self.tables={};self.combined={}
        self.colour=None
        controller=bridge.read_u32(player+0x604)
        self.wall=bridge.read_u32(controller+0x28) if _heap(controller) else None
        if not _heap(self.wall) or bridge.read_u32(self.wall)!=player:
            raise ValueError('Le contrôleur de peinture local n’est pas prêt.')
        self.models=tuple(bridge.read_u32(self.wall+0x28+i*0x28) for i in (0,1))
        if any(not _heap(p) or bridge.read_u32(p)!=0x6EBEF0 for p in self.models):
            raise ValueError('Les formes locales de peinture ne sont pas prêtes.')

    def _alloc(self,size):
        address=self.next_address;end=(address+size+3)&~3
        if end>self.buffer+PALETTE_SIZE:raise ValueError('Budget de couleurs murales dépassé.')
        self.next_address=end
        return address

    def _write(self,address,data):
        if not self.buffer+0xB000<=address<=self.buffer+PALETTE_SIZE-len(data):
            raise ValueError('Couleur murale hors de sa réserve.')
        if not self.bridge.write_memory(address,data):raise RuntimeError('Copie de couleur murale impossible.')

    def _material_table(self,model):
        b=self.bridge;controller=b.read_u32(self.player+0x604)
        if not _heap(controller) or b.read_u32(controller+0x28)!=self.wall or b.read_u32(self.wall)!=self.player:
            raise ValueError('Le propriétaire de la peinture locale a changé.')
        if model not in self.models or b.read_u32(model)!=0x6EBEF0:
            raise ValueError('Modèle local de peinture non reconnu.')
        wrapper=b.read_u32(model+0x20)
        if not _heap(wrapper):raise ValueError('Matériaux locaux absents.')
        table,count=struct.unpack('<2I',self._read(wrapper,8))
        if not _heap(table) or count not in (10,11):raise ValueError('Matériaux locaux non reconnus.')
        return table,count

    def combined_table(self,colour):
        if colour in self.combined:return self.combined[colour]
        entries=[]
        for model in self.models:
            pointer=self.table_for(model,colour)
            count=self.bridge.read_u32(pointer)
            entries.extend(struct.unpack('<'+'I'*(count*2),self._read(pointer+4,count*8)))
        if not 1<=len(entries)//2<=96:
            # 25/10: seen after the game restarted under a stale palette buffer.
            raise ValueError(f'Nombre de liaisons locales non reconnu ({len(entries)//2}).')
        data=struct.pack('<I',len(entries)//2)+struct.pack('<'+'I'*len(entries),*entries)
        address=self._alloc(len(data));self._write(address,data);self.combined[colour]=address
        return address

    def publish(self,colour):
        if colour not in COLOURS:raise ValueError('Couleur locale inconnue.')
        if colour==self.colour:return
        original=self.combined_table('green');selected=self.combined_table(colour)
        # The native helper changes only material command pointers, after the
        # local animations. Original game textures/commands remain untouched.
        control=self.buffer+0xB000
        if self.colour is None:self._write(control,struct.pack('<5I',0x4C50414C,selected,original,self.wall,0))
        elif not self.bridge.write_u32(control+4,selected):raise RuntimeError('Changement de couleur locale impossible.')
        if not self.bridge.write_u32(MAILBOX+0x164,control):raise RuntimeError('Publication de couleur locale impossible.')
        self.colour=colour
