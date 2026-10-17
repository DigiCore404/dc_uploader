"""Public TVmaze series metadata; no account or API key required."""
import html,re
from html.parser import HTMLParser
from urllib.parse import urlparse
import requests
API='https://api.tvmaze.com'
class PlainText(HTMLParser):
 def __init__(self):super().__init__();self.parts=[]
 def handle_data(self,data):self.parts.append(data)
 def handle_starttag(self,tag,attrs):
  if tag in ('p','br'):self.parts.append('\n')
def request(path,params=None):
 try:
  with requests.Session() as session:
   session.trust_env=False
   response=session.get(API+path,params=params,headers={'Accept':'application/json'},timeout=20,allow_redirects=False)
   if response.status_code==429:raise ValueError('TVmaze rate limit reached. Try again shortly.')
   if response.status_code!=200:raise ValueError('TVmaze is unavailable. Try again later.')
   return response.json()
 except requests.RequestException as exc:raise ValueError('Could not read TVmaze metadata. Try again later.') from exc

def normalize(row):
 parser=PlainText();parser.feed(str(row.get('summary') or ''))
 image=str((row.get('image') or {}).get('original') or (row.get('image') or {}).get('medium') or '')
 images=[image] if urlparse(image).scheme=='https' and urlparse(image).hostname in ('static.tvmaze.com','tvmaze.com','www.tvmaze.com') else []
 date=str(row.get('premiered') or '')
 imdb=str((row.get('externals') or {}).get('imdb') or '')
 cast=', '.join(str((item.get('person') or {}).get('name') or '') for item in (row.get('_embedded') or {}).get('cast',[])[:12])
 network=row.get('network') or row.get('webChannel') or {}
 return {'id':str(row.get('id') or ''),'kind':'tv','site':'TVmaze','title':str(row.get('name') or '')[:500],'date':date[:10],
   'url':'https://www.tvmaze.com/shows/'+str(row.get('id') or ''),'description':''.join(parser.parts).strip()[:30000],'images':images,
   'metadata':{'imdbId':imdb if re.fullmatch(r'tt[0-9]{7,10}',imdb) else '', 'studio':str(network.get('name') or ''), 'performers':cast[:1000],
    'content_tags':', '.join(str(g) for g in row.get('genres') or [])[:1000],'release_year':date[:4] if re.match(r'^(19|20)[0-9]{2}',date) else '',
    'runtime_minutes':str(row.get('runtime') or row.get('averageRuntime') or ''),'language':str(row.get('language') or '')}}
def search(title):
 title=str(title).strip()
 if not 2<=len(title)<=250:raise ValueError('Enter a series title (2–250 characters).')
 rows=request('/search/shows',{'q':title})
 if not isinstance(rows,list):raise ValueError('TVmaze returned an unexpected result.')
 return [normalize(row['show']) for row in rows[:12] if isinstance(row,dict) and isinstance(row.get('show'),dict)]
def detail(identity):
 if not re.fullmatch(r'[0-9]{1,12}',str(identity)):raise ValueError('Choose a valid TVmaze result.')
 return normalize(request('/shows/'+str(identity),{'embed':'cast'}))
