import json,unittest
from unittest.mock import Mock,patch
from utils import igdb,tvmaze,digitalcore_images,image_host
from utils.web_settings import defaults
class ProviderTests(unittest.TestCase):
 def setUp(self):igdb._cache.clear();digitalcore_images._cached=None
 def session(self):
  s=Mock();s.__enter__=Mock(return_value=s);s.__exit__=Mock(return_value=False);return s
 def test_igdb_uses_fixed_origins_body_secret_and_cached_token(self):
  s=self.session();auth=Mock(status_code=200);auth.json.return_value={'access_token':'issued-token','expires_in':3600}
  games=Mock(status_code=200);games.json.return_value=[];s.post.side_effect=[auth,games,games]
  with patch.object(igdb.requests,'Session',return_value=s):
   self.assertEqual(igdb.search('example-client','example-secret','Example'),[]);self.assertEqual(igdb.search('example-client','example-secret','Example'),[])
  self.assertEqual(s.post.call_count,3);self.assertEqual(s.post.call_args_list[0].args[0],igdb.AUTH);self.assertEqual(s.post.call_args_list[0].kwargs['data']['client_secret'],'example-secret')
  self.assertEqual(s.post.call_args_list[1].args[0],igdb.API);self.assertNotIn('example-secret',str(s.post.call_args_list[1]));self.assertFalse(s.post.call_args_list[1].kwargs['allow_redirects'])
 def test_igdb_maps_game_covers_and_rejects_paths(self):
  value=igdb.normalize({'id':1,'name':'Example','url':'https://www.igdb.com/games/example','first_release_date':1704067200,'cover':{'url':'//images.igdb.com/igdb/image/upload/t_thumb/test.jpg'},'genres':[{'name':'Adventure'}],'platforms':[{'name':'PC'}]})
  self.assertEqual(value['metadata']['gameUrl'],'https://www.igdb.com/games/example');self.assertEqual(value['metadata']['release_year'],'2024');self.assertIn('/t_cover_big/',value['images'][0]);self.assertIn('Platforms: PC',value['description'])
  with self.assertRaises(ValueError):igdb.detail('id','secret','../../games')
 def test_igdb_query_is_escaped(self):
  with patch.object(igdb,'request',return_value=[]) as request:igdb.search('id','secret','Game"; fields *;')
  self.assertIn(json.dumps('Game"; fields *;'),request.call_args.args[2])
 def test_tvmaze_maps_safe_summary_imdb_cast_and_cover(self):
  value=tvmaze.normalize({'id':1,'name':'Example','summary':'<p>A <b>series</b>.</p>','premiered':'2024-01-01','externals':{'imdb':'tt1234567'},'image':{'original':'https://static.tvmaze.com/cover.jpg'},'_embedded':{'cast':[{'person':{'name':'Actor'}}]}})
  self.assertEqual(value['description'],'A series.');self.assertEqual(value['metadata']['imdbId'],'tt1234567');self.assertEqual(value['metadata']['performers'],'Actor');self.assertEqual(len(value['images']),1)
  with patch.object(tvmaze,'request',return_value=[{'show':{'id':1,'name':'Example'}}]) as req:self.assertEqual(tvmaze.search('Example')[0]['title'],'Example')
  self.assertEqual(req.call_args.args[0],'/search/shows')
  with self.assertRaises(ValueError):tvmaze.detail('https://evil.invalid')
 def test_digitalcore_configuration_stays_in_memory(self):
  s=self.session();s.get.return_value.status_code=200;s.get.return_value.text='window.__NUXT__={config:{public:{imageUploadUrl:"https://img.digitalcore.club/api/upload",imageUploadToken:"public-upload-config"}}}'
  c=defaults();c.set('ImageHost','AUTHCODE','custom-host-secret')
  with patch.object(digitalcore_images.requests,'Session',return_value=s):
   self.assertEqual(digitalcore_images.authentication(c),'public-upload-config');self.assertEqual(digitalcore_images.authentication(c),'public-upload-config')
  self.assertEqual(s.get.call_count,1);self.assertEqual(s.get.call_args.args[0],'https://digitalcore.club/')
 def test_digitalcore_upload_never_uses_tracker_key(self):
  c=defaults();c.set('Website','API_KEY','tracker-private-key');c.set('ImageHost','PROVIDER','digitalcore')
  s=self.session()
  with patch.object(digitalcore_images,'authentication',return_value='public-upload-config'),patch.object(image_host.requests,'Session',return_value=s):image_host.post(c,'test.gif',b'GIF89a')
  self.assertEqual(s.post.call_args.args[0],'https://img.digitalcore.club/api/upload');self.assertEqual(s.post.call_args.kwargs['headers'],{'Authorization':'public-upload-config'});self.assertNotIn('tracker-private-key',str(s.post.call_args))
if __name__=='__main__':unittest.main()
