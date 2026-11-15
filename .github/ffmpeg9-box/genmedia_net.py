#!/usr/bin/env python3
"""Generate the DASH / HLS network test media under media4/ (ffmpeg 9.0.2 from imageio-ffmpeg in ffpy).

  python genmedia_net.py            everything (about 2 minutes)
  python genmedia_net.py dash hls   only some steps (src, dash, cenc, dsubs, dextra, hls, index)

Layout:  media4/_src/   intermediates (NOT copied to the box)
         media4/net/    goes to the box as /media/hdd/ep3test/media/net/ (served as /f/net/... and used by
                        the dynamic endpoints /dlive and /hlive of ep3http.py through net/live/index.json)

Timing: 25 fps video with a GOP of 48 frames and 48 kHz AAC (1024 samples per frame) meet every 1.92 s
(48 video frames = 90 audio frames), so every segment of every track is exactly 1.92 s and a loop of
32 segments (61.44 s) can be repeated without a gap. Audio tones: en 440 Hz, de 880 Hz, part B 660 Hz.
"""
import glob, json, os, shutil, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
FF = glob.glob(os.path.join(HERE, 'ffpy', 'imageio_ffmpeg', 'binaries', 'ffmpeg*'))[0]
OUT = os.path.join(HERE, 'media4')
SRC = os.path.join(OUT, '_src')
NET = os.path.join(OUT, 'net')
SEG = 1.92
NSEG = 32
T = SEG * NSEG            # 61.44 s
VFRAMES = 48 * NSEG       # 1536
AFRAMES = 90 * NSEG       # 2880
KEY = '00112233445566778899aabbccddeeff'      # cenc key (video, or everything)
KID = '11111111111111111111111111111111'
KEY2 = 'ffeeddccbbaa99887766554433221100'     # cenc key of the audio tracks in the two-key set
KID2 = '22222222222222222222222222222222'
AESKEY = bytes(range(0x30, 0x40))             # HLS AES-128 key "0123456789:;<=>?"
SRV = 'http://127.0.0.1:8099'


def ff(*args, cwd=None):
    cmd = [FF, '-hide_banner', '-loglevel', 'error', '-y'] + [str(a) for a in args]
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode('utf-8', 'replace').strip()
    if r.returncode or out:
        print('ffmpeg rc=%d: %s\n   %s' % (r.returncode, ' '.join(cmd[4:])[:300], out[:1500]))
    if r.returncode:
        sys.exit(1)


def fresh(*parts):
    d = os.path.join(NET, *parts)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    return d


