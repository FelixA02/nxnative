#!/usr/bin/env python3
# The game files for a build on GitHub's Mac (.github/workflows/build-ipa.yml, docs/build-on-github.md): downloads the run's links, unpacks archives and puts every NSP / XCI / NSZ / XCZ / NUP
#   into one folder, the NSP folder of build_ipa_from_nsp.py (which reads only that folder's top level, and only lower-case extensions).
# The links: the command line's, else the run's "link" input (GITHUB_EVENT_PATH), else the GAME_LINK environment variable (the secret of that name); several are separated by white space
#   (the base game, its update, its DLC). The log of a public repository is public: each link and its host are masked first (::add-mask::) and never printed.
# A file keeps its name, which the NSP script reads version tags from ([v327680]; an NSP named (v327680) instead gets the [v327680] added): its name in the archive, else the server's (Content-Disposition), else the URL's when that ends in a game or
#   archive extension; a game file without a game extension gets the one its header calls for (PFS0: .nsp, or .nsz when it holds .ncz entries; XCI: .xci; NUP: .nup).
# Archives (.zip, .7z, .rar, .tar and compressed tars; nested up to DEPTH deep) are unpacked with 7-Zip (7zz, brew install sevenzip), bsdtar (macOS's tar), or for a .zip Python's zipfile,
#   and each is deleted as soon as it is unpacked: the runner's disk is short. Other files are dropped.
# A console dump (a Switch 2 game, Donkey Kong Bananza: folders instead of NSPs) is recognized by its layout, base/exefs/main.npdm with base/romfs (updates/<version>/exefs/main.npdm, dlc/<title ID>/ too),
#   wherever it sits in an archive (a wrapper folder is fine): its folders are moved into the NSP folder as they are, and the NSP script, which tells a dump folder the same way, packs it with
#   scripts/pack_dump.py and needs no prod.keys. Several archives (base, update, DLC) merge into one folder.
# A game in one NUP container (a .nup file: https://github.com/a55uka/nup, scripts/pack_nup.py) is a game file like an NSP: it goes into the NSP folder as it is and is never unpacked, since
#   the NSP script reads the container itself (base game, updates, DLC). It needs no prod.keys and carries its own title ID and update versions, so its name is left alone: no [title ID] and no [vN].
# Usage: ci_fetch_game.py --out <NSP folder> --work <scratch folder> [link ...]; exit 0 when the folder holds a game file, 1 otherwise (the reason as a ::error:: line)
import argparse, email.message, json, os, re, shutil, stat, struct, subprocess, sys, tempfile, threading, time, zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

GAME = ('.nsp', '.xci', '.nsz', '.xcz', '.nup')
ARCHIVE = ('.zip', '.7z', '.rar', '.tar', '.tgz', '.gz', '.bz2', '.xz', '.zst')
DEPTH = 3     # archives inside archives
BEAT = 30     # seconds between download progress lines
TRIES = 10    # downloads that break off: tries in all, each resuming where the last one stopped when the server allows it
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15'   # some file hosts turn curl's own away
BROKE = {16, 18, 28, 52, 55, 56, 92}   # curl exits of a transfer that broke off or stalled (--speed-time): tried again when it brought data (curl itself retries a host that does not answer)
CURL_HINT = {6: 'the host was not found', 7: 'the host did not answer', 22: 'the server refused the request: an expired link, or one that needs a browser', 23: 'the file could not be written: '
             'is the runner\'s disk full?', 28: 'it timed out', 35: 'the secure connection failed', 47: 'too many redirects', 60: 'the server\'s certificate is not trusted'}


class Fail(Exception):
    pass


def say(msg):
    print(msg, flush=True)


def gb(n):
    return f'{n / 2**30:.2f} GB'


