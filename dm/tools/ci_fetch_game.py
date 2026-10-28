#!/usr/bin/env python3
# The game files for a build on GitHub's Mac (.github/workflows/build-ipa.yml, docs/build-on-github.md): downloads the run's links, unpacks archives and puts every NSP / XCI / NSZ / XCZ
#   into one folder, the NSP folder of build_ipa_from_nsp.py (which reads only that folder's top level, and only lower-case extensions).
# The links: the command line's, else the run's "link" input (GITHUB_EVENT_PATH), else the GAME_LINK environment variable (the secret of that name); several are separated by white space
#   (the base game, its update, its DLC). The log of a public repository is public: each link and its host are masked first (::add-mask::) and never printed.
# A file keeps its name, which the NSP script reads version tags from ([v327680]): its name in the archive, else the server's (Content-Disposition), else the URL's when that ends in a game or
#   archive extension; a game file without a game extension gets the one its header calls for (PFS0: .nsp, or .nsz when it holds .ncz entries; XCI: .xci).
# Archives (.zip, .7z, .rar, .tar and compressed tars; nested up to DEPTH deep) are unpacked with 7-Zip (7zz, brew install sevenzip), bsdtar (macOS's tar), or for a .zip Python's zipfile,
#   and each is deleted as soon as it is unpacked: the runner's disk is short. Other files are dropped.
# Usage: ci_fetch_game.py --out <NSP folder> --work <scratch folder> [link ...]; exit 0 when the folder holds a game file, 1 otherwise (the reason as a ::error:: line)
import argparse, email.message, json, os, re, shutil, stat, struct, subprocess, sys, tempfile, threading, time, zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

GAME = ('.nsp', '.xci', '.nsz', '.xcz')
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
    """What a file's header says it is: 'pfs0' (an NSP or NSZ), 'xci' (an XCI or XCZ), 'zip', '7z', 'rar', 'tar' (also a compressed one), 'html' (a web page), or None"""
    with open(p, 'rb') as f: h = f.read(0x200)
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
    if ext.lower() in (('.nsp', '.nsz') if kind == 'pfs0' else ('.xci', '.xcz')): return stem + ext.lower()
    if kind == 'pfs0': return name + ('.nsz' if any(n.endswith('.ncz') for n in pfs0_names(p)) else '.nsp')
    return name + '.xci'


def place(p, name, out):   # move a game file into the NSP folder (a rename: the same volume), with " (2)" … when the name is taken
    stem, ext = os.path.splitext(name); dst, i = out / name, 2
    while dst.exists(): dst, i = out / f'{stem} ({i}){ext}', i + 1
    shutil.move(p, dst)
    say(f'game file: {dst.name} ({gb(dst.stat().st_size)})')
    return dst


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


def files_in(d):   # the regular files under d, sorted (links are neither followed nor taken)
    out = []
    for root, dirs, files in os.walk(d):
        dirs.sort()
        out += [Path(root) / n for n in sorted(files) if stat.S_ISREG(os.lstat(Path(root) / n).st_mode)]
    return out


def take(p, name, out, work, depth=0):
    """A downloaded or unpacked file p, called name: a game file goes into out, an archive is unpacked and its files taken in turn, anything else is dropped. Returns the game files placed"""
    kind, shown = sniff(p), name or 'the download'
    if kind in ('pfs0', 'xci'): return [place(p, game_name(p, name, kind), out)]
    if kind == 'html' and depth == 0: p.unlink(); raise Fail('the link gave a web page, not a file: it must be a direct download link (in a browser, the file downloads at once, with no page or button)')
    if kind in (None, 'html'): say(f'skipped {shown}: not a game file or an archive'); p.unlink(); return []
    if depth >= DEPTH: raise Fail(f'{shown}: archives nested more than {DEPTH} deep')
    say(f'unpacking {shown} ({kind}, {gb(p.stat().st_size)})')
    d = Path(tempfile.mkdtemp(prefix='unpack_', dir=work))
    try:
        unpack(p, kind, d); p.unlink()   # the archive's space back before its files are moved
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
                if re.search(r'\[[0-9A-Fa-f]{16}\]', p.name): continue
                stem, ext = os.path.splitext(p.name)
                q = a.out / f'{stem} [{tid.upper()}]{ext}'; k = 2
                while q.exists(): q = a.out / f'{stem} [{tid.upper()}] ({k}){ext}'; k += 1
                p.rename(q)
                got[idx] = q   # the totals below stat the renamed files
                say(f'tagged {p.name} with title ID {tid.upper()}')
        if not got: raise Fail('the download holds no NSP, XCI, NSZ or XCZ file')
    except Fail as e:
        say(f'::error::{command_value(str(e))}'); return 1
    total = sum(p.stat().st_size for p in got)
    say(f'{len(got)} game file{"s" if len(got) > 1 else ""}, {gb(total)}, in {a.out}; {gb(shutil.disk_usage(a.out).free)} free')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as s:
            s.write(f'### Game files\n\n{len(got)} file{"s" if len(got) > 1 else ""}, {gb(total)}:\n\n' + ''.join(f'- `{p.name}` ({gb(p.stat().st_size)})\n' for p in got) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