def wr(path, text):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def rd(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------- intermediates
def x264(size, kbps):
    return ['-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-s', size, '-r', 25, '-g', 48,
            '-keyint_min', 48, '-sc_threshold', 0, '-bf', 0, '-b:v', '%dk' % kbps, '-maxrate', '%dk' % (kbps * 3 // 2),
            '-bufsize', '%dk' % (kbps * 2), '-frames:v', VFRAMES, '-x264-params', 'log-level=error',
            '-threads', 1]            # one thread: the same bytes on every run


def step_src():
    os.makedirs(SRC, exist_ok=True)
    tsrc = ['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25']
    bars = ['-f', 'lavfi', '-i', 'smptebars=size=512x288:rate=25']
    for name, size, kbps in (('v1', '640x360', 600), ('v2', '480x270', 350), ('v3', '320x180', 150)):
        ff(*tsrc, *x264(size, kbps), os.path.join(SRC, name + '.mp4'))
    ff(*bars, *x264('512x288', 300), os.path.join(SRC, 'vb.mp4'))
    for name, freq, lang in (('a_en', 440, 'eng'), ('a_de', 880, 'deu'), ('a_b', 660, 'eng')):
        ff('-f', 'lavfi', '-i', 'sine=frequency=%d:sample_rate=48000' % freq, '-c:a', 'aac', '-b:a', '64k', '-ac', 2,
           '-t', T + 1, '-f', 'adts', os.path.join(SRC, name + '_raw.aac'))
        # ADTS: first packet at 0, no priming edit list; exactly AFRAMES frames
        ff('-i', os.path.join(SRC, name + '_raw.aac'), '-c', 'copy', '-frames:a', AFRAMES, '-f', 'adts',
           os.path.join(SRC, name + '.aac'))
        os.remove(os.path.join(SRC, name + '_raw.aac'))
    ff(*tsrc, '-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-s', '640x360', '-r', 25, '-g', 48,
       '-keyint_min', 48, '-bf', 0, '-b:v', '500k', '-frames:v', VFRAMES, '-tag:v', 'hvc1',
       '-x265-params', 'log-level=error:scenecut=0:open-gop=0:repeat-headers=1:pools=none:frame-threads=1',
       os.path.join(SRC, 'hevc.mp4'))


# ---------------------------------------------------------------- mp4 box helpers
def boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    p = start
    while p + 8 <= end:
        size, typ = struct.unpack('>I4s', data[p:p + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack('>Q', data[p + 8:p + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - p
        if size < hdr:
            break
        yield typ.decode('latin-1'), p, hdr, size
        p += size


def seg_info(path):
    """tfdt and the sum of the sample durations of the first traf, the timescale of an init segment"""
    data = open(path, 'rb').read()
    res = {}
    for typ, p, hdr, size in boxes(data):
        if typ == 'moof':
            for t2, p2, h2, s2 in boxes(data, p + hdr, p + size):
                if t2 != 'traf':
                    continue
                defdur = 0
                for t3, p3, h3, s3 in boxes(data, p2 + h2, p2 + s2):
                    body = data[p3 + h3:p3 + s3]
                    if t3 == 'tfhd':
                        fl = struct.unpack('>I', body[:4])[0] & 0xffffff
                        o = 8 + (8 if fl & 1 else 0) + (4 if fl & 2 else 0)
                        if fl & 8:
                            defdur = struct.unpack('>I', body[o:o + 4])[0]
                    elif t3 == 'tfdt':
                        res['tfdt'] = struct.unpack('>Q', body[4:12])[0] if body[0] == 1 else struct.unpack('>I', body[4:8])[0]
                    elif t3 == 'trun':
                        fl = struct.unpack('>I', body[:4])[0] & 0xffffff
                        n = struct.unpack('>I', body[4:8])[0]
                        o = 8 + (4 if fl & 1 else 0) + (4 if fl & 4 else 0)
                        per = sum(4 for b in (0x100, 0x200, 0x400, 0x800) if fl & b)
                        dur = 0
                        for i in range(n):
                            dur += struct.unpack('>I', body[o:o + 4])[0] if fl & 0x100 else defdur
                            o += per
                        res['dur'] = res.get('dur', 0) + dur
                        res['samples'] = res.get('samples', 0) + n
        elif typ == 'moov':
            i = data.find(b'mdhd', p)
            ver = data[i + 4]
            res['timescale'] = struct.unpack('>I', data[i + (24 if ver == 1 else 16):i + (28 if ver == 1 else 20)])[0]
    return res


# ---------------------------------------------------------------- DASH VOD
LANG = {'a_en.aac': 'eng', 'a_de.aac': 'deu', 'a_b.aac': 'eng'}
REPS = [('v1', 'video'), ('v2', 'video'), ('v3', 'video'), ('a_en', 'audio'), ('a_de', 'audio')]


def dash_mux(outdir, *opts, inputs=None, extra_in=()):
    inputs = inputs or ['v1.mp4', 'v2.mp4', 'v3.mp4', 'a_en.aac', 'a_de.aac']
    a = []
    for i in inputs:
        a += ['-i', os.path.join(SRC, i)]
    for i in range(len(inputs)):
        a += ['-map', '%d:0' % i]
        if inputs[i] in LANG:
            a += ['-metadata:s:%d' % i, 'language=' + LANG[inputs[i]]]
    nv = sum(1 for i in inputs if i.startswith(('v', 'hevc')))
    sets = 'id=0,streams=' + ','.join(str(i) for i in range(nv))
    for k in range(nv, len(inputs)):
        sets += ' id=%d,streams=%d' % (k - nv + 1, k)
    ff(*extra_in, *a, '-c', 'copy', '-f', 'dash', '-seg_duration', SEG, '-adaptation_sets', sets,
       '-init_seg_name', 'init_$RepresentationID$.mp4', '-media_seg_name', 'seg_$RepresentationID$_$Number$.m4s',
       *opts, 'manifest.mpd', cwd=outdir)


def step_dash():
    d = fresh('dash', 'tpl_num')
    dash_mux(d, '-use_template', 1, '-use_timeline', 0)
    d = fresh('dash', 'tpl_tl')
    dash_mux(d, '-use_template', 1, '-use_timeline', 1)
    d = fresh('dash', 'seglist')
    dash_mux(d, '-use_template', 0)
    d = fresh('dash', 'single')
    dash_mux(d, '-single_file', 1, '-single_file_name', 'rep_$RepresentationID$.mp4')
    os.rename(os.path.join(d, 'manifest.mpd'), os.path.join(d, 'sf_list.mpd'))
    d2 = fresh('dash', 'single_sidx')
    dash_mux(d2, '-single_file', 1, '-single_file_name', 'rep_$RepresentationID$.mp4', '-global_sidx', 1)
    # ffmpeg writes a SegmentList here too; make it the on-demand form: SegmentBase with the range of the sidx
    import re
    mpd = rd(os.path.join(d2, 'manifest.mpd'))
    os.remove(os.path.join(d2, 'manifest.mpd'))

    def segbase(m):
        data = open(os.path.join(d2, m.group(1)), 'rb').read(4096)
        rng = {typ: (p, p + size - 1) for typ, p, hdr, size in boxes(data) if typ in ('moov', 'sidx')}
        return ('<BaseURL>%s</BaseURL>\n\t\t\t\t<SegmentBase indexRange="%d-%d">\n\t\t\t\t\t<Initialization range="0-%d" />\n'
                '\t\t\t\t</SegmentBase>' % (m.group(1), rng['sidx'][0], rng['sidx'][1], rng['moov'][1]))
    mpd = re.sub(r'<BaseURL>(rep_\d\.mp4)</BaseURL>\s*<SegmentList.*?</SegmentList>', segbase, mpd, flags=re.S)
    wr(os.path.join(d2, 'sf_base.mpd'), mpd.replace('isoff-live', 'isoff-on-demand'))
    # check the exact segment grid of the template set (the live endpoints rely on it)
    d = os.path.join(NET, 'dash', 'tpl_num')
    for rep in range(5):
        ts = seg_info(os.path.join(d, 'init_%d.mp4' % rep))['timescale']
        for n in range(1, NSEG + 1):
            s = seg_info(os.path.join(d, 'seg_%d_%d.m4s' % (rep, n)))
            want = round((n - 1) * SEG * ts)
            if s.get('tfdt') != want or s.get('dur') != round(SEG * ts):
                print('GRID MISMATCH rep %d seg %d: %r want tfdt %d dur %d' % (rep, n, s, want, round(SEG * ts)))
        if os.path.exists(os.path.join(d, 'seg_%d_%d.m4s' % (rep, NSEG + 1))):
            print('GRID: rep %d has more than %d segments' % (rep, NSEG))


# ---------------------------------------------------------------- DASH CENC
PROT = ('\t\t\t<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc" cenc:default_KID="%s"/>\n'
        '\t\t\t<ContentProtection schemeIdUri="urn:uuid:e2719d58-a985-b3c9-781a-b030af78d30e" value="ClearKey1.0"/>\n')


def kid_uuid(k):
    return '%s-%s-%s-%s-%s' % (k[:8], k[8:12], k[12:16], k[16:20], k[20:])


def add_protection(mpd, kid_video, kid_audio):
    out = []
    for line in mpd.split('\n'):
        out.append(line)
        if '<AdaptationSet' in line:
            out.append((PROT % kid_uuid(kid_video if 'contentType="video"' in line else kid_audio)).rstrip('\n'))
    return '\n'.join(out).replace('xmlns="urn:mpeg:dash:schema:mpd:2011"',
                                  'xmlns="urn:mpeg:dash:schema:mpd:2011"\n\txmlns:cenc="urn:mpeg:cenc:2013"')


def step_cenc():
    """cenc1: one key for everything.  cenc2: video KEY/KID, both audio tracks KEY2/KID2."""
    enc = 'encryption_scheme=cenc-aes-ctr:encryption_key=%s:encryption_kid=%s'
    ins = ['v2.mp4', 'a_en.aac', 'a_de.aac']
    d = fresh('dash', 'cenc1')
    dash_mux(d, '-use_template', 1, '-use_timeline', 0, '-format_options', enc % (KEY, KID), inputs=ins)
    mpd = add_protection(rd(os.path.join(d, 'manifest.mpd')), KID, KID)
    wr(os.path.join(d, 'manifest.mpd'), mpd)
    # the key as an attribute of every Representation (patch "parsing cenc key(s) from MPD playlist")
    wr(os.path.join(d, 'manifest_mpdkey.mpd'), mpd.replace('<Representation ', '<Representation cenc_decryption_key="%s" ' % KEY))
    d = fresh('dash', 'cenc2')
    dash_mux(d, '-use_template', 1, '-use_timeline', 0, '-format_options', enc % (KEY2, KID2), inputs=ins)
    tmp = fresh('dash', 'cenc2_tmp')
    dash_mux(tmp, '-use_template', 1, '-use_timeline', 0, '-format_options', enc % (KEY, KID), inputs=ins[:1])
    for f in os.listdir(tmp):
        if f != 'manifest.mpd':
            os.replace(os.path.join(tmp, f), os.path.join(d, f))
    shutil.rmtree(tmp)
    mpd = add_protection(rd(os.path.join(d, 'manifest.mpd')), KID, KID2)
    wr(os.path.join(d, 'manifest.mpd'), mpd)
    wr(os.path.join(d, 'manifest_mpdkeys.mpd'),
       mpd.replace('<Representation ', '<Representation cenc_decryption_keys="%s=%s:%s=%s" ' % (KID, KEY, KID2, KEY2)))


# ---------------------------------------------------------------- subtitles
def vtt_time(t):
    t = max(0.0, t)
    return '%02d:%02d:%06.3f' % (int(t // 3600), int(t // 60) % 60, t % 60)


def cue(n, lang, shift=0.0):
    return '%s --> %s\n%s cue %d\n\n' % (vtt_time(n * SEG + 0.2 - shift), vtt_time(n * SEG + 1.7 - shift), lang, n)


SUBSET = ('\t\t<AdaptationSet id="%d" contentType="text" mimeType="text/vtt" lang="%s">\n'
          '\t\t\t<Representation id="sub_%s" bandwidth="256">\n%s\t\t\t</Representation>\n\t\t</AdaptationSet>\n')


def step_dsubs():
    """WebVTT as its own AdaptationSet: one file per language (BaseURL) and one file per segment (SegmentTemplate).
    TTML / stpp and wvtt in fMP4 are not built: FFmpeg has no TTML decoder, exteplayer3 does not list the codec,
    and FFmpeg cannot write wvtt samples."""
    d = fresh('dash', 'subs')
    base = rd(os.path.join(NET, 'dash', 'tpl_num', 'manifest.mpd'))
    base = base.replace('initialization="init_', 'initialization="../tpl_num/init_').replace('media="seg_', 'media="../tpl_num/seg_')
    single = seg = ''
    for i, (lang, l3) in enumerate((('en', 'eng'), ('de', 'deu'))):
        wr(os.path.join(d, 'sub_%s.vtt' % lang), 'WEBVTT\n\n' + ''.join(cue(n, lang) for n in range(NSEG)))
        for n in range(NSEG):
            wr(os.path.join(d, 'seg_%s_%d.vtt' % (lang, n + 1)), 'WEBVTT\n\n' + cue(n, lang))
        single += SUBSET % (3 + i, l3, lang, '\t\t\t\t<BaseURL>sub_%s.vtt</BaseURL>\n' % lang)
        seg += SUBSET % (3 + i, l3, lang, '\t\t\t\t<SegmentTemplate timescale="1000" duration="1920" '
                                          'media="seg_%s_$Number$.vtt" startNumber="1"/>\n' % lang)
    wr(os.path.join(d, 'subs_single.mpd'), base.replace('\t</Period>', single + '\t</Period>'))
    wr(os.path.join(d, 'subs_seg.mpd'), base.replace('\t</Period>', seg + '\t</Period>'))


# ---------------------------------------------------------------- DASH extras
def step_dextra():
    d = fresh('dash', 'extra')
    src = rd(os.path.join(NET, 'dash', 'tpl_num', 'manifest.mpd'))
    rel = src.replace('initialization="init_', 'initialization="../tpl_num/init_').replace('media="seg_', 'media="../tpl_num/seg_')
    # two periods: 10 segments (19.2 s), then 22 segments (42.24 s) that go on at segment 11
    a = rel.index('\t<Period')
    b = rel.index('\t</Period>') + len('\t</Period>\n')
    p0 = rel[a:b].replace('<Period id="0" start="PT0.0S">', '<Period id="0" start="PT0.0S" duration="PT19.2S">')
    p1 = rel[a:b].replace('<Period id="0" start="PT0.0S">', '<Period id="1" start="PT19.2S" duration="PT42.24S">')
    p1 = p1.replace('startNumber="1"', 'startNumber="11" presentationTimeOffset="19200000"')
    wr(os.path.join(d, 'multiperiod.mpd'), rel[:a] + p0 + p1 + rel[b:])
    # the same with the long period first
    q0 = rel[a:b].replace('<Period id="0" start="PT0.0S">', '<Period id="0" start="PT0.0S" duration="PT42.24S">')
    q1 = rel[a:b].replace('<Period id="0" start="PT0.0S">', '<Period id="1" start="PT42.24S" duration="PT19.2S">')
    q1 = q1.replace('startNumber="1"', 'startNumber="23" presentationTimeOffset="42240000"')
    wr(os.path.join(d, 'multiperiod_longfirst.mpd'), rel[:a] + q0 + q1 + rel[b:])
    # BaseURL: absolute at MPD level, relative at Period level
    wr(os.path.join(d, 'baseurl_abs.mpd'), src.replace('\t<Period', '\t<BaseURL>%s/f/net/dash/tpl_num/</BaseURL>\n\t<Period' % SRV, 1))
    wr(os.path.join(d, 'baseurl_rel.mpd'), src.replace('<Period id="0" start="PT0.0S">',
                                                      '<Period id="0" start="PT0.0S">\n\t\t<BaseURL>../tpl_num/</BaseURL>', 1))
    # single file per representation, SegmentBase with the sidx range (what -global_sidx writes), kept beside sf_list.mpd
    # HEVC
    d = fresh('dash', 'hevc')
    dash_mux(d, '-use_template', 1, '-use_timeline', 0, inputs=['hevc.mp4', 'a_en.aac'])


# ---------------------------------------------------------------- MPEG-TS helpers
def ts_scan(path):
    """per pid: number of packets with payload; first PTS of each pid that carries PES"""
    data = open(path, 'rb').read()
    cnt, first = {}, {}
    for i in range(0, len(data) - 187, 188):
        if data[i] != 0x47:
            raise SystemExit('lost TS sync in ' + path)
        pid = ((data[i + 1] & 0x1f) << 8) | data[i + 2]
        afc = (data[i + 3] >> 4) & 3
        if not afc & 1:
            continue
        cnt[pid] = cnt.get(pid, 0) + 1
        if data[i + 1] & 0x40 and pid not in first:
            p = i + 4 + ((1 + data[i + 4]) if afc & 2 else 0)
            if data[p:p + 3] == b'\x00\x00\x01' and data[p + 7] & 0x80:
                b = data[p + 9:p + 14]
                first[pid] = ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1
    return cnt, first


def hls_mux(outdir, inputs, *opts, name='seg_%d.ts', pl='index.m3u8', check=True):
    a = []
    for i in inputs:
        a += ['-i', os.path.join(SRC, i)]
    for i in range(len(inputs)):
        a += ['-map', '%d:0' % i]
    ff(*a, '-c', 'copy', '-f', 'hls', '-hls_time', SEG, '-hls_list_size', 0, '-hls_playlist_type', 'vod',
       *opts, '-hls_segment_filename', name, pl, cwd=outdir)
    txt = rd(os.path.join(outdir, pl))
    if check and (txt.count('#EXTINF:1.920000,') != NSEG or txt.count('#EXTINF') != NSEG):
        print('HLS GRID MISMATCH in', outdir)
    return txt


def ts_index(d):
    tot, first = {}, None
    for n in range(NSEG):
        cnt, f = ts_scan(os.path.join(d, 'seg_%d.ts' % n))
        if n == 0:
            first = f
        for pid, c in cnt.items():
            tot[pid] = tot.get(pid, 0) + c
    return {'dir': os.path.relpath(d, OUT).replace('\\', '/'), 'pcc': {str(k): v % 16 for k, v in sorted(tot.items())},
            'first_pts': min(first.values()), 'pts': {str(k): v for k, v in sorted(first.items())}}


def byteranges(pl):
    """[(length, offset)] of a single-file playlist"""
    out = []
    for line in pl.split('\n'):
        if line.startswith('#EXT-X-BYTERANGE:'):
            ln, off = line.split(':')[1].split('@')
            out.append((int(ln), int(off)))
    return out


def step_hls():
    H = os.path.join(NET, 'hls')
    sets = {}
    for name, ins, opts in (('ts_av', ['v1.mp4', 'a_en.aac'], ()), ('ts_v', ['v1.mp4'], ()),
                            ('ts_aen', ['a_en.aac'], ()), ('ts_ade', ['a_de.aac'], ()),
                            ('ts_b', ['vb.mp4', 'a_b.aac'], ('-output_ts_offset', 1000))):
        d = fresh('hls', name)
        hls_mux(d, ins, *opts)
        sets[name] = ts_index(d)
    hdr = '#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-INDEPENDENT-SEGMENTS\n'
    aud = ('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",DEFAULT=YES,AUTOSELECT=YES%s\n'
           '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Deutsch",LANGUAGE="de",DEFAULT=NO,AUTOSELECT=YES,URI="%s"\n')
    # audio separate from the video / audio muxed into the video segments (English without URI = in the variant)
    wr(os.path.join(H, 'master_sep.m3u8'), hdr + aud % (',URI="ts_aen/index.m3u8"', 'ts_ade/index.m3u8') +
       '#EXT-X-STREAM-INF:BANDWIDTH=750000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2",AUDIO="aud"\nts_v/index.m3u8\n')
    wr(os.path.join(H, 'master_muxed.m3u8'), hdr + aud % ('', 'ts_ade/index.m3u8') +
       '#EXT-X-STREAM-INF:BANDWIDTH=750000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2",AUDIO="aud"\nts_av/index.m3u8\n')

    # --- discontinuity, VOD: A 0..4 | B 0..4 (other resolution, 660 Hz, timestamps +1000 s) | A 5..9
    d = fresh('hls', 'disc')
    def disc_pl(tag, seq=None, br=None):
        t = '#EXTM3U\n#EXT-X-VERSION:%d\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n' % (4 if br else 3)
        if seq is not None:
            t += '#EXT-X-DISCONTINUITY-SEQUENCE:%d\n' % seq
        t += '#EXT-X-PLAYLIST-TYPE:VOD\n'
        for k, (s, rng) in enumerate((('av', range(0, 5)), ('b', range(0, 5)), ('av', range(5, 10)))):
            if k and tag:
                t += '#EXT-X-DISCONTINUITY\n'
            for n in rng:
                t += '#EXTINF:1.920000,\n'
                if br:
                    t += '#EXT-X-BYTERANGE:%d@%d\n%s.ts\n' % (br[s][n][0], br[s][n][1], s)
                else:
                    t += '../ts_%s/seg_%d.ts\n' % (s, n)
        return t + '#EXT-X-ENDLIST\n'
    wr(os.path.join(d, 'vod_disc.m3u8'), disc_pl(True))
    wr(os.path.join(d, 'vod_disc_seq.m3u8'), disc_pl(True, seq=7))
    wr(os.path.join(d, 'vod_notag.m3u8'), disc_pl(False))

    # --- byte ranges: one .ts per stream, also across a discontinuity; fMP4 single file with EXT-X-MAP byte range
    d = fresh('hls', 'br')
    br = {}
    br['av'] = byteranges(hls_mux(d, ['v1.mp4', 'a_en.aac'], '-hls_flags', 'single_file', name='av.ts', pl='av.m3u8'))
    br['b'] = byteranges(hls_mux(d, ['vb.mp4', 'a_b.aac'], '-output_ts_offset', 1000, '-hls_flags', 'single_file',
                                 name='b.ts', pl='b.m3u8'))
    wr(os.path.join(d, 'disc.m3u8'), disc_pl(True, br=br))
    sf = rd(os.path.join(NET, 'dash', 'single', 'sf_list.mpd'))
    rep0 = sf[sf.index('<BaseURL>rep_0.mp4'):sf.index('</Representation>')]
    import re
    init = re.search(r'Initialization range="(\d+)-(\d+)"', rep0)
    t = '#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n'
    t += '#EXT-X-MAP:URI="../../dash/single/rep_0.mp4",BYTERANGE="%d@%s"\n' % (int(init.group(2)) + 1, init.group(1))
    for m in re.finditer(r'mediaRange="(\d+)-(\d+)"', rep0):
        t += '#EXTINF:1.920000,\n#EXT-X-BYTERANGE:%d@%s\n../../dash/single/rep_0.mp4\n' % (int(m.group(2)) - int(m.group(1)) + 1, m.group(1))
    wr(os.path.join(d, 'fmp4_single.m3u8'), t + '#EXT-X-ENDLIST\n')

    # --- AES-128 (whole segment CBC, IV = media sequence number)
    d = fresh('hls', 'aes')
    with open(os.path.join(d, 'aes.key'), 'wb') as f:
        f.write(AESKEY)
    wr(os.path.join(d, 'keyinfo.txt'), 'aes.key\naes.key\n')
    pl = hls_mux(d, ['v1.mp4', 'a_en.aac'], '-hls_key_info_file', 'keyinfo.txt')
    os.remove(os.path.join(d, 'keyinfo.txt'))
    wr(os.path.join(d, 'index_keyhost.m3u8'), pl.replace('URI="aes.key"', 'URI="http://keyhost.invalid/k/aes.key"'))
    wr(os.path.join(d, 'index_direct.m3u8'), pl.replace('URI="aes.key"', 'URI="key://%s"' % AESKEY.hex()))
    wr(os.path.join(d, 'index_badkey.m3u8'), pl.replace('URI="aes.key"', 'URI="missing.key"'))

    # --- fMP4 / CMAF over the DASH segments (EXT-X-MAP), separate audio renditions
    d = fresh('hls', 'cmaf')
    def cmaf_pl(rep, base='../../dash/tpl_num/', key=''):
        t = '#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:1\n#EXT-X-PLAYLIST-TYPE:VOD\n' + key
        t += '#EXT-X-MAP:URI="%sinit_%d.mp4"\n' % (base, rep)
        for n in range(1, NSEG + 1):
            t += '#EXTINF:1.920000,\n%sseg_%d_%d.m4s\n' % (base, rep, n)
        return t + '#EXT-X-ENDLIST\n'
    for rep in range(5):
        wr(os.path.join(d, 'rep_%d.m3u8' % rep), cmaf_pl(rep))
    wr(os.path.join(d, 'master.m3u8'), hdr + aud % (',URI="rep_3.m3u8"', 'rep_4.m3u8') +
       '#EXT-X-STREAM-INF:BANDWIDTH=700000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2",AUDIO="aud"\nrep_0.m3u8\n'
       '#EXT-X-STREAM-INF:BANDWIDTH=450000,RESOLUTION=480x270,CODECS="avc1.640015,mp4a.40.2",AUDIO="aud"\nrep_1.m3u8\n'
       '#EXT-X-STREAM-INF:BANDWIDTH=250000,RESOLUTION=320x180,CODECS="avc1.64000c,mp4a.40.2",AUDIO="aud"\nrep_2.m3u8\n')
    # HEVC in fMP4
    wr(os.path.join(d, 'hevc_v.m3u8'), cmaf_pl(0, '../../dash/hevc/'))
    wr(os.path.join(d, 'hevc_a.m3u8'), cmaf_pl(1, '../../dash/hevc/'))
    wr(os.path.join(d, 'hevc.m3u8'), hdr +
       '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",DEFAULT=YES,AUTOSELECT=YES,URI="hevc_a.m3u8"\n'
       '#EXT-X-STREAM-INF:BANDWIDTH=600000,RESOLUTION=640x360,CODECS="hvc1.1.6.L90.90,mp4a.40.2",AUDIO="aud"\nhevc_v.m3u8\n')
    # cenc encrypted fMP4 (the DASH cenc1 set) as SAMPLE-AES HLS: key file, key://, keys://kid=key
    with open(os.path.join(d, 'cenc.key'), 'wb') as f:
        f.write(bytes.fromhex(KEY))
    for nm, uri in (('file', 'cenc.key'), ('direct', 'key://' + KEY), ('kid', 'keys://%s=%s' % (KID, KEY))):
        k = '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="%s"\n' % uri
        wr(os.path.join(d, 'cenc_%s_v.m3u8' % nm), cmaf_pl(0, '../../dash/cenc1/', k))
        wr(os.path.join(d, 'cenc_%s_a.m3u8' % nm), cmaf_pl(1, '../../dash/cenc1/', k))
        wr(os.path.join(d, 'cenc_%s.m3u8' % nm), hdr +
           '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",DEFAULT=YES,AUTOSELECT=YES,URI="cenc_%s_a.m3u8"\n'
           '#EXT-X-STREAM-INF:BANDWIDTH=450000,RESOLUTION=480x270,CODECS="avc1.640015,mp4a.40.2",AUDIO="aud"\ncenc_%s_v.m3u8\n' % (nm, nm))

    # --- WebVTT renditions. en: X-TIMESTAMP-MAP of the first video pts, STYLE / REGION / NOTE blocks, cue ids and
    # cue settings. de: no STYLE; the map is shifted by 10 s (cue 00:00 = video start + 10 s), so a demuxer that
    # ignores X-TIMESTAMP-MAP shows the German cues 10 s early. st ("Stray"): like en plus a block without a timing
    # line before the cue (stock FFmpeg 9.0.2 then drops every cue of the file).
    d = fresh('hls', 'vtt')
    pts0 = sets['ts_av']['first_pts']
    style = ('STYLE\n::cue {\n  color: yellow;\n}\n\nREGION\nid:bottom\nwidth:80%\nlines:3\nregionanchor:50%,100%\n'
             'viewportanchor:50%,90%\n\nNOTE a comment block\nover two lines\n\n')
    for lang in ('en', 'de', 'st'):
        t = '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n'
        for n in range(NSEG):
            t += '#EXTINF:1.920000,\n%s_%d.vtt\n' % (lang, n)
            if lang in ('en', 'st'):
                body = 'WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:%d,LOCAL:00:00:00.000\n\n' % pts0 + style
                if lang == 'st':
                    body += 'stray block without timing\n\n'
                body += 'c%d\n' % n + cue(n, lang).replace('\n', ' region:bottom align:center\n', 1)
            else:
                body = 'WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:%d\n\n' % (pts0 + 900000)
                body += cue(n, lang, 10.0) if n * SEG + 0.2 >= 10.0 else ''
            wr(os.path.join(d, '%s_%d.vtt' % (lang, n)), body)
        wr(os.path.join(d, 'sub_%s.m3u8' % lang), t + '#EXT-X-ENDLIST\n')
    wr(os.path.join(d, 'master.m3u8'), hdr +
       '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",DEFAULT=YES,AUTOSELECT=YES,FORCED=NO,URI="sub_en.m3u8"\n'
       '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Deutsch",LANGUAGE="de",DEFAULT=NO,AUTOSELECT=YES,FORCED=NO,URI="sub_de.m3u8"\n'
       '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Stray",LANGUAGE="fr",DEFAULT=NO,AUTOSELECT=YES,FORCED=NO,URI="sub_st.m3u8"\n'
       '#EXT-X-STREAM-INF:BANDWIDTH=750000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2",SUBTITLES="subs"\n../ts_av/index.m3u8\n')
    os.makedirs(os.path.join(NET, 'live'), exist_ok=True)
    wr(os.path.join(NET, 'live', 'ts.json'), json.dumps(sets, indent=1))


# ---------------------------------------------------------------- index for the live endpoints
def step_index():
    """net/live/index.json: what ep3http.py needs to loop the segment sets (/dlive, /hlive)"""
    import re
    d = os.path.join(NET, 'dash', 'tpl_num')
    mpd = rd(os.path.join(d, 'manifest.mpd'))
    reps = {}
    for m in re.finditer(r'<Representation id="(\d+)" mimeType="(\w+)/mp4" codecs="([^"]+)" bandwidth="(\d+)"([^>]*)>', mpd):
        rid = m.group(1)
        reps[rid] = {'type': m.group(2), 'codecs': m.group(3), 'bw': int(m.group(4)), 'attrs': m.group(5).strip(),
                     'timescale': seg_info(os.path.join(d, 'init_%s.mp4' % rid))['timescale']}
    idx = {'segdur_ms': int(round(SEG * 1000)), 'nseg': NSEG,
           'fmp4': {'dir': 'net/dash/tpl_num', 'reps': reps, 'lang': {'3': 'eng', '4': 'deu'}},
           'ts': json.loads(rd(os.path.join(NET, 'live', 'ts.json')))}
    wr(os.path.join(NET, 'live', 'index.json'), json.dumps(idx, indent=1))
    os.remove(os.path.join(NET, 'live', 'ts.json'))
    total = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(NET) for f in fs)
    print('media4/net: %.1f MB in %d files' % (total / 1e6, sum(len(fs) for _, _, fs in os.walk(NET))))


STEPS = {}
if __name__ == '__main__':
    STEPS.update({k[5:]: v for k, v in list(globals().items()) if k.startswith('step_')})
    todo = sys.argv[1:] or ['src', 'dash', 'cenc', 'dsubs', 'dextra', 'hls', 'index']
    os.makedirs(NET, exist_ok=True)
    for s in todo:
        if s in STEPS:
            print('== ' + s)
            STEPS[s]()
