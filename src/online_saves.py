"""Progression EUR : lecture de l'hôte, secours intégral et import d'un seul fichier."""
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import uuid
import zlib
from src.local_saves import SAVE_NAMES,validate_save,_atomic_write

TITLE='00040000000EC400'
MAX_BUNDLE=16384


def validate_slot(data):
    validate_save('Save_0.bin',data)
    for start,end in ((0xE00,0x13FC),(0x1400,0x15FC)):
        if zlib.crc32(data[start:end])!=struct.unpack_from('<I',data,end)[0]:
            raise ValueError('Un bloc de la progression est endommagé.')


def archive_for(emulator):
    from src.local_session import source_user_dir,settings_at
    user=source_user_dir(emulator);config=settings_at(user/'config/qt-config.ini')
    custom=(config.value('Data Storage/use_custom_storage',False,type=bool)
            and not config.value('Data Storage/use_custom_storage/default',False,type=bool))
    value=config.value('Data Storage/sdmc_directory','') if custom and not config.value('Data Storage/sdmc_directory/default',False,type=bool) else ''
    if value and not Path(value).is_absolute():raise ValueError('Choisissez un chemin SD absolu dans les réglages de stockage Azahar avant cet import.')
    # Azahar ignore un chemin personnalisé vide ou qui n'est plus un dossier.
    sdmc=Path(value) if value and Path(value).is_dir() else user/'sdmc'
    sdmc=sdmc.resolve()
    found=list(sdmc.glob('Nintendo 3DS/*/*/title/00040000/000ec400/data/00000001'))
    found=[p.resolve() for p in found if p.resolve().is_relative_to(sdmc)]
    if len(found)!=1:
        raise ValueError('Ouvrez une fois ALBW EUR dans cet Azahar pour créer ses sauvegardes.' if not found
                         else 'Plusieurs archives ALBW existent dans cet Azahar ; choisissez son dossier de données habituel.')
    return found[0]


def export_progress(archive,slot):
    if type(slot) is not int or not 0<=slot<=2:raise ValueError('L’hôte doit charger une partie puis enregistrer à une girouette.')
    path=Path(archive)/f'Save_{slot}.bin';raw=path.read_bytes();validate_slot(raw)
    if path.read_bytes()!=raw:raise ValueError('L’hôte enregistre : réessayez dans quelques secondes.')
    return json.dumps(dict(title=TITLE,slot=slot,sha256=hashlib.sha256(raw).hexdigest(),
        data=base64.b64encode(raw).decode()),separators=(',',':')).encode()


def decode_progress(bundle):
    if not isinstance(bundle,bytes) or len(bundle)>MAX_BUNDLE:raise ValueError('Transfert de progression invalide.')
    value=json.loads(bundle)
    if (not isinstance(value,dict) or set(value)!={'title','slot','sha256','data'} or value['title']!=TITLE
            or type(value['slot']) is not int or not 0<=value['slot']<=2):raise ValueError('La progression reçue ne correspond pas à ALBW EUR.')
    try:raw=base64.b64decode(value['data'],validate=True)
    except (ValueError,TypeError):raise ValueError('Progression reçue incomplète.')
    if hashlib.sha256(raw).hexdigest()!=value['sha256']:raise ValueError('L’empreinte de la progression ne correspond pas.')
    validate_slot(raw)
    return raw,value['slot']


def backup_progress(archive,backup_root):
    archive=Path(archive).resolve(strict=True);files={}
    for path in archive.iterdir():
        if not path.is_file():continue
        if path.is_symlink() or not path.resolve().is_relative_to(archive):raise ValueError('Un fichier de sauvegarde sort du dossier du jeu.')
        if path.stat().st_size>1024*1024:raise ValueError('Fichier inattendu dans les sauvegardes.')
        files[path.name]=path.read_bytes()
    if any(name not in files for name in SAVE_NAMES):raise ValueError('Les sauvegardes locales sont incomplètes ; ouvrez ALBW une fois.')
    # Une partie locale endommagée doit tout de même pouvoir être mise à l'abri.
    if any((archive/name).read_bytes()!=raw for name,raw in files.items()):raise ValueError('La partie enregistre encore ; réessayez.')
    backup=Path(backup_root)/f'progression-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}'
    backup.mkdir(parents=True,exist_ok=False)
    for name,raw in files.items():
        (backup/name).write_bytes(raw)
        if (backup/name).read_bytes()!=raw:raise OSError('La copie de secours n’est pas confirmée.')
    metadata=archive.with_name(archive.name+'.metadata')
    if metadata.is_file():
        if metadata.is_symlink() or metadata.stat().st_size>1024*1024:raise ValueError('Métadonnées de sauvegarde inattendues.')
        raw=metadata.read_bytes();(backup/'archive.metadata').write_bytes(raw)
        if (backup/'archive.metadata').read_bytes()!=raw:raise OSError('La copie des métadonnées n’est pas confirmée.')
    manifest=dict(archive=str(archive),state='backup',files={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()})
    _atomic_write(backup/'verification.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode())
    return backup


def install_progress(archive,bundle,slot,backup_root,*,is_running):
    raw,_=decode_progress(bundle)
    if type(slot) is not int or not 1<=slot<=3:raise ValueError('Choisissez le fichier 1, 2 ou 3.')
    if is_running():raise RuntimeError('Fermez proprement Azahar avant d’installer la progression.')
    archive=Path(archive).resolve(strict=True);backup=backup_progress(archive,backup_root)
    before={name:(backup/name).read_bytes() for name in SAVE_NAMES}
    if is_running() or any((archive/name).read_bytes()!=data for name,data in before.items()):
        raise RuntimeError('Azahar a rouvert ou les sauvegardes ont changé ; aucun fichier remplacé.')
    target=archive/f'Save_{slot-1}.bin'
    if target.is_symlink() or not target.resolve().is_relative_to(archive):raise ValueError('Emplacement de sauvegarde refusé.')
    manifest=json.loads((backup/'verification.json').read_bytes());manifest.update(slot=slot,state='prepared')
    _atomic_write(backup/'verification.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode())
    try:
        _atomic_write(target,raw)
        if target.read_bytes()!=raw:raise OSError('L’écriture de la progression n’est pas confirmée.')
    except Exception:
        _atomic_write(target,before[target.name]);manifest['state']='rolled_back'
        _atomic_write(backup/'verification.json',json.dumps(manifest).encode());raise
    manifest['state']='installed';_atomic_write(backup/'verification.json',json.dumps(manifest).encode())
    return backup
