#!/bin/bash
# Stages of the box build, called one after the other by
# .github/workflows/ffmpeg9-boxbuild.yml:
#   sysroot toolchain ffmpeg players bundle qemu
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
W=${GITHUB_WORKSPACE:-$PWD}
. "$HERE/pins"

SR=$W/sysroot                 # the feed's packages, unpacked
PFX=/opt/ep3ff9               # where the series is installed inside the sysroot
OUT=$W/out
B=$OUT/ep3ff9                 # the bundle
TC=$W/tc
BOXCC=$TC/boxcc
CROSS=arm-linux-gnueabihf-
FEEDS="$FEED/$FEED_ARCH,$FEED/all"

# what the image passes to every compiler call (the cc of the image's
# FFmpeg configure line), and its LDFLAGS
OE_CC_FLAGS="-mfpu=neon-vfpv4 -mfloat-abi=hard -mcpu=cortex-a15 -D_TIME_BITS=64 -D_FILE_OFFSET_BITS=64 -fstack-protector-strong -Os -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=2 -Wformat -Wformat-security -Werror=format-security"
OE_LDFLAGS="-Wl,-O1 -Wl,--hash-style=gnu -Wl,--as-needed -Wl,-z,relro,-z,now"
FF_LINK="-L$SR$PFX/lib -Wl,-rpath-link,$SR$PFX/lib"

export PKG_CONFIG_SYSROOT_DIR=$SR
export PKG_CONFIG_LIBDIR=$SR$PFX/lib/pkgconfig:$SR/usr/lib/pkgconfig:$SR/usr/share/pkgconfig

Q="timeout -k 10 ${TMO:-300} qemu-arm -cpu cortex-a15 -L $SR"
PTS_ASSERT='_Static_assert(AUDIO_GET_PTS == 0x80086f13, "AUDIO_GET_PTS is not _IOR(o, 19, __u64)");'

notice() { echo "::notice::$*"; }

stage_sysroot() {
  python3 "$HERE/mksysroot.py" --list "$W/sysroot.list" "$SR" "$FEEDS" \
    libc6 libc6-dev linux-libc-headers-dev libgcc1 libgcc-s-dev libstdc++6 \
    libz-dev bzip2-dev xz-dev openssl-dev libxml2-dev libbluray-dev libudfread-dev \
    rtmpdump-dev libgmp-dev libfreetype-dev libsrt-dev libtheora-dev libx264-dev x265-dev \
    libasound-dev libxcb-dev libxv-dev libx11-dev libxext-dev
  cp "$W/sysroot.list" "$W/sysroot-build.list"
  wc -l "$W/sysroot.list"
  du -sh "$SR"
  ls "$SR" "$SR/lib" | head -60
}