def hms(s):   # seconds → m:ss, or h:mm:ss from an hour on
    s = int(s); h, m = divmod(s // 60, 60)
    return f'{h}:{m:02d}:{s % 60:02d}' if h else f'{m}:{s % 60:02d}'


def command_value(s):   # a workflow command's value, escaped as the runner unescapes it (a link's %20 must stay %20 in the mask)
    return s.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def host_of(link):
    try: return urlsplit(link).hostname or ''
    except ValueError: return ''


def scheme_of(link):
    try: return urlsplit(link).scheme.lower()
    except ValueError: return ''


def mask(link):   # the link and its host are hidden from here on in the run's log (curl's errors name the host)
    say(f'::add-mask::{command_value(link)}')
    if host_of(link): say(f'::add-mask::{command_value(host_of(link))}')


def given_links(argv):
    if argv: return argv
    text, ev = '', os.environ.get('GITHUB_EVENT_PATH')
    if ev and Path(ev).is_file(): text = ((json.loads(Path(ev).read_text()) or {}).get('inputs') or {}).get('link') or ''
    return (text if text.strip() else os.environ.get('GAME_LINK', '')).split()


def clean(name):   # a bare file name: no folders (either slash), no control characters, not . or ..
    n = re.sub(r'[\x00-\x1f\x7f]', '', name.replace('\\', '/')).rsplit('/', 1)[-1].strip()
    return '' if n in ('.', '..') else n


def known(name):
    return name.lower().endswith(GAME + ARCHIVE)


def served_name(cd, url):
    """The file name the server gives (Content-Disposition, with RFC 5987's filename*), else the URL's last path segment when it ends in a game or archive extension (any other one
    may be part of the link's secret: it would be printed); '' when neither"""
    n = ''
    if cd:
        m = email.message.Message(); m['content-disposition'] = cd
        n = clean(m.get_filename() or '')
    if not n:
        try: seg = clean(unquote(PurePosixPath(urlsplit(url).path).name))
        except ValueError: seg = ''
        if known(seg): n = seg
    return n


def sniff(p):
    """What a file's header says it is: 'pfs0' (an NSP or NSZ), 'xci' (an XCI or XCZ), 'nup' (a NUP container), 'zip', '7z', 'rar', 'tar' (also a compressed one), 'html' (a web page), or None"""
    with open(p, 'rb') as f: h = f.read(0x200)
    if h[:3] == b'NUP': return 'nup'   # the container's magic; pack_nup.py reads its version and rejects one it cannot read
    if h[:4] == b'PFS0': return 'pfs0'
    if h[0x100:0x104] == b'HEAD': return 'xci'
    if h[:4] in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'): return 'zip'
    if h[:6] == b"7z\xbc\xaf'\x1c": return '7z'
    if h[:7] == b'Rar!\x1a\x07\x00' or h[:8] == b'Rar!\x1a\x07\x01\x00': return 'rar'
    if h[257:262] == b'ustar' or h[:2] == b'\x1f\x8b' or h[:3] == b'BZh' or h[:6] == b'\xfd7zXZ\x00' or h[:4] == b'(\xb5/\xfd': return 'tar'
    if re.match(rb'\s*(<!doctype html|<html)', h.lower()): return 'html'
    return None


def pfs0_names(p):   # the names of a PFS0's entries, as build_ipa_from_nsp.py pfs0_names reads them
    with open(p, 'rb') as f:
        h = f.read(16)
        if len(h) < 16: return []
        n, st = struct.unpack_from('<II', h, 4)
        if n > 0x10000 or st > 1 << 24: return []
        ent, strs = f.read(n * 0x18), f.read(st)
    out = []
    for i in range(min(n, len(ent) // 0x18)):
        o = struct.unpack_from('<I', ent, i * 0x18 + 0x10)[0]; e = strs.find(b'\0', o)
        out.append(strs[o:e if e >= 0 else len(strs)].decode(errors='replace'))
    return out


def game_name(p, name, kind):   # its own name when the extension fits the header (made lower case), else the name with the extension the header calls for
    name = name or 'game'; stem, ext = os.path.splitext(name)
    if ext.lower() in {'pfs0': ('.nsp', '.nsz'), 'xci': ('.xci', '.xcz'), 'nup': ('.nup',)}.get(kind, ()): return stem + ext.lower()
    if kind == 'pfs0': return name + ('.nsz' if any(n.endswith('.ncz') for n in pfs0_names(p)) else '.nsp')
    return name + ('.nup' if kind == 'nup' else '.xci')   # the NSP script looks for *.nup (build_ipa_from_nsp.py's nup_file)


def place(p, name, out):   # move a game file into the NSP folder (a rename: the same volume), with " (2)" … when the name is taken
    stem, ext = os.path.splitext(name); dst, i = out / name, 2
    while dst.exists(): dst, i = out / f'{stem} ({i}){ext}', i + 1
    shutil.move(p, dst)
    say(f'game file: {dst.name} ({gb(dst.stat().st_size)})')
    return dst


VERSION_TAG, ROUND_VERSION = re.compile(r'\[v\d+\]'), re.compile(r'\(v(\d+)\)')


def version_tagged(name):
    """An update's version for the NSP script, which reads it only from a [vN] in the file name: dumps often write it (v786432) instead, so the script drops the update ("leaving out update NSPs without
    a version tag") and a base game that needs it cannot be read (Pokémon Violet). The name with [vN] added for an NSP / NSZ that has (vN) and no [vN]; any other name unchanged"""
    stem, ext = os.path.splitext(name)
    m = ROUND_VERSION.search(name)
    if ext.lower() not in ('.nsp', '.nsz') or VERSION_TAG.search(name) or not m: return name
    return f'{stem} [v{m.group(1)}]{ext}'


def empty(d):
    for c in d.iterdir():
        if c.is_dir() and not c.is_symlink(): shutil.rmtree(c)
        else: c.unlink()


def unzip(p, d):   # Python's zipfile, member by member into numbered folders under their bare names: no member path reaches outside d
    with zipfile.ZipFile(p) as z:
        for i, m in enumerate(z.infolist()):
            n = clean(m.filename)
            if m.is_dir() or not n: continue
            t = d / str(i); t.mkdir()
            with z.open(m) as src, open(t / n, 'wb') as dst: shutil.copyfileobj(src, dst, 1 << 20)


def unpack(p, kind, d):
    """Unpack archive p into the empty folder d: 7-Zip first (bsdtar first for a tar, which 7-Zip unpacks one layer at a time), then the other, then zipfile for a .zip. Nothing reads
    standard input, so a password-protected archive fails instead of waiting for a password"""
    seven = shutil.which('7zz') or shutil.which('7z')
    bsd = shutil.which('bsdtar') or (shutil.which('tar') if sys.platform == 'darwin' else None)   # macOS's tar is bsdtar (libarchive), which reads zip, 7z and rar too
    runs: list[tuple[list[str], tuple[int, ...]]] = [([seven, 'x', '-y', '-bd', '-bso0', '-bsp0', f'-o{d}', str(p)], (0, 1))] if seven else []   # 7-Zip's exit 1 is a warning (data after the end, …): the files are out
    if bsd: runs.insert(0 if kind == 'tar' else len(runs), ([bsd, '-xf', str(p), '-C', str(d)], (0,)))
    errs = []
    for cmd, ok in runs:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors='replace')
        if r.returncode in ok: return
        errs.append(' '.join(r.stderr.split()) or f'{Path(cmd[0]).name} exit {r.returncode}'); empty(d)
    if kind == 'zip':
        try: return unzip(p, d)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as e: errs.append(str(e)); empty(d)   # RuntimeError: a password-protected member; NotImplementedError: Deflate64
    if any(re.search('password|encrypt', e, re.I) for e in errs): raise Fail(f'the {kind} archive is password-protected, which the build cannot open: pack the files without a password')
    raise Fail(f'could not unpack the {kind} archive ({errs[0][-300:] if errs else "no tool to unpack it"})' + ('' if seven else '; 7-Zip is not installed: brew install sevenzip'))


DUMP_NAMES = (re.compile(r'(^|/)base/exefs/main\.npdm$'), re.compile(r'(^|/)updates/\d+/exefs/main\.npdm$'), re.compile(r'(^|/)dlc/[0-9A-Fa-f]{16}/'))


def dump_in(names):   # whether an archive's entry names hold a console dump's layout
    return any(m.search(n.replace('\\', '/')) for n in names for m in DUMP_NAMES)


def is_dump_root(r):   # a folder that holds a dump (or a part of one: an update or DLC alone)
    if (r / 'base/exefs/main.npdm').is_file(): return True
    if (r / 'updates').is_dir() and any((r / 'updates').glob('[0-9]*/exefs/main.npdm')): return True
    return (r / 'dlc').is_dir() and any(c.is_dir() and re.fullmatch(r'[0-9A-Fa-f]{16}', c.name) for c in (r / 'dlc').iterdir())


def dump_roots(d, depth=4):
    """The topmost folders under d that hold a dump, looking up to depth folders down (an archive of the game's folder has it one level in)"""
    found = []

    def walk(r, k):
        if is_dump_root(r): found.append(r); return
        if k < depth:
            for c in sorted(r.iterdir()):
                if c.is_dir() and not c.is_symlink(): walk(c, k + 1)
    walk(d, 0)
    return found


def merge_into(src, dst):
    """Move what is in folder src into folder dst, folder by folder (a rename: the same volume, so a dump of tens of GB costs no copy). A file both have is an error: two archives of one dump must not overlap.
    Returns the paths of src's entries as they are now in dst"""
    dst.mkdir(parents=True, exist_ok=True); placed = []
    for c in sorted(src.iterdir()):
        t = dst / c.name
        if c.is_dir() and not c.is_symlink() and t.is_dir(): merge_into(c, t)
        elif t.exists() or t.is_symlink(): raise Fail(f'{c.name} is in two of the downloads, both holding the dump\'s files')
        else: shutil.move(c, t)
        placed.append(t)
    return placed


def size_of(p):   # a file's size, or a folder's total
    if p.is_file(): return p.stat().st_size
    return sum(f.stat().st_size for f in files_in(p))


def readable(d):
    """Archives keep their files' unix permissions, and a file or folder dumped without read access (a `Phive` in a game folder, run 38) cannot be opened by the build's user: add the owner's read / write (and
    search, for a folder) bits to everything under d. A folder is opened up before os.walk goes into it"""
    def add(q, bits):
        try:
            if not os.path.islink(q): os.chmod(q, os.stat(q).st_mode | bits)
        except OSError: pass
    for root, dirs, files in os.walk(d):
        for n in dirs: add(os.path.join(root, n), 0o700)
        for n in files: add(os.path.join(root, n), 0o600)


def listing(p, kind):
    """The entry names of archive p, from its index alone: a zip through zipfile, else 7-Zip's or bsdtar's listing; None when it cannot be listed (encrypted headers, no tool, a tar: listing it reads
    the whole stream)"""
    if kind == 'zip':
        try:
            with zipfile.ZipFile(p) as z: return z.namelist()
        except (zipfile.BadZipFile, NotImplementedError, OSError): pass
    if kind not in ('zip', '7z', 'rar'): return None
    seven = shutil.which('7zz') or shutil.which('7z')
    bsd = shutil.which('bsdtar') or (shutil.which('tar') if sys.platform == 'darwin' else None)
    for cmd, pick in (([seven, 'l', '-slt', '-ba', '-bd', str(p)], lambda ln: ln[7:] if ln.startswith('Path = ') else None), ([bsd, '-tf', str(p)], lambda ln: ln or None)) if seven or bsd else ():
        if not cmd[0]: continue
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors='replace')
        if r.returncode == 0: return [n for n in map(pick, r.stdout.splitlines()) if n]
    return None


SKIPPED = {'n': 0}   # files that were neither game files nor archives: counted, not listed (a game folder has tens of thousands)


def files_in(d):   # the regular files under d, sorted (links are neither followed nor taken)
    out = []
    for root, dirs, files in os.walk(d):
        dirs.sort()
        out += [Path(root) / n for n in sorted(files) if stat.S_ISREG(os.lstat(Path(root) / n).st_mode)]
    return out


def take(p, name, out, work, depth=0):
    """A downloaded or unpacked file p, called name: a game file goes into out, an archive is unpacked and its files taken in turn, anything else is dropped. Returns the game files placed"""
    shown = name or 'the download'
    try: kind = sniff(p)
    except OSError: SKIPPED['n'] += 1; return []   # cannot be read: not a game file
    if kind in ('pfs0', 'xci', 'nup'): return [place(p, game_name(p, name, kind), out)]
    if kind == 'html' and depth == 0: p.unlink(); raise Fail('the link gave a web page, not a file: it must be a direct download link (in a browser, the file downloads at once, with no page or button)')
    # a file inside an archive is unpacked only when its name says archive: a game folder is full of files that look like one by their first bytes (.zs is a zstd stream, .pak ...)
    if kind in (None, 'html') or (depth > 0 and not name.lower().endswith(ARCHIVE)): SKIPPED['n'] += 1; p.unlink(); return []
    if depth >= DEPTH: raise Fail(f'{shown}: archives nested more than {DEPTH} deep')
    names = listing(p, kind)   # the index only: an extracted game folder is told apart before its tens of GB are unpacked
    if names and len(names) > 20 and not any(n.lower().endswith(GAME + ARCHIVE) for n in names) and not dump_in(names):
        eg = ', '.join(os.path.basename(n.rstrip('/')) for n in names[:3])
        raise Fail(f'the {kind} archive holds {len(names):,} files and none is an NSP, XCI, NSZ, XCZ or NUP (or an archive of them), for example {eg}: it looks like an extracted game folder. The build needs the game as NSP, XCI or NUP files')
    say(f'unpacking {shown} ({kind}, {gb(p.stat().st_size)})')
    d = Path(tempfile.mkdtemp(prefix='unpack_', dir=work))
    try:
        unpack(p, kind, d); p.unlink()   # the archive's space back before its files are moved
        readable(d)
        roots = dump_roots(d)
        if roots:   # a console dump: its folders go into the NSP folder as they are
            got = []
            for r in roots:
                say(f'a game dump: {", ".join(sorted(c.name for c in r.iterdir() if c.is_dir()))}')
                got += [q for q in merge_into(r, out) if q not in got]
            return got
        got = []
        for f in files_in(d): got += take(f, f.name, out, work, depth + 1)
        return got
    finally: shutil.rmtree(d, ignore_errors=True)


def download(link, what, work):
    """curl the link into a folder of its own under work, trying again where a connection broke off (resuming when the server allows it, else from the start); returns the file and the
    name the server or the URL gives it. Only curl sees the link: its errors (-sS) name no URL, and the host is masked"""
    d = Path(tempfile.mkdtemp(prefix='link_', dir=work)); f = d / 'download'
    stop, t0 = threading.Event(), time.monotonic()

    def beat():
        last = 0
        while not stop.wait(BEAT):
            size = f.stat().st_size if f.exists() else 0
            say(f'downloading {what}: {gb(size)} ({max(size - last, 0) / BEAT / 2**20:.0f} MB/s)'); last = size
    threading.Thread(target=beat, daemon=True).start()
    resume, r = True, None
    try:
        for n in range(1, TRIES + 1):
            before = f.stat().st_size if f.exists() and resume else 0   # without resuming, each try starts from nothing
            r = subprocess.run(['curl', '-fsSL', '--proto', '=http,https', '--proto-redir', '=http,https', '--retry', '5', '--retry-delay', '10', '--connect-timeout', '30',
                                '--speed-limit', '1024', '--speed-time', '120', '-A', UA, *(['-C', '-'] if resume else []), '-o', str(f),
                                '-w', '%{url_effective}\\n%header{content-disposition}', link], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, errors='replace')
            if r.returncode == 0: break
            if resume and r.returncode in (33, 36): say(f'the server cannot resume {what}: from the start'); resume = False; f.unlink(missing_ok=True); continue
            grew = (f.stat().st_size if f.exists() else 0) > before
            if r.returncode in BROKE and grew and n < TRIES:
                say(f'downloading {what} broke off (curl exit {r.returncode}): trying again in {min(10 * n, 60)} s'); time.sleep(min(10 * n, 60)); continue
            raise Fail(f'downloading {what} failed: curl exit {r.returncode}' + (f', {CURL_HINT[r.returncode]}' if r.returncode in CURL_HINT else ''))
    finally: stop.set()
    if r is None or r.returncode: raise Fail(f'downloading {what} failed')
    url, _, cd = r.stdout.partition('\n')
    say(f'downloaded {what}: {gb(f.stat().st_size)} in {hms(time.monotonic() - t0)}')
    return f, served_name(cd.strip(), url.strip())


def main(argv=None):
    ap = argparse.ArgumentParser(description='Download a game for build_ipa_from_nsp.py: its links, unpacked, as one folder of NSP / XCI / NSZ / XCZ files')
    ap.add_argument('--out', type=Path, required=True, help='the NSP folder (made when missing)')
    ap.add_argument('--work', type=Path, required=True, help='a scratch folder for the downloads and unpacked archives')
    ap.add_argument('links', nargs='*', help='download links (default: the run\'s "link" input, else GAME_LINK)')
    a = ap.parse_args(argv)
    links = given_links(a.links)
    for link in links: mask(link)   # before anything can print one
    try:
        if not links: raise Fail('no link: give one in the run\'s "link" field, or set the GAME_LINK secret')
        bad = [str(i) for i, link in enumerate(links, 1) if scheme_of(link) not in ('http', 'https')]
        if bad: raise Fail(f'link {", ".join(bad)} of {len(links)} is not an http:// or https:// link')
        a.out.mkdir(parents=True, exist_ok=True); a.work.mkdir(parents=True, exist_ok=True)
        got = []
        for i, link in enumerate(links, 1):
            f, name = download(link, f'link {i} of {len(links)}' if len(links) > 1 else 'the link', a.work)
            got += take(f, name, a.out, a.work); shutil.rmtree(f.parent, ignore_errors=True)
        tid = re.sub(r'[^0-9A-Fa-f]', '', os.environ.get('IN_TITLE_ID', ''))[:16]
        if len(tid) == 16:   # a cartridge dump (.xci) with no [title ID] in its name: tag it, which the NSP script reads title IDs from
            for idx, p in enumerate(got):
                if p.is_dir() or p.suffix.lower() == '.nup' or re.search(r'\[[0-9A-Fa-f]{16}\]', p.name): continue   # a dump's folders are not renamed; a NUP's own program ID is what pack_nup.scan reads
                stem, ext = os.path.splitext(p.name)
                q = a.out / f'{stem} [{tid.upper()}]{ext}'; k = 2
                while q.exists(): q = a.out / f'{stem} [{tid.upper()}] ({k}){ext}'; k += 1
                p.rename(q)
                got[idx] = q   # the totals below stat the renamed files
                say(f'tagged {p.name} with title ID {tid.upper()}')
        for idx, p in enumerate(got):   # an update named (vN): add the [vN] the NSP script reads
            new = version_tagged(p.name)
            if new == p.name: continue
            stem, ext = os.path.splitext(new); q = a.out / new; k = 2
            while q.exists(): q = a.out / f'{stem} ({k}){ext}'; k += 1
            p.rename(q)
            got[idx] = q
            say(f'tagged {p.name} with its version: {q.name}')
        if SKIPPED['n']: say(f'left out {SKIPPED["n"]:,} files that are neither game files nor archives')
        if not got: raise Fail('the download holds no NSP, XCI, NSZ, XCZ or NUP file')
    except Fail as e:
        say(f'::error::{command_value(str(e))}'); return 1
    total = sum(size_of(p) for p in got)
    what = 'game folder' if all(p.is_dir() for p in got) else 'game file'
    say(f'{len(got)} {what}{"s" if len(got) > 1 else ""}, {gb(total)}, in {a.out}; {gb(shutil.disk_usage(a.out).free)} free')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as s:
            s.write(f'### Game files\n\n{len(got)} {what}{"s" if len(got) > 1 else ""}, {gb(total)}:\n\n' + ''.join(f'- `{p.name}{"/" if p.is_dir() else ""}` ({gb(size_of(p))})\n' for p in got) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
