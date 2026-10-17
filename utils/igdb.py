"""IGDB game metadata using server-side Twitch application credentials."""
from datetime import datetime,timezone
import hashlib,json,re,threading,time
from urllib.parse import urlparse
import requests
API='https://api.igdb.com/v4/games'
AUTH='https://id.twitch.tv/oauth2/token'
_cache={}
_lock=threading.Lock()
FIELDS='name,summary,storyline,first_release_date,url,cover.url,genres.name,platforms.name,involved_companies.company.name,websites.url'

def access_token(client_id,client_secret):
 if not client_id or not client_secret:raise ValueError('Add your IGDB / Twitch Client ID and Client secret in Settings first.')
 fingerprint=hashlib.sha256((client_id+'\0'+client_secret).encode()).hexdigest()
 with _lock:
  saved=_cache.get(fingerprint)
  if saved and saved[1]>time.time():return saved[0]
  try:
   with requests.Session() as session:
    session.trust_env=False
    response=session.post(AUTH,data={'client_id':client_id,'client_secret':client_secret,'grant_type':'client_credentials'},timeout=20,allow_redirects=False)
    if response.status_code!=200:raise ValueError('Twitch rejected the IGDB application credentials. Check Settings.')
    data=response.json();token=data.get('access_token');lifetime=int(data.get('expires_in',0))
    if not isinstance(token,str) or not token or lifetime<1:raise ValueError('Could not obtain an IGDB access token.')
    _cache.clear();_cache[fingerprint]=(token,time.time()+max(0,lifetime-60));return token
  except (requests.RequestException,KeyError,TypeError) as exc:raise ValueError('IGDB authentication is unavailable. Try again later.') from exc

def request(client_id,client_secret,query):
 token=access_token(client_id,client_secret)
 try:
  with requests.Session() as session:
   session.trust_env=False
   response=session.post(API,headers={'Client-ID':client_id,'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'text/plain'},data=query.encode('utf-8'),timeout=20,allow_redirects=False)
   if response.status_code==401:
    with _lock:_cache.clear()
    raise ValueError('IGDB authentication expired. Search again to refresh it.')
   if response.status_code==429:raise ValueError('IGDB rate limit reached. Try again shortly.')
   if response.status_code!=200:raise ValueError('IGDB is unavailable. Try again later.')
   rows=response.json()
   if not isinstance(rows,list):raise ValueError('IGDB returned an unexpected response.')
   return rows
 except requests.RequestException as exc:raise ValueError('Could not read IGDB metadata. Try again later.') from exc

def normalize(row):
 def names(values):return ', '.join(str(v.get('name','')) for v in values if isinstance(v,dict) and v.get('name'))[:1000]
 date=''
 try:
  if row.get('first_release_date'):date=datetime.fromtimestamp(int(row['first_release_date']),timezone.utc).strftime('%Y-%m-%d')
 except (ValueError,OverflowError,OSError):pass
 url=str(row.get('url') or '')
 if urlparse(url).hostname not in ('www.igdb.com','igdb.com') or urlparse(url).scheme!='https':url=''
 cover=str((row.get('cover') or {}).get('url') or '')
 if cover.startswith('//'):cover='https:'+cover
 images=[]
 if urlparse(cover).scheme=='https' and urlparse(cover).hostname=='images.igdb.com':images=[cover.replace('/t_thumb/','/t_cover_big/')]
 description=str(row.get('summary') or '')[:20000]
 if row.get('storyline') and row['storyline']!=description:description+='\n\n'+str(row['storyline'])[:10000]
 platforms=names(row.get('platforms') or [])
 if platforms:description+='\n\nPlatforms: '+platforms
 companies=[v.get('company',{}) for v in row.get('involved_companies') or [] if isinstance(v,dict)]
 return {'id':str(row.get('id') or ''),'kind':'game','site':'IGDB','title':str(row.get('name') or '')[:500],'date':date,'url':url,'description':description.strip(),'images':images,
         'metadata':{'gameUrl':url,'studio':names(companies),'content_tags':names(row.get('genres') or []),'release_year':date[:4]}}

def search(client_id,client_secret,title):
 title=str(title).strip()
 if not 2<=len(title)<=250 or any(ord(c)<32 for c in title):raise ValueError('Enter a game title (2–250 characters).')
 # JSON string escaping is compatible with the quoted IGDB search syntax.
 query='search '+json.dumps(title,ensure_ascii=True)+'; fields '+FIELDS+'; limit 12;'
 return [normalize(row) for row in request(client_id,client_secret,query) if isinstance(row,dict)]

def detail(client_id,client_secret,identity):
 if not re.fullmatch(r'[0-9]{1,12}',str(identity)):raise ValueError('Choose a valid IGDB result.')
 rows=request(client_id,client_secret,'fields '+FIELDS+'; where id = '+str(identity)+'; limit 1;')
 if not rows:raise ValueError('This IGDB game could not be found.')
 return normalize(rows[0])
