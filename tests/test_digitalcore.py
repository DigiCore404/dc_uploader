import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from utils import tmdb
from utils.release_metadata import extract_metadata
import uploader
class DigitalCoreTests(unittest.TestCase):
 def test_categories_match_site_and_manual_game_choice_wins(self):
  cats=uploader.categories();self.assertEqual(len(cats),42)
  self.assertEqual(next(c['name'] for c in cats if c['id']==25),'Games/PC')
  from utils.category_utils import determine_category
  self.assertEqual(determine_category('Toy.Shire.Linux-I_KnoW',manual_id=25),('Games/PC','25'))
  with self.assertRaises(ValueError):determine_category('Title',manual_id=9999)
 def test_tmdb_movie_and_tv_ids(self):
  row={'id':42,'name':'Example','first_air_date':'2026-01-01','external_ids':{'imdb_id':'tt1234567'},'poster_path':'/example.jpg','genres':[{'name':'Drama'}]}
  value=tmdb.normalize(row,'tv');self.assertEqual(value['metadata']['imdbId'],'tt1234567');self.assertEqual(value['metadata']['content_tags'],'Drama');self.assertEqual(value['kind'],'tv')
  with patch.object(tmdb,'request',return_value={'results':[row]}) as req:
   self.assertEqual(tmdb.search('token','Example','tv')[0]['title'],'Example');self.assertEqual(req.call_args.args[1],'/search/tv')
  with self.assertRaises(ValueError):tmdb.detail('token','../../private','movie')
 def test_nfo_imdb_no_studio_or_cast_guess(self):
  with tempfile.TemporaryDirectory() as d:
   Path(d,'release.nfo').write_text('https://www.imdb.com/title/tt1234567/')
   meta=extract_metadata('Example.2026.1080p',d)
   self.assertEqual(meta['imdbId'],'tt1234567');self.assertNotIn('studio',meta);self.assertNotIn('performers',meta)
 def test_image_host_uses_separate_key_and_sfw_default(self):
  from utils.web_settings import defaults
  from utils import image_host
  c=defaults();c.set('ImageHost','PROVIDER','imghost');c.set('Website','API_KEY','tracker-secret');c.set('ImageHost','API_KEY','image-secret')
  self.assertEqual(image_host.provider(c),'imghost')
  session=__import__('unittest.mock',fromlist=['Mock']).Mock();session.__enter__=lambda _:session;session.__exit__=lambda *args:False
  with patch.object(image_host.requests,'Session',return_value=session):image_host.post(c,'cover.gif',b'GIF89a')
  args,kwargs=session.post.call_args
  self.assertEqual(args[0],'https://imghost.dev/api/1/upload');self.assertEqual(kwargs['headers'],{'X-API-Key':'image-secret'});self.assertEqual(kwargs['data']['nsfw'],'0')
 def test_site_specific_defaults_and_theme(self):
  text=Path('defaults.ini').read_text();self.assertIn('https://digitalcore.club',text);self.assertIn('esource = DigitalCore',text)
  html=Path('webui/index.html').read_text();self.assertIn('id="imdb"',html);self.assertIn('id="game-url"',html);self.assertNotIn('Jeedoo',html)
if __name__=='__main__':unittest.main()
