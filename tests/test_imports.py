import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
from torrent_fixtures import Torrent
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import uploader
import web
from utils.import_queue import ImportQueue, parse_source
from utils import tmdb

HASH='a'*40

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tmp')
        self.root=Path(self.temp.name);self.content=self.root/'content';self.content.mkdir()
        config=self.root/'config.ini'
        config.write_text(f'[Website]\nAPI_KEY=test\n[Torrent]\nANNOUNCEURL=https://example.invalid/a\n[Paths]\nDATADIR={self.content}\nWATCHFOLDER={self.root / "watch"}\n',encoding='utf-8')
        self.env=patch.dict(os.environ,DIGITALCORE_CONFIG=str(config),DIGITALCORE_CONTAINER='0');self.env.start()
        self.workspace=web.Workspace('private',self.root/'uploads.db');self.queue=self.workspace.imports
        self.client=self.workspace.clients.save({'name':'Test','url':'http://example.invalid:8080'})
        self.add=patch.object(self.workspace.clients,'add_torrent').start()
        self.check=patch.object(uploader,'check_config',return_value=[]).start()
        self.write=patch.object(uploader,'check_writable').start()
        self.status=patch.object(self.workspace.clients,'import_status').start()
        self.status.return_value=(None,[])
        self.original=patch.object(self.queue,'_original_torrent',return_value=self.root/'original.torrent').start()
        self.data={'client_id':self.client,'remote_root':'/downloads','local_root':str(self.content),
                   'magnet':'magnet:?xt=urn:btih:'+HASH+'&dn=Sample.Release',
                   'category':uploader.categories()[0]['id'],'metadata':{},'images':[],'description':'Reviewed details'}
    def tearDown(self):
        patch.stopall();self.env.stop()
        from utils.config_loader import ConfigLoader
        ConfigLoader._cache.clear();self.temp.cleanup()
    def add_queue(self):
        result=self.queue.add(self.data)
        return next(r for r in self.queue.rows() if r['id']==result['id'])
    def release_source(self,row):
        return Path(row['local_path']) if row['folder_layout']!='legacy' else Path(row['local_path'])/'Sample.Release'
    def legacy_queue(self):
        row=self.add_queue()
        remote='/downloads/DigitalCore-imports/'+row['id']+'/Sample.Release'
        local=str(self.content/'DigitalCore-imports'/row['id']/'Sample.Release')
        self.queue.update(row['id'],remote_path=remote,local_path=local,folder_layout='legacy')
        return dict(row,remote_path=remote,local_path=local,folder_layout='legacy')
    def complete(self,row,progress=1):
        root=Path(row['local_path']);folder=self.release_source(row);folder.mkdir(parents=True)
        (folder/'video.mp4').write_bytes(b'test media')
        self.status.return_value=({'hash':HASH,'progress':1,'state':'uploading','save_path':row['remote_path']},
            [{'name':('video.mp4' if row['folder_layout']!='legacy' else 'Sample.Release/video.mp4'),'size':10,'progress':progress}])
    def test_file_and_magnet_hashes(self):
        file=self.root/'video.mp4';file.write_bytes(b'test media')
        torrent=Torrent(path=file);torrent.generate()
        raw=torrent.dump()
        value,_,parsed,_=parse_source({'torrent':base64.b64encode(raw).decode()})
        self.assertEqual(value,torrent.infohash);self.assertEqual(parsed,raw)
        self.assertEqual(parse_source(self.data)[0],HASH)
        self.assertEqual(parse_source({'magnet':'magnet:?xt=urn:btih:'+base64.b32encode(bytes.fromhex(HASH)).decode()})[0],HASH)
    def test_reject_invalid_and_multiple_sources(self):
        for data in ({'magnet':'https://example.org/test.torrent'},{'torrent':'bad'},{'magnet':self.data['magnet'],'torrent':'bad'},{}):
            with self.assertRaises(ValueError):parse_source(data)
    def test_add_durable_and_never_store_trackers(self):
        self.data['magnet']+='&tr=https%3A%2F%2Fexample.invalid%2Fsecret-passkey'
        row=self.add_queue();self.assertEqual(row['status'],'downloading')
        self.assertNotIn('secret-passkey',json.dumps(self.queue.rows()))
        self.assertNotIn('secret-passkey',(self.root/'uploads.db').read_bytes().decode(errors='ignore'))
        reopened=ImportQueue(self.workspace);self.assertEqual(reopened.rows()[0]['id'],row['id'])
        self.assertEqual(row['remote_path'],'/downloads/Sample.Release')
        self.assertEqual(row['local_path'],str(self.content/'Sample.Release'))
        self.assertEqual(row['folder_layout'],'direct')
        self.add.assert_called_once_with(self.client,HASH,'/downloads/Sample.Release',None,self.data['magnet'],content_layout='NoSubfolder')
    def test_existing_folder_is_reported_and_client_never_called(self):
        folder=self.content/'Sample.Release';folder.mkdir()
        sentinel=folder/'keep.txt';sentinel.write_text('existing content')
        with self.assertRaises(ValueError) as caught:self.queue.add(self.data)
        self.assertIn('already exists',str(caught.exception))
        self.assertIn(str(folder),str(caught.exception))
        self.add.assert_not_called()
        self.assertEqual(sentinel.read_text(),'existing content')
        self.assertEqual(self.queue.rows(),[])

    def test_different_torrent_cannot_claim_queued_release_folder(self):
        self.add_queue();self.add.reset_mock()
        self.data['magnet']=self.data['magnet'].replace(HASH,'b'*40)
        with self.assertRaisesRegex(ValueError,'already uses this release folder'):self.queue.add(self.data)
        self.add.assert_not_called()

    def test_cancelled_direct_import_can_resume_original_folder(self):
        old=self.add_queue();self.queue.cancel(old['id'])
        new=self.add_queue();self.queue.update(new['id'],status='needs_review')
        self.status.return_value=({'hash':HASH,'progress':0,'state':'downloading','save_path':old['remote_path']},[])
        self.add.reset_mock();self.queue.reconcile(new['id']);self.add.assert_not_called()
        self.assertEqual(self.queue.rows()[0]['status'],'downloading')

    def test_duplicates_and_existing_client_torrents(self):
        self.add_queue()
        with self.assertRaisesRegex(ValueError,'already'):self.queue.add(self.data)
        self.add.assert_called_once()
    def test_bad_mapping_and_client_failures_do_not_upload(self):
        row=self.add_queue();self.complete(row)
        self.status.return_value[1][0]['size']=999
        with patch.object(self.workspace,'upload') as upload:
            self.queue.tick();upload.assert_not_called()
        self.assertEqual(self.queue.rows()[0]['status'],'waiting')
    def test_skipped_files_and_checking_never_publish(self):
        row=self.add_queue();self.complete(row,progress=0)
        with patch.object(self.workspace,'upload') as upload:
            self.queue.tick();upload.assert_not_called()
        self.status.return_value[1][0]['progress']=1
        self.status.return_value[0]['state']='checkingUP'
        with patch.object(self.workspace,'upload') as upload:
            self.queue.tick();upload.assert_not_called()
    def test_complete_download_runs_existing_worker_once(self):
        row=self.add_queue();self.complete(row)
        with patch.object(uploader,'run_job',return_value=0) as worker:
            self.queue.tick()
            for _ in range(100):
                job=self.workspace.history.get(row['id'])
                if job and job['status']!='running':break
                time.sleep(.01)
            self.assertEqual(job['status'],'completed');worker.assert_called_once()
            self.queue.tick();worker.assert_called_once()
            self.assertEqual(self.queue.rows()[0]['job_id'],row['id'])
            self.assertEqual(job['description'],'Reviewed details')
    def test_reconcile_requires_exact_hash_path_and_never_readds(self):
        row=self.add_queue();self.complete(row);self.queue.update(row['id'],status='needs_review')
        self.add.reset_mock()
        self.queue.reconcile(row['id'])
        self.assertEqual(self.queue.rows()[0]['status'],'downloading')
        self.add.assert_not_called()
        self.queue.update(row['id'],status='needs_review')
        self.status.return_value[0]['save_path']='/wrong'
        with self.assertRaises(ValueError):self.queue.reconcile(row['id'])
        self.queue.cancel(row['id'])
        with self.assertRaises(ValueError):self.queue.reconcile(row['id'])

    def test_reconcile_never_restarts_an_existing_upload_attempt(self):
        row=self.add_queue();self.complete(row);self.queue.update(row['id'],status='needs_review')
        self.workspace.history.create(row['id'],uploader.validate_job(self.release_source(row),self.data['category']))
        with self.assertRaises(ValueError):self.queue.reconcile(row['id'])

    def test_reconcile_reuses_only_cancelled_isolated_download_without_readding(self):
        old=self.legacy_queue();self.complete(old);self.queue.cancel(old['id'])
        new=self.add_queue();self.queue.update(new['id'],status='needs_review')
        self.add.reset_mock()
        self.queue.reconcile(new['id'])
        rows={row['id']:row for row in self.queue.rows()}
        self.assertEqual(rows[old['id']]['status'],'cancelled')
        self.assertEqual(rows[new['id']]['remote_path'],old['remote_path'])
        self.assertEqual(rows[new['id']]['local_path'],old['local_path'])
        self.assertEqual(rows[new['id']]['status'],'downloading')
        self.add.assert_not_called()

    def test_reconcile_rejects_cancelled_download_with_changed_mapping(self):
        old=self.legacy_queue();self.complete(old);self.queue.cancel(old['id'])
        new=self.add_queue();self.queue.update(new['id'],status='needs_review')
        self.queue.update(old['id'],local_path=str(self.content/'unrelated'))
        with self.assertRaisesRegex(ValueError,'different folder mapping'):
            self.queue.reconcile(new['id'])
        self.assertEqual(next(r for r in self.queue.rows() if r['id']==new['id'])['status'],'needs_review')

    def test_reconcile_links_manual_upload_in_existing_folder_without_publishing_twice(self):
        old=self.legacy_queue();self.complete(old);self.queue.cancel(old['id'])
        source=self.release_source(old)
        self.workspace.history.create('manual-job',uploader.validate_job(source,self.data['category']))
        self.workspace.history.finish('manual-job','completed',0)
        new=self.add_queue();self.queue.update(new['id'],status='needs_review')
        with patch.object(self.workspace,'upload') as upload:
            result=self.queue.reconcile(new['id'])
            self.assertTrue(result['existing_upload'])
            row=next(r for r in self.queue.rows() if r['id']==new['id'])
            self.assertEqual(row['job_id'],'manual-job');self.assertEqual(row['status'],'uploaded')
            upload.assert_not_called()

    def test_cancel_keeps_client_and_never_uploads(self):
        row=self.add_queue();self.complete(row);self.queue.cancel(row['id'])
        with patch.object(self.workspace,'upload') as upload:
            self.queue.tick();upload.assert_not_called()
        self.assertEqual(self.queue.rows()[0]['status'],'cancelled')
    def test_restart_during_publish_requires_review_not_retry(self):
        row=self.add_queue();self.queue.update(row['id'],status='publishing')
        queue=ImportQueue(self.workspace)
        self.assertEqual(queue.rows()[0]['status'],'needs_review')
        with patch.object(self.workspace,'upload') as upload:queue.tick();upload.assert_not_called()
    def test_seed_new_digitalcore_torrent_from_matching_parent(self):
        row=self.add_queue();self.complete(row)
        source=self.release_source(row);job=uploader.validate_job(source,self.data['category'])
        torrent=Torrent(path=source);torrent.generate();raw=torrent.dump()
        watch=self.root/'watch';watch.mkdir();target=watch/'confirmed.torrent';target.write_bytes(raw)
        self.workspace.history.create(row['id'],job)
        self.workspace.history.update(row['id'],{'torrent_hash':torrent.infohash,'watch_torrent':str(target)})
        self.workspace.history.finish(row['id'],'completed',0)
        self.queue.update(row['id'],status='uploaded',job_id=row['id'])
        self.status.return_value=(None,[]);self.add.reset_mock()
        with patch.object(self.workspace.clients,'sync'):
            self.queue.tick();self.queue.tick()
        self.add.assert_called_once_with(self.client,torrent.infohash,str(__import__('pathlib').PurePosixPath(row['remote_path']).parent),raw=raw)
        self.assertEqual(self.queue.rows()[0]['seed_status'],'added')

