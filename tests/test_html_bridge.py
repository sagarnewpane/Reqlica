"""Exercise the actual embedded local bridge with an isolated upstream."""

import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class DashboardBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []

        class Upstream(BaseHTTPRequestHandler):
            def dispatch(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                cls.requests.append((self.command, self.path, dict(self.headers), len(body)))
                if self.path.endswith('/export') or self.path == '/api/large':
                    payload = b'PK' + b'x' * (17 * 1024 * 1024)
                    media = 'application/zip'
                else:
                    payload = json.dumps({'bytes': len(body)}).encode()
                    media = 'application/json'
                self.send_response(204 if self.command == 'OPTIONS' else 200)
                self.send_header('Content-Type', media)
                self.send_header('Access-Control-Allow-Origin', 'http://localhost:5173')
                self.send_header('Content-Length', str(0 if self.command == 'OPTIONS' else len(payload)))
                self.end_headers()
                if self.command != 'OPTIONS':
                    self.wfile.write(payload)
            do_GET = do_POST = do_OPTIONS = dispatch
            def log_message(self, *_args):
                pass

        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        p = Path(__file__).parents[1] / 'backend/reqlica/reqlicav1.html'
        source = p.read_text().split('<!-- REQLICA_LOCAL_SERVER\n')[1].split('\nREQLICA_LOCAL_SERVER -->')[0]
        namespace = {'p': p}
        exec(source.split('server = ThreadingHTTPServer')[0], namespace)
        port = cls.upstream.server_port
        namespace['targets'] = {service: ('127.0.0.1', port, f'http://127.0.0.1:{port}') for service in ('console', 'gateway')}
        cls.bridge = ThreadingHTTPServer(('127.0.0.1', 0), namespace['Handler'])
        cls.authority = f'127.0.0.1:{cls.bridge.server_port}'
        namespace['allowed_hosts'] = {cls.authority}
        namespace['allowed_origins'] = {'http://' + cls.authority}
        threading.Thread(target=cls.bridge.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.bridge, cls.upstream):
            server.shutdown()
            server.server_close()

    def request(self, path, method='GET', body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.bridge.server_port, timeout=10)
        try:
            connection.request(method, path, body, {'X-Reqlica-UI':'1', **(headers or {})})
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def test_large_zip_uploads_only_allowed_on_import_routes(self):
        payload = b'PK' + b'x' * (3 * 1024 * 1024)
        for path in ['/console/api/projects/import?directory=/tmp/new', '/console/api/projects/workspace-import?directory=/tmp/new']:
            status, _headers, body = self.request(path, 'POST', payload, {'Content-Type':'application/zip'})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['bytes'], len(payload))
        self.assertEqual(self.request('/console/api/projects/p/scenarios', 'POST', headers={'Content-Length':str(len(payload))})[0], 413)
        self.assertEqual(self.request('/console/api/projects/import', 'POST', headers={'Content-Length':str(32 * 1024 * 1024 + 1)})[0], 413)

    def test_zip_download_bypasses_text_display_limit_only_for_archive_paths(self):
        status, headers, content = self.request('/console/api/projects/p/export')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/zip')
        self.assertTrue(content.startswith(b'PK'))
        self.assertGreater(len(content), 16 * 1024 * 1024)
        self.assertEqual(self.request('/console/api/large')[0], 502)

    def test_cors_diagnostic_preserves_guards_and_returns_upstream_headers_as_data(self):
        headers = {'Origin':'http://' + self.authority, 'X-Reqlica-Cors-Probe':'1',
                   'X-Reqlica-Probe-Origin':'http://localhost:5173', 'X-Reqlica-Probe-Method':'POST',
                   'X-Reqlica-Probe-Headers':'Content-Type, X-API-Key'}
        path = '/gateway/mock/p/hello'
        status, response_headers, content = self.request(path, 'OPTIONS', headers=headers)
        self.assertEqual(status, 200)
        data = json.loads(content)
        self.assertEqual(data['status'], 204)
        self.assertEqual(data['headers']['Access-Control-Allow-Origin'], 'http://localhost:5173')
        self.assertNotIn('Access-Control-Allow-Origin', response_headers)
        upstream = self.requests[-1][2]
        self.assertEqual(upstream['Origin'], 'http://localhost:5173')
        self.assertEqual(upstream['Access-Control-Request-Method'], 'POST')
        self.assertFalse(any(name.lower().startswith('x-reqlica-probe') for name in upstream))
        self.assertNotIn('X-Reqlica-UI', upstream)
        self.assertEqual(self.request('/gateway/api/projects', 'OPTIONS', headers=headers)[0], 400)
        self.assertEqual(self.request(path, 'OPTIONS', headers={**headers, 'Origin':'https://untrusted.example'})[0], 403)
        self.assertEqual(self.request(path, 'OPTIONS', headers={**headers, 'X-Reqlica-UI':''})[0], 403)
        self.assertEqual(self.request(path, 'OPTIONS', headers={**headers, 'X-Reqlica-Probe-Origin':'file:///tmp'})[0], 400)
        self.assertEqual(self.request(path, 'OPTIONS', headers={**headers, 'X-Reqlica-Probe-Headers':'invalid header'})[0], 400)

    def test_plain_requests_never_forward_private_probe_or_origin_headers(self):
        status, headers, _content = self.request('/gateway/mock/p/hello', headers={'Origin':'http://' + self.authority, 'X-Reqlica-Probe-Origin':'https://untrusted.example'})
        self.assertEqual(status, 200)
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertNotIn('Origin', self.requests[-1][2])
        self.assertNotIn('X-Reqlica-Probe-Origin', self.requests[-1][2])
