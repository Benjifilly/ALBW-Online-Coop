"""Adresses à partager et ouverture UPnP facultative, sans paquet externe."""
import ipaddress
import json
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

HELP='À distance, utilisez Tailscale ensemble ou ouvrez le port UDP 45990 sur la box de l’hôte.'


def local_addresses(port):
    rows=[]
    try:
        command="[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress,InterfaceAlias | ConvertTo-Json -Compress"
        raw=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],capture_output=True,
            timeout=4,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout
        value=json.loads(raw.decode('utf-8-sig'));rows=value if isinstance(value,list) else [value]
    except (OSError,ValueError,subprocess.SubprocessError):pass
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1',9));rows.append(dict(IPAddress=s.getsockname()[0],InterfaceAlias='Réseau local'))
    except OSError:pass
    result=[];seen=set()
    for row in rows:
        try:ip=ipaddress.IPv4Address(row['IPAddress'])
        except (ValueError,KeyError,TypeError):continue
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or str(ip) in seen:continue
        alias=str(row.get('InterfaceAlias','Réseau local'));name=alias.lower()
        label=next((n for n in ('Tailscale','ZeroTier','Radmin') if n.lower() in name),None)
        if not label:label='Tailscale / VPN' if ip in ipaddress.ip_network('100.64.0.0/10') else 'Réseau local'
        seen.add(str(ip));result.append(dict(label=label,address=f'{ip}:{port}',vpn=label!='Réseau local'))
    return sorted(result,key=lambda r:not r['vpn'])


def local_url(url,host=None):
    p=urllib.parse.urlsplit(url)
    try:ip=ipaddress.IPv4Address(p.hostname or '')
    except ValueError:raise ValueError('Adresse de box non reconnue.')
    if (p.scheme!='http' or p.username or p.password or not ip.is_private or ip.is_loopback
            or ip.is_unspecified or ip.is_multicast or host and p.hostname!=host):
        raise ValueError('Adresse UPnP refusée.')
    return p.hostname


def fetch(url,body=None,headers=None):
    local_url(url)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args):raise ValueError('Redirection UPnP refusée.')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:response=opener.open(urllib.request.Request(url,data=body,headers=headers or {}),timeout=1.5)
    except urllib.error.HTTPError as exc:response=exc
    with response as r:
        if r.geturl()!=url:raise ValueError('Redirection UPnP refusée.')
        data=r.read(262145)
        if len(data)>262144:raise ValueError('Réponse de la box trop longue.')
        return ET.fromstring(data)


def discover():
    request=('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
             'MX: 1\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n').encode()
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
        s.settimeout(.25);s.sendto(request,('239.255.255.250',1900));deadline=time.monotonic()+1.2
        while time.monotonic()<deadline:
            try:raw,_=s.recvfrom(4096)
            except socket.timeout:continue
            headers=dict(line.split(':',1) for line in raw.decode('ascii','ignore').split('\r\n')[1:] if ':' in line)
            location=next((v.strip() for k,v in headers.items() if k.lower()=='location'),None)
            if location:
                try:local_url(location);return location
                except ValueError:pass
    return None


class PortMapping:
    def __init__(self,port):self.port=port;self.url=None;self.service=None;self.client=None;self.description=f'ALBW-{secrets.token_hex(4)}';self.owned=False

    def soap(self,action,**values):
        fields=''.join(f'<{k}>{escape(str(v))}</{k}>' for k,v in values.items())
        body=(f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
              f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
              f'<u:{action} xmlns:u="{self.service}">{fields}</u:{action}></s:Body></s:Envelope>').encode()
        result=fetch(self.url,body,{'Content-Type':'text/xml; charset="utf-8"','SOAPAction':f'"{self.service}#{action}"'})
        code=result.findtext('.//{*}errorCode')
        if code:raise PortError(code)
        return result

    def open(self):
        try:
            location=discover()
            if not location:return None
            host=local_url(location);root=fetch(location)
            for service in root.findall('.//{*}service'):
                kind=service.findtext('{*}serviceType','')
                if any(n in kind for n in (':WANIPConnection:',':WANPPPConnection:')):
                    self.service=kind;self.url=urllib.parse.urljoin(location,service.findtext('{*}controlURL',''))
                    local_url(self.url,host);break
            if not self.url:return None
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
                s.connect((host,9));self.client=s.getsockname()[0]
            base=dict(NewRemoteHost='',NewExternalPort=self.port,NewProtocol='UDP')
            # Ne jamais remplacer la redirection d'un autre programme.
            try:
                existing=self.soap('GetSpecificPortMappingEntry',**base)
            except PortError as exc:
                if str(exc)!='714':raise
                existing=None
            if existing is None:
                self.soap('AddPortMapping',**base,NewInternalPort=self.port,NewInternalClient=self.client,
                    NewEnabled=1,NewPortMappingDescription=self.description,NewLeaseDuration=0)
                self.owned=True
            checked=existing if existing is not None else self.soap('GetSpecificPortMappingEntry',**base)
            if (checked.findtext('.//{*}NewInternalClient')!=self.client
                    or checked.findtext('.//{*}NewInternalPort')!=str(self.port)
                    or checked.findtext('.//{*}NewEnabled','1')!='1'):return None
            ip=self.soap('GetExternalIPAddress').findtext('.//{*}NewExternalIPAddress','')
            if ipaddress.IPv4Address(ip).is_global:return dict(label='Internet · UPnP (à tester par votre ami)',address=f'{ip}:{self.port}',vpn=False)
        except (OSError,ValueError,ET.ParseError):pass
        return None

    def close(self):
        if not self.owned:return
        try:
            row=self.soap('GetSpecificPortMappingEntry',NewRemoteHost='',NewExternalPort=self.port,NewProtocol='UDP')
            if (row.findtext('.//{*}NewInternalClient')==self.client and row.findtext('.//{*}NewPortMappingDescription')==self.description):
                self.soap('DeletePortMapping',NewRemoteHost='',NewExternalPort=self.port,NewProtocol='UDP')
        except (OSError,ValueError,ET.ParseError):pass
        self.owned=False


class PortError(ValueError):pass


class HostingAccess:
    def __init__(self,log=lambda text:None):
        self.log=log;self.generation=0;self.mapping=None;self.info={};self.lock=threading.Lock()

    def start(self,port):
        self.close()
        with self.lock:self.generation+=1;generation=self.generation;self.info=dict(hosting_addresses=[],network_hint='Recherche des adresses…')
        def prepare():
            rows=local_addresses(port);mapping=PortMapping(port);public=mapping.open()
            if public:rows.append(public)
            hint=('UPnP activé. Demandez à votre ami de tester cette adresse.' if public else
                  f'À distance, utilisez Tailscale ensemble ou ouvrez le port UDP {port} sur la box de l’hôte.')
            with self.lock:
                stale=self.generation!=generation
                if not stale:self.mapping=mapping;self.info=dict(hosting_addresses=rows,network_hint=hint)
            if stale:mapping.close();return
            for row in rows:self.log(f"Adresse à donner · {row['label']} : {row['address']}")
            self.log(hint)
        threading.Thread(target=prepare,daemon=True).start()

    def snapshot(self):
        with self.lock:return dict(self.info)

    def close(self):
        with self.lock:
            self.generation+=1;mapping=self.mapping;self.mapping=None;self.info={}
        if mapping:threading.Thread(target=mapping.close,daemon=True).start()
