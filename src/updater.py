"""Mise à jour publique facultative, avec retour arrière."""
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
import zipfile
from src.version import ROOT,read_version,version_tuple
from src.local_saves import _atomic_write
from src.console import BOLD,COLOR,GOLD,GREEN,MUTED,PALE,RED,RESET

REPO='Benjifilly/ALBW-Online-Coop'
API=f'https://api.github.com/repos/{REPO}'
MAX_ZIP=64*1024*1024
BOX=66
NEWS=6
NO_WINDOW=getattr(subprocess,'CREATE_NO_WINDOW',0)


def fetch(url,limit=2*1024*1024):
    request=urllib.request.Request(url,headers={'User-Agent':'ALBW-Online-Coop','Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=15 if limit==MAX_ZIP else 2) as response:
        raw=response.read(limit+1)
    if len(raw)>limit:raise ValueError('Téléchargement trop volumineux.')
    return raw


def download(url,progress=lambda done,total:None):
    request=urllib.request.Request(url,headers={'User-Agent':'ALBW-Online-Coop'})
    with urllib.request.urlopen(request,timeout=15) as response:
        total=int(response.headers.get('Content-Length') or 0) or None;parts=[];done=0
        while chunk:=response.read(256*1024):
            done+=len(chunk)
            if done>MAX_ZIP:raise ValueError('Téléchargement trop volumineux.')
            parts.append(chunk);progress(done,total)
    return b''.join(parts)


def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,capture_output=True,timeout=2,creationflags=NO_WINDOW)


def git_head(root):
    """(commit, copie propre) d'une copie Git, sinon None."""
    if not (Path(root)/'.git').exists():return None
    try:
        head=git(root,'rev-parse','HEAD').stdout.decode().strip()
        if not re.fullmatch('[0-9a-f]{40}',head):return None
        return head,not git(root,'status','--porcelain').stdout.strip()
    except (OSError,subprocess.SubprocessError):return None


def _process_name(pid):
    """Exécutable d'un processus encore vivant, '' sinon."""
    if type(pid) is not int or pid<=0:return ''
    import ctypes
    from ctypes import wintypes
    kernel32=ctypes.windll.kernel32
    handle=kernel32.OpenProcess(0x1000,False,pid)
    if not handle:return ''
    try:
        code=wintypes.DWORD();size=wintypes.DWORD(1024);buffer=ctypes.create_unicode_buffer(1024)
        # Un processus terminé dont une poignée reste ouverte n'est plus vivant.
        if not kernel32.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return ''
        if not kernel32.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)):return ''
        return buffer.value.rsplit('\\',1)[-1].lower()
    finally:kernel32.CloseHandle(handle)


