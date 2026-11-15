#!/usr/bin/env python3
"""Small HTTP server for player tests. Serves /media/hdd/ep3test/media with Range support and
some misbehaving paths:
  /f/<path>              the file, Range honoured
  /noext/<name>          HLS playlist <name>.m3u8 served under a path without .m3u8, type application/vnd.apple.mpegurl,
                         its segment uris rewritten to /seg/<n> (no extension)
  /seg/<file>            a segment by its file name without the .ts
  /stall/<path>?after=N  the file, but the connection goes silent after N bytes (never closes)
  /cut/<path>?after=N    the file, but the connection is closed after N bytes
  /gz/<path>             the file gzip'ed when the request has Accept-Encoding: gzip (logs the header)
  /redir/<path>          302 to /f/<path>  (?code=301|302|307, ?abs=1 = Location with scheme and host)
  /redir1/<path>         the same for .mpd / .m3u8 only, everything else under /redir1 is 404 (a client must
                         resolve relative urls against the redirected location)

Live streams made from the looping segment sets of media4 (net/live/index.json, written by genmedia_net.py).
Every stream has its own clock that starts with the first request for it (the driver starts one server per
case, so a case always sees the stream from second 0). At that moment the stream is already `pre` seconds old.
Options are given as query string of the manifest; the manifest then points at /dlive/<opts>/... or
/hlive/<opts>/... so that the segment requests carry them too (the manifest can also be requested there).

  /dlive/manifest.mpd?opt=val&...     dynamic MPD: 3 video representations, 2 audio AdaptationSets (eng 440 Hz, deu 880 Hz)
     mode=tl|num     SegmentTimeline (default) / SegmentTemplate with duration and availabilityStartTime
     addr=number|time  $Number$ (default) or $Time$ in the segment url (mode=tl)
     mup=4           minimumUpdatePeriod, seconds      tsb=30   timeShiftBufferDepth, seconds
     mbt=2           minBufferTime                     spd=N    suggestedPresentationDelay (not written by default)
     sn=1            number of the very first segment of the stream (startNumber)
     pto=0           presentationTimeOffset in seconds: segment timestamps start there instead of 0
     one=1           the timeline lists only the newest segment
     tlfix=1         timeline with a fixed first S and a growing r (no sliding window)
     lag=0           segments get available this many seconds later than the manifest says
     early=404|wait|ok  a segment requested before it is available: 404 (default), held back, served anyway
     gone=1          segments older than the window + 10 s give 404 (0 = kept for ever)
     end=N           N seconds after the start the stream ends: static MPD with mediaPresentationDuration
     startover=1     startover="1" on the MPD element       pre=600  age of the stream at the first request
     s=x             anything, only to get a separate clock
  /hlive/index.m3u8?opt=val&...       live media playlist, video + audio muxed in MPEG-TS
  /hlive/master.m3u8?opt=val&...      master playlist
     fmt=ts|fmp4     MPEG-TS (default) or fMP4 with EXT-X-MAP (master only: video r0..r2, audio renditions r3, r4)
     sep=1           master: video only variant + both audio renditions separate (0 = English muxed, Deutsch separate)
     subs=1          master: WebVTT rendition (one cue per segment, X-TIMESTAMP-MAP)
     win=6           segments in the playlist           type=live|event
     end=N           EXT-X-ENDLIST N seconds after the start
     wrapat=N        N seconds after the start the playlist starts again with EXT-X-MEDIA-SEQUENCE 0
     freeze=A-B      between second A and B the playlist is not updated; fz=cont (default: goes on where it
                     stopped) or fz=jump (continues at the real live edge)
     disc=N          EXT-X-DISCONTINUITY every N segments: alternating set A (640x360, 440 Hz) and set B
                     (512x288, 660 Hz, timestamps +1000 s); dseq=1 adds EXT-X-DISCONTINUITY-SEQUENCE   (ts, index.m3u8)
     pre=600, s=x    as above
  MPEG-TS segments get continuous PCR/PTS/DTS and continuity counters, fMP4 segments tfdt/sidx/mfhd.

Log lines (LOGFILE): "<unix time> GET <path> ..." for every request, and for the live endpoints
  "<unix time> DLIVE|HLIVE mpd|pl|seg key=<opts> t=<stream clock> ..." with the window served / the segment
  number and how early or late it was requested (due=+x: x seconds before it is available).
usage: ep3http.py PORT [LOGFILE]      environment: EP3_ROOT (media root), EP3_BIND (address, default 127.0.0.1)"""
import gzip, json, os, re, struct, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

