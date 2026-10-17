"""Bounded HTTPS downloads with public DNS addresses pinned for the TLS connection."""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlparse, urljoin

CATALOGUE_HOSTS = frozenset({'themoviedb.org'})


def validate_url(url, allowed_hosts=None):
    parsed=urlparse(url)
    host=(parsed.hostname or '').lower().removeprefix('www.')
    if parsed.scheme!='https' or not host or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None,443) or any(c in url for c in '\r\n\x00'):
        raise ValueError('Use a complete public HTTPS URL without login information.')
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ValueError('This catalogue is not supported. Choose one of the listed sources.')
    return parsed


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host,timeout=timeout,context=ssl.create_default_context())
        self.address=address
    def connect(self):
        self.sock=socket.create_connection((self.address,443),self.timeout)
        try:self.sock=self._context.wrap_socket(self.sock,server_hostname=self.host)
        except Exception:
            self.sock.close();raise


def download(url, allowed_hosts=None, limit=2*1024*1024):
    for _ in range(4):
        parsed=validate_url(url,allowed_hosts)
        try:
            addresses=list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)))
            if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
                raise ValueError('Private, local and reserved network addresses are not allowed.')
            address=next((ip for ip in addresses if ':' not in ip),addresses[0])
            connection=PinnedHTTPS(parsed.hostname,address,20)
            try:
                target=parsed.path or '/'
                if parsed.query:target+='?'+parsed.query
                connection.request('GET',target,headers={'User-Agent':'DigitalCore-MetadataHelper/1.0','Accept-Language':'en-US,en;q=0.8'})
                response=connection.getresponse()
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.getheader('Location') or '')
                    continue
                if response.status!=200:
                    raise ValueError('This source returned HTTP '+str(response.status)+'. Use a direct catalogue URL or another source; login and browser challenges cannot be scanned here.')
                if int(response.getheader('Content-Length') or 0)>limit:raise ValueError('This source is too large.')
                body=response.read(limit+1)
                if len(body)>limit:raise ValueError('This source is too large.')
                return body,response.getheader('Content-Type') or '',url
            finally:connection.close()
        except (OSError,http.client.HTTPException) as exc:
            raise ValueError('Could not read this public source. Try another source or enter the details manually.') from exc
    raise ValueError('This source redirected too many times.')


def catalogue_page(url):
    body,mime,final=download(url,CATALOGUE_HOSTS)
    if 'html' not in mime.lower():raise ValueError('This catalogue did not return an HTML page.')
    return {'html':body.decode('utf-8',errors='replace'),'url':final}
