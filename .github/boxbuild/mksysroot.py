#!/usr/bin/env python3
"""Cross sysroot from the packages of an opkg feed.

usage: mksysroot.py [--dry-run] [--list FILE] SYSROOT FEED_URL[,FEED_URL...] PACKAGE...

Reads Packages.gz of every feed, resolves the Depends of the named packages
(first alternative, Provides are followed), downloads the ipk files, checks
their SHA256 against the index and unpacks data.tar.* into SYSROOT.
Packages already listed in FILE are not unpacked again; FILE gets one line
"name version filename sha256" per package unpacked.
"""
import gzip
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import urllib.request


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'opkg'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - retried, then raised
            err = e
    raise SystemExit('download failed: %s: %s' % (url, err))


def index(feeds):
    pkgs, provides = {}, {}
    for feed in feeds:
        text = gzip.decompress(fetch(feed + '/Packages.gz')).decode('utf-8', 'replace')
        for block in text.split('\n\n'):
            d = dict(re.findall(r'^([A-Za-z0-9-]+): (.*)$', block, re.M))
            name = d.get('Package')
            if not name or name in pkgs:
                continue
            d['_feed'] = feed
            pkgs[name] = d
            for p in d.get('Provides', '').split(','):
                p = p.strip().split(' ')[0]
                if p:
                    provides.setdefault(p, name)
    return pkgs, provides


def resolve(pkgs, provides, roots):
    todo, done, missing = list(roots), [], []
    while todo:
        n = todo.pop(0)
        n = n if n in pkgs else provides.get(n)
        if n is None or n in done:
            continue
        done.append(n)
        for dep in pkgs[n].get('Depends', '').split(','):
            dep = dep.split('|')[0].strip().split(' ')[0]
            if not dep:
                continue
            if dep in pkgs or dep in provides:
                todo.append(dep)
            elif dep not in missing:
                missing.append(dep)
    return done, missing


def ar_members(data):
    assert data[:8] == b'!<arch>\n', 'not an ar archive'
    o = 8
    while o + 60 <= len(data):
        h = data[o:o + 60]
        size = int(h[48:58])
        yield h[:16].decode().strip().rstrip('/'), data[o + 60:o + 60 + size]
        o += 60 + size + (size & 1)


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    if dry:
        args.remove('--dry-run')
    listfile = None
    if '--list' in args:
        i = args.index('--list')
        listfile = args[i + 1]
        del args[i:i + 2]
    sysroot, feeds, roots = args[0], args[1].split(','), args[2:]
    pkgs, provides = index(feeds)
    for r in roots:
        if r not in pkgs and r not in provides:
            raise SystemExit('package not in the feed: ' + r)
    names, missing = resolve(pkgs, provides, roots)
    have = set()
    if listfile and os.path.exists(listfile):
        have = {l.split(' ')[0] for l in open(listfile) if l.strip()}
    print('%d packages (%d already there), dependencies not in the feed: %s'
          % (len(names), len([n for n in names if n in have]), ' '.join(missing) or 'none'))
    total = 0
    for n in sorted(names):
        d = pkgs[n]
        total += int(d.get('Size', 0))
        if dry:
            print('  %s %s' % (n, d['Version']))
    print('download size %.1f MB' % (total / 1e6))
    if dry:
        return
    os.makedirs(sysroot, exist_ok=True)
    out = open(listfile, 'a') if listfile else None
    for n in sorted(names):
        if n in have:
            continue
        d = pkgs[n]
        data = fetch(d['_feed'] + '/' + d['Filename'])
        sha = hashlib.sha256(data).hexdigest()
        if d.get('SHA256sum') and d['SHA256sum'] != sha:
            raise SystemExit('SHA256 mismatch: ' + d['Filename'])
        member = [(m, b) for m, b in ar_members(data) if m.startswith('data.tar')]
        if not member:
            raise SystemExit('no data.tar in ' + d['Filename'])
        with tempfile.NamedTemporaryFile(suffix='.' + member[0][0]) as t:
            t.write(member[0][1])
            t.flush()
            subprocess.check_call(['tar', '-xf', t.name, '-C', sysroot, '--no-same-owner',
                                   '--no-overwrite-dir', '--delay-directory-restore'])
        if out:
            out.write('%s %s %s %s\n' % (n, d['Version'], d['Filename'], sha))
    if out:
        out.close()
    # directories of some packages are not writable: later packages and the
    # build have to write into them
    subprocess.check_call(['chmod', '-R', 'u+rwX', sysroot])


if __name__ == '__main__':
    main()
