"""Public std.web example: CLI, HTTP, WebSocket, and graceful shutdown.

Keep this a *black-box* smoke test of the documented Valiance source example,
not a test built from private Python HTTP server APIs.
"""
from __future__ import annotations

import http.client
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "samples" / "web" / "hello_http_websocket.vlnc"


class WebPreviewSmokeTests(unittest.TestCase):
    def test_cli_example_http_websocket_origin_and_graceful_stop(self):
        # Copy the real example and use an ephemeral port so simultaneous test
        # jobs cannot collide with the documented developer port 8765.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("port = 8765", source)
        self.assertIn('get("/health")', source)
        self.assertIn('websocket("/ws")', source)
        with tempfile.TemporaryDirectory(prefix="valiance-web-preview-") as tmp:
            example = Path(tmp) / "example.vlnc"
            example.write_text(source.replace("port = 8765", f"port = {port}"),
                               encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(
                (str(ROOT / "src"), str(ROOT), env.get("PYTHONPATH", "")))
            process = subprocess.Popen(
                [sys.executable, "-m", "valiance.main", "run", "--file", str(example)],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=os.name == "posix")
            evidence = []
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(f"example exited before listening: {process.returncode}")
                    try:
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=.5)
                        connection.request("GET", "/health")
                        response = connection.getresponse()
                        body = response.read()
                        connection.close()
                        if response.status == 200 and body == b"ok":
                            evidence.append("http_get")
                            break
                    except (OSError, http.client.HTTPException):
                        time.sleep(.1)
                else:
                    self.fail("example did not serve GET /health before deadline")
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                connection.request("HEAD", "/health")
                head = connection.getresponse()
                self.assertEqual(head.status, 200)
                self.assertEqual(head.read(), b"")
                connection.close()
                evidence.append("http_head")
                with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5,
                             close_timeout=2) as ws:
                    ws.send("hello")
                    self.assertEqual(ws.recv(), "hello from Valiance")
                evidence.append("websocket")
                with self.assertRaises(InvalidStatus) as rejection:
                    with connect(f"ws://127.0.0.1:{port}/ws",
                                 origin="https://untrusted.example",
                                 open_timeout=5, close_timeout=2):
                        pass
                self.assertEqual(rejection.exception.response.status_code, 403)
                evidence.append("origin_rejected")
            finally:
                if process.poll() is None:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=6)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    self.fail("CLI example did not stop within its shutdown deadline")
            self.assertEqual(evidence, ["http_get", "http_head", "websocket",
                                        "origin_rejected"])
            if os.name == "posix":
                self.assertEqual(process.returncode, 0, f"stdout={stdout}\nstderr={stderr}")
            # No lingering listener after normal server termination.
            with socket.socket() as probe:
                probe.settimeout(1)
                self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)


class FeatureDemoSmokeTests(unittest.TestCase):
    """End-to-end public CLI smoke for the everyday HTTP feature envelope."""

    def test_compiled_feature_demo_http_security_and_websocket(self):
        import gzip
        import json
        source = ROOT / 'samples' / 'web' / 'feature_demo.vlnc'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix='valiance-web-features-') as tmp:
            path = Path(tmp) / 'app.vlnc'
            path.write_text(source.read_text(encoding='utf-8').replace(
                'port = 8765', f'port = {port}'), encoding='utf-8')
            env = os.environ.copy()
            env['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT),
                                                  env.get('PYTHONPATH', '')))
            process = subprocess.Popen(
                [sys.executable, '-m', 'valiance.main', 'run', '--file', str(path)],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=os.name == 'posix')
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(f'feature app exited early: {process.returncode}')
                    try:
                        c = http.client.HTTPConnection('127.0.0.1', port, timeout=.6)
                        try:
                            c.request('GET', '/health')
                            response = c.getresponse()
                            if (response.status, response.read()) == (200, b'ok'):
                                break
                        finally:
                            c.close()
                    except (OSError, http.client.HTTPException):
                        time.sleep(.1)
                else:
                    self.fail('feature demo did not start before deadline')

                def request(method, target, body=None, headers=None):
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                    try:
                        connection.request(method, target, body, headers or {})
                        res = connection.getresponse()
                        result = res.status, dict((k.lower(), v) for k, v in res.getheaders()), res.read()
                        return result
                    finally:
                        connection.close()

                self.assertEqual(request('GET', '/greet?name=Ada')[::2], (200, b'Ada'))
                self.assertEqual(request('POST', '/number', b'42', {
                    'Content-Type': 'application/json'})[::2], (200, b'{"value":42}'))
                self.assertEqual(request('POST', '/number', b'42', {
                    'Content-Type': 'text/plain'})[0], 415)
                self.assertEqual(request('HEAD', '/health')[::2], (200, b''))
                status, headers, body = request('OPTIONS', '/health')
                self.assertEqual(status, 204)
                self.assertIn('GET', headers['allow'])
                status, headers, body = request('GET', '/redirect')
                self.assertEqual((status, headers['location']), (303, '/health'))
                status, headers, _ = request('GET', '/cookie')
                self.assertEqual(status, 200)
                self.assertIn('preview=example', headers['set-cookie'])
                status, headers, body = request('GET', '/cache')
                self.assertEqual((status, body), (200, b'cached'))
                self.assertIn('etag', headers)
                self.assertIn('public', headers['cache-control'])
                self.assertEqual(request('GET', '/cache', headers={
                    'If-None-Match': headers['etag']})[0], 304)
                status, headers, body = request('GET', '/assets/readme.txt', headers={
                    'Accept-Encoding': 'gzip'})
                self.assertEqual(status, 200)
                self.assertEqual(headers.get('content-encoding'), 'gzip')
                self.assertIn(b'Static resources', gzip.decompress(body))
                self.assertNotEqual(request('GET', '/assets/../README.md')[0], 200)
                status, headers, body = request('GET', '/absent')
                self.assertEqual(status, 404)
                self.assertEqual(headers.get('x-preview-error'), 'handled')
                self.assertNotIn(b'Traceback', body)
                status, headers, body = request('OPTIONS', '/number', headers={
                    'Origin': 'https://preview.example',
                    'Access-Control-Request-Method': 'POST',
                    'Access-Control-Request-Headers': 'Content-Type'})
                self.assertEqual(status, 204)
                self.assertEqual(headers.get('access-control-allow-origin'),
                                 'https://preview.example')
                status, headers, body = request('OPTIONS', '/number', headers={
                    'Origin': 'https://attacker.example',
                    'Access-Control-Request-Method': 'POST'})
                self.assertEqual(status, 403)
                self.assertNotIn('access-control-allow-origin', headers)
                with connect(f'ws://127.0.0.1:{port}/ws', open_timeout=5,
                             close_timeout=2) as ws:
                    ws.send('hello')
                    self.assertEqual(ws.recv(), 'hello from Valiance')
            finally:
                if process.poll() is None:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=7)
                except subprocess.TimeoutExpired:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    self.fail('feature demo failed to shut down within deadline')
            self.assertEqual(process.returncode, 0, f'stdout={stdout}\nstderr={stderr}')


if __name__ == "__main__":
    unittest.main()
