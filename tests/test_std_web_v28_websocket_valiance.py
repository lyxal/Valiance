"""Compiled Valiance WebSocket route end-to-end over real HTTP/1.1 sockets."""
from __future__ import annotations

import unittest

from websockets.sync.client import connect

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import web as bridge


class WebSocketValianceTests(unittest.TestCase):
    SOURCE = r'''import {std.web, std.web.App, std.file.#byte}
$app = App
$app := websocket("/echo"): fn(socket: web.WebSocket) =>
  $incoming = web.websocketReceive($socket) ?
  web.websocketSendText($socket, "valiance") ?
  web.websocketSendBytes($socket, [0 #byte, 255 #byte, 17 #byte]) ?
  web.websocketClose($socket) ?
end
web.serve($app, web.ServerConfig(port = 0)) ?'''

    def test_public_routes_mark_upgrade_separately(self):
        source = r'''import {std.web, std.web.App}
$app = App
$app := websocket("/ws"): fn(socket: web.WebSocket) =>
  web.websocketClose($socket) ?
end
web.routes($app) ?'''
        analyser = Analyser()
        checked = analyser.analyse(parse(source))
        self.assertEqual([str(d) for d in analyser.diagnostics], [])
        descriptors, = run(compile_program(checked))
        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0].fields['method'].type_name, 'web.Method.GET')
        self.assertEqual(int(descriptors[0].fields['upgrade'].value), 1)

    def test_valiance_middleware_receives_websocket_request_variant(self):
        source = r'''import {std.web, std.web.App, std.web.[websocket, middleware]}
$app = App
$app := middleware: fn (req: web.Request) => $req end
$app := websocket("/echo/{room}"): fn(socket: web.WebSocket) =>
  $req = web.websocketRequest($socket)
  web.websocketSendText($socket, $req.originalMethod) ?
  web.websocketSendText($socket, $req.path) ?
  web.websocketClose($socket) ?
end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        for serialized in (False, True):
            with self.subTest(serialized=serialized):
                analyser = Analyser()
                checked = analyser.analyse(parse(source))
                self.assertEqual([str(d) for d in analyser.diagnostics], [])
                program = compile_program(checked, optimize=serialized)
                lease, = run(loads(dumps(program)) if serialized else program)
                token = bridge._lease_token(lease)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        port = bridge._SERVER_LEASES[token].bound_address[1]
                    with connect(f'ws://127.0.0.1:{port}/echo/blue',
                                 open_timeout=3, close_timeout=1) as sock:
                        self.assertEqual(sock.recv(), 'GET')
                        self.assertEqual(sock.recv(), '/echo/blue')
                    with bridge._WS_LOCK:
                        self.assertFalse(bridge._WS_LEASES)
                finally:
                    bridge._close_server_token(token, .5)

    def test_native_bridge_real_socket_normal_optimized_serialized(self):
        for serialize in (False, True):
            with self.subTest(serialized=serialize):
                analyser = Analyser()
                checked = analyser.analyse(parse(self.SOURCE))
                self.assertEqual([str(d) for d in analyser.diagnostics], [])
                program = compile_program(checked, optimize=serialize)
                lease, = run(loads(dumps(program)) if serialize else program)
                token = bridge._lease_token(lease)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        addr = bridge._SERVER_LEASES[token].bound_address
                    with connect(f'ws://127.0.0.1:{addr[1]}/echo',
                                 open_timeout=3, close_timeout=1) as sock:
                        sock.send('hello')
                        self.assertEqual(sock.recv(), 'valiance')
                        self.assertEqual(sock.recv(), b'\x00\xff\x11')
                    with bridge._WS_LOCK:
                        self.assertFalse(bridge._WS_LEASES)
                finally:
                    bridge._close_server_token(token, .5)


if __name__ == '__main__':
    unittest.main()