stage_toolchain() {
  mkdir -p "$TC"
  GCCDIR=$(dirname "$(${CROSS}gcc -print-libgcc-file-name)")
  inc="-isystem $GCCDIR/include"
  if [ -d "$GCCDIR/include-fixed" ]; then inc="$inc -isystem $GCCDIR/include-fixed"; fi
  # The Ubuntu cross compiler with nothing of Ubuntu's armhf C library:
  # headers, start files and libraries come from the feed sysroot; of the
  # compiler itself only its own headers, crtbegin/crtend and libgcc.a.
  cat > "$BOXCC" <<EOF
#!/bin/sh
exec ${CROSS}gcc --sysroot=$SR -nostdinc $inc -isystem $SR/usr/include \\
  -B$SR/usr/lib/ -L$SR/usr/lib -L$SR/lib -Wl,-rpath-link,$SR/usr/lib -Wl,-rpath-link,$SR/lib \\
  -marm $OE_CC_FLAGS "\$@"
EOF
  chmod +x "$BOXCC"
  cat "$BOXCC"
  ${CROSS}gcc --version | head -1
  ${CROSS}ld --version | head -1
  qemu-arm --version | head -1
  cd "$TC"
  cat > hello.c <<'EOF'
#include <stdio.h>
#include <time.h>
#include <pthread.h>
#include <sys/ioctl.h>
#include <gnu/libc-version.h>
#include <linux/version.h>
#include <linux/dvb/video.h>
#include <linux/dvb/audio.h>
_Static_assert(sizeof(time_t) == 8, "time_t has 64 bits, as in the image");
_Static_assert(sizeof(off_t) == 8, "off_t has 64 bits");
_Static_assert(AUDIO_GET_PTS == 0x80086f13, "AUDIO_GET_PTS");
_Static_assert(VIDEO_GET_PTS == 0x80086f39, "VIDEO_GET_PTS");
static void *thread(void *arg) { return arg; }
int main(void)
{
    pthread_t t;
    void *r = NULL;
    pthread_create(&t, NULL, thread, (void *)main);
    pthread_join(t, &r);
    printf("glibc %s at run time, %d.%d headers, kernel headers %d.%d, time_t %d bytes, AUDIO_GET_PTS 0x%lx, thread %s\n",
           gnu_get_libc_version(), __GLIBC__, __GLIBC_MINOR__, LINUX_VERSION_CODE >> 16, (LINUX_VERSION_CODE >> 8) & 255,
           (int)sizeof(time_t), (unsigned long)AUDIO_GET_PTS, r == (void *)main ? "ok" : "failed");
    return time(NULL) > 0 ? 0 : 1;
}
EOF
  "$BOXCC" -H -o hello hello.c -lpthread -Wl,-Map=hello.map 2> hello.H || { cat hello.H; exit 1; }
  # every header and every file the linker loaded is from the sysroot or
  # from the compiler's own directory
  grep -E '^\.+ ' hello.H | awk '{print $2}' | sort -u > hello.headers
  grep '^LOAD /' hello.map | awk '{print $2}' | sort -u > hello.loaded
  cat hello.loaded
  bad=$(cat hello.headers hello.loaded | grep -v "^$SR/" | grep -v "^$GCCDIR/" | grep -v '^/tmp/' || true)
  if [ -n "$bad" ]; then echo "files from outside the sysroot:"; echo "$bad"; exit 1; fi
  grep -c "^$SR/" hello.headers
  ${CROSS}readelf -hd hello | grep -E 'Flags|Type|NEEDED'
  ${CROSS}readelf -A hello | grep -E 'Tag_CPU_arch|Tag_FP_arch|Tag_Advanced_SIMD|Tag_ABI_VFP_args|Tag_ARM_ISA|Tag_THUMB_ISA' || true
  $Q ./hello | tee hello.out
  grep -q '^glibc 2.44 at run time, 2.44 headers' hello.out
  notice "toolchain: $(${CROSS}gcc --version | head -1 | sed 's/ *Copyright.*//'), sysroot of $(grep -c . "$W/sysroot.list") feed packages; $(cat hello.out)"
}

stage_ffmpeg() {
  test "$(git -C "$W/ffsrc" rev-parse HEAD)" = "$FFMPEG_COMMIT"
  mapfile -t OPTS < <(grep -v '^#' "$HERE/ffmpeg-image-options" | grep .)
  FULL=(--cross-prefix=$CROSS "--cc=$BOXCC" "--ld=$BOXCC" "--extra-cflags=-Os -pipe"
        "--extra-ldflags=$OE_LDFLAGS" "--sysroot=$SR" "--prefix=$PFX" "--libdir=$PFX/lib"
        "--shlibdir=$PFX/lib" "--datadir=$PFX/share/ffmpeg" "${OPTS[@]}")
  mkdir -p "$W/ffbuild" && cd "$W/ffbuild"
  printf '%q ' configure "${FULL[@]}" > "$W/ffmpeg-configure-line.txt"
  echo >> "$W/ffmpeg-configure-line.txt"
  ../ffsrc/configure "${FULL[@]}" || { tail -80 ffbuild/config.log; exit 1; }
  grep -E '^(EXTRALIBS|CFLAGS|LDFLAGS|CC|LD)[-a-z]*=' ffbuild/config.mak
  make -j"$(nproc)"
  make install DESTDIR="$SR" > install.log
  ls -l "$SR$PFX/lib" "$SR$PFX/bin"
}

