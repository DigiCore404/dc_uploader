"""Durable per-attempt history shared by the HTML UI and upload worker events."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

STATUSES = ('running', 'completed', 'duplicate', 'needs_review', 'interrupted')
EVENT_FIELDS = {'stage', 'site_status', 'torrent_id', 'torrent_name', 'watch_torrent', 'error', 'exit_code', 'torrent_hash', 'hash_progress', 'hash_mode', 'hash_total'}


class JobHistory:
    def __init__(self, path, legacy_path=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS upload_jobs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
                    category INTEGER, status TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'starting', started REAL NOT NULL, finished REAL,
                    error TEXT, exit_code INTEGER, site_status TEXT NOT NULL DEFAULT 'not_started',
                    torrent_id INTEGER, torrent_name TEXT, watch_torrent TEXT,
                    description TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',
                    images_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_upload_jobs_started ON upload_jobs(started DESC, id);
                CREATE INDEX IF NOT EXISTS idx_upload_jobs_status ON upload_jobs(status, started DESC);
                CREATE TABLE IF NOT EXISTS upload_job_logs (
                    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES upload_jobs(id),
                    created REAL NOT NULL, message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_upload_job_logs ON upload_job_logs(job_id, id);
                CREATE TABLE IF NOT EXISTS upload_job_events (
                    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES upload_jobs(id),
                    created REAL NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_upload_job_events ON upload_job_events(job_id, id);
                CREATE TABLE IF NOT EXISTS upload_history_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            columns={row[1] for row in db.execute('PRAGMA table_info(upload_jobs)')}
            for name,kind in [('hash_progress','REAL'),('hash_mode','TEXT'),('hash_total','INTEGER')]:
                if name not in columns:db.execute(f'ALTER TABLE upload_jobs ADD COLUMN {name} {kind}')
            if 'torrent_hash' not in {row[1] for row in db.execute('PRAGMA table_info(upload_jobs)')}:
                db.execute('ALTER TABLE upload_jobs ADD COLUMN torrent_hash TEXT')
        if __import__('os').name != 'nt':
            self.path.chmod(0o600)
        if legacy_path:
            self.import_json(Path(legacy_path))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=15000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_json(self, path):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM upload_history_meta WHERE key='json_imported'").fetchone():
                return
            if not path.exists():
                return
            old = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(old, list):
                raise ValueError('The previous history file is invalid; it was left unchanged.')
            for job in old:
                if not isinstance(job, dict) or not job.get('id') or not job.get('name'):
                    raise ValueError('The previous history has an invalid record; it was left unchanged.')
                status = job.get('status', 'needs_review')
                if status not in STATUSES:
                    status = 'needs_review'
                inserted = db.execute('''INSERT OR IGNORE INTO upload_jobs
                    (id, name, category, status, stage, started, finished, site_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                    (job['id'], job['name'], job.get('category'), status, status,
                     job.get('started', time.time()), job.get('finished'),
                     'accepted' if status == 'completed' else 'duplicate' if status == 'duplicate' else 'unknown')).rowcount
                if inserted:
                    db.executemany('INSERT INTO upload_job_logs(job_id, created, message) VALUES (?, ?, ?)',
                                   [(job['id'], job.get('started', time.time()), str(line)) for line in job.get('logs', [])])
                    self._event(db, job['id'], status, status, {'imported': True})
            db.execute("INSERT INTO upload_history_meta VALUES ('json_imported', ?)", (str(time.time()),))

    def _event(self, db, identity, status, stage, details):
        db.execute('INSERT INTO upload_job_events(job_id, created, status, stage, details_json) VALUES (?, ?, ?, ?, ?)',
                   (identity, time.time(), status, stage, json.dumps(details, ensure_ascii=False)))

    def recover_interrupted(self):
        with self.connect() as db:
            rows = db.execute("SELECT id, site_status FROM upload_jobs WHERE status='running'").fetchall()
            for row in rows:
                message = 'The worker stopped before completion. Check DigitalCore before retrying.'
                db.execute("UPDATE upload_jobs SET status='interrupted', stage='interrupted', finished=?, error=? WHERE id=?",
                           (time.time(), message, row['id']))
                self._event(db, row['id'], 'interrupted', 'interrupted', {'site_status': row['site_status'], 'error': message})
        return len(rows)

    def create(self, identity, job):
        now = time.time()
        with self.connect() as db:
            db.execute('''INSERT INTO upload_jobs
                (id, name, source, category, status, stage, started, description, metadata_json, images_json)
                VALUES (?, ?, ?, ?, 'running', 'starting', ?, ?, ?, ?)''',
                (identity, Path(job['source']).name, job['source'], job['category'], now,
                 job.get('description', ''), json.dumps(job.get('metadata', {}), ensure_ascii=False),
                 json.dumps(job.get('images', []), ensure_ascii=False)))
            self._event(db, identity, 'running', 'starting', {})

    def append_log(self, identity, line):
        with self.connect() as db:
            db.execute('INSERT INTO upload_job_logs(job_id, created, message) VALUES (?, ?, ?)',
                       (identity, time.time(), line))

    def update(self, identity, event):
        fields = {key: value for key, value in event.items() if key in EVENT_FIELDS}
        if not fields:
            return
        with self.connect() as db:
            db.execute('UPDATE upload_jobs SET ' + ', '.join(f'{key}=?' for key in fields) + ' WHERE id=?',
                       (*fields.values(), identity))
            row = db.execute('SELECT status, stage FROM upload_jobs WHERE id=?', (identity,)).fetchone()
            if row:
                self._event(db, identity, row['status'], row['stage'], fields)

    def finish(self, identity, status, code=None, error=None):
        if status not in STATUSES or status == 'running':
            raise ValueError('Invalid final upload status.')
        with self.connect() as db:
            db.execute('''UPDATE upload_jobs SET status=?, stage=?, finished=?, exit_code=?,
                       error=COALESCE(?, error) WHERE id=?''', (status, status, time.time(), code, error, identity))
            self._event(db, identity, status, status, {'exit_code': code, 'error': error})

    def _record(self, row):
        if row is None:
            return None
        record = dict(row)
        record['metadata'] = json.loads(record.pop('metadata_json'))
        record['images'] = json.loads(record.pop('images_json'))
        return record

    def get(self, identity):
        with self.connect() as db:
            record = self._record(db.execute('SELECT * FROM upload_jobs WHERE id=?', (identity,)).fetchone())
            if record is None:
                return None
            # All lines remain in SQLite; the live view returns the latest 1000.
            logs = db.execute('SELECT message FROM upload_job_logs WHERE job_id=? ORDER BY id DESC LIMIT 1000', (identity,)).fetchall()
            record['logs'] = [row['message'] for row in reversed(logs)]
            record['log_count'] = db.execute('SELECT COUNT(*) FROM upload_job_logs WHERE job_id=?', (identity,)).fetchone()[0]
            events = db.execute('SELECT created, status, stage, details_json FROM upload_job_events WHERE job_id=? ORDER BY id', (identity,)).fetchall()
            record['events'] = [{'created': row['created'], 'status': row['status'], 'stage': row['stage'],
                                 'details': json.loads(row['details_json'])} for row in events]
        return record

    def page(self, limit=30, offset=0, status='', search='', sort='started', direction='desc'):
        limit = max(1, min(100, int(limit)))
        offset = max(0, int(offset))
        if status and status not in (*STATUSES, 'attention'):
            raise ValueError('Unknown status filter.')
        filters, values = [], []
        if status == 'attention':
            filters.append("status IN ('needs_review','interrupted')")
        elif status:
            filters.append('status=?'); values.append(status)
        if search.strip():
            filters.append("(instr(lower(name),lower(?))>0 OR instr(lower(source),lower(?))>0 OR CAST(torrent_id AS TEXT)=?)")
            values.extend([search.strip()[:500]]*3)
        where = ' WHERE ' + ' AND '.join(filters) if filters else ''
        columns = {'started':'started', 'name':'name COLLATE NOCASE', 'status':'status COLLATE NOCASE', 'torrent_id':'torrent_id'}
        order = columns.get(sort, 'started') + (' ASC' if direction == 'asc' else ' DESC')
        with self.connect() as db:
            rows = db.execute('SELECT * FROM upload_jobs' + where + ' ORDER BY ' + order + ', id DESC LIMIT ? OFFSET ?',
                              (*values, limit, offset)).fetchall()
            count = db.execute('SELECT COUNT(*) FROM upload_jobs' + where, values).fetchone()[0]
            counts = {key: 0 for key in STATUSES}
            counts.update({row[0]: row[1] for row in db.execute('SELECT status, COUNT(*) FROM upload_jobs GROUP BY status')})
        return {'jobs': [self._record(row) for row in rows], 'total': count, 'counts': counts,
                'all_total': sum(counts.values()), 'offset': offset, 'limit': limit}
