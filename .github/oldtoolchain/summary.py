#!/usr/bin/env python3
"""Table of all builds and the comparison of the series with the tree without it.
usage: summary.py <directory with result-<target>-<build>/ directories>"""
import os
import sys

base = sys.argv[1]
results = {}
for name in sorted(os.listdir(base)):
    if not name.startswith('result-'):
        continue
    top = os.path.join(base, name)
    if not os.path.isfile(os.path.join(top, 'result.txt')) and os.path.isfile(os.path.join(top, 'out', 'result.txt')):
        top = os.path.join(top, 'out')
    path = os.path.join(top, 'result.txt')
    if not os.path.isfile(path):
        continue
    _, target, build = name.split('-', 2)
    d = {}
    for line in open(path, encoding='utf-8', errors='replace'):
        k, _, v = line.rstrip('\n').partition('=')
        d[k] = v
    results[(target, build)] = (d, top)


def cell(text):
    return (text or '-').replace('|', '\\|')


def stage_configure(d):
    fails = [k for k in d if k.startswith('cfg_main_fail')]
    return '%s (%d rejected)' % (d.get('cfg_main', 'FAILED'), len(fails)) if fails else d.get('cfg_main', 'FAILED')


print('## FFmpeg and exteplayer3 in a Debian 8 userland (GCC 4.9.2, glibc 2.19, binutils 2.25) under qemu-user\n')
print('| target | build | configure | -std | make | player | ffmpeg -version | HLS file | DASH file | HLS https | DASH https | package |')
print('|---|---|---|---|---|---|---|---|---|---|---|---|')
for (target, build), (d, _) in sorted(results.items()):
    print('| ' + ' | '.join(cell(x) for x in (
        target, build, stage_configure(d), d.get('std'), d.get('make'), d.get('player_build'),
        d.get('run_ffmpeg_version', '')[:60], d.get('run_hls_file'), d.get('run_dash_file'),
        d.get('run_hls_https'), d.get('run_dash_https'), d.get('run_pkg_selftest'))) + ' |')

print('\n## Everything recorded\n')
for (target, build), (d, _) in sorted(results.items()):
    print('### %s %s\n' % (target, build))
    print('```')
    for k, v in d.items():
        print('%s = %s' % (k, v))
    print('```\n')

print('## Series compared with the tree without it\n')
for target in sorted({t for t, _ in results}):
    for variant in ('sky', 'asm224'):
        a = results.get((target, 'stock9-' + variant))
        b = results.get((target, 'series9-' + variant))
        if not a or not b:
            continue
        print('### %s %s\n' % (target, variant))
        for key in sorted(set(a[0]) | set(b[0])):
            if key in ('tree', 'make_minutes', 'warnings', 'cfg_components') or key.startswith('run_ffmpeg_version'):
                continue
            va, vb = a[0].get(key), b[0].get(key)
            if va != vb:
                print('- `%s` differs:\n  - without: `%s`\n  - with: `%s`' % (key, va, vb))
        for fname in ('failed.txt', 'errors.txt'):
            la = open(os.path.join(a[1], fname), errors='replace').read().splitlines() if os.path.isfile(os.path.join(a[1], fname)) else []
            lb = open(os.path.join(b[1], fname), errors='replace').read().splitlines() if os.path.isfile(os.path.join(b[1], fname)) else []
            only_b = [x for x in lb if x not in la]
            only_a = [x for x in la if x not in lb]
            print('- %s: %d lines without, %d with the series; only with the series: %d, only without: %d' % (
                fname, len(la), len(lb), len(only_b), len(only_a)))
            for x in only_b[:40]:
                print('  - series only: `%s`' % x)
            for x in only_a[:40]:
                print('  - without only: `%s`' % x)

        def warnings(path):
            out = {}
            p = os.path.join(path, 'warnings.txt')
            if os.path.isfile(p):
                for line in open(p, errors='replace'):
                    n, _, text = line.strip().partition(' ')
                    if n.isdigit():
                        out[text] = int(n)
            return out
        wa, wb = warnings(a[1]), warnings(b[1])
        new = sorted(x for x in wb if x not in wa)
        gone = sorted(x for x in wa if x not in wb)
        print('- warnings: %d distinct without, %d with the series; %d only with the series, %d only without' % (
            len(wa), len(wb), len(new), len(gone)))
        for x in new[:80]:
            print('  - series only (%d): `%s`' % (wb[x], x[:300]))
        print()
