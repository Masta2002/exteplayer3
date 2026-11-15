#!/usr/bin/env python3
"""Verdict on the cases exp.sh ran with two builds: the series as it was on the
receiver ("old") must show the failures of the receiver test (the case
reproduces them), the series as it is now ("new") must not.
usage: check.py <out dir>      exit 1 if a line is not as expected"""
import os
import re
import sys

OUT = sys.argv[1]
bad = []


def lines(name, label):
    """[(command, text)] of a boxplay output, the jump lines attached to their command"""
    res, jumps = [], []
    path = os.path.join(OUT, '%s__%s.txt' % (name, label))
    if not os.path.exists(path):
        return res
    for l in open(path, encoding='utf-8', errors='replace').read().splitlines():
        if l.startswith('  '):
            jumps.append(l.strip())
            continue
        cmd, _, text = l.partition(': ')
        if not text and '=' in cmd:
            cmd, text = cmd.split('=')[0], l
        res.append((cmd, text, jumps))
        jumps = []
    return res


def video(text):
    """(count, first, last) of the video packets of a read line"""
    m = re.search(r'video (\d+) \((-?\d+)\.\.(-?\d+) ms\)', text)
    return tuple(int(x) for x in m.groups()) if m else (0, -1, -1)


def expect(cond, name, label, what):
    print('%s %-22s %-3s %s' % ('ok  ' if cond else 'FAIL', name, label, what))
    if not cond:
        bad.append((name, label, what))


def after(res, cmd, nth=0):
    """the read line that follows the nth occurrence of a seek command"""
    idx = [i for i, r in enumerate(res) if r[0] == cmd]
    if len(idx) <= nth or idx[nth] + 1 >= len(res):
        return ''
    return res[idx[nth] + 1][1]


def rc(res):
    return next((r[1] for r in res if r[0] == 'rc'), 'rc=?')


# 1. DASH VOD: seeks after the stream was read to its end
for lay in ('tpl_tl', 'seglist', 'sf_base', 'tpl_num'):
    for label in ('old', 'new'):
        res = lines('dash_' + lay, label)
        got = [video(after(res, s)) for s in ('s28.8', 's18.8', 's50')]
        works = (got[0][0] > 0 and 26800 <= got[0][1] <= 28900 and
                 got[1][0] > 0 and 16800 <= got[1][1] <= 18900 and
                 got[2][0] > 0 and 48000 <= got[2][1] <= 50100)
        what = 'seeks after the end of the stream: first video packets at %s ms' % [g[1] for g in got]
        if label == 'new' or lay == 'tpl_num':
            expect(works, 'dash_' + lay, label, what)
        else:
            expect(not works and got[0][0] == 0, 'dash_' + lay, label,
                   what + ' (the failure of the receiver test)')
        if label == 'new':
            ends = [r for r in res if r[0] == 'e']
            expect(len(ends) == 2 and all('(end)' in r[1] for r in ends), 'dash_' + lay, label,
                   'the stream ends with the end of file, before and after the seeks')
            expect(rc(res) == 'rc=0', 'dash_' + lay, label, rc(res))
        res = lines('dash_%s_early' % lay, label)
        got = [video(after(res, s)) for s in ('s28.8', 's18.8', 's50')]
        expect(got[0][0] > 0 and 26800 <= got[0][1] <= 28900 and 48000 <= got[2][1] <= 50100,
               'dash_%s_early' % lay, label,
               'seeks before the end: first video packets at %s ms' % [g[1] for g in got])

# 4. HLS VOD with discontinuities
for label in ('old', 'new'):
    for name in ('hls_disc_seek', 'hls_disc_seek_early'):
        res = lines(name, label)
        got = [video(after(res, s)) for s in ('s14', 's24', 's3')]
        jumps = sum(len(r[2]) for r in res)
        cont = (12000 <= got[0][1] <= 14100 and 22000 <= got[1][1] <= 24100 and
                1000 <= got[2][1] <= 3100)
        what = 'seeks to 14, 24 and 3 s: first video packets at %s ms, %d timestamp jumps while reading' % (
            [g[1] for g in got], jumps)
        if label == 'new':
            expect(cont and jumps == 0, name, label, what)
            full = [video(r[1]) for r in res if r[0] in ('e', 't5')]
            if name == 'hls_disc_seek':
                expect(full and 28000 <= full[0][2] <= 28800, name, label,
                       'read through: last video packet at %d ms of 28800' % (full[0][2] if full else -1))
        elif name == 'hls_disc_seek':
            expect(not cont and got[0][1] > 900000, name, label, what + ' (the failure of the receiver test)')

# 5. hls_audio_default_only
for label in ('old', 'new'):
    n0 = re.search(r'streams=(\d+)', (lines('hls_D0', label) or [('', '', [])])[0][1])
    n1 = re.search(r'streams=(\d+)', (lines('hls_D1', label) or [('', '', [])])[0][1])
    n0, n1 = (int(n0.group(1)) if n0 else -1), (int(n1.group(1)) if n1 else -1)
    if label == 'new':
        expect(n0 == 3 and n1 == 2, 'hls_D1', label, '%d streams, %d with hls_audio_default_only' % (n0, n1))
    else:
        expect(n0 == 3 and n1 == 3, 'hls_D1', label,
               '%d streams, %d with hls_audio_default_only (the option is not known)' % (n0, n1))

# 3. live DASH, duration template without suggestedPresentationDelay
for label in ('old', 'new'):
    res = lines('dlive_num_nospd', label)
    opened = any(r[0] == 'open' and 'ret=0' in r[1] for r in res)
    got = video(next((r[1] for r in res if r[0] == 't20'), ''))
    log = open(os.path.join(OUT, '..', 'out_%s.txt' % label), encoding='utf-8', errors='replace').read()
    m = re.search(r'segment requests: (\d+)\s+before the segment was there: (\d+)', log)
    req, early = (int(m.group(1)), int(m.group(2))) if m else (-1, -1)
    what = 'opened: %s, video up to %d ms, %d segment requests, %d of them too early, %s' % (
        opened, got[2], req, early, rc(res))
    if label == 'new':
        # the clock is read in whole seconds: a request can be up to a
        # second early and is tried again; the stream is read on (the old
        # state asks too early every time and gets nothing)
        expect(opened and got[2] >= 20000 and rc(res) == 'rc=0' and 0 <= early * 2 <= req, 'dlive_num_nospd', label, what)
    else:
        expect(not opened or got[2] < 20000, 'dlive_num_nospd', label, what + ' (the failure of the receiver test)')

# 2. live HLS that ends: under valgrind, with reads after the end
for label in ('old', 'new'):
    res = lines('hlive_end', label)
    ends = [r for r in res if r[0] == 'e']
    waits = [r for r in res if r[0].startswith('w')]
    expect(rc(res) == 'rc=0' and ends and '(end)' in ends[0][1] and waits and ' 0 packets' in waits[0][1],
           'hlive_end', label, 'ends, further reads give the end again, closes; valgrind: %s' % rc(res))

print()
if bad:
    print('%d checks failed' % len(bad))
    sys.exit(1)
print('all checks as expected')