def patch_owners(root):
    """Profils dont le rendu est encore installé dans un jeu ouvert.

    Un marqueur reste après un jeu fermé ; le compagnon suivant le remplace.
    Seul un Azahar ou un compagnon encore vivant interdit la mise à jour.
    """
    owners=[]
    for marker in sorted((Path(root)/'.local/profiles').glob('*.patch-owner')):
        try:
            owner=json.loads(marker.read_bytes())
            alive=(_process_name(owner.get('emulator_pid')) in ('azahar.exe','azahar-qt.exe')
                   or _process_name(owner.get('companion_pid')) in ('python.exe','pythonw.exe'))
        except Exception:alive=True  # illisible : prudence
        if alive:owners.append(marker.stem)
    return owners


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
        # Une seule requête : la dernière révision et les titres des précédentes.
        commits=json.loads(fetch(API+'/commits?sha=main&per_page=20'))
        if not isinstance(commits,list) or not commits:return None
        revision=commits[0]['sha']
        if not re.fullmatch('[0-9a-f]{40}',revision):return None
        content=json.loads(fetch(API+'/contents/VERSION?ref='+revision))
        remote=base64.b64decode(content['content'],validate=False).decode('utf-8').strip()
        if version_tuple(remote) is None:return None
        local=read_version(root);stamp=root/'.local/updater.json'
        try:installed=json.loads(stamp.read_bytes()).get('revision')
        except (OSError,ValueError,AttributeError):installed=None
        head=git_head(root)
        if head and head[1]:
            # Copie Git propre : son HEAD fait foi, y compris quand elle a
            # publié elle-même cette révision ou qu'elle est déjà plus récente.
            if head[0]==revision or git(root,'merge-base','--is-ancestor',revision,'HEAD').returncode==0:return None
            installed=head[0]
        elif installed is None and head:installed=head[0]
        if version_tuple(remote)<version_tuple(local):return None
        changed=version_tuple(remote)>version_tuple(local) or installed is not None and revision!=installed
        if not changed and installed is None:changed=not zip_revision_matches(root,revision)
        # Une copie ZIP aux fichiers natifs abîmés (copie partielle, antivirus)
        # réinstalle sa propre révision ; une copie Git reste à Git.
        from src.install_check import damaged_binaries
        repair=[] if head else damaged_binaries(root/'src'/'memory')
        if repair:changed=True
        if not changed:
            if not stamp.exists():_atomic_write(stamp,json.dumps(dict(revision=revision)).encode())
            return None
        news=[]
        for row in commits:
            if installed is None and news or row.get('sha')==installed:break
            title=str(row['commit']['message']).strip().split('\n',1)[0].strip()
            if title:news.append(title)
        return dict(version=remote,revision=revision,url=f'https://codeload.github.com/{REPO}/zip/{revision}',
                    local=local,installed=installed,date=commits[0]['commit']['committer']['date'],repair=repair,
                    news=news,complete=installed is not None and any(row.get('sha')==installed for row in commits))
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
    if patch_owners(root):
        raise RuntimeError('Un jeu est encore ouvert avec le compagnon : fermez Azahar, puis relancez run_coop.bat.')
    # Les fichiers remplacés restent en mémoire pour le retour arrière : une
    # copie sur disque n'apporterait rien, le dépôt public garde chaque version.
    before={}
    for name in planned:
        path=root/name
        if not path.resolve().is_relative_to(root):raise ValueError('Un dossier du projet pointe hors de son installation.')
        before[name]=path.read_bytes() if path.is_file() else None
    changed=[];deps_started=False
    dependencies=dependencies or pip_install
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
        if deps_started and before.get('requirements.txt') is not None:
            try:dependencies(root/'requirements.txt')
            except Exception:raise RuntimeError('Les fichiers précédents sont restaurés. La réinstallation des composants a échoué ; vérifiez Internet puis relancez run_coop.bat.') from exc
        raise
    return sum(before[name]!=data for name,data in planned.items())


def pip_install(path):
    subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','-r',str(path)],check=True)


def say(text=''):
    try:print(text,flush=True)
    except (OSError,UnicodeEncodeError):pass


def status(text):
    """Une ligne réécrite sur place : recherche, téléchargement."""
    try:sys.stdout.write(f'\r  {text}\033[K' if COLOR else f'\r  {text:<{BOX}}');sys.stdout.flush()
    except (OSError,UnicodeEncodeError,AttributeError):pass


def ask(prompt):
    try:return input(f'  {PALE}{prompt}{RESET}').strip().lower()
    except (EOFError,KeyboardInterrupt):return None


def clip(text,width):return text if len(text)<=width else text[:width-1]+'…'
def megabytes(size):return f'{size/1048576:.1f}'.replace('.',',')


def when(stamp):
    try:return datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone().strftime('%d/%m à %H:%M')
    except (AttributeError,TypeError,ValueError):return ''


def owner_label(name):
    if name=='default':return 'votre partie'
    found=re.fullmatch(r'local-p(\d)',name)
    return f'essai à deux, joueur {found.group(1)}' if found else name


