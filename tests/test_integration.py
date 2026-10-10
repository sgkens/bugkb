"""Integration checks against disposable PocketBase data or a disposable container."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proc = None
        cls.temp = None
        cls.url = os.environ.get('KB_TEST_URL')
        if not cls.url:
            cls.temp = tempfile.TemporaryDirectory(prefix='bugkb-test-')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            cls.url = f'http://127.0.0.1:{port}'
            binary = os.environ.get('PB_BINARY', str(ROOT / 'linux_amd64/pocketbase'))
            cls.log = open(Path(cls.temp.name) / 'server.log', 'w+')
            cls.proc = subprocess.Popen([
                binary, 'serve', f'--http=127.0.0.1:{port}',
                f'--dir={cls.temp.name}/data', f'--hooksDir={ROOT}/pb_hooks',
                f'--migrationsDir={ROOT}/pb_migrations',
            ], stdout=cls.log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    code, info = cls.request('GET', '/api/kb/info')
                    if code == 200 and info['hooks'] == 1:
                        break
                except (OSError, KeyError):
                    pass
                time.sleep(0.1)
            else:
                if cls.proc:
                    cls.log.seek(0)
                    raise RuntimeError(cls.log.read())
                raise RuntimeError('Disposable test server did not become ready')
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait()
            cls.log.close()
            cls.temp.cleanup()

    @classmethod
    def request(cls, method, path, body=None, headers=None):
        req = urllib.request.Request(
            cls.url + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json', 'X-KB-Project': 'test-project', **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read() or '{}')
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.loads(response.read() or '{}')

    def add(self, **changes):
        body = dict(type='bug', title='Integration ' + self._testMethodName,
                    symptom='Distinct symptom ' + self._testMethodName,
                    fix='Apply verified correction', tags=['DOCKER'], project='test-project')
        body.update(changes)
        code, record = self.request('POST', '/api/collections/entries/records', body, {'X-KB-Force': '1'})
        self.assertEqual(code, 200, record)
        return record

    def test_create_trust_and_search(self):
        rec = self.add(status='confirmed', hits=100, seen_in=['fake'], source='human')
        self.assertEqual((rec['status'], rec['hits'], rec['seen_in'], rec['source']),
                         ('unverified', 1, ['test-project'], 'agent'))
        self.assertEqual(rec['tags'], ['docker'])
        code, results = self.request('GET', '/api/kb/search?q=test_create_trust_and_search')
        self.assertEqual(code, 200)
        self.assertIn(rec['id'], [r['id'] for r in results['items']])

    def test_validation(self):
        for changes in ({'tags': []}, {'fix': ''}, {'env': {'token': 'abcdefghijklmno'}},
                        {'type': 'perf', 'metrics': {'before': 'slow', 'after': 1}}):
            body = dict(type='bug', title='Validation test entry', symptom='failure', fix='fix',
                        tags=['test'], project='test')
            body.update(changes)
            code, _ = self.request('POST', '/api/collections/entries/records', body)
            self.assertEqual(code, 400, changes)
        self.add(type='perf', metrics={'before': 100, 'after': 10, 'unit': 'ms'})

    def test_duplicate_refusal(self):
        rec = self.add()
        code, _ = self.request('POST', '/api/collections/entries/records', {
            key: rec[key] for key in ('type', 'title', 'symptom', 'fix', 'tags', 'project')})
        self.assertEqual(code, 409)

    def test_protected_fields_and_delete(self):
        rec = self.add()
        path = '/api/collections/entries/records/' + rec['id']
        for changes in ({'hits': 100}, {'status': 'confirmed'}, {'source': 'human'}, {'project': 'fake'}):
            code, _ = self.request('PATCH', path, changes)
            self.assertEqual(code, 400)
        code, _ = self.request('DELETE', path)
        self.assertIn(code, (403, 404))

    def test_confirmation_and_fix_history(self):
        rec = self.add(env={'python': '3.12'})
        code, confirmed = self.request('POST', '/api/kb/confirm/' + rec['id'],
                                       {'project': 'second', 'env': {'python': '3.14'}})
        self.assertEqual(code, 200, confirmed)
        self.assertEqual(confirmed['status'], 'confirmed')
        self.assertEqual(confirmed['env']['python'], '3.12, 3.14')
        code, updated = self.request('PATCH', '/api/collections/entries/records/' + rec['id'],
                                     {'fix': 'A newer verified correction'})
        self.assertEqual(code, 200, updated)
        self.assertEqual(updated['status'], 'unverified')
        self.assertIn(rec['fix'], updated['notes'])
        code, _ = self.request('PATCH', '/api/collections/entries/records/' + rec['id'], {'notes': 'replace history'})
        self.assertEqual(code, 400)

    def test_confirmation_secret_rollback(self):
        rec = self.add()
        code, _ = self.request('POST', '/api/kb/confirm/' + rec['id'], {'env': {'token': 'abcdefghijklmno'}})
        self.assertEqual(code, 400)
        _, current = self.request('GET', '/api/collections/entries/records/' + rec['id'])
        self.assertEqual(current['hits'], 1)

    def test_parallel_confirmations(self):
        rec = self.add()
        def confirm(i):
            return self.request('POST', '/api/kb/confirm/' + rec['id'], {'project': f'parallel-{i}'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for code, result in pool.map(confirm, range(12)):
                self.assertEqual(code, 200, result)
        _, current = self.request('GET', '/api/collections/entries/records/' + rec['id'])
        self.assertEqual(current['hits'], 13)
        self.assertEqual(len(current['seen_in']), 13)

    def test_obsolete_and_revive(self):
        rec = self.add()
        code, _ = self.request('POST', '/api/kb/obsolete/' + rec['id'], {'reason': 'Replaced by a newer tool'})
        self.assertEqual(code, 200)
        _, result = self.request('GET', '/api/kb/search?q=test_obsolete_and_revive')
        self.assertNotIn(rec['id'], [r['id'] for r in result['items']])
        _, result = self.request('GET', '/api/kb/search?q=test_obsolete_and_revive&all=1')
        self.assertIn(rec['id'], [r['id'] for r in result['items']])
        _, result = self.request('POST', '/api/kb/confirm/' + rec['id'], {'project': 'third'})
        self.assertEqual(result['status'], 'unverified')

    def test_cli(self):
        env = {**os.environ, 'KB_URL': self.url}
        for platform in ('codex', 'claude', 'antigravity'):
            cli = ROOT / 'skills' / platform / 'bugkb/scripts/kb.py'
            result = subprocess.run(['python3', str(cli), 'health'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(['python3', str(cli), 'show'], env=env, capture_output=True)
        self.assertEqual(result.returncode, 4)

    def test_index_consistency(self):
        _, info = self.request('GET', '/api/kb/info')
        self.assertEqual(info['entries'], info['indexed'])


if __name__ == '__main__':
    unittest.main()
