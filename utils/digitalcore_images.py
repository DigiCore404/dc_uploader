"""Discover the same public image-upload authentication used by DigitalCore's UI."""
import json,re,threading,time
import requests
ENDPOINT='https://img.digitalcore.club/api/upload'
_lock=threading.Lock()
_cached=None

def authentication(config):
 global _cached
 with _lock:
  if _cached and _cached[1]>time.time():return _cached[0]
  try:
   with requests.Session() as session:
    session.trust_env=False
    response=session.get('https://digitalcore.club/',timeout=20,allow_redirects=False)
    if response.status_code!=200:raise ValueError('DigitalCore image-host configuration is temporarily unavailable.')
    text=response.text
    if len(text)>4*1024*1024:raise ValueError('Unexpected DigitalCore page size.')
    def setting(name):
     match=re.search(r'\b'+name+r'\s*:\s*("(?:[^"\\]|\\.)*")',text)
     return json.loads(match.group(1)) if match else ''
    endpoint=setting('imageUploadUrl');token=setting('imageUploadToken')
    if endpoint!=ENDPOINT or not isinstance(token,str) or not token or any(c in token for c in '\r\n'):
     raise ValueError('Could not verify DigitalCore image-host settings. Check the website and try again later.')
    _cached=(token,time.time()+300);return token
  except requests.RequestException as exc:raise ValueError('Could not read DigitalCore image-host configuration. Try again later.') from exc
