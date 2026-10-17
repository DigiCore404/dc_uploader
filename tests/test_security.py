import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from utils.job_history import JobHistory
from utils.web_security import Security, password_hash, verify_password, networks, allows
from utils.qbit_clients import added_confirmed
from scripts.service import unit

class SecurityTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tmp');self.history=JobHistory(Path(self.temp.name)/'security.db')
  self.security=Security(self.history,'original-password')
 def tearDown(self):self.temp.cleanup()
 def test_salted_hash_and_persistent_login(self):
  self.assertTrue(self.security.verify('admin','original-password'))
  self.assertFalse(self.security.verify('other','original-password'))
  self.assertFalse(self.security.verify('admin','wrong'))
  reopened=Security(self.history,'different-bootstrap')
  self.assertTrue(reopened.verify('admin','original-password'))
  self.assertNotIn('original-password',self.history.path.read_bytes().decode(errors='ignore'))
  self.assertNotEqual(password_hash('same'),password_hash('same'))
 def test_password_and_ip_update(self):
  self.security.update({'username':'owner','password':'new-long-password','current_password':'original-password','allowed_ips':'192.168.1.0/24\n127.0.0.1\n::1'},'192.168.1.20')
  self.assertTrue(self.security.verify('owner','new-long-password'))
  self.assertFalse(self.security.verify('admin','original-password'))
  self.assertTrue(self.security.allowed('192.168.1.21'))
  self.assertTrue(self.security.allowed('::ffff:192.168.1.20'))
  self.assertFalse(self.security.allowed('10.0.0.1'))
 def test_reject_bad_current_password_weak_password_and_lockout(self):
  base={'username':'admin','current_password':'original-password','allowed_ips':[]}
  for change in ({'current_password':'wrong'},{'password':'short'},{'allowed_ips':['bad']},{'allowed_ips':['10.0.0.0/8']},{'username':''}):
   with self.assertRaises(ValueError):self.security.update(dict(base,**change),'192.168.1.20')
  self.assertTrue(self.security.verify('admin','original-password'))
 def test_empty_allowlist_still_requires_valid_login(self):
  self.assertTrue(self.security.allowed('203.0.113.1'))
  self.assertFalse(self.security.verify('','original-password'))
 def test_service_is_headless_and_unprivileged(self):
  rendered=unit(Path('/home/a/user % folder'),Path('/home/a/user % folder/.venv/bin/python'),'127.0.0.1',6080)
  self.assertIn('--no-browser',rendered);self.assertIn('Restart=on-failure',rendered)
  self.assertIn('%%',rendered);self.assertIn('UMask=0077',rendered)
  with self.assertRaises(ValueError):unit(ROOT,ROOT/'python','0.0.0.0',80)
 def test_https_cookies_are_secure(self):
  import io,socket,ssl,web
  connection=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).wrap_socket(socket.socket(),server_hostname='localhost',do_handshake_on_connect=False)
  try:
   handler=object.__new__(web.Handler);handler.connection=connection;handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock();handler.wfile=io.BytesIO()
   handler.send({'ok':True},cookie='db_session=test; HttpOnly; SameSite=Strict')
   cookies=[call.args[1] for call in handler.send_header.call_args_list if call.args[0]=='Set-Cookie']
   self.assertIn('; Secure',cookies[0])
  finally:connection.close()
 def test_qbit52_json_confirmation(self):
  response=Mock(status_code=200,content=b'{}');response.json.return_value={'success_count':1,'failure_count':0,'pending_count':0,'added_torrent_ids':['a'*40]}
  self.assertTrue(added_confirmed(response,'a'*40));self.assertFalse(added_confirmed(response,'b'*40))
  response.status_code=202;response.json.return_value={'pending_count':1,'added_torrent_ids':[]}
  self.assertTrue(added_confirmed(response,'a'*40))
  response.status_code=409;self.assertFalse(added_confirmed(response,'a'*40))
