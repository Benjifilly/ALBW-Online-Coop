"""Version commune au lanceur et au salon."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def version_tuple(value):
    return tuple(map(int,value.split('.'))) if isinstance(value,str) and re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}',value) else None


def read_version(root=ROOT):
    try:value=(Path(root)/'VERSION').read_text(encoding='utf-8').strip()
    except OSError:return '0.0.0'
    return value if version_tuple(value) else '0.0.0'


VERSION=read_version()


def mismatch(remote,local=VERSION):
    remote=remote if isinstance(remote,str) and version_tuple(remote) else 'ancienne'
    a,b=version_tuple(local),version_tuple(remote)
    who='L’hôte doit se mettre à jour' if b is None or b<a else 'Mettez votre mod à jour' if b>a else 'Mettez les deux mods à jour'
    return f'Votre version : {local} · hôte : {remote}. {who} avec run_coop.bat.'
