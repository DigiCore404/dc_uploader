"""Record non-browser uploads in the same Activity database as the workspace."""
import re
import uuid
from .job_history import JobHistory


def record(root, job, config, output, worker):
    history = JobHistory(root / 'data/uploads.db')
    identity = uuid.uuid4().hex
    secrets = [value for section in config.sections() for key, value in config.items(section)
               if value and re.search(r'(password|passwd|passkey|api_?key|token|secret|authcode|cookie|announceurl)$', key, re.I)]

    def redact(value):
        text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', str(value))
        for secret in sorted(secrets, key=len, reverse=True):
            text = text.replace(secret, '[redacted]')
        return text

    entry = dict(job)
    entry.setdefault('category', None)
    history.create(identity, entry)

    def emit(line):
        history.append_log(identity, redact(line))
        output(line)

    def event(fields):
        history.update(identity, {key: redact(value) if isinstance(value, str) else value
                                  for key, value in fields.items()})
        handler = getattr(output, 'event', None)
        if handler:
            handler(fields)

    emit.event = event
    emit.history_managed = True
    try:
        code = worker(emit)
    except BaseException as exc:
        error = redact(exc) or type(exc).__name__
        history.append_log(identity, error)
        history.finish(identity, 'interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'needs_review', error=error)
        raise
    status = 'completed' if code == 0 else 'duplicate' if code == 10 else 'needs_review'
    history.finish(identity, status, code, history.get(identity)['error'])
    return code
