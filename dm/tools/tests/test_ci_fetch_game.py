"""The game download of the build on GitHub's Mac (dm/tools/ci_fetch_game.py): names from the server and the URL, file kinds from their headers, archives unpacked (nested, folders
flattened, junk dropped, names kept unique), links masked and never printed, a download that breaks off resumed. A local HTTP server stands in for the file host; no game files,
keys or network needed (temporary files only in a temporary folder)."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_fetch_game as ci   # noqa: E402

BASE, UPDATE = 'Game [0100000000010000][v0]', 'Game [0100000000010800][v65536]'


def pfs0(names, pad=4096):   # a PFS0 (an NSP's container) with these entry names
    strs = b''.join(n.encode() + b'\0' for n in names)
    ent = b''.join(struct.pack('<QQII', 0, 0, sum(len(m.encode()) + 1 for m in names[:i]), 0) for i in range(len(names)))
    return b'PFS0' + struct.pack('<III', len(names), len(strs), 0) + ent + strs + b'\0' * pad


def xci():
    return b'\0' * 0x100 + b'HEAD' + b'\0' * 4096


def zipped(members):   # {name: bytes} → a .zip's bytes
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, data in members.items(): z.writestr(n, data)
    return b.getvalue()


class Names(unittest.TestCase):
    def test_served_name(self):
        self.assertEqual(ci.served_name(f'attachment; filename="{BASE}.nsp"', 'https://h/x?id=1'), f'{BASE}.nsp')
        self.assertEqual(ci.served_name("attachment; filename*=UTF-8''Game%20%5Bv0%5D.nsp", 'https://h/x'), 'Game [v0].nsp')   # RFC 5987
        self.assertEqual(ci.served_name('attachment; filename="../../etc/x.zip"', 'https://h/x'), 'x.zip')   # no folders
        self.assertEqual(ci.served_name('attachment; filename="C:\\dl\\x.7z"', 'https://h/x'), 'x.7z')
        self.assertEqual(ci.served_name('', 'https://h/files/Game%20%5Bv0%5D.NSP'), 'Game [v0].NSP')   # the URL's, with a game extension
        self.assertEqual(ci.served_name('', 'https://h/d/AbC123secret'), '')   # a segment without one may be the link's secret: not taken, so never printed
        self.assertEqual(ci.served_name('', 'https://h/'), '')

    def test_game_name(self):
        with tempfile.TemporaryDirectory() as t:
            nsp, nsz, x = Path(t, 'a'), Path(t, 'b'), Path(t, 'c')
            nsp.write_bytes(pfs0(['0100000000010000000000000000000a.tik', 'x.nca'])); nsz.write_bytes(pfs0(['x.ncz'])); x.write_bytes(xci())
            self.assertEqual(ci.sniff(nsp), 'pfs0'); self.assertEqual(ci.sniff(x), 'xci')
            self.assertEqual(ci.game_name(nsp, f'{BASE}.NSP', 'pfs0'), f'{BASE}.nsp')   # the NSP script looks for lower-case extensions
            self.assertEqual(ci.game_name(nsp, 'download', 'pfs0'), 'download.nsp')
            self.assertEqual(ci.game_name(nsz, 'download', 'pfs0'), 'download.nsz')   # .ncz entries: compressed
            self.assertEqual(ci.game_name(nsz, 'g.nsz', 'pfs0'), 'g.nsz')
            self.assertEqual(ci.game_name(x, '', 'xci'), 'game.xci')
            self.assertEqual(ci.game_name(x, 'g.XCZ', 'xci'), 'g.xcz')

    def test_command_value(self):   # what the runner unescapes back to the link
        self.assertEqual(ci.command_value('https://h/a%20b?c=1\n'), 'https://h/a%2520b?c=1%0A')


class Take(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.t)
        self.out, self.work = self.t / 'nsp', self.t / 'work'; self.out.mkdir(); self.work.mkdir()

    def take(self, data, name):
        p = self.work / 'download'; p.write_bytes(data)
        with contextlib.redirect_stdout(io.StringIO()): return ci.take(p, name, self.out, self.work)

    def test_archive_flattened_nested_and_junk_dropped(self):
        inner = zipped({f'{UPDATE}.nsp': pfs0(['u.nca'])})
        got = self.take(zipped({f'Game/Base/{BASE}.NSP': pfs0(['b.nca']), f'Other/{BASE}.NSP': pfs0(['c.nca']), 'Game/readme.txt': b'hello', 'Game/update.zip': inner}), 'game.zip')
        self.assertEqual(sorted(p.name for p in got), sorted([f'{BASE}.nsp', f'{BASE} (2).nsp', f'{UPDATE}.nsp']))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(p.name for p in got))   # the folder's top level, where the NSP script reads
        self.assertEqual(list(self.work.iterdir()), [])   # archives and unpacked folders gone: the runner's disk is short

    def test_game_file_as_is(self):
        got = self.take(pfs0(['x.nca']), f'{BASE}.nsp')
        self.assertEqual([p.name for p in got], [f'{BASE}.nsp'])

    def test_web_page_fails(self):
        with self.assertRaisesRegex(ci.Fail, 'web page'): self.take(b'<!DOCTYPE html><html><body>Download</body></html>', '')

    def test_web_page_in_an_archive_is_dropped(self):
        got = self.take(zipped({'index.html': b'<html></html>', f'{BASE}.nsp': pfs0(['x.nca'])}), 'g.zip')
        self.assertEqual([p.name for p in got], [f'{BASE}.nsp'])

    def test_nothing_usable(self):
        self.assertEqual(self.take(b'just text', 'notes.txt'), [])


class Host(BaseHTTPRequestHandler):
    """Serves the server's routes: {path: (body, headers, breaks once)}; a range request gets the rest (206). A route that breaks sends half its body and closes, once"""
    def do_GET(self):
        path = self.path.split('?')[0]
        if path not in self.server.routes: self.send_error(404); return   # type: ignore[attr-defined]
        body, headers, breaks = self.server.routes[path]   # type: ignore[attr-defined]
        m = re.match(r'bytes=(\d+)-', self.headers.get('Range', '')); start = int(m.group(1)) if m else 0
        self.server.ranges.append(start)   # type: ignore[attr-defined]
        self.send_response(206 if m else 200)
        if m: self.send_header('Content-Range', f'bytes {start}-{len(body) - 1}/{len(body)}')
        for k, v in headers.items(): self.send_header(k, v)
        self.send_header('Content-Length', str(len(body) - start)); self.end_headers()
        if breaks and path not in self.server.broke:   # type: ignore[attr-defined]
            self.server.broke.add(path); self.wfile.write(body[start:len(body) // 2]); self.wfile.flush(); self.close_connection = True; return   # type: ignore[attr-defined]
        self.wfile.write(body[start:])

    def log_message(self, format, *args): pass   # noqa: A002


class Main(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.t)
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), Host); self.srv.routes, self.srv.broke, self.srv.ranges = {}, set(), []   # type: ignore[attr-defined]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start(); self.addCleanup(self.srv.server_close); self.addCleanup(self.srv.shutdown)
        self.base = f'http://127.0.0.1:{self.srv.server_address[1]}'
        env = {k: v for k, v in os.environ.items() if k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'github_event_path', 'game_link', 'github_step_summary')}
        p = mock.patch.dict(os.environ, env, clear=True); p.start(); self.addCleanup(p.stop)

    def run_main(self, *links):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(ci.time, 'sleep'): rc = ci.main(['--out', str(self.t / 'nsp'), '--work', str(self.t / 'work'), *links])
        return rc, out.getvalue()

    def test_link_masked_and_never_printed(self):
        data = pfs0(['x.nca'], pad=200_000)
        self.srv.routes['/dl/secret%20token'] = (data, {'Content-Disposition': "attachment; filename*=UTF-8''Game%20%5B0100000000010000%5D%5Bv0%5D.nsp"}, False)   # type: ignore[attr-defined]
        link = f'{self.base}/dl/secret%20token?key=s3cret'
        rc, text = self.run_main(link)
        self.assertEqual(rc, 0, text)
        self.assertEqual((self.t / 'nsp' / f'{BASE}.nsp').read_bytes(), data)
        lines = text.splitlines()
        self.assertIn(f'::add-mask::{link.replace("%", "%25")}', lines)
        self.assertIn('::add-mask::127.0.0.1', lines)
        for ln in lines:
            if not ln.startswith('::add-mask::'): self.assertNotRegex(ln, 'secret|s3cret|127\\.0\\.0\\.1', ln)

    def test_broken_download_resumes(self):
        data = zipped({f'Folder/{BASE}.nsp': pfs0(['x.nca'], pad=300_000)})
        self.srv.routes['/g.zip'] = (data, {}, True)   # type: ignore[attr-defined]
        rc, text = self.run_main(f'{self.base}/g.zip')
        self.assertEqual(rc, 0, text)
        self.assertIn('broke off', text)
        self.assertEqual(self.srv.ranges[0], 0); self.assertGreater(self.srv.ranges[-1], 0)   # type: ignore[attr-defined]  # the second request went on where the first stopped
        self.assertEqual([p.name for p in (self.t / 'nsp').iterdir()], [f'{BASE}.nsp'])

    def test_refused_link(self):
        rc, text = self.run_main(f'{self.base}/gone.nsp')
        self.assertEqual(rc, 1); self.assertIn('::error::downloading the link failed: curl exit 22', text)
        self.assertNotIn('gone.nsp', text.replace(f'::add-mask::{self.base}/gone.nsp', ''))

    def test_dead_host_fails_at_once(self):   # nothing came: no tries of our own on top of curl's
        import socket
        s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
        with mock.patch.object(ci.time, 'sleep') as slept:
            rc, text = self.run_main(f'http://127.0.0.1:{port}/g.nsp')
        self.assertEqual(rc, 1); self.assertIn('curl exit 7', text); self.assertNotIn('broke off', text); slept.assert_not_called()

    def test_several_links(self):
        path = '/' + BASE.replace(' ', '%20').replace('[', '%5B').replace(']', '%5D') + '.nsp'   # the name only in the URL: taken for its game extension
        self.srv.routes[path] = (pfs0(['b.nca']), {}, False)   # type: ignore[attr-defined]
        self.srv.routes['/u'] = (pfs0(['u.nca']), {'Content-Disposition': f'attachment; filename="{UPDATE}.nsp"'}, False)   # type: ignore[attr-defined]
        rc, text = self.run_main(self.base + path, f'{self.base}/u')
        self.assertEqual(rc, 0, text)
        self.assertEqual(sorted(p.name for p in (self.t / 'nsp').iterdir()), [f'{BASE}.nsp', f'{UPDATE}.nsp'])

    def test_not_http(self):
        rc, text = self.run_main('file:///etc/passwd')
        self.assertEqual(rc, 1); self.assertIn('is not an http:// or https:// link', text)
        self.assertNotIn('passwd', text.replace('::add-mask::file:///etc/passwd', ''))

    def test_no_link(self):
        rc, text = self.run_main()
        self.assertEqual(rc, 1); self.assertIn('::error::no link', text)

    def test_links_from_the_run_then_the_secret(self):
        ev = self.t / 'event.json'; ev.write_text(json.dumps({'inputs': {'link': ' https://a/1.nsp\nhttps://a/2.nsp '}}))
        with mock.patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(ev), 'GAME_LINK': 'https://s/x.zip'}):
            self.assertEqual(ci.given_links([]), ['https://a/1.nsp', 'https://a/2.nsp'])
            ev.write_text(json.dumps({'inputs': {'link': ''}}))
            self.assertEqual(ci.given_links([]), ['https://s/x.zip'])
            self.assertEqual(ci.given_links(['https://c/y.nsp']), ['https://c/y.nsp'])


if __name__ == '__main__':
    unittest.main()
