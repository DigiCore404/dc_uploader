from pathlib import Path
import configparser,json,os,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from utils.web_settings import schema,apply,defaults
class ConfigSettingsTests(unittest.TestCase):
 def test_supported_options_and_secret_redaction(self):
  c=defaults();c.set('ImageHost','authcode','private-auth')
  result=schema(c);raw=json.dumps(result)
  self.assertNotIn('private-auth',raw)
  fields={(s['section'],f['key']) for s in result for f in s['fields']}
  self.assertIn(('MediaTools','screenshot_sheet'),fields);self.assertIn(('MediaTools','mtnpostby'),fields)
  self.assertIn(('Settings','screenshot_categories'),fields)
 def test_validated_settings_and_blank_secret_preservation(self):
  c=defaults();c.set('ImageHost','authcode','secret')
  apply(c,{'MediaTools':{'screenshot_singles':'7','screenshot_sheet':'true'},'ImageHost':{'authcode':''}})
  self.assertEqual(c.getint('MediaTools','screenshot_singles'),7);self.assertEqual(c.get('ImageHost','authcode'),'secret')
  for fields in [{'MediaTools':{'screenshot_singles':'99'}},{'Settings':{'screenshot_categories':'9,abc'}},{'Network':{'timeout':'NaN'}},{'Torrent':{'unknown':'value'}},{'MediaTools':{'mtnfontfile':'a\nb'}}]:
   with self.assertRaises(ValueError):apply(c,fields)
 def test_container_paths_cannot_be_changed_through_advanced_fields(self):
  with patch.dict(os.environ,DIGITALCORE_CONTAINER='1'):
   with self.assertRaises(ValueError):apply(defaults(),{'Paths':{'tmp_dir':'/elsewhere'}})
   for key in ('mount_base','registry','log','cleanup_log'):
    with self.assertRaises(ValueError):apply(defaults(),{'UnrarUpload':{key:'/elsewhere'}})
