"""The title ID read before a build starts (dm/tools/game_title_id.py): the scan build_ipa_from_nsp.py makes, from an NSP folder, a dump folder or a
NUP container, with only the answer on standard output. The packages are written here (test_nsp_nup.py writes the container); no game files, keys,
dotnet or network needed."""
import contextlib
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import game_title_id as g   # noqa: E402
import test_nsp_nup as fixtures   # noqa: E402  # npdm(), version(), write_nup(): the writers the NUP tests use

TID = fixtures.TID


def pfs0(names):   # an NSP's container with these entry names
    strs = b''.join(n + b'\0' for n in names)
    ent = b''.join(struct.pack('<QQII', 0, 0, sum(len(m) + 1 for m in names[:i]), 0) for i in range(len(names)))
    return b'PFS0' + struct.pack('<III', len(names), len(strs), 0) + ent + strs + b'\0' * 4096


class TitleID(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        self.dir = Path(td.name)

    def run_tool(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): rc = g.main([str(a) for a in args])
        return rc, out.getvalue(), err.getvalue()

    def nsp_folder(self, ticket=True):   # NSPs: the ticket's rights ID names the game, else the name's [title ID]
        d = self.dir / 'nsp'; d.mkdir(exist_ok=True)
        names = [f'{TID:016X}'.encode() + b'0' * 16 + b'.tik', b'x.nca'] if ticket else [b'x.nca']
        (d / (f'Game [{TID:016X}][v0].nsp' if not ticket else 'Game [v0].nsp')).write_bytes(pfs0(names))
        return d

    @unittest.skipUnless(sys.platform == 'darwin', 'the packages are read through nx_extract, a macOS binary')
    def test_nsps_ticket(self):
        self.assertEqual(self.run_tool(self.nsp_folder()), (0, f'{TID:016X}\n', ''))

    @unittest.skipUnless(sys.platform == 'darwin', 'the packages are read through nx_extract, a macOS binary')
    def test_nsps_name_tag_when_there_is_no_ticket(self):
        self.assertEqual(self.run_tool(self.nsp_folder(ticket=False))[:2], (0, f'{TID:016X}\n'))

    def test_a_dump_folder(self):   # a Switch 2 dump: base/exefs/main.npdm names the program, no keys needed
        d = self.dir / 'dump' / 'base'; (d / 'exefs').mkdir(parents=True); (d / 'romfs').mkdir()
        (d / 'exefs/main.npdm').write_bytes(fixtures.npdm(TID)); (d / 'exefs/main').write_bytes(b'MAIN')
        (d / 'exefs/sdk').write_bytes(b'SDK'); (d / 'romfs/x').write_bytes(b'x')
        self.assertEqual(self.run_tool(d.parent), (0, f'{TID:016X}\n', ''))

    def test_a_nup_container(self):   # the container's program ID, no keys needed
        d = self.dir / 'nup'; d.mkdir()
        fixtures.write_nup(d / 'game.nup', [(0, TID, 0, fixtures.version(), b'')])
        self.assertEqual(self.run_tool(d), (0, f'{TID:016X}\n', ''))

    @unittest.skipUnless(sys.platform == 'darwin', 'the packages are read through nx_extract, a macOS binary')
    def test_only_the_answer_goes_to_standard_output(self):   # the scan's own lines must not be mistaken for the answer
        d = self.nsp_folder()
        (d / 'empty.nsp').write_bytes(b'')   # the scan says it skips this one
        rc, out, err = self.run_tool(d)
        self.assertEqual((rc, out), (0, f'{TID:016X}\n')); self.assertIn('skipping an empty file', err)

    def test_a_game_it_cannot_read_fails_with_the_builds_message(self):
        d = self.dir / 'nothing'; d.mkdir()
        rc, out, err = self.run_tool(d)
        self.assertEqual((rc, out), (1, '')); self.assertIn('[RED] no game package with a readable content meta', err)

    def test_a_title_id_of_the_wrong_shape_is_refused(self):
        rc, out, err = self.run_tool(self.nsp_folder(), '--title-id', '040074A01BF12')
        self.assertEqual((rc, out), (1, '')); self.assertIn('16 hex digits', err)

class CallsIntoTheBuild(unittest.TestCase):
    """game_title_id.py is a thin wrapper over build_ipa_from_nsp.py, so the names it calls and the arguments it passes are the build's business, not this
    file's: a renamed or re-signatured helper is an AttributeError on the one run that needed the cache, after the download. Bind every call against the
    real signature instead of trusting the source to have been read correctly."""

    def test_every_call_matches_the_build(self):
        import ast, inspect, build_ipa_from_nsp as build
        src = (HERE.parent / 'game_title_id.py').read_text()
        checked = 0
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'build'):
                continue
            name = node.func.attr
            self.assertTrue(hasattr(build, name), f'game_title_id.py calls build.{name}, which the build does not have')
            target = getattr(build, name)
            if inspect.isclass(target):   # build.BuildError(...): an exception, its arguments are its own
                continue
            kwargs = {}
            for k in node.keywords:
                self.assertIsNotNone(k.arg, f'build.{name} is called with **{ast.unparse(k.value)}: cannot check it')
                kwargs[k.arg] = None
            try:
                inspect.signature(target).bind(*[None] * len(node.args), **kwargs)
            except TypeError as e:
                self.fail(f'game_title_id.py calls build.{name}({ast.unparse(node)}), which does not fit its signature: {e}')
            checked += 1
        self.assertGreater(checked, 5, 'no calls were checked: the parse of game_title_id.py found none')


if __name__ == '__main__':
    unittest.main()
