"""Reproduce restrictive checkout modes and check staged code normalization."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PayloadPermissionTests(unittest.TestCase):
    def test_restrictive_checkout_is_readable_in_stage_without_touching_source_or_symlink_target(self):
        source = (ROOT / 'src/hst_autocompile.sh').read_text()
        function = re.search(r'^normalize_hestia_payload\(\) \{\n.*?^\}', source, re.M | re.S)
        self.assertIsNotNone(function)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / 'checkout'
            page = checkout / 'web/list/mail/performance'
            page.mkdir(parents=True)
            (page / 'index.php').write_text('<?php echo "page";\n')
            private = root / 'private'
            private.write_text('private fixture\n')
            private.chmod(0o600)
            (checkout / 'external').symlink_to(private)
            for path in checkout.rglob('*'):
                if path.is_symlink():
                    continue
                path.chmod(0o700 if path.is_dir() else 0o600)
            stage = root / 'stage'
            stage.mkdir()
            command = function[0] + '\ncp -R "$1/." "$2/"\nnormalize_hestia_payload "$2"\n'
            subprocess.run(['bash', '-c', command, 'test', str(checkout), str(stage)], check=True)
            self.assertEqual((stage / 'web/list/mail/performance/index.php').stat().st_mode & 0o777, 0o644)
            self.assertEqual((stage / 'web/list/mail/performance').stat().st_mode & 0o777, 0o755)
            self.assertEqual((page / 'index.php').stat().st_mode & 0o777, 0o600)
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
            self.assertEqual(stage.stat().st_mode & 0o777, 0o755)


if __name__ == '__main__':
    unittest.main()
