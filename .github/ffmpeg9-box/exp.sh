#!/bin/bash
# The network cases of the receiver test that differed between FFmpeg 6.1 and
# the series, read with boxplay (the calls of skyjet18/exteplayer3 without
# its outputs) from the test server of the receiver test.
# usage: exp.sh <label> <prefix of the FFmpeg build> <media root> <out dir> <port>
set -u
L=$1; P=$2; ROOT=$3; OUT=$4; PORT=$5
HERE=$(cd "$(dirname "$0")" && pwd)
export LD_LIBRARY_PATH=$P/lib
mkdir -p "$OUT"
gcc -O1 -g -Wall -o "$OUT/boxplay_$L" "$HERE/boxplay.c" \
    $(PKG_CONFIG_PATH=$P/lib/pkgconfig pkg-config --cflags --libs libavformat libavcodec libavutil) -lm || exit 1
: > "$OUT/srv_$L.log"
EP3_ROOT=$ROOT python3 "$HERE/ep3http.py" "$PORT" "$OUT/srv_$L.log" 2> /dev/null &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 1
H=http://127.0.0.1:$PORT

# run <name> <url> [options] -- commands; PRE="valgrind ..." runs it under a tool
run() {
  name=$1; shift
  echo "== $L $name: $*"
  timeout -k 5 "${TMO:-90}" ${PRE:-} "$OUT/boxplay_$L" "$@" > "$OUT/${name}__$L.txt" 2> "$OUT/${name}__$L.err"
  echo "rc=$?" >> "$OUT/${name}__$L.txt"
  cat "$OUT/${name}__$L.txt"
  grep -v 'Opening \|HTTP error 404\|^\[http @' "$OUT/${name}__$L.err" | tail -${ERRLINES:-6}
}
# requests of a case: lines of the server log since the mark
mark() { MARK=$(wc -l < "$OUT/srv_$L.log"); }
since() { tail -n +$((MARK + 1)) "$OUT/srv_$L.log"; }

# 1. DASH VOD: the stream is read to its end (a player buffers ahead), then
#    seeks to 28.8 s, 18.8 s and 50 s, then to the end again
for c in "tpl_tl tpl_tl/manifest.mpd" "seglist seglist/manifest.mpd" "sf_base single_sidx/sf_base.mpd" "tpl_num tpl_num/manifest.mpd"; do
  set -- $c
  mark
  run dash_$1 $H/f/net/dash/$2 -- d e s28.8 r40 s18.8 r40 s50 r40 e w1 d
  echo "requests: $(since | grep -c ' GET ')  404: $(since | grep -c ' 404')"
  # the same seeks before the end of the stream was reached
  run dash_$1_early $H/f/net/dash/$2 -- t8 s28.8 r40 s18.8 r40 s50 r40 e
done

# 4. HLS VOD with two #EXT-X-DISCONTINUITY (the middle part 1000 s later):
#    seeks into the middle part, the last part and the first one, after the
#    end of the stream was reached and before
run hls_disc_seek $H/f/net/hls/disc/vod_disc.m3u8 -- d e s14 r40 s24 r40 s3 r40 e
run hls_disc_seek_early $H/f/net/hls/disc/vod_disc.m3u8 -- t5 s14 r40 s24 r40 s3 r40 e

# 5. hls_audio_default_only (exteplayer3 -D 1)
run hls_D0 $H/f/net/hls/master_sep.m3u8 -- r20
run hls_D1 $H/f/net/hls/master_sep.m3u8 hls_audio_default_only=1 -- r20

# 3. live DASH, SegmentTemplate with a duration, no suggestedPresentationDelay
mark
TMO=70 run dlive_num_nospd "$H/dlive/manifest.mpd?mode=num&mup=4&tsb=30&s=$L" -- t20 d
echo "segment requests: $(since | grep -c 'DLIVE seg')  before the segment was there: $(since | grep -c 'EARLY-404')"

# 2. live HLS that gets #EXT-X-ENDLIST after 30 s; the player goes on calling
#    av_read_frame() for some seconds after the end
mark
TMO=150 PRE="valgrind -q --error-exitcode=99 --num-callers=20" ERRLINES=60 \
  run hlive_end "$H/hlive/index.m3u8?win=6&end=30&s=$L" -- d e d w9 d
since | tail -4

# live DASH that ends after 30 s (ends on neither build on the receiver)
mark
TMO=100 run dlive_end "$H/dlive/manifest.mpd?mode=tl&mup=4&tsb=30&end=30&s=$L" -- d e d w3
since | tail -3
exit 0
