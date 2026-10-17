"""Optional movie/TV metadata from the fixed TMDB API origin."""
import re
import requests
API = 'https://api.themoviedb.org/3'

def request(token, path, params=None):
    if not token:
        raise ValueError('Add your TMDB API read access token in Settings first.')
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(API + path, params=params, headers={
                'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
                timeout=20, allow_redirects=False)
            if response.status_code in (401, 403):
                raise ValueError('TMDB rejected the token. Check Settings.')
            if response.status_code != 200:
                raise ValueError('TMDB is unavailable. Try again later.')
            return response.json()
    except (requests.RequestException, KeyError, TypeError) as exc:
        raise ValueError('Could not read TMDB metadata. Try again later.') from exc

def normalize(row, kind):
    date = str(row.get('release_date') or row.get('first_air_date') or '')
    def names(values):
        return ', '.join(str(v.get('name', '')) for v in values if isinstance(v, dict) and v.get('name'))[:1000]
    poster = str(row.get('poster_path') or '')
    images = ['https://image.tmdb.org/t/p/original' + poster] if re.fullmatch(r'/[a-zA-Z0-9._-]+', poster) else []
    imdb = (row.get('external_ids') or {}).get('imdb_id') or ''
    return {'id': str(row.get('id') or ''), 'kind': kind, 'site': 'TMDB',
            'title': str(row.get('title') or row.get('name') or '')[:500],
            'date': date[:10], 'description': str(row.get('overview') or '')[:30000],
            'images': images, 'url': 'https://www.themoviedb.org/' + kind + '/' + str(row.get('id') or ''),
            'metadata': {'studio': names(row.get('production_companies') or []),
                'performers': names((row.get('credits') or {}).get('cast', [])[:12]),
                'content_tags': names(row.get('genres') or []),
                'imdbId': imdb if re.fullmatch(r'tt[0-9]{7,10}', imdb) else '',
                'release_year': date[:4] if re.match(r'^(19|20)[0-9]{2}', date) else '',
                'runtime_minutes': str(row.get('runtime') or '')}}

def search(token, query, kind='movie'):
    if kind not in ('movie', 'tv') or not 2 <= len(query.strip()) <= 250:
        raise ValueError('Choose Movies or TV and enter a title (2–250 characters).')
    rows = request(token, '/search/' + kind, {'query': query.strip(), 'include_adult': 'false'}).get('results')
    if not isinstance(rows, list):
        raise ValueError('TMDB returned an unexpected search result.')
    return [normalize(row, kind) for row in rows[:12] if isinstance(row, dict)]

def detail(token, identity, kind):
    if kind not in ('movie', 'tv') or not re.fullmatch(r'[0-9]{1,12}', identity):
        raise ValueError('Choose a valid TMDB result.')
    return normalize(request(token, '/' + kind + '/' + identity,
                             {'append_to_response': 'external_ids,credits'}), kind)
