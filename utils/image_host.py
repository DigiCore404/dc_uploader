"""Shared image-host request/response contract for UI files, folder covers and screenshots."""
import re
from urllib.parse import urlparse
import requests

MAX_IMAGE=8*1024*1024

def provider(config):
    value=config.get('ImageHost','PROVIDER',fallback='').strip().lower()
    return value or 'digitalcore'


def identify(raw):
    if not 0<len(raw)<=MAX_IMAGE:raise ValueError('Choose an image up to 8 MB.')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):return 'png','image/png'
    if raw.startswith(b'\xff\xd8\xff'):return 'jpg','image/jpeg'
    if raw.startswith((b'GIF87a',b'GIF89a')):return 'gif','image/gif'
    if raw.startswith(b'RIFF') and raw[8:12]==b'WEBP':return 'webp','image/webp'
    raise ValueError('Choose a PNG, JPEG, GIF or WebP image. SVG and other file types are not accepted.')

def post(config,filename,raw):
    extension,mime=identify(raw)
    filename=re.sub(r'[^\w. -]','_',str(filename).replace('\\','/').split('/')[-1])[:100]
    filename=(filename.rsplit('.',1)[0] or 'image')+'.'+extension
    name=provider(config)
    data={'title':filename.rsplit('.',1)[0]}
    if name=='imghost':
        endpoint='https://imghost.dev/api/1/upload'
        key=config.get('ImageHost','API_KEY',fallback='').strip()
        if not key:raise ValueError('Add your imghost.dev API key in Settings first.')
        headers={'X-API-Key':key};field='source';data.update(format='json',nsfw='1' if config.getboolean('ImageHost','NSFW',fallback=False) else '0')
    elif name=='digitalcore':
        from utils.digitalcore_images import ENDPOINT, authentication
        endpoint=ENDPOINT;headers={'Authorization':authentication(config)};field='file'
    elif name=='custom':
        endpoint=config.get('ImageHost','UPLOADIMGURL');headers={'Authorization':config.get('ImageHost','AUTHCODE',fallback='')};field='file'
    else:raise ValueError('Choose a supported image host.')
    with requests.Session() as session:
        session.trust_env=False
        return session.post(endpoint,headers=headers,files={field:(filename,raw,mime)},data=data,
                            timeout=config.getint('Network','Timeout',fallback=120),allow_redirects=False)

def image_url(response):
    if response.status_code not in (200,201):return ''
    try:
        result=response.json()
        value=(result.get('image') or {}).get('url') or (result.get('data') or {}).get('link') or ''
        parsed=urlparse(value)
        return value if parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password and not any(c in value for c in '[]\r\n') else ''
    except (ValueError,TypeError,AttributeError):return ''

def upload(config,filename,raw):
    try:response=post(config,filename,raw)
    except requests.RequestException as exc:raise ValueError('Image host could not be reached. Check network access and try again.') from exc
    value=image_url(response)
    if not value:
        if response.status_code in (401,403):raise ValueError('Image host rejected the API key or upload permissions. Check Settings.')
        raise ValueError('Image host did not confirm the upload (HTTP '+str(response.status_code)+'). Check the image host before retrying.')
    return {'url':value,'provider':provider(config)}