class MetadataTests(unittest.TestCase):
    def test_normalizes_optional_fields_and_metadata(self):
        result=tmdb.normalize({'id':123,'title':'Title','overview':'Summary','release_date':'2024-01-20',
            'production_companies':[{'name':'Studio'}], 'credits':{'cast':[{'name':'Performer'}]},
            'genres':[{'name':'Tag'}], 'poster_path':'/cover.jpg'},'movie')
        self.assertEqual(result['metadata']['release_year'],'2024')
        self.assertEqual(result['metadata']['performers'],'Performer')
        self.assertEqual(len(result['images']),1)
        self.assertEqual(tmdb.normalize({},'movie')['images'],[])
    def test_fixed_origin_and_no_token_in_url(self):
        session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False)
        response=session.get.return_value;response.status_code=200;response.json.return_value={'results':[]}
        with patch.object(tmdb.requests,'Session',return_value=session):
            self.assertEqual(tmdb.search('private-token','Sample title'),[])
        args,kwargs=session.get.call_args
        self.assertEqual(args[0],'https://api.themoviedb.org/3/search/movie')
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer private-token')
        self.assertFalse(kwargs['allow_redirects'])
    def test_reject_url_in_detail_path_and_missing_token(self):
        with self.assertRaises(ValueError):tmdb.detail('secret','https://evil.invalid','movie')
        with self.assertRaises(ValueError):tmdb.search('','Sample')
    def test_auth_errors_do_not_expose_token(self):
        session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False)
        session.get.return_value.status_code=401
        with patch.object(tmdb.requests,'Session',return_value=session),self.assertRaises(ValueError) as caught:
            tmdb.search('private-token','Sample')
        self.assertNotIn('private-token',str(caught.exception))