ROOT = os.environ.get('EP3_ROOT', '/media/hdd/ep3test/media')
LOG = sys.argv[2] if len(sys.argv) > 2 else None
LOCK = threading.Lock()
CLOCKS = {}
INDEX = None


def log(msg):
    if LOG:
        with LOCK:
            with open(LOG, 'a') as f:
                f.write('%.3f %s\n' % (time.time(), msg))


# ----------------------------------------------------------------------------- live helpers
def index():
    global INDEX
    if INDEX is None:
        with open(os.path.join(ROOT, 'net', 'live', 'index.json')) as f:
            INDEX = json.load(f)
    return INDEX


def clock(kind, key):
    """(seconds since the first request of this stream, unix time of that request)"""
    now = time.time()
    with LOCK:
        t0 = CLOCKS.setdefault((kind, key), now)
    return now - t0, t0


def parse_key(key):
    o = {}
    for kv in key.split(','):
        if '=' in kv:
            k, v = kv.split('=', 1)
            o[k] = v
    return o


def make_key(q):
    return ','.join('%s=%s' % (k, q[k][0]) for k in sorted(q)) or '-'


def num(o, k, d):
    try:
        return float(o.get(k, d))
    except ValueError:
        return float(d)


def patch_fmp4(data, add, seqno):
    """add `add` (track timescale units) to tfdt and to the sidx presentation time, set the mfhd sequence number"""
    b = bytearray(data)
    p = 0
    while p + 8 <= len(b):
        size, typ = struct.unpack_from('>I4s', b, p)
        if size < 8:
            break
        if typ == b'sidx':
            if b[p + 8] == 0:
                struct.pack_into('>I', b, p + 20, (struct.unpack_from('>I', b, p + 20)[0] + add) & 0xffffffff)
            else:
                struct.pack_into('>Q', b, p + 20, struct.unpack_from('>Q', b, p + 20)[0] + add)
        elif typ == b'moof':
            q = p + 8
            while q + 8 <= p + size:
                s2, t2 = struct.unpack_from('>I4s', b, q)
                if s2 < 8:
                    break
                if t2 == b'mfhd':
                    struct.pack_into('>I', b, q + 12, seqno & 0xffffffff)
                elif t2 == b'traf':
                    r = q + 8
                    while r + 8 <= q + s2:
                        s3, t3 = struct.unpack_from('>I4s', b, r)
                        if s3 < 8:
                            break
                        if t3 == b'tfdt':
                            if b[r + 8] == 1:
                                struct.pack_into('>Q', b, r + 12, struct.unpack_from('>Q', b, r + 12)[0] + add)
                            else:
                                struct.pack_into('>I', b, r + 12, (struct.unpack_from('>I', b, r + 12)[0] + add) & 0xffffffff)
                        r += s3
                q += s2
        p += size
    return bytes(b)


def _ts_get(b, o):
    return ((b[o] >> 1) & 7) << 30 | b[o + 1] << 22 | (b[o + 2] >> 1) << 15 | b[o + 3] << 7 | b[o + 4] >> 1


def _ts_put(b, o, v):
    v &= 0x1ffffffff
    b[o] = (b[o] & 0xf1) | ((v >> 29) & 0x0e)
    b[o + 1] = (v >> 22) & 0xff
    b[o + 2] = (b[o + 2] & 1) | ((v >> 14) & 0xfe)
    b[o + 3] = (v >> 7) & 0xff
    b[o + 4] = (b[o + 4] & 1) | ((v << 1) & 0xfe)


