"""Check isolation, dump handling and cleanup without a Docker daemon."""
import gzip
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().with_suffix('.sh')


class RestoreTests(unittest.TestCase):
    def invoke(self, engine='postgresql', fail=False, duplicate=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump = root / 'backup.sql.gz'
            sql = 'CREATE ROLE rap_admin;\nCREATE ROLE application_role;\nGRANT application_role TO rap_admin;\n'
            if duplicate:
                sql += 'CREATE ROLE rap_admin;\n'
            with gzip.open(dump, 'wt') as stream:
                stream.write(sql)
            envfile = root / 'environment.sh'
            envfile.write_text('''
mktemp() { command mktemp -d "$TEST_ROOT/logs-XXXXXXXX"; }
docker() {
    printf '%s ' "$@" >> "$TEST_ROOT/commands"
    printf '\\n' >> "$TEST_ROOT/commands"
    case "$1" in
        create) echo test-container-id ;;
        start|rm|logs) : ;;
        exec)
            if [ "$2" = -i ]; then
                cat > "$TEST_ROOT/restored.sql"
                [ "$FAIL_RESTORE" != yes ] || return 3
            fi
            if [[ "$*" = *information_schema.SCHEMATA* ]]; then echo 3; fi
            ;;
        *) return 1 ;;
    esac
}
''')
            result = subprocess.run(['bash', str(SCRIPT), engine, str(dump)],
                env=dict(os.environ, BASH_ENV=str(envfile), TEST_ROOT=str(root),
                         FAIL_RESTORE='yes' if fail else 'no'), capture_output=True, text=True)
            commands = (root / 'commands').read_text() if (root / 'commands').exists() else ''
            restored = (root / 'restored.sql').read_text() if (root / 'restored.sql').exists() else ''
            return result, commands, restored, sql

    def test_postgres_only_skips_duplicate_bootstrap_role(self):
        result, commands, restored, original = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(restored, original.replace('CREATE ROLE rap_admin;\n', ''))
        self.assertIn('--network none --memory 1g --cpus 0.5', commands)
        self.assertIn('POSTGRES_USER=rap_admin', commands)
        self.assertIn('ON_ERROR_STOP=1', commands)
        self.assertNotIn('global_postgres', commands)
        self.assertIn('rm -fv hestia-restore-check-postgresql-', commands)

    def test_mariadb_restores_original_dump_without_network_or_events(self):
        result, commands, restored, original = self.invoke(engine='mariadb')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(restored, original)
        self.assertIn('--network none --memory 512m --cpus 0.5', commands)
        self.assertIn('--skip-networking --event-scheduler=OFF', commands)
        self.assertIn('rm -fv hestia-restore-check-mariadb-', commands)

    def test_failed_restore_is_reported_and_only_test_container_is_removed(self):
        result, commands, _, _ = self.invoke(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('rm -fv hestia-restore-check-postgresql-', commands)
        self.assertNotIn('prune', commands)
        self.assertNotIn('Isolated restore completed', result.stdout)

    def test_unsupported_bootstrap_is_rejected_before_container_creation(self):
        result, commands, _, _ = self.invoke(duplicate=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, '')


if __name__ == '__main__':
    unittest.main()
