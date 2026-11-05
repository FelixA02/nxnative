#!/usr/bin/env python3
# One game's title ID, read the way build_ipa_from_nsp.py reads it before it extracts anything: a NUP container's program ID, a dump folder's
#   base/exefs/main.npdm, else the NSPs' tickets (and, for a package with neither a ticket nor a [title ID] in its name, its meta NCA with prod.keys).
#   The build on GitHub's Mac runs this after the download to name the build cache before the build starts, so the Title ID field is only needed
#   when the game cannot be read this way (docs/build-on-github.md).
# Usage: game_title_id.py <NSP folder | dump folder | .nup> [-k prod.keys] [--title-id HEX]; the 16 hex digits on standard output and nothing else,
#   the scan's own lines on standard error, exit 1 on error (as a [RED] line)
import argparse, contextlib, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))   # this file lives in dm/tools; the build is in scripts/
import build_ipa_from_nsp as build   # the build's own scan: a copy of it would drift from what the build packs


def title_id(path, keys=None, want=None):   # the game's title ID; raises build.BuildError when it cannot be read
    path = Path(path).resolve()
    nup = build.nup_file(path)                     # path when it is a .nup, else the one .nup in the folder
    folder = path.parent if path.is_file() else path
    env = dict(os.environ, PATH=os.pathsep.join(build.TOOL_PATHS + [os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')]))
    build.configure_dotnet(env)
    if nup:
        app, fam = build.scan_nup(nup, want)
    elif build.dump_folder(folder):                # a Switch 2 dump, or a dump of any game: its NPDM names the program
        app, fam = build.scan_dump(folder, want)
    else:
        with build.package_title_reader(build.find_repo(Path(__file__).resolve()), str(keys or ''), env) as from_package:
            app, fam = build.scan_nsps(folder, from_package, from_package.contents, want)
    if fam.get('nup'): fam['nup'].close()          # the container's map, opened by scan_nup
    return f'{app:016X}'


def main(argv=None):
    ap = argparse.ArgumentParser(description="A game's title ID, read as build_ipa_from_nsp.py reads it")
    ap.add_argument('path', type=Path, help='the NSP folder, the dump folder, or the .nup file')
    ap.add_argument('-k', '--keys', type=Path, help='prod.keys: only a package with neither a ticket nor a [title ID] in its name needs it')
    ap.add_argument('--title-id', help='the game to read when the folder holds several (16 hex digits)')
    a = ap.parse_args(argv)
    try:
        if a.title_id and not re.fullmatch(r'[0-9A-Fa-f]{16}', a.title_id): raise build.BuildError(f'--title-id takes 16 hex digits: {a.title_id}')
        keys = a.keys.resolve() if a.keys else None
        with contextlib.redirect_stdout(sys.stderr):   # the scan says what it skips; the answer is the only line on standard output
            tid = title_id(a.path, keys, int(a.title_id, 16) if a.title_id else None)
    except (build.BuildError, OSError) as e:
        print(f'[RED] {e}', file=sys.stderr)
        return 1
    print(tid)
    return 0


if __name__ == '__main__':
    sys.exit(main())