def patch_ts(data, add, ccadd):
    """add `add` (90 kHz) to every PCR, PTS and DTS and ccadd[pid] to the continuity counters"""
    b = bytearray(data)
    for i in range(0, len(b) - 187, 188):
        if b[i] != 0x47:
            break
        pid = ((b[i + 1] & 0x1f) << 8) | b[i + 2]
        afc = (b[i + 3] >> 4) & 3
        d = ccadd.get(pid)
        if d:
            b[i + 3] = (b[i + 3] & 0xf0) | ((b[i + 3] + d) & 0x0f)
        p = i + 4
        if afc & 2:
            alen = b[p]
            if alen >= 7 and b[p + 1] & 0x10 and add:
                base = (b[p + 2] << 25 | b[p + 3] << 17 | b[p + 4] << 9 | b[p + 5] << 1 | b[p + 6] >> 7) + add
                base &= 0x1ffffffff
                b[p + 2] = (base >> 25) & 0xff
                b[p + 3] = (base >> 17) & 0xff
                b[p + 4] = (base >> 9) & 0xff
                b[p + 5] = (base >> 1) & 0xff
                b[p + 6] = (b[p + 6] & 0x7f) | ((base & 1) << 7)
            p += 1 + alen
        if afc & 1 and b[i + 1] & 0x40 and add and p + 14 <= i + 188 and b[p] == 0 and b[p + 1] == 0 and b[p + 2] == 1 \
                and b[p + 3] >= 0xbd and b[p + 3] not in (0xbe, 0xbf):
            fl = b[p + 7]
            if fl & 0x80:
                _ts_put(b, p + 9, _ts_get(b, p + 9) + add)
                if fl & 0x40 and p + 19 <= i + 188:
                    _ts_put(b, p + 14, _ts_get(b, p + 14) + add)
    return bytes(b)


def iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t)) + ('.%03dZ' % int((t % 1) * 1000))


