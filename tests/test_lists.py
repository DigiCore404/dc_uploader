"""Large-list filtering/sorting/pagination must operate on all SQLite rows."""
from pathlib import Path
import sys,tempfile,unittest,json
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from utils.job_history import JobHistory
from utils.import_queue import ImportQueue

class ListTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tmp')
  self.history=JobHistory(Path(self.temp.name)/'lists.db')
  self.queue=ImportQueue(SimpleNamespace(history=self.history))
 def tearDown(self):self.temp.cleanup()
 def populate(self):
  for i in range(143):
   name=f'Release.{i:03}';identity=f'row{i:03}';status=['completed','downloading','waiting','needs_review','cancelled','duplicate'][i%6]
   job_id=None
   if status in ('completed','duplicate'):
    job_id=identity;self.history.create(identity,{'source':'/content/'+name,'category':9})
    self.history.finish(identity,status,0)
    with self.history.connect() as db:db.execute('UPDATE upload_jobs SET started=?,torrent_id=? WHERE id=?',(i,i+100,identity))
   with self.history.connect() as db:
    db.execute('INSERT INTO torrent_imports(id,client_id,source_hash,name,status,remote_path,local_path,details,created,updated,job_id,progress) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
      (identity,'client',str(i).zfill(40),name,'uploaded' if job_id else status,'/downloads/'+name,'/content/'+name,json.dumps({'private':'not returned'}),i,i,job_id,i/143))
 def test_imports_beyond_first_hundred_and_global_counts(self):
  self.populate();first=self.queue.page(limit=100);second=self.queue.page(limit=100,offset=100)
  self.assertEqual(first['total'],143);self.assertEqual(len(second['imports']),43)
  self.assertFalse({r['id'] for r in first['imports']}&{r['id'] for r in second['imports']})
  self.assertEqual(sum(first['counts'].values()),143)
  self.assertTrue(all('details' not in row for row in first['imports']))
  for status,count in first['counts'].items():self.assertEqual(self.queue.page(status=status)['total'],count)
 def test_import_search_sort_and_pagination(self):
  self.populate()
  for key in ['created','name','status','progress']:
   for direction in ['asc','desc']:
    result=self.queue.page(limit=100,sort=key,direction=direction)['imports']
    values=[(r.get('upload_status') or r['status']) if key=='status' else r[key] for r in result]
    self.assertEqual(values,sorted(values,reverse=direction=='desc'))
  result=self.queue.page(search='Release.142');self.assertEqual(result['total'],1)
  self.assertEqual(result['all_total'],143)
  self.assertEqual(self.queue.page(search='%')['total'],0)
  self.assertEqual(self.queue.page(sort='name; DROP TABLE torrent_imports')['total'],143)
  with self.assertRaises(ValueError):self.queue.page(status='invalid')
 def test_history_search_sort_and_global_counts(self):
  self.populate()
  self.assertEqual(self.history.page(search='Release.000')['total'],1)
  self.assertEqual(self.history.page(search='100')['jobs'][0]['torrent_id'],100)
  for key in ['started','name','status','torrent_id']:
   for direction in ['asc','desc']:
    rows=self.history.page(limit=100,sort=key,direction=direction)['jobs'];values=[r[key] for r in rows]
    self.assertEqual(values,sorted(values,reverse=direction=='desc'))
  allrows=self.history.page();self.assertEqual(allrows['all_total'],47)
  self.assertEqual(self.history.page(search='Release.000')['all_total'],47)
  self.assertEqual(self.history.page(sort='name; DROP TABLE upload_jobs')['total'],47)
 def test_history_combined_attention_filter(self):
  for i,status in enumerate(['needs_review','interrupted','completed']):
   self.history.create(str(i),{'source':'/content/Test','category':9});self.history.finish(str(i),status,0)
  self.assertEqual(self.history.page(status='attention')['total'],2)
