import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import runtime_tools
from utils.web_settings import defaults


class RuntimeToolTests(unittest.TestCase):
    def test_bundled_tool_wins_over_old_system_tool(self):
        for tool, relative in [('mkbrr', 'bin/mkbrr/linux/x86_64/mkbrr'), ('mtn', 'bin/mtn/mtn')]:
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                bundled = root / relative
                bundled.parent.mkdir(parents=True)
                bundled.write_text('fixture')
                with patch.object(runtime_tools, 'ROOT', root), patch.object(runtime_tools.platform, 'system', return_value='Linux'), patch.object(runtime_tools.platform, 'machine', return_value='x86_64'), patch.object(runtime_tools.os, 'access', return_value=True), patch.object(runtime_tools.shutil, 'which', return_value='/old/system/' + tool):
                    self.assertEqual(runtime_tools.find_tool(tool, defaults()), str(bundled))

    def test_explicit_configuration_is_still_respected(self):
        config = defaults()
        config.set('Torrent', 'MKBRR_BIN', 'my-mkbrr')
        with patch.object(runtime_tools.shutil, 'which', return_value='/custom/mkbrr') as which:
            self.assertEqual(runtime_tools.find_tool('mkbrr', config), '/custom/mkbrr')
            which.assert_called_once_with('my-mkbrr')
