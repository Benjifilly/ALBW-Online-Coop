"""Byte-for-byte repair of isolated test saves, with backup and rollback.

Only the selected title under player2/user/sdmc is replaced. The original
installation and player1 remain read-only; no save fields are rewritten.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import struct
import uuid
import zlib

SAVE_NAMES=('Save_0.bin','Save_1.bin','Save_2.bin','SaveCommon.bin')


def _inside(path,root):
    path=Path(path).resolve();root=Path(root).resolve()
    if not path.is_relative_to(root):raise ValueError('Le chemin sort du dossier des essais.')
    return path


def validate_save(name,data):
    if name=='SaveCommon.bin':
        if (len(data)!=0x200 or struct.unpack_from('<I',data)[0]!=4
                or zlib.crc32(data[:0x1FC])!=struct.unpack_from('<I',data,0x1FC)[0]):
            raise ValueError('Le fichier de sélection de la source est endommagé.')
    elif name in SAVE_NAMES:
        # The original EUR loader validates the first 0xE00-byte SaveData
        # block. Remaining bytes are copied verbatim, never reinterpreted.
        if (len(data)!=0x1604
                or zlib.crc32(data[:0xDFC])!=struct.unpack_from('<I',data,0xDFC)[0]):
            raise ValueError('Une sauvegarde de la source est endommagée : '+name)


def running_player_pids(lab,index):
    import win32com.client
    from src.overlay.window_tracker import _process_path
    expected={str(Path(lab).resolve()/f'player{index}'/name).lower()
              for name in ('azahar.exe','azahar-qt.exe')}
    wmi=win32com.client.GetObject('winmgmts:')
    processes=wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='azahar.exe' OR Name='azahar-qt.exe'")
    return [int(p.ProcessId) for p in processes if _process_path(int(p.ProcessId)) in expected]


def _atomic_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def repair_player_two(lab,title_id,*,backup_root=None):
    """Explicit repair only: never invoked as a side effect of normal launch."""
    lab=Path(lab).resolve(strict=True)
    if type(title_id) is not int or title_id not in (0x00040000000EC400,0x00040000000EC300):
        raise ValueError('Choisissez une version ALBW reconnue.')
    if running_player_pids(lab,2):
        raise RuntimeError('Fermez le jeu du joueur 2 avant de réparer sa sauvegarde.')
    source=_inside(lab/'player1/user/sdmc',lab)
    destination=_inside(lab/'player2/user/sdmc',lab)
    pattern=f'Nintendo 3DS/*/*/title/{title_id>>32:08x}/{title_id&0xFFFFFFFF:08x}/data/00000001'
    archives=sorted(source.glob(pattern))
    if not archives:raise ValueError('La sauvegarde du joueur 1 est introuvable.')
    planned={};source_paths={}
    for archive in archives:
        archive=_inside(archive,source)
        for name in SAVE_NAMES:
            path=_inside(archive/name,source)
            if not path.is_file():raise ValueError('La source est incomplète : '+name)
            data=path.read_bytes();validate_save(name,data)
            relative=path.relative_to(source);planned[relative]=data;source_paths[relative]=path
        metadata=archive.with_name(archive.name+'.metadata')
        if metadata.is_file():
            metadata=_inside(metadata,source);relative=metadata.relative_to(source)
            planned[relative]=metadata.read_bytes();source_paths[relative]=metadata
    for relative in planned:_inside(destination/relative,destination)
    before={relative:(destination/relative).read_bytes() if (destination/relative).is_file() else None
            for relative in planned}
    folder=Path(backup_root or lab.parents[1]/'backups').resolve()
    backup=folder/f'local-save-repair-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}'
    backup.mkdir(parents=True,exist_ok=False)
    manifest=[]
    for owner,files in (('source-player1',planned),('before-player2',before)):
        for relative,data in files.items():
            if data is None:continue
            path=backup/owner/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            if path.read_bytes()!=data:raise OSError('La copie de secours n’est pas confirmée.')
            manifest.append(dict(file=(Path(owner)/relative).as_posix(),bytes=len(data),
                                 sha256=hashlib.sha256(data).hexdigest()))
    report=dict(source=str(source),destination=str(destination),files=manifest,state='prepared')
    record=backup/'verification.json'
    record.write_text(json.dumps(report,indent=2),encoding='utf-8')
    # Confirm a stable donor and destination immediately before committing.
    if any(path.read_bytes()!=planned[relative] for relative,path in source_paths.items()):
        raise RuntimeError('Le joueur 1 a enregistré pendant la copie ; recommencez.')
    if running_player_pids(lab,2):raise RuntimeError('Le joueur 2 a été rouvert pendant la préparation.')
    if any(((destination/relative).read_bytes() if (destination/relative).is_file() else None)!=data
           for relative,data in before.items()):
        raise RuntimeError('La destination a changé pendant la préparation.')
    changed=[]
    try:
        for relative,data in planned.items():
            changed.append(relative)
            _atomic_write(destination/relative,data)
            if (destination/relative).read_bytes()!=data:raise OSError('La sauvegarde copiée diffère de la source.')
    except Exception:
        for relative in reversed(changed):
            previous=before[relative]
            if previous is None:(destination/relative).unlink(missing_ok=True)
            else:_atomic_write(destination/relative,previous)
        report['state']='rolled_back';record.write_text(json.dumps(report,indent=2),encoding='utf-8')
        raise
    report['state']='verified';record.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return dict(backup=str(backup),files=len(planned),verified=True)