def dur(s):
    return 'PT%.3fS' % s


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def reply(self, data, ctype, code=200, extra=()):
        if isinstance(data, str):
            data = data.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command == 'HEAD':
            return
        try:
            self.wfile.write(data)
        except Exception as e:
            log('client gone: %r' % (e,))

    def fail(self, code, why):
        self.reply(why + '\n', 'text/plain', code)

    # ------------------------------------------------------------------------- DASH live
    def dash_state(self, o, t):
        ix = index()
        D = ix['segdur_ms'] / 1000.0
        pre = num(o, 'pre', 600)
        end = num(o, 'end', 0)
        ended = end > 0 and t >= end
        T = pre + (min(t, end) if end > 0 else t)
        last = int((T - num(o, 'lag', 0)) / D + 1e-6) - 1          # newest segment that is available
        if ended:
            last = int((pre + end) / D + 1e-6) - 1
        nwin = max(1, int(num(o, 'tsb', 30) / D))
        first = max(0, last - nwin + 1)
        if o.get('tlfix') == '1':
            first = max(0, int(pre / D + 1e-6) - 1 - nwin + 1)
        if o.get('one') == '1':
            first = last
        return D, pre, first, last, ended, nwin

    def dash_mpd(self, key, o):
        ix = index()
        t, t0 = clock('dlive', key)
        D, pre, first, last, ended, nwin = self.dash_state(o, t)
        mode = o.get('mode', 'tl')
        sn = int(num(o, 'sn', 1))
        pto = int(num(o, 'pto', 0))
        base = '/dlive/%s/' % key
        x = ['<?xml version="1.0" encoding="utf-8"?>',
             '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-live:2011"']
        if ended:
            x.append(' type="static" mediaPresentationDuration="%s"' % dur((last + 1) * D))
        else:
            x.append(' type="dynamic" minimumUpdatePeriod="%s" timeShiftBufferDepth="%s"' % (dur(num(o, 'mup', 4)), dur(num(o, 'tsb', 30))))
            if 'spd' in o:
                x.append(' suggestedPresentationDelay="%s"' % dur(num(o, 'spd', 6)))
        if o.get('startover') == '1':
            x.append(' startover="1"')
        x.append(' availabilityStartTime="%s" publishTime="%s" maxSegmentDuration="%s" minBufferTime="%s">' % (
            iso(t0 - pre), iso(time.time()), dur(D), dur(num(o, 'mbt', 2))))
        x.append('<Period id="p0" start="PT0S">')
        reps = ix['fmp4']['reps']
        groups = [('video', '', [r for r in sorted(reps) if reps[r]['type'] == 'video'])]
        groups += [('audio', ix['fmp4']['lang'].get(r, 'und'), [r]) for r in sorted(reps) if reps[r]['type'] == 'audio']
        for gi, (typ, lang, rids) in enumerate(groups):
            x.append('<AdaptationSet id="%d" contentType="%s" mimeType="%s/mp4" segmentAlignment="true" startWithSAP="1"%s>' % (
                gi, typ, typ, ' lang="%s"' % lang if lang else ''))
            for r in rids:
                ts = reps[r]['timescale']
                d = int(round(D * ts))
                x.append('<Representation id="%s" codecs="%s" bandwidth="%d" %s>' % (r, reps[r]['codecs'], reps[r]['bw'], reps[r]['attrs']))
                if typ == 'audio':
                    x.append('<AudioChannelConfiguration schemeIdUri="urn:mpeg:dash:23003:3:audio_channel_configuration:2011" value="2"/>')
                init = 'initialization="%sinit_$RepresentationID$.mp4"' % base
                ptoa = ' presentationTimeOffset="%d"' % (pto * ts) if pto else ''
                if mode == 'num':
                    x.append('<SegmentTemplate timescale="%d" duration="%d" startNumber="%d"%s %s media="%sseg_$RepresentationID$_$Number$.m4s"/>' % (
                        ts, d, sn, ptoa, init, base))
                else:
                    media = 'seg_$RepresentationID$_t$Time$.m4s' if o.get('addr') == 'time' else 'seg_$RepresentationID$_$Number$.m4s'
                    x.append('<SegmentTemplate timescale="%d" startNumber="%d"%s %s media="%s%s">' % (ts, sn + first, ptoa, init, base, media))
                    x.append('<SegmentTimeline><S t="%d" d="%d"%s/></SegmentTimeline>' % (
                        first * d + pto * ts, d, ' r="%d"' % (last - first) if last > first else ''))
                    x.append('</SegmentTemplate>')
                x.append('</Representation>')
            x.append('</AdaptationSet>')
        x.append('</Period>')
        x.append('</MPD>')
        log('DLIVE mpd key=%s t=%.3f first=%d last=%d num=%d..%d%s' % (key, t, first, last, sn + first, sn + last, ' ENDED' if ended else ''))
        self.reply('\n'.join(x) + '\n', 'application/dash+xml')

    def dash_file(self, key, o, name):
        ix = index()
        d = os.path.join(ROOT, ix['fmp4']['dir'])
        m = re.match(r'init_(\d+)\.mp4$', name)
        if m:
            try:
                with open(os.path.join(d, name), 'rb') as f:
                    return self.reply(f.read(), 'video/mp4')
            except OSError:
                return self.fail(404, 'no such representation')
        m = re.match(r'seg_(\d+)_(t?)(\d+)\.m4s$', name)
        if not m or m.group(1) not in ix['fmp4']['reps']:
            return self.fail(404, 'unknown file')
        rep = m.group(1)
        ts = ix['fmp4']['reps'][rep]['timescale']
        t, t0 = clock('dlive', key)
        D, pre, first, last, ended, nwin = self.dash_state(o, t)
        dt = int(round(D * ts))
        pto = int(num(o, 'pto', 0))
        if m.group(2):
            n, rem = divmod(int(m.group(3)) - pto * ts, dt)
            if rem:
                log('DLIVE seg key=%s rep=%s time=%s t=%.3f BAD-TIME-404' % (key, rep, m.group(3), t))
                return self.fail(404, 'no segment starts at this time')
        else:
            n = int(m.group(3)) - int(num(o, 'sn', 1))
        due = (n + 1) * D + num(o, 'lag', 0) - (pre + t)           # > 0: requested too early
        tag = 'OK'
        if n < 0:
            tag = 'BEFORE-START-404'
        elif ended and n > last:
            tag = 'AFTER-END-404'
        elif due > 0:
            early = o.get('early', '404')
            if early == 'wait':
                tag = 'EARLY-WAIT'
                time.sleep(min(due, 15))
            elif early == 'ok':
                tag = 'EARLY-OK'
            else:
                tag = 'EARLY-404'
        elif o.get('gone', '1') == '1' and n < last - nwin - int(10 / D):
            tag = 'GONE-404'
        log('DLIVE seg key=%s rep=%s n=%d t=%.3f due=%+.3f edge=%d %s' % (key, rep, n, t, due, last, tag))
        if tag.endswith('404'):
            return self.fail(404, tag)
        i = n % ix['nseg']
        with open(os.path.join(d, 'seg_%s_%d.m4s' % (rep, i + 1)), 'rb') as f:
            data = f.read()
        self.reply(patch_fmp4(data, (n - i) * dt + pto * ts, n + 1), 'video/mp4')

    # ------------------------------------------------------------------------- HLS live
    def hls_state(self, o, t):
        ix = index()
        D = ix['segdur_ms'] / 1000.0
        pre = num(o, 'pre', 600)
        end = num(o, 'end', 0)
        ended = end > 0 and t >= end
        tt = min(t, end) if end > 0 else t
        fr = re.match(r'([\d.]+)-([\d.]+)$', o.get('freeze', ''))
        frozen = False
        if fr:
            a, b = float(fr.group(1)), float(fr.group(2))
            if a <= tt < b:
                tt, frozen = a, True
            elif tt >= b and o.get('fz', 'cont') != 'jump':
                tt -= b - a
        last = int((pre + tt) / D + 1e-6) - 1
        win = max(1, int(num(o, 'win', 6)))
        first = max(0, last - win + 1)
        ev0 = max(0, int(pre / D + 1e-6) - 3)
        if o.get('type') == 'event':
            first = ev0
        seq0 = 0                                                  # media sequence number of segment `first`
        wrap = num(o, 'wrapat', 0)
        if wrap > 0:
            nw = int((pre + wrap) / D + 1e-6)                     # first segment after the restart
            if last >= nw:
                first = max(first, nw)
                seq0 = -nw
        return D, first, last, seq0, ended, frozen

    def hls_tracks(self, o):
        if o.get('fmt') == 'fmp4':
            return {'r%s' % r: ('fmp4', r) for r in index()['fmp4']['reps']}
        return {'av': ('ts', 'ts_av'), 'v': ('ts', 'ts_v'), 'aen': ('ts', 'ts_aen'), 'ade': ('ts', 'ts_ade'), 'sub': ('vtt', '')}

    def hls_master(self, key, o):
        base = '/hlive/%s/' % key
        aud = ('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",DEFAULT=YES,AUTOSELECT=YES%s\n'
               '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Deutsch",LANGUAGE="de",DEFAULT=NO,AUTOSELECT=YES,URI="%s"\n')
        x = '#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-INDEPENDENT-SEGMENTS\n'
        subs = ''
        if o.get('subs') == '1' and o.get('fmt') != 'fmp4':
            x += ('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",DEFAULT=YES,AUTOSELECT=YES,'
                  'FORCED=NO,URI="%ssub.m3u8"\n' % base)
            subs = ',SUBTITLES="subs"'
        if o.get('fmt') == 'fmp4':
            reps = index()['fmp4']['reps']
            x += aud % (',URI="%sr3.m3u8"' % base, base + 'r4.m3u8')
            for r in sorted(reps):
                if reps[r]['type'] == 'video':
                    res = re.search(r'width="(\d+)" height="(\d+)"', reps[r]['attrs'])
                    x += '#EXT-X-STREAM-INF:BANDWIDTH=%d,RESOLUTION=%sx%s,CODECS="%s,mp4a.40.2",AUDIO="aud"\n%sr%s.m3u8\n' % (
                        reps[r]['bw'] + 70000, res.group(1), res.group(2), reps[r]['codecs'], base, r)
        elif o.get('sep', '1') == '1':
            x += aud % (',URI="%saen.m3u8"' % base, base + 'ade.m3u8')
            x += '#EXT-X-STREAM-INF:BANDWIDTH=750000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2",AUDIO="aud"%s\n%sv.m3u8\n' % (subs, base)
        else:
            x += aud % ('', base + 'ade.m3u8')
            x += '#EXT-X-STREAM-INF:BANDWIDTH=750000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2",AUDIO="aud"%s\n%sav.m3u8\n' % (subs, base)
        clock('hlive', key)
        log('HLIVE master key=%s' % key)
        self.reply(x, 'application/vnd.apple.mpegurl')

    def hls_playlist(self, key, o, track):
        tracks = self.hls_tracks(o)
        if track not in tracks:
            return self.fail(404, 'unknown track')
        kind, src = tracks[track]
        t, t0 = clock('hlive', key)
        D, first, last, seq0, ended, frozen = self.hls_state(o, t)
        disc = int(num(o, 'disc', 0)) if kind == 'ts' and track == 'av' else 0
        base = '/hlive/%s/' % key
        x = '#EXTM3U\n#EXT-X-VERSION:%d\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:%d\n' % (7 if kind == 'fmp4' else 3, first + seq0)
        if disc and o.get('dseq') == '1':
            x += '#EXT-X-DISCONTINUITY-SEQUENCE:%d\n' % ((first - 1) // disc if first > 0 else 0)
        if o.get('type') == 'event':
            x += '#EXT-X-PLAYLIST-TYPE:EVENT\n'
        if kind == 'fmp4':
            x += '#EXT-X-MAP:URI="%s%s_init.mp4"\n' % (base, track)
        ext = {'ts': 'ts', 'fmp4': 'm4s', 'vtt': 'vtt'}[kind]
        for n in range(first, last + 1):
            if disc and n % disc == 0 and n > 0:
                x += '#EXT-X-DISCONTINUITY\n'
            x += '#EXTINF:%.6f,\n%s%s_%d.%s\n' % (D, base, track, n, ext)
        if ended:
            x += '#EXT-X-ENDLIST\n'
        log('HLIVE pl key=%s track=%s t=%.3f seq=%d first=%d last=%d%s%s' % (
            key, track, t, first + seq0, first, last, ' FROZEN' if frozen else '', ' ENDED' if ended else ''))
        self.reply(x, 'application/vnd.apple.mpegurl')

    def hls_file(self, key, o, name):
        ix = index()
        tracks = self.hls_tracks(o)
        m = re.match(r'([a-z0-9]+)_(init|\d+)\.(ts|m4s|mp4|vtt)$', name)
        if not m or m.group(1) not in tracks:
            return self.fail(404, 'unknown file')
        track = m.group(1)
        kind, src = tracks[track]
        if m.group(2) == 'init':
            with open(os.path.join(ROOT, ix['fmp4']['dir'], 'init_%s.mp4' % src), 'rb') as f:
                return self.reply(f.read(), 'video/mp4')
        n = int(m.group(2))
        t, t0 = clock('hlive', key)
        D, first, last, seq0, ended, frozen = self.hls_state(o, t)
        tag = 'OK' if first <= n <= last else ('NOT-YET-404' if n > last else 'OLD')
        log('HLIVE seg key=%s track=%s n=%d t=%.3f window=%d..%d %s' % (key, track, n, t, first, last, tag))
        if tag.endswith('404'):
            return self.fail(404, tag)
        i = n % ix['nseg']
        if kind == 'vtt':
            def tm(s):
                return '%02d:%02d:%06.3f' % (int(s // 3600), int(s // 60) % 60, s % 60)
            body = 'WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:%d,LOCAL:00:00:00.000\n\n%s --> %s\nlive cue %d\n\n' % (
                ix['ts']['ts_av']['first_pts'], tm(n * D + 0.2), tm(n * D + 1.7), n)
            return self.reply(body, 'text/vtt')
        if kind == 'fmp4':
            ts = ix['fmp4']['reps'][src]['timescale']
            with open(os.path.join(ROOT, ix['fmp4']['dir'], 'seg_%s_%d.m4s' % (src, i + 1)), 'rb') as f:
                data = f.read()
            return self.reply(patch_fmp4(data, (n - i) * int(round(D * ts)), n + 1), 'video/mp4')
        disc = int(num(o, 'disc', 0)) if track == 'av' else 0
        if disc and (n // disc) % 2:
            src = 'ts_b'
        s = ix['ts'][src]
        with open(os.path.join(ROOT, s['dir'], 'seg_%d.ts' % i), 'rb') as f:
            data = f.read()
        loop = n // ix['nseg']
        ccadd = {int(pid): (loop * c) % 16 for pid, c in s['pcc'].items()}
        self.reply(patch_ts(data, (n - i) * int(round(D * 90000)), ccadd), 'video/mp2t')

    def live(self, mode, rest, q):
        """/dlive/manifest.mpd?opts, /dlive/<opts>/<file>, /hlive/index.m3u8?opts, /hlive/master.m3u8?opts, /hlive/<opts>/<file>"""
        try:
            if '/' in rest:
                key, name = rest.split('/', 1)
            else:
                key, name = make_key(q), rest
            o = parse_key(key)
            if mode == 'dlive':
                if name == 'manifest.mpd':
                    return self.dash_mpd(key, o)
                return self.dash_file(key, o, name)
            if name == 'master.m3u8':
                return self.hls_master(key, o)
            if name == 'index.m3u8':
                return self.hls_master(key, o) if o.get('fmt') == 'fmp4' else self.hls_playlist(key, o, 'av')
            if name.endswith('.m3u8'):
                return self.hls_playlist(key, o, name[:-5])
            return self.hls_file(key, o, name)
        except OSError as e:
            log('live error: %r' % (e,))
            return self.fail(404, 'media missing: %r' % (e,))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        parts = unquote(u.path).lstrip('/').split('/', 1)
        mode, rest = parts[0], parts[1] if len(parts) > 1 else ''
        log('GET %s range=%s ae=%s ua=%s' % (self.path, self.headers.get('Range'), self.headers.get('Accept-Encoding'),
                                             (self.headers.get('User-Agent') or '')[:40]))
        if mode in ('dlive', 'hlive'):
            return self.live(mode, rest, q)
        if mode == 'redir1' and not rest.endswith(('.mpd', '.m3u8')):
            log('REDIR1 404 (only the manifest is redirected) %s' % rest)
            return self.fail(404, 'only the manifest lives here')
        if mode in ('redir', 'redir1'):
            loc = '/f/' + rest
            if q.get('abs', ['0'])[0] == '1':
                loc = 'http://%s%s' % (self.headers.get('Host') or '127.0.0.1:%s' % sys.argv[1], loc)
            code = int(q.get('code', ['302'])[0])
            log('REDIR %d -> %s' % (code, loc))
            return self.reply('', 'text/plain', code, [('Location', loc)])
        if mode == 'noext':
            path = os.path.join(ROOT, 'hls', rest + '.m3u8')
            try:
                txt = open(path).read()
            except OSError:
                return self.send_error(404)
            txt = re.sub(r'^([A-Za-z0-9_]+)\.ts$', r'/seg/\1', txt, flags=re.M)
            data = txt.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if mode == 'seg':
            path = os.path.join(ROOT, 'hls', rest + '.ts')
            ctype = 'application/octet-stream'
        else:
            path = os.path.join(ROOT, rest)
            ctype = 'application/vnd.apple.mpegurl' if path.endswith('.m3u8') else 'application/octet-stream'
            if path.endswith('.mpd'):
                ctype = 'application/dash+xml'
            elif path.endswith('.vtt'):
                ctype = 'text/vtt'
        if not os.path.isfile(path):
            return self.send_error(404)
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get('Range')
        m = re.match(r'bytes=(\d*)-(\d*)', rng or '')
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(size - 1, int(m.group(2)))
            else:
                start = max(0, size - int(m.group(2)))
            if start >= size:
                self.send_response(416)
                self.send_header('Content-Range', 'bytes */%d' % size)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        with open(path, 'rb') as f:
            f.seek(start)
            body = f.read(end - start + 1)
        if mode == 'gz' and 'gzip' in (self.headers.get('Accept-Encoding') or ''):
            body = gzip.compress(body)
            self.send_response(200)
            self.send_header('Content-Encoding', 'gzip')
        elif m:
            self.send_response(206)
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        else:
            self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command == 'HEAD':
            return
        limit = int(q.get('after', ['0'])[0])
        try:
            if mode in ('stall', 'cut') and limit and start + len(body) > limit:
                keep = max(0, limit - start)
                self.wfile.write(body[:keep])
                self.wfile.flush()
                log('%s after %d bytes of %s' % (mode, keep, rest))
                if mode == 'stall':
                    time.sleep(600)
                self.close_connection = True
                return
            self.wfile.write(body)
        except Exception as e:
            log('client gone: %r' % (e,))


if __name__ == '__main__':
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer((os.environ.get('EP3_BIND', '127.0.0.1'), int(sys.argv[1])), H).serve_forever()
