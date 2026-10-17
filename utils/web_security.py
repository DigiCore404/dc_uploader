"""Workspace credentials and direct-peer IP access control, persisted privately in SQLite."""
import hashlib
import hmac
import ipaddress
import json
import secrets


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
    return 'pbkdf2_sha256$600000$' + salt + '$' + digest


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or int(rounds) != 600000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def networks(value):
    entries = value if isinstance(value, list) else str(value).replace(',', '\n').splitlines()
    if len(entries) > 100:
        raise ValueError('Use at most 100 IP addresses or CIDR ranges.')
    try:
        return list(dict.fromkeys(str(ipaddress.ip_network(str(v).strip(), strict=False)) for v in entries if str(v).strip()))
    except ValueError as exc:
        raise ValueError('Enter one valid IP address or CIDR network per line.') from exc


def allows(address, rules):
    try:
        ip = ipaddress.ip_address(address)
        if getattr(ip, 'ipv4_mapped', None):
            ip = ip.ipv4_mapped
        return not rules or any(ip in ipaddress.ip_network(rule) for rule in rules)
    except ValueError:
        return False


class Security:
    def __init__(self, history, password, username='admin', allowed_ips=''):
        self.history = history
        self.created = False
        with history.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workspace_security (id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL, allowed_ips TEXT NOT NULL)')
            if not db.execute('SELECT 1 FROM workspace_security WHERE id=1').fetchone():
                db.execute('INSERT INTO workspace_security VALUES (1,?,?,?)',
                           (username.strip() or 'admin', password_hash(password), json.dumps(networks(allowed_ips))))
                self.created = True

    def _row(self):
        with self.history.connect() as db:
            return dict(db.execute('SELECT * FROM workspace_security WHERE id=1').fetchone())

    def state(self):
        row = self._row()
        return {'username': row['username'], 'allowed_ips': json.loads(row['allowed_ips'])}

    def allowed(self, address):
        return allows(address, self.state()['allowed_ips'])

    def verify(self, username, password):
        row = self._row()
        valid_password = verify_password(str(password), row['password_hash'])
        return hmac.compare_digest(str(username).encode(), row['username'].encode()) and valid_password

    def update(self, data, address):
        row = self._row()
        if not verify_password(str(data.get('current_password', '')), row['password_hash']):
            raise ValueError('Enter your current workspace password to change access settings.')
        username = str(data.get('username', '')).strip()
        if not 1 <= len(username) <= 100 or any(c in username for c in '\r\n\x00'):
            raise ValueError('Enter a username between 1 and 100 characters.')
        password = str(data.get('password', ''))
        if password and not 12 <= len(password) <= 256:
            raise ValueError('Use a new password between 12 and 256 characters.')
        rules = networks(data.get('allowed_ips', []))
        if not allows(address, rules):
            raise ValueError('Keep your current IP address allowed to avoid locking yourself out.')
        encoded = password_hash(password) if password else row['password_hash']
        with self.history.connect() as db:
            db.execute('UPDATE workspace_security SET username=?,password_hash=?,allowed_ips=? WHERE id=1',
                       (username, encoded, json.dumps(rules)))
        return self.state()
