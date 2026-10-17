"""Machine-readable worker events, separate from human-readable console output."""
import json

PREFIX = 'DIGITALCORE_EVENT '


def publish(**fields):
    print(PREFIX + json.dumps(fields, ensure_ascii=False), flush=True)
