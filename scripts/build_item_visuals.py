"""Build only the owned held-tool presenter; no emulator connection."""
from pathlib import Path
from keystone import Ks,KS_ARCH_ARM,KS_MODE_ARM

def build():
    folder=Path(__file__).resolve().parents[1]/'src/memory'
    data=bytes(Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'item_visuals.arm').read_text(),addr=0)[0])
    if len(data)>0x800:raise ValueError('Held tools overlap the private descriptor table.')
    (folder/'item_visuals.bin').write_bytes(data)
    return data

if __name__=='__main__':print(len(build()))