stage_players() {
  mkdir -p "$OUT/players"
  FFINC=$(pkg-config --cflags libavformat libavcodec libavutil libswresample libswscale)
  echo "FFmpeg headers: $FFINC"
  case "$FFINC" in *"$SR$PFX/include"*) ;; *) echo "not the headers of the series"; exit 1;; esac

  # --- skyjet18/exteplayer3: the compiler call of its exteplayer3_git.bb.
  # The file list of the .bb is older than the tree (it names h263.c and
  # wma.c, which are gone, and lacks tools/debug.c and others), so the
  # list is the one of its tests/build.sh with the real output
  # (output/linuxdvb_mipsel.c) in place of the host test one, plus xiph.c.
  cd "$W/skyjet"
  test "$(git rev-parse HEAD)" = "$SKY_COMMIT"
  grep -q 'EPLAYER3_EXTENDED.*, 186);' main/exteplayer.c
  SRC="main/exteplayer.c tools/debug.c tools/strbuffer.c tools/pcm_export.c
       container/container.c container/container_ffmpeg.c
       manager/manager.c manager/audio.c manager/video.c manager/subtitle.c
       playback/playback.c output/output.c output/output_subtitle.c output/graphic_subtitle.c
       output/linuxdvb_mipsel.c output/linuxdvb_buffering.c
       output/writer/common/pes.c output/writer/common/misc.c output/writer/common/writer.c
       $(echo output/writer/mipsel/*.c) $(echo external/ffmpeg/src/*.c)
       $(echo external/flv2mpeg4/src/*.c) external/plugins/src/png.c"
  # every file the .bb names for a non-sh4 box and that still exists is in the list
  for f in $(grep -o '[a-z0-9_/]*\.c' exteplayer3_git.bb | grep -v sh4); do
    if [ -f "$f" ]; then echo " $SRC " | tr '\n' ' ' | grep -q " $f " || { echo "not in the list: $f"; exit 1; }
    else echo "named by the .bb, not in the tree: $f"; fi
  done
  echo "$PTS_ASSERT" >> output/linuxdvb_mipsel.c
  # shellcheck disable=SC2086
  "$BOXCC" $SRC -D_FILE_OFFSET_BITS=64 -D_LARGEFILE64_SOURCE -D_LARGEFILE_SOURCE -DHAVE_FLV2MPEG4_CONVERTER \
    -Iinclude -Iexternal -Iexternal/flv2mpeg4 $FFINC \
    -lpthread -lavformat -lavcodec -lavutil -lswresample -lswscale -lm -o "$OUT/players/ep3_sky186_ff9" \
    $OE_LDFLAGS $FF_LINK -Wl,--disable-new-dtags "-Wl,-rpath,\$ORIGIN/lib" 2> "$W/sky-build.log" || { cat "$W/sky-build.log"; exit 1; }
  grep -c 'warning:' "$W/sky-build.log" || true
  git checkout -- output/linuxdvb_mipsel.c

  # --- oe-alliance/exteplayer3: its autotools build, cross
  cd "$W/oea"
  test "$(git rev-parse HEAD)" = "$OEA_COMMIT"
  echo "$PTS_ASSERT" >> output/linuxdvb_mipsel.c
  ./autogen.sh > "$W/oea-autogen.log" 2>&1 || { cat "$W/oea-autogen.log"; exit 1; }
  ./configure --build=x86_64-pc-linux-gnu --host=arm-linux-gnueabihf CC="$BOXCC" CFLAGS="-Os -pipe" \
    LDFLAGS="$OE_LDFLAGS $FF_LINK" || { tail -60 config.log; exit 1; }
  grep -n 'AUDIO_GET_PTS' config.h
  make -j"$(nproc)" V=1 LDFLAGS="$OE_LDFLAGS $FF_LINK -Wl,--disable-new-dtags -Wl,-rpath,'\$\$ORIGIN/lib'" \
    > "$W/oea-build.log" 2>&1 || { tail -80 "$W/oea-build.log"; exit 1; }
  grep -c 'warning:' "$W/oea-build.log" || true
  grep -A3 'AUDIO_GET_PTS' "$W/oea-build.log" | head -12 || true
  tail -3 "$W/oea-build.log"
  cp exteplayer3 "$OUT/players/ep3_oea_ff9"
  git checkout -- output/linuxdvb_mipsel.c
  ls -l "$OUT/players"
}

# NEEDED, RPATH and the like of one file of the bundle; fails if the search
# path is not the private one or a needed library is neither in the bundle
# nor in the feed sysroot.
elf_report() {
  local f=$1 want=$2 d rpath runpath n where
  d=$(${CROSS}readelf -d "$f")
  rpath=$(echo "$d" | sed -n 's/.*(RPATH).*\[\(.*\)\]/\1/p')
  runpath=$(echo "$d" | sed -n 's/.*(RUNPATH).*\[\(.*\)\]/\1/p')
  echo "$(basename "$f")"
  echo "  SONAME   $(echo "$d" | sed -n 's/.*(SONAME).*\[\(.*\)\]/\1/p')"
  echo "  RPATH    $rpath"
  echo "  RUNPATH  ${runpath:-(none)}"
  for n in $(echo "$d" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'); do
    if [ -e "$B/lib/$n" ]; then where="bundle lib/$n"
    elif [ -e "$SR/usr/lib/$n" ]; then where="box /usr/lib/$n -> $(basename "$(readlink -f "$SR/usr/lib/$n")")"
    elif [ -e "$SR/lib/$n" ]; then where="box /lib/$n -> $(basename "$(readlink -f "$SR/lib/$n")")"
    else echo "  NEEDED   $n  NOT FOUND"; return 1
    fi
    echo "  NEEDED   $n  ($where)"
  done
  echo "  newest glibc symbol version: $(${CROSS}objdump -T "$f" | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)"
  test "$rpath" = "$want" || { echo "RPATH is not $want"; return 1; }
  test -z "$runpath" || { echo "has a RUNPATH"; return 1; }
}

stage_bundle() {
  rm -rf "$B" && mkdir -p "$B/lib"
  for l in avutil swresample swscale avcodec avformat avfilter avdevice; do
    f=$(readlink -f "$SR$PFX/lib/lib$l.so")
    so=$(${CROSS}readelf -d "$f" | sed -n 's/.*(SONAME).*\[\(.*\)\]/\1/p')
    echo "lib$l: $(basename "$f") as $so"
    cp "$f" "$B/lib/$so"
  done
  cp "$SR$PFX/bin/ffmpeg" "$SR$PFX/bin/ffprobe" "$OUT/players/ep3_sky186_ff9" "$OUT/players/ep3_oea_ff9" "$B/"
  ${CROSS}strip --strip-unneeded "$B"/lib/* "$B/ffmpeg" "$B/ffprobe" "$B/ep3_sky186_ff9" "$B/ep3_oea_ff9"
  # the FFmpeg libraries find each other and the tools find them, whatever
  # the environment says: DT_RPATH (searched before LD_LIBRARY_PATH and
  # /usr/lib), not DT_RUNPATH. The players got theirs from the linker.
  for f in "$B"/lib/*; do patchelf --force-rpath --set-rpath '$ORIGIN' "$f"; done
  patchelf --force-rpath --set-rpath '$ORIGIN/lib' "$B/ffmpeg"
  patchelf --force-rpath --set-rpath '$ORIGIN/lib' "$B/ffprobe"
  chmod 755 "$B"/lib/* "$B/ffmpeg" "$B/ffprobe" "$B/ep3_sky186_ff9" "$B/ep3_oea_ff9"

  ok=1
  : > "$W/elf-report.txt"
  for f in "$B"/lib/*; do elf_report "$f" '$ORIGIN' >> "$W/elf-report.txt" || ok=0; done
  for f in ffmpeg ffprobe ep3_sky186_ff9 ep3_oea_ff9; do elf_report "$B/$f" '$ORIGIN/lib' >> "$W/elf-report.txt" || ok=0; done
  cat "$W/elf-report.txt"
  test $ok = 1
  # nothing needs a newer C library than the box has
  if grep 'newest glibc' "$W/elf-report.txt" | grep -vE 'GLIBC_2\.([0-9]|[1-3][0-9]|4[0-4])$'; then
    echo "needs a newer glibc than 2.44"; exit 1
  fi
  # the libraries are the series: a message of its HLS patches is in them
  grep -c 'Timeline anchor' "$B/lib/libavformat.so.63"

  cp "$HERE/README.txt" "$B/README.txt"
  {
    echo "ep3ff9 - FFmpeg 9 patch series and exteplayer3 for a Vu+ Uno 4K SE (openATV 8.0, ARMv7 hard-float)"
    echo "built $(date -u +%Y-%m-%dT%H:%M:%SZ) by ${GITHUB_SERVER_URL:-}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-} (commit ${GITHUB_SHA:-})"
    echo
    echo "== sources"
    echo "FFmpeg       Masta2002/FFmpeg release/9.0-patched $FFMPEG_COMMIT"
    echo "             $(git -C "$W/ffsrc" log -1 --format='%ad %s' --date=short)"
    echo "ep3_sky186_ff9  skyjet18/exteplayer3 master $SKY_COMMIT"
    echo "             $(git -C "$W/skyjet" log -1 --format='%ad %s' --date=short)"
    echo "ep3_oea_ff9  oe-alliance/exteplayer3 master $OEA_COMMIT"
    echo "             $(git -C "$W/oea" log -1 --format='%ad %s' --date=short)"
    echo
    echo "== toolchain"
    ${CROSS}gcc --version | head -1
    ${CROSS}ld --version | head -1
    echo "compiler call ($BOXCC):"
    sed 1d "$BOXCC"
    echo
    echo "== FFmpeg configure line"
    cat "$W/ffmpeg-configure-line.txt"
    echo
    echo "== library versions of the series"
    grep -hE '^(Name|Version):' "$SR$PFX"/lib/pkgconfig/*.pc | paste - - | sed 's/Name: //; s/\tVersion: / /'
    echo
    echo "== files, their search path and the libraries they need"
    cat "$W/elf-report.txt"
    echo
    echo "== feed packages of the sysroot the build linked against ($FEED/$FEED_ARCH)"
    awk '{print $1, $2}' "$W/sysroot-build.list"
    echo
    echo "== sha256"
    (cd "$B" && find . -type f ! -name MANIFEST.txt | sort | xargs sha256sum)
  } > "$B/MANIFEST.txt"
  name=ep3ff9-$(echo "$FFMPEG_COMMIT" | cut -c1-10).tar.gz
  tar -C "$OUT" --owner=0 --group=0 --numeric-owner -czf "$OUT/$name" ep3ff9
  ls -l "$OUT/$name" "$B" "$B/lib"
  sha256sum "$OUT/$name" | tee "$OUT/$name.sha256"
}

# the libraries the dynamic loader initialised for a command (LD_DEBUG=libs)
# (for every library searched, the last file tried is the one that was taken)
loaded() {
  $Q -E LD_DEBUG=libs "$@" 2>&1 >/dev/null </dev/null |
    awk '/find library=/ { if (last) print last; last = "" }
         /trying file=/  { sub(/.*trying file=/, ""); last = $0 }
         END             { if (last) print last }' | sort -u || true
}

stage_qemu() {
  # The sysroot becomes more of the box: the image's own FFmpeg 9.0.2 with
  # the same SONAMEs goes into /usr/lib and /usr/bin (after the build, so
  # nothing was compiled or linked against it).
  python3 "$HERE/mksysroot.py" --list "$W/sysroot.list" "$SR" "$FEEDS" ffmpeg
  ls -l "$SR"/usr/lib/libav*.so.* "$SR"/usr/lib/libsw*.so.* "$SR/usr/bin/ffmpeg"
  mkdir -p "$W/q" && cd "$W/q"

  $Q "$SR/usr/bin/ffmpeg" -version | head -3 | tee image_version.txt
  grep -q '^ffmpeg version 9.0.2' image_version.txt
  $Q "$B/ffmpeg" -version | tee version.txt
  $Q "$B/ffprobe" -version | head -1
  if grep -q '^ffmpeg version 9.0.2 ' version.txt; then echo "that is the image's FFmpeg"; exit 1; fi
  grep -q -- '--enable-encoder=eac3' version.txt
  $Q "$B/ffmpeg" -hide_banner -encoders | grep -E ' (eac3|ac3|aac|libx264) '
  $Q "$B/ffmpeg" -hide_banner -protocols | tr '\n' ' '; echo
  $Q "$B/ffmpeg" -hide_banner -demuxers | grep -E ' (hls|dash|webvtt|matroska,webm|mpegts|mov,mp4,m4a,3gp,3g2,mj2) '
  notice "ARM ffmpeg of the bundle under qemu: $(head -1 version.txt); the image's /usr/bin/ffmpeg in the same sysroot: $(head -1 image_version.txt)"

  # which libraries each program really loads, with the image's FFmpeg
  # 9.0.2 present in /usr/lib
  for p in ffmpeg ffprobe ep3_sky186_ff9 ep3_oea_ff9; do
    args=""; if [ $p = ffmpeg ] || [ $p = ffprobe ]; then args=-version; fi
    loaded "$B/$p" $args > loaded_$p.txt
    echo "== $p loads"; cat loaded_$p.txt
    n=$(grep -cE '/lib(av|sw)[a-z]+\.so' loaded_$p.txt)
    own=$(grep -cE "^$B/lib/lib(av|sw)[a-z]+\.so" loaded_$p.txt)
    test "$n" -ge 5
    test "$n" = "$own"
    notice "$p under qemu with the image's FFmpeg 9.0.2 in /usr/lib: $own of $n FFmpeg libraries loaded from the bundle's lib/, $(grep -vcE '/lib(av|sw)[a-z]+\.so' loaded_$p.txt) other libraries from the sysroot"
  done
  # and with LD_LIBRARY_PATH pointing at /usr/lib: still the bundle's
  loaded -E LD_LIBRARY_PATH=/usr/lib "$B/ep3_sky186_ff9" > loaded_env.txt
  test "$(grep -cE '/lib(av|sw)[a-z]+\.so' loaded_env.txt)" = "$(grep -cE "^$B/lib/lib(av|sw)[a-z]+\.so" loaded_env.txt)"
  notice "with LD_LIBRARY_PATH=/usr/lib the player still loads the bundle's FFmpeg libraries (DT_RPATH)"

  # the players start: without arguments they report themselves and print the usage
  for p in ep3_sky186_ff9 ep3_oea_ff9; do
    $Q "$B/$p" > start_$p.txt 2>&1 </dev/null || true
    head -3 start_$p.txt
    grep -q 'EPLAYER3_EXTENDED' start_$p.txt
    grep -q -- '-Q' start_$p.txt
    notice "$p under qemu, no arguments: $(grep -o '{"EPLAYER3_EXTENDED.*}' start_$p.txt | head -1), usage printed ($(grep -c . start_$p.txt) lines)"
  done

  # samples, generated with the runner's own ffmpeg
  mkdir -p s/hls s/dash s/disc
  printf 'WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello\n' > s/sub.vtt
  V="-f lavfi -i testsrc=d=6:s=160x90:r=25"; A="-f lavfi -i sine=d=6"
  ffmpeg -nostdin -v error $V $A -i s/sub.vtt -map 0:v -map 1:a -map 2:s -c:v libx264 -pix_fmt yuv420p -g 25 \
    -c:a aac -c:s webvtt -f hls -hls_time 2 -hls_playlist_type vod -master_pl_name master.m3u8 \
    -var_stream_map "v:0,a:0,s:0,sgroup:subs" s/hls/hls_%v.m3u8
  ffmpeg -nostdin -v error $V $A -c:v libx264 -pix_fmt yuv420p -g 25 -c:a aac -f dash -seg_duration 2 s/dash/dash.mpd
  for i in 0 1; do
    ffmpeg -nostdin -v error -f lavfi -i testsrc=d=2:s=160x90:r=25 -f lavfi -i sine=d=2 -c:v libx264 -pix_fmt yuv420p -g 25 \
      -c:a aac -output_ts_offset $((i * 100)) -f mpegts s/disc/d$i.ts
  done
  printf '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:2.0,\nd0.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:2.0,\nd1.ts\n#EXT-X-ENDLIST\n' > s/disc/disc.m3u8
  cat s/hls/master.m3u8; ls -l s/hls s/dash s/disc

  FP="$Q $B/ffprobe"; FF="$Q $B/ffmpeg -nostdin"
  types() { $FP -v error -show_entries stream=codec_type,codec_name -of csv=p=0 "$@" | sort | tr '\n' ' '; }
  python3 "$HERE/../ffmpeg9-tests/range_server.py" 8089 > /dev/null 2>&1 &
  SRV=$!
  openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 2 -subj /CN=localhost 2> /dev/null
  python3 "$HERE/https_server.py" . 8443 cert.pem key.pem > /dev/null 2>&1 &
  SRV2=$!
  trap 'kill $SRV $SRV2 2>/dev/null || true' EXIT
  sleep 2
  for u in s/hls/master.m3u8 s/dash/dash.mpd http://127.0.0.1:8089/s/hls/master.m3u8 http://127.0.0.1:8089/s/dash/dash.mpd \
           https://127.0.0.1:8443/s/hls/master.m3u8 https://127.0.0.1:8443/s/dash/dash.mpd; do
    t=$(types "$u")
    echo "$u: $t"
    echo "$t" | grep -q 'h264,video'
    echo "$t" | grep -q 'aac,audio'
    case $u in *m3u8) echo "$t" | grep -q 'webvtt,subtitle';; esac
    # demux and decode everything (the image's configuration has no null
    # muxer: decoded video and audio into a Matroska file that is thrown away)
    $FF -v error -i "$u" -map 0:v:0 -map 0:a:0 -c:v rawvideo -c:a pcm_s16le -f matroska -y /dev/null 2> dec.err || { cat dec.err; exit 1; }
    f=$($FP -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of csv=p=0 "$u")
    test "$f" = 150
    notice "ARM ffprobe/ffmpeg of the bundle under qemu, $u: $t; $f video frames decoded, $(grep -c . dec.err || true) lines of errors"
  done
  # the subtitle rendition of the HLS sample
  $FP -v error -select_streams s:0 -show_packets -show_data s/hls/master.m3u8 | tee sub_out.txt | head -20
  grep -q '68 656c 6c6f' sub_out.txt

  # HLS with #EXT-X-DISCONTINUITY (patch 0050 of the series): the second
  # file starts 100 s later in its own time stamps. Information: the same
  # playlist with the image's FFmpeg 9.0.2 in the same sysroot.
  last() { "$@" -v error -select_streams v:0 -show_entries packet=pts_time -of csv=p=0 s/disc/disc.m3u8 | sort -n | tail -1; }
  a=$(last $Q "$B/ffprobe" || true)
  b=$(last $Q "$SR/usr/bin/ffprobe" || true)
  notice "HLS with #EXT-X-DISCONTINUITY, last video pts: ${a:-none} s with the bundle's ffprobe (series), ${b:-none} s with the image's /usr/bin/ffprobe 9.0.2"

  # Information: the players with a file. There are no DVB devices here,
  # so this ends when the output cannot be opened; it shows that the
  # player gets through FFmpeg's open and stream analysis on ARM.
  set +e
  for p in ep3_sky186_ff9 ep3_oea_ff9; do
    echo "== $p s/hls/master.m3u8"
    TMO=40 timeout -k 5 40 qemu-arm -cpu cortex-a15 -L "$SR" "$B/$p" s/hls/master.m3u8 > play_$p.txt 2>&1 </dev/null
    rc=$?
    head -40 play_$p.txt
    notice "$p under qemu with the HLS sample (no DVB devices): exit $rc, $(grep -c . play_$p.txt) lines of output, track lines: $(grep -cE '"(v_c|a_l|a_c|s_l|v_e)"' play_$p.txt)"
  done
  exit 0
}

stage_$1
