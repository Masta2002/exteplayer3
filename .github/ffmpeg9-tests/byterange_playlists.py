#!/usr/bin/env python3
"""HLS playlists with byte range segments for the CI checks, made from the
fixtures of the FATE tests fate-hls-discontinuity (two MPEG-TS files with
their own timestamps, a discontinuity in between) and
fate-hls-discontinuity-notag-live (the segments of one stream).

usage: byterange_playlists.py <dir with the copied fixtures>

  disc.m3u8     the two files as two segments, as the FATE test has them
  disc_br.m3u8  the same content as ONE file (disc_all.ts), every region as
                two byte range segments; the range of the second region
                directly follows the first one in the file
  nt.m3u8       the segments of the tag-less stream as files (VOD)
  nt_br.m3u8    the same as one file (nt_all.ts), every segment as two byte
                range segments: one run over the whole file
"""
import os
import re
import sys

d = sys.argv[1]


def halves(size):
    # whole transport stream packets in both parts
    first = size // 2 // 188 * 188
    return [first, size - first]


def write(name, lines):
    with open(os.path.join(d, name), 'w', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def build(prefix, files, sequence, extra, discontinuity):
    head = ['#EXTM3U', '#EXT-X-VERSION:4', '#EXT-X-TARGETDURATION:1',
            '#EXT-X-MEDIA-SEQUENCE:%d' % sequence] + extra
    plain = list(head)
    ranges = list(head)
    offset = 0
    with open(os.path.join(d, prefix + '_all.ts'), 'wb') as out:
        for i, name in enumerate(files):
            with open(os.path.join(d, name), 'rb') as f:
                data = f.read()
            assert len(data) % 188 == 0 and len(data) >= 2 * 188, name
            out.write(data)
            if discontinuity and i:
                plain.append('#EXT-X-DISCONTINUITY')
                ranges.append('#EXT-X-DISCONTINUITY')
            plain += ['#EXTINF:1.0,', name]
            for size in halves(len(data)):
                ranges += ['#EXTINF:0.5,', '#EXT-X-BYTERANGE:%d@%d' % (size, offset),
                           prefix + '_all.ts']
                offset += size
    write(prefix + '.m3u8', plain + ['#EXT-X-ENDLIST'])
    write(prefix + '_br.m3u8', ranges + ['#EXT-X-ENDLIST'])


build('disc', ['hls_discontinuity_0.ts', 'hls_discontinuity_1.ts'], 10,
      ['#EXT-X-DISCONTINUITY-SEQUENCE:7'], True)

nt = sorted((n for n in os.listdir(d) if re.match(r'hls_notag_live_\d+\.ts$', n)),
            key=lambda n: int(re.findall(r'\d+', n)[-1]))
assert len(nt) >= 2, nt
build('nt', nt, 325, [], False)
