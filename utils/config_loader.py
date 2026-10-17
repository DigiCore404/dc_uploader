# utils/config_loader.py

import configparser
import os
from pathlib import Path

class ConfigLoader:
    _cache = {}

    def __init__(self, config_file='config.ini'):
        root = Path(__file__).resolve().parent.parent
        config_path = Path(os.environ.get('DIGITALCORE_CONFIG', str(root / config_file))).resolve()
        cache_key = config_path.resolve()

        if cache_key not in self._cache:
            config = configparser.ConfigParser(interpolation=None)
            config.read(root / 'defaults.ini', encoding='utf-8')
            if not config.read(config_path, encoding='utf-8'):
                raise ValueError(f'Configuration missing: {config_path}. Copy config.example.ini to config.ini.')
            config.remove_section('PussyTorrents')
            for key in ('DATADIR', 'WATCHFOLDER', 'TMP_DIR', 'TEMPLATE_PATH', 'FILTERS', 'UPLOADLOG'):
                value = Path(config.get('Paths', key)).expanduser()
                config.set('Paths', key, str(value if value.is_absolute() else root / value))
            if config.get('ImageHost','PROVIDER',fallback='digitalcore').strip() != 'custom' and not config.get('ImageHost', 'UPLOADIMGURL', fallback='').strip():
                config.set('ImageHost', 'UPLOADIMGURL', 'https://imghost.dev/api/1/upload' if config.get('ImageHost','PROVIDER',fallback='digitalcore')=='imghost' else 'https://img.digitalcore.club/api/upload')
            if os.environ.get('DIGITALCORE_CONTAINER') == '1':
                for key, value in {'DATADIR': '/content', 'WATCHFOLDER': '/watch', 'TMP_DIR': '/app/tmp',
                                   'UPLOADLOG': '/app/logs/upload.log'}.items():
                    config.set('Paths', key, value)
                config.set('UnrarUpload','MOUNT_BASE','/mounts')
                config.set('UnrarUpload','REGISTRY','/app/data/unrar_mounts.json')
                config.set('UnrarUpload','LOG','/app/logs/unrar_upload.log')
                config.set('UnrarUpload','CLEANUP_LOG','/app/logs/unrar_cleanup.log')
            if config.get('Torrent','HASHER',fallback='').lower()=='torf':
                config.set('Torrent','HASHER','mkbrr')
            self._cache[cache_key] = config

        self.config = self._cache[cache_key]

    def get_config(self):
        return self.config