def panel(release):
    """Encadré du lanceur : versions, puis ce qui change."""
    inner=BOX-4;installed=release.get('installed');repair=release.get('repair') or []
    same=installed==release['revision']
    title=' Réparation du mod ' if repair and same else ' Mise à jour disponible '
    def row(text='',colour=PALE):
        text=clip(text,inner);say(f'  {GOLD}│{RESET} {colour}{text}{RESET}{" "*(inner-len(text))} {GOLD}│{RESET}')
    say();say(f'  {GOLD}╭─{RESET}{PALE}{BOLD}{title}{RESET}{GOLD}{"─"*(BOX-3-len(title))}╮{RESET}')
    row(f"{'Installée':<12}{release.get('local','')}"+(f' · {installed[:7]}' if installed else ''),MUTED)
    stamp=when(release.get('date'))
    row(f"{'Réinstallée' if same else 'Nouvelle':<12}{release['version']} · {release['revision'][:7]}"
        +(f' · {stamp}' if stamp else ''),GREEN)
    if repair:
        row(f"{'Abîmés':<12}{len(repair)} fichier(s) natif(s) : "+', '.join(repair[:3])+(' …' if len(repair)>3 else ''),RED)
    news=release.get('news') or []
    if news:
        row();row('Nouveautés' if installed else 'Dernier changement',GOLD)
        for title in news[:NEWS]:row('• '+title)
        if len(news)>NEWS:row(f'… et {len(news)-NEWS} autre(s) changement(s)',MUTED)
        elif installed and not release.get('complete'):row('… et des changements plus anciens',MUTED)
    say(f'  {GOLD}╰{"─"*(BOX-2)}╯{RESET}')


def main():
    # Ne pas remplacer une copie de travail ou une publication en préparation.
    if (ROOT/'scripts/verify_coop.py').exists():return 0
    if (ROOT/'.git').exists():
        try:
            dirty=git(ROOT,'status','--porcelain').stdout
            if dirty and not managed_copy(ROOT):return 0
        except (OSError,subprocess.SubprocessError):return 0
    status(f'{MUTED}Recherche de mise à jour…{RESET}')
    found=[]
    worker=threading.Thread(target=lambda:found.append(latest(ROOT)),daemon=True);worker.start();worker.join(4)
    status('')
    if not found or not found[0]:return 0
    release=found[0];panel(release)
    if ask('Installer maintenant ? [O/n] ') in (None,'n','non'):return 0
    try:
        while owners:=patch_owners(ROOT):
            say(f'\n  {RED}✖{RESET} {PALE}Un jeu est encore ouvert avec le compagnon{RESET} '
                f'{MUTED}({", ".join(map(owner_label,owners))}){RESET}')
            if ask('Fermez Azahar, puis Entrée pour réessayer · N pour jouer sans mettre à jour : ') in (None,'n','non'):return 0
        frames='◐◓◑◒';step=[0]
        def progress(done,total):
            step[0]+=1;size=f'{megabytes(done)} / {megabytes(total)} Mo · {done*100//total} %' if total else f'{megabytes(done)} Mo'
            status(f'{GOLD}{frames[step[0]%4]}{RESET} {PALE}Téléchargement…{RESET} {MUTED}{size}{RESET}')
        say();progress(0,None);raw=download(release['url'],progress)
        status(f'{GREEN}✔{RESET} {PALE}Téléchargée{RESET} {MUTED}· {megabytes(len(raw))} Mo{RESET}');say()
        status(f'{GOLD}◐{RESET} {PALE}Installation des fichiers…{RESET}')
        def components(path):
            say(f'\n  {GOLD}◐{RESET} {PALE}Installation des nouveaux composants…{RESET}');pip_install(path)
        count=install(raw,release,ROOT,dependencies=components)
    except KeyboardInterrupt:
        say(f'\n  {MUTED}Mise à jour interrompue. Votre version actuelle reste en place.{RESET}');return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,zipfile.BadZipFile) as exc:
        say(f'\n  {RED}✖ Mise à jour annulée :{RESET} {PALE}{exc}{RESET}')
        say(f'  {MUTED}Votre version actuelle reste en place.{RESET}')
        # Le lanceur efface l'écran : laisser le temps de lire.
        ask('Entrée pour lancer le jeu quand même.')
        return 0
    status(f'{GREEN}✔{RESET} {PALE}{BOLD}Mise à jour installée{RESET} {MUTED}· {release["version"]} · '
           f'{release["revision"][:7]} · {count} fichier{"s" if count>1 else ""} modifié{"s" if count>1 else ""}{RESET}')
    say(f'\n  {PALE}Relancement…{RESET}\n')
    return 10


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
