# utils/config_loader.py

import configparser
from pathlib import Path

class ConfigLoader:
    def __init__(self, config_file='config.ini'):
        self.config = configparser.ConfigParser()
        config_path = Path(__file__).resolve().parent.parent / config_file
        self.config.read(config_path)

    def get_config(self):
        return self.config
