"""Build the checked-in render hook. Development only; never connects to a game."""
import hashlib
from pathlib import Path
from keystone import Ks, KS_ARCH_ARM, KS_MODE_ARM

def build():
    from scripts.build_item_visuals import build as build_items
    build_items()
    folder = Path(__file__).resolve().parents[1] / 'src/memory'
    source = (folder / 'remote_model.arm').read_text()
    machine, count = Ks(KS_ARCH_ARM, KS_MODE_ARM).asm(source, addr=0x692200)
    data = bytes(machine)
    if len(data)>0x440:raise ValueError('Renderer overlaps private Fresco heap entry')
    heap,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'fresco_heap.arm').read_text(),addr=0x692640)
    data=data.ljust(0x440,b'\0')+bytes(heap)
    if len(data)>0x4E0:raise ValueError('Fresco heap entry overlaps RTT presentation')
    present,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'fresco_present.arm').read_text(),addr=0x6926E0)
    data=data.ljust(0x4E0,b'\0')+bytes(present)
    if len(data)>0x5E0:raise ValueError('RTT presentation overlaps surface commit')
    commit,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'fresco_publish.arm').read_text(),addr=0x6927E0)
    data=data.ljust(0x5E0,b'\0')+bytes(commit)
    if len(data)>0x600:raise ValueError('RTT presentation overlaps local colour entry')
    colour,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'local_colour.arm').read_text(),addr=0x692800)
    data=data.ljust(0x600,b'\0')+bytes(colour)
    if len(data)>0x680:raise ValueError('Colour entry overlaps surface publication')
    surface,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'fresco_surface.arm').read_text(),addr=0x692880)
    data=data.ljust(0x680,b'\0')+bytes(surface)
    if len(data)>0x800:raise ValueError('Surface publication overlaps visual helper')
    if len(surface)>0xC4:raise ValueError('Surface publication overlaps the closed job table')
    table,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'native_job_functions.arm').read_text(),addr=0x692944)
    if len(table)>0x7C:raise ValueError('Closed job table overlaps particle retirement')
    data=data[:0x744]+bytes(table)+data[0x744+len(table):]
    retire,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'effect_retire.arm').read_text(),addr=0x6929C0)
    if len(retire)>0x40:raise ValueError('Particle retirement overlaps visual helper')
    data=data.ljust(0x7C0,b'\0')+bytes(retire)
    visuals,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'remote_visuals.arm').read_text(),addr=0x692A00)
    data=data.ljust(0x800,b'\0')+bytes(visuals)
    if len(data)>0x940:raise ValueError('Visual helper overlaps local painting colours')
    local,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'local_painting.arm').read_text(),addr=0x692B40)
    data=data.ljust(0x940,b'\0')+bytes(local)
    if len(local)>0xA0:raise ValueError('Local painting overlaps particle unload guard')
    unload,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'effect_unload.arm').read_text(),addr=0x692BE0)
    data=data.ljust(0x9E0,b'\0')+bytes(unload)
    if len(data)>0xA00:raise ValueError('Visual helper overlaps development jobs')
    jobs,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'native_jobs.arm').read_text(),addr=0x692C00)
    data=data.ljust(0xA00,b'\0')+bytes(jobs)
    if len(jobs)>0x19C:raise ValueError('Closed jobs overlap the effect matrix helper')
    matrix,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'effect_matrix.arm').read_text(),addr=0x692D9C)
    data=data.ljust(0xB9C,b'\0')+bytes(matrix)
    if len(data)>0xC00:raise ValueError('Development jobs overlap painting presentation')
    wall,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'wall_model.arm').read_text(),addr=0x692E00)
    data=data.ljust(0xC00,b'\0')+bytes(wall)
    # The shared phase helper is called by the painting renderer.
    if data[0x8C4:0x8C8]!=bytes.fromhex('70402de9'):
        raise ValueError('Shared animation helper moved')
    if len(data) > 0xE00:
        raise ValueError('Hook exceeds verified executable padding')
    effects,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'attack_effects.arm').read_text(),addr=0x692100)
    if len(effects)>0xE0:raise ValueError('Particle presenter overlaps unload entry')
    entry,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'effect_unload_entry.arm').read_text(),addr=0x6921E0)
    auxiliary=bytes(effects).ljust(0xE0,b'\0')+bytes(entry)
    if len(auxiliary)>0x100:raise ValueError('Particle entries overlap main renderer')
    world,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'world_entry.arm').read_text(),addr=0x7AFD00)
    if len(world)>0x80:raise ValueError('World entry overlaps capture entry')
    capture,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'world_grass_entry.arm').read_text(),addr=0x7AFD80)
    world=bytes(world).ljust(0x80,b'\0')+bytes(capture)
    if len(world)>0x100:raise ValueError('Grass observation overlaps combat observation')
    enemy,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'world_enemy_entry.arm').read_text(),addr=0x7AFE00)
    world=world.ljust(0x100,b'\0')+bytes(enemy)
    if len(world)>0x180:raise ValueError('Damage observation overlaps contact observation')
    hit,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'world_hit_entry.arm').read_text(),addr=0x7AFE80)
    world=world.ljust(0x180,b'\0')+bytes(hit)
    if len(world)>0x280:raise ValueError('Contact entry overlaps enemy movement entry')
    pose_entry,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'enemy_pose_entry.arm').read_text(),addr=0x7AFF80)
    world=world.ljust(0x280,b'\0')+bytes(pose_entry)
    if len(world)>0x2F0:raise ValueError('Movement entry overlaps original integrator trampoline')
    trampoline,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm('push {r4,r5,lr}; b 0x4f48cc',addr=0x7AFFF0)
    world=world.ljust(0x2F0,b'\0')+bytes(trampoline)
    if len(world)>0x2FC:raise ValueError('World entry overlaps owned pointer')
    payload=b''
    from src.mass_families import native_table,prop_native_table
    for name,start,limit in (('world_dispatch',0,0x400),('world_context',0x400,0x600),
                           ('world_grass_capture',0x600,0xA00),('world_enemy_capture',0xA00,0xC00),
                           ('world_hit_capture',0xC00,0x1000),('world_mass',0x1000,0x1400),
                           ('world_prop',0x1400,0x1800)):
        source=(folder/(name+'.arm')).read_text().replace('MASS_FAMILIES',native_table()).replace('PROP_TABLE',prop_native_table())
        block,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm(source,addr=start)
        payload=payload.ljust(start,b'\0')+bytes(block)
        if len(payload)>limit:raise ValueError('World payload overlaps next entry: '+name)
    (folder/'world_entry.bin').write_bytes(world)
    pose,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'enemy_pose.arm').read_text(),addr=0)
    if len(pose)>0x1000:raise ValueError('Enemy movement exceeds owned code region')
    (folder/'enemy_pose.bin').write_bytes(bytes(pose))
    health,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'enemy_health.arm').read_text(),addr=0)
    if len(health)>0x1000:raise ValueError('Enemy health exceeds owned code region')
    (folder/'enemy_health.bin').write_bytes(bytes(health))
    foliage,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'foliage_motion.arm').read_text(),addr=0)
    if len(foliage)>0x1000:raise ValueError('Foliage response exceeds owned code region')
    (folder/'foliage_motion.bin').write_bytes(bytes(foliage))
    shrub,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'shrub_motion.arm').read_text(),addr=0)
    if len(shrub)>0x1000:raise ValueError('Shrub response exceeds owned code region')
    (folder/'shrub_motion.bin').write_bytes(bytes(shrub))
    cuts,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'shrub_capture.arm').read_text(),addr=0)
    if len(cuts)>0x1000:raise ValueError('Shrub observation exceeds owned code region')
    (folder/'shrub_capture.bin').write_bytes(bytes(cuts))
    sign,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'sign_capture.arm').read_text(),addr=0)
    if len(sign)>0x1000:raise ValueError('Signboard observation exceeds owned code region')
    (folder/'sign_capture.bin').write_bytes(bytes(sign))
    # One Mass observer source, assembled for each native state store.
    from src.memory.capture_hooks import MASS_SITES
    mass_source=(folder/'mass_capture.arm').read_text()
    for site in MASS_SITES:
        source=mass_source
        for key,value in site.substitutions().items():source=source.replace(key,value)
        built,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm(source,addr=0)
        if len(built)>0x1000:raise ValueError('Mass observation exceeds owned code region')
        (folder/f'{site.name}.bin').write_bytes(bytes(built))
    from src.memory.capture_hooks import CARRY_SITES
    carry_source=(folder/'carry_bind.arm').read_text()
    for site in CARRY_SITES:
        source=carry_source
        for key,value in site.substitutions().items():source=source.replace(key,value)
        built,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm(source,addr=0)
        if len(built)>0xF00:raise ValueError('Carried binding overlaps its table')
        (folder/f'{site.name}.bin').write_bytes(bytes(built))
    from src.memory.capture_hooks import REACTION_SITES
    reaction_source=(folder/'reaction_capture.arm').read_text()
    for site in REACTION_SITES:
        source=reaction_source
        for key,value in site.substitutions().items():source=source.replace(key,value)
        built,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm(source,addr=0)
        if len(built)>0x1000:raise ValueError('Reaction observation exceeds owned code region')
        (folder/f'{site.name}.bin').write_bytes(bytes(built))
    machine,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'machine_capture.arm').read_text(),addr=0)
    if len(machine)>0x1000:raise ValueError('Machine observation exceeds owned code region')
    (folder/'machine_capture.bin').write_bytes(bytes(machine))
    from src.combat_profiles import SWORD_TARGETS
    vtables=sorted({target.vtable for target in SWORD_TARGETS.values()})
    table='\n'.join(f'.word {v:#x}' for v in vtables)+'\n.word 0'
    nearest,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'nearest_player.arm').read_text().replace('ENEMY_VTABLES',table),addr=0)
    if len(nearest)>0x1000:raise ValueError('Nearest-player hook exceeds its region')
    (folder/'nearest_player.bin').write_bytes(bytes(nearest))
    life,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'player_life.arm').read_text(),addr=0)
    if len(life)>0x1000:raise ValueError('Player life hook exceeds its region')
    (folder/'player_life.bin').write_bytes(bytes(life))
    party,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'party_native.arm').read_text(),addr=0)
    if len(party)>0x1000:raise ValueError('Party return exceeds owned region')
    (folder/'party_native.bin').write_bytes(bytes(party))
    from src.memory.world_layout import reserve
    from src.memory.player_life import CONTROL
    delta=reserve('input_lock')-(reserve('player_life')+CONTROL)
    lock,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'input_lock.arm').read_text().replace('LIFE_CONTROL_DELTA',hex(delta)),addr=0)
    if len(lock)>0x1000:raise ValueError('Input lock exceeds its region')
    (folder/'input_lock.bin').write_bytes(bytes(lock))
    delta=reserve('life_hide')-(reserve('player_life')+CONTROL)
    hide,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'life_hide.arm').read_text().replace('LIFE_CONTROL_DELTA',hex(delta)),addr=0)
    if len(hide)>0x1000:raise ValueError('Life hide exceeds its region')
    (folder/'life_hide.bin').write_bytes(bytes(hide))
    light,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'model_light.arm').read_text(),addr=0)
    if len(light)>0x1000:raise ValueError('Model lighting exceeds its region')
    (folder/'model_light.bin').write_bytes(bytes(light))
    mirror,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'carried_mirror.arm').read_text(),addr=0)
    if len(mirror)>0x700:raise ValueError('Carried mirror overlaps its break table')
    (folder/'carried_mirror.bin').write_bytes(bytes(mirror))
    restore_source=(folder/'grass_restore.arm').read_text()
    restore_source=restore_source.replace('RESTORE_DROP_ORIGINAL','push {r4,lr}')
    restore,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm(restore_source,addr=0)
    (folder/'grass_restore.bin').write_bytes(bytes(restore))
    loot,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'shared_loot.arm').read_text(),addr=0)
    if len(loot)>0x8000:raise ValueError('Shared loot code exceeds its owned region')
    (folder/'shared_loot.bin').write_bytes(bytes(loot))
    progress,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'shared_progress.arm').read_text(),addr=0)
    if len(progress)>0x3000:raise ValueError('Shared progress code overlaps its capture ring')
    (folder/'shared_progress.bin').write_bytes(bytes(progress))
    chain,_=Ks(KS_ARCH_ARM,KS_MODE_ARM).asm((folder/'wall_chain.arm').read_text(),addr=0)
    if len(chain)>0x100:raise ValueError('Wall chain checker exceeds its page slot')
    (folder/'wall_chain.bin').write_bytes(bytes(chain))
    from src.memory.enemy_impact_trial import compile_payload
    (folder/'enemy_impact_trial.bin').write_bytes(compile_payload())
    (folder/'world_payload.bin').write_bytes(payload)
    (folder/'attack_effects.bin').write_bytes(auxiliary)
    (folder / 'remote_model.bin').write_bytes(data)
    # Every player's copy is checked against it (install_check, updater repair).
    from src.install_check import write_manifest
    write_manifest(folder)
    print(len(data), 'bytes', hashlib.sha256(data).hexdigest())
    return data

if __name__ == '__main__':
    build()
