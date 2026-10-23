"""Mise à jour publique facultative, avec sauvegarde et retour arrière."""
import base64
from contextlib import contextmanager
from datetime import datetime
import io
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import stat
import subprocess
import sys
import threading
import urllib.request
import uuid
import zipfile
from src.version import ROOT,read_version,version_tuple
from src.local_saves import _atomic_write

REPO='Benjifilly/ALBW-Online-Coop'
API=f'https://api.github.com/repos/{REPO}'
MAX_ZIP=64*1024*1024


def fetch(url,limit=2*1024*1024):
    request=urllib.request.Request(url,headers={'User-Agent':'ALBW-Online-Coop','Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=15 if limit==MAX_ZIP else 2) as response:
        raw=response.read(limit+1)
    if len(raw)>limit:raise ValueError('Téléchargement trop volumineux.')
    return raw


def zip_revision_matches(root,revision):
    tree=json.loads(fetch(API+'/git/trees/'+revision+'?recursive=1'))
    entries=tree['tree']
    if (tree.get('truncated') or not isinstance(entries,list) or len(entries)>8192
            or any(not isinstance(e,dict) or not isinstance(e.get('path'),str) for e in entries)):
        raise ValueError('Liste de fichiers GitHub incomplète.')
    files=[e for e in entries if e.get('type')=='blob' and allowed(e.get('path',''))]
    if not {'VERSION','run_coop.bat','requirements.txt','src/main.py','src/launcher.py'}<={e['path'] for e in files}:
        raise ValueError('Publication incomplète.')
    for entry in files:
        path=Path(root)/entry['path']
        if not path.resolve().is_relative_to(Path(root).resolve()):return False
        if not path.is_file() or path.stat().st_size>32*1024*1024:return False
        raw=path.read_bytes();digest=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if digest!=entry['sha']:return False
    return True


def latest(root=ROOT):
    root=Path(root)
    try:
        commit=json.loads(fetch(API+'/commits/main'));revision=commit['sha']
        if not re.fullmatch('[0-9a-f]{40}',revision):return None
        content=json.loads(fetch(API+'/contents/VERSION?ref='+revision))
        remote=base64.b64decode(content['content'],validate=False).decode('utf-8').strip()
        if version_tuple(remote) is None:return None
        local=read_version(root);stamp=root/'.local/updater.json'
        try:installed=json.loads(stamp.read_bytes()).get('revision')
        except (OSError,ValueError,AttributeError):installed=None
        if installed is None and (root/'.git').exists():
            process=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,timeout=2,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            candidate=process.stdout.decode().strip()
            if re.fullmatch('[0-9a-f]{40}',candidate):installed=candidate
        if version_tuple(remote)<version_tuple(local):return None
        changed=version_tuple(remote)>version_tuple(local) or installed is not None and revision!=installed
        if not changed and installed is None:changed=not zip_revision_matches(root,revision)
        if not changed:
            if not stamp.exists():_atomic_write(stamp,json.dumps(dict(revision=revision)).encode())
            return None
        return dict(version=remote,revision=revision,url=f'https://codeload.github.com/{REPO}/zip/{revision}')
    except (OSError,ValueError,KeyError,TypeError,UnicodeError,subprocess.SubprocessError):return None


def allowed(path):
    p=PurePosixPath(path)
    if any(part in ('','..','.') or ':' in part or '\\' in part for part in p.parts) or p.is_absolute():return False
    if any(part.lower().split('.')[0] in ('con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10)))
           or part.endswith((' ','.')) for part in p.parts):return False
    if any(part.startswith('.') or part.lower()=='__pycache__' or part.lower().endswith(('.patch-owner','.pyc','.pyo')) for part in p.parts):return False
    return (len(p.parts)==1 and (p.name in ('VERSION','README.md','ARCHITECTURE.md') or p.suffix=='.bat'
            or p.name.startswith('requirements') and p.suffix=='.txt')
        or len(p.parts)>1 and p.parts[0] in ('src','assets')
        or len(p.parts)==2 and p.parts[0]=='scripts' and p.name.startswith('build_') and p.suffix=='.py'
        or len(p.parts)>2 and p.parts[:2]==('docs','images'))


def unpack(raw,expected):
    result={};seen=set();total=0;top=None
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for entry in archive.infolist():
            if entry.is_dir():continue
            parts=PurePosixPath(entry.filename).parts
            if len(parts)<2:raise ValueError('Archive de mise à jour invalide.')
            if top is None:top=parts[0]
            if parts[0]!=top or stat.S_ISLNK(entry.external_attr>>16):raise ValueError('Lien ou racine inattendu dans la mise à jour.')
            name='/'.join(parts[1:])
            if name.lower() in seen:raise ValueError('Deux fichiers de la mise à jour portent le même nom.')
            seen.add(name.lower())
            if not allowed(name):
                # Les dossiers privés ne sont jamais installés, même s'ils apparaissent dans le ZIP.
                if '..' in parts or '\\' in entry.filename:raise ValueError('Chemin refusé dans la mise à jour.')
                continue
            total+=entry.file_size
            if entry.file_size>32*1024*1024 or total>256*1024*1024:raise ValueError('Archive trop volumineuse.')
            result[name]=archive.read(entry)
    if any(name not in result for name in ('VERSION','run_coop.bat','requirements.txt','src/main.py','src/launcher.py')):
        raise ValueError('La mise à jour est incomplète.')
    if result['VERSION'].decode('utf-8').strip()!=expected:raise ValueError('La version téléchargée a changé.')
    return result


@contextmanager
def update_lock(root):
    path=Path(root)/'.local/update.lock';path.parent.mkdir(parents=True,exist_ok=True)
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise RuntimeError('Une mise à jour est déjà en cours. Si elle a été interrompue, retirez .local/update.lock puis réessayez.') from None
    try:
        os.write(fd,str(os.getpid()).encode());os.close(fd);fd=None
        yield
    finally:
        if fd is not None:os.close(fd)
        path.unlink(missing_ok=True)


def install(raw,release,root=ROOT,*,dependencies=None):
    with update_lock(root):return _install(raw,release,root,dependencies=dependencies)


def _install(raw,release,root,*,dependencies=None):
    root=Path(root).resolve();planned=unpack(raw,release['version'])
    if list((root/'.local/profiles').glob('*.patch-owner')):
        raise RuntimeError('Fermez normalement le compagnon avant la mise à jour, puis relancez run_coop.bat.')
    before={};backup=root/'backups'/f'mise-a-jour-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}'
    for name in planned:
        path=root/name
        if not path.resolve().is_relative_to(root):raise ValueError('Un dossier du projet pointe hors de son installation.')
        before[name]=path.read_bytes() if path.is_file() else None
    backup.mkdir(parents=True,exist_ok=False)
    for name,data in before.items():
        if data is not None:
            path=backup/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            if path.read_bytes()!=data:raise OSError('La copie de secours de la mise à jour a échoué.')
    record=dict(state='prepared',version=release['version'],revision=release['revision'],created=[n for n,d in before.items() if d is None])
    _atomic_write(backup/'verification.json',json.dumps(record,indent=2).encode())
    changed=[];deps_started=False
    def pip(path):subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','-r',str(path)],check=True)
    dependencies=dependencies or pip
    try:
        for name,data in planned.items():
            changed.append(name);_atomic_write(root/name,data)
            if (root/name).read_bytes()!=data:raise OSError('Écriture de mise à jour non confirmée.')
        if before.get('requirements.txt')!=planned['requirements.txt']:
            deps_started=True;dependencies(root/'requirements.txt')
        hashes={name:hashlib.sha256(data).hexdigest() for name,data in planned.items()}
        _atomic_write(root/'.local/updater.json',json.dumps(dict(revision=release['revision'],files=hashes)).encode())
    except Exception as exc:
        for name in reversed(changed):
            if before[name] is None:(root/name).unlink(missing_ok=True)
            else:_atomic_write(root/name,before[name])
        record['state']='rolled_back';_atomic_write(backup/'verification.json',json.dumps(record).encode())
        if deps_started and before.get('requirements.txt') is not None:
            try:dependencies(root/'requirements.txt')
            except Exception:raise RuntimeError('Les fichiers précédents sont restaurés. La réinstallation des composants a échoué ; vérifiez Internet puis relancez run_coop.bat.') from exc
        raise
    record['state']='installed';_atomic_write(backup/'verification.json',json.dumps(record).encode())
    return backup


def main():
    # Ne pas remplacer une copie de travail ou une publication en préparation.
    if (ROOT/'scripts/verify_coop.py').exists():return 0
    if (ROOT/'.git').exists():
        try:
            dirty=subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,timeout=2,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout
            if dirty and not managed_copy(ROOT):return 0
        except (OSError,subprocess.SubprocessError):return 0
    found=[]
    worker=threading.Thread(target=lambda:found.append(latest()),daemon=True);worker.start();worker.join(4)
    if not found or not found[0]:return 0
    release=found[0]
    version=read_version();label=f"{release['version']} · révision {release['revision'][:7]}"
    if version!=release['version']:label=version+' → '+label
    print(f'\n  Mise à jour disponible : {label}.')
    try:answer=input('  Mettre à jour maintenant ? (O/N) ').strip().lower()
    except (EOFError,KeyboardInterrupt):return 0
    if answer not in ('o','oui'):return 0
    try:
        raw=fetch(release['url'],MAX_ZIP);backup=install(raw,release)
        print(f'  Mise à jour installée. Copie précédente : {backup}\n  Relancement…')
        return 10
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,zipfile.BadZipFile) as exc:
        print(f'  Mise à jour annulée : {exc}\n  Copie de secours disponible dans backups/.')
        return 0


def managed_copy(root):
    # Reconnaître nos propres remplacements, sans effacer les modifications personnelles.
    try:
        files=json.loads((Path(root)/'.local/updater.json').read_bytes())['files']
        if not isinstance(files,dict) or not files:return False
        if any(not allowed(name) or hashlib.sha256((Path(root)/name).read_bytes()).hexdigest()!=digest for name,digest in files.items()):return False
        changes=subprocess.run(['git','diff','--name-only','HEAD'],cwd=root,capture_output=True,timeout=2,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout.decode().splitlines()
        untracked=subprocess.run(['git','ls-files','--others','--exclude-standard'],cwd=root,capture_output=True,timeout=2,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout.decode().splitlines()
        return all(name in files for name in changes+untracked)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):return False


if __name__=='__main__':sys.exit(main())
