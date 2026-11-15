#!/bin/bash
# Runs inside the old userland (chroot, qemu-user): configures and compiles one
# FFmpeg tree, compiles exteplayer3 against it and starts the programs.
# Never fails; everything found is written to /w/out/result.txt (key=value).
#
# usage: build.sh <arm|mips> <tree> <sky|asm224>
#   /w             exteplayer3 (this repository)
#   /w/src/<tree>  FFmpeg
#   sky     the configure options of the exteplayer3 ipk of skyjet18 (read from
#           the libavutil in the package), external libraries: openssl,
#           libxml2, zlib, bzlib, lzma
#   asm224  default assembler code (NEON, inline), assembled with the as of
#           binutils 2.24; external libraries: libxml2, zlib
set -u
TARGET=$1
TREE=$2
VARIANT=$3
SRC=/w/src/$TREE
OUT=/w/out
B=/b
J=$(nproc)
RES=$OUT/result.txt
git config --global --add safe.directory '*' 2> /dev/null
rm -rf $OUT $B
mkdir -p $OUT $B
: > $RES

res() {
    echo "$1=$2" >> $RES
    echo "RESULT $1=$2"
}

case $TARGET in
    arm)
        FFARCH=arm
        ARCHFLAGS="-march=armv7-a -mfloat-abi=hard -marm -mfpu=vfp"
        ASMFLAGS="-march=armv7-a -mfloat-abi=hard"
        ARCHOPTS=(--enable-armv6 --enable-armv6t2 --enable-vfp --enable-neon)
        HASH="-Wl,--hash-style=gnu "
        ;;
    mips)
        FFARCH=mipsel
        ARCHFLAGS="-mel -mabi=32 -mhard-float -march=mips32"
        ASMFLAGS="-mel -mabi=32 -mhard-float -march=mips32r2"
        ARCHOPTS=(--enable-mipsfpu --extra-libs=-latomic --disable-mips32r5 --disable-mipsdsp --disable-mipsdspr2
                  --disable-loongson2 --disable-loongson3 --disable-mmi --disable-msa)
        HASH=""
        ;;
esac
RPATH=/usr/lib/exteplayer3_deps

echo "======== environment"
bash /versions.sh
res tree "$(git -C $SRC log -1 --format='%h %s' 2>/dev/null || cat $SRC/RELEASE)"
res ffmpeg_release "$(cat $SRC/RELEASE 2>/dev/null)"
res gcc "$(gcc -dumpversion) $(gcc -dumpmachine)"
res binutils "$(ld --version | head -1)"
res glibc "$(ldd --version | head -1)"
res openssl_system "$(dpkg-query -W -f='${Version}' libssl-dev)"

PKGP=""
if [ $VARIANT = sky ]; then
    # the option line of the package, without the cross compilation options,
    # without -g and with the external libraries reduced to what the player
    # needs (the others are tried at the end, configure only)
    ARGS=(--disable-stripping --enable-pic --enable-shared --enable-pthreads --enable-nonfree
          --cc="gcc $ARCHFLAGS" --arch=$FFARCH --target-os=linux
          --extra-cflags="-Os -pipe -feliminate-unused-debug-types $ARCHFLAGS -O2"
          --extra-ldflags="-Wl,-O1 ${HASH}-Wl,--as-needed -Wl,-rpath,$RPATH"
          --prefix=/usr --disable-static --disable-runtime-cpudetect --enable-ffprobe
          --disable-altivec --disable-amd3dnow --disable-amd3dnowext --disable-mmx --disable-mmxext
          --disable-sse --disable-sse2 --disable-sse3 --disable-ssse3 --disable-sse4 --disable-sse42
          --disable-avx --disable-xop --disable-fma3 --disable-fma4 --disable-avx2
          --disable-inline-asm --disable-yasm --disable-x86asm --disable-fast-unaligned
          --enable-protocol=http --enable-demuxer=dash --disable-muxers
          --enable-muxer=adts --enable-muxer=mpeg1video --enable-muxer=h264 --enable-muxer=mp4
          --enable-muxer=image2 --enable-muxer=mjpeg --enable-muxer=rawvideo --enable-muxer=mpeg2video
          --enable-muxer=matroska --enable-muxer=m4v --enable-muxer=image2pipe --enable-muxer=apng
          --enable-muxer=mpegts --enable-muxer=asf --enable-muxer=spdif
          --disable-encoders --disable-encoder=libx264 --enable-encoder=ac3 --enable-encoder=aac
          --enable-encoder=mpeg1video --enable-encoder=ljpeg --enable-encoder=mjpeg --enable-encoder=mpeg4
          --enable-encoder=jpeg2000 --enable-encoder=jpegls --enable-encoder=png --enable-encoder=rawvideo
          --enable-encoder=wmav2 --enable-decoder=truehd --enable-decoder=mlp
          --disable-debug --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages
          "${ARCHOPTS[@]}"
          --libdir=/usr/lib --shlibdir=/usr/lib --datadir=/usr/share/ffmpeg
          --disable-mipsdsp --disable-mipsdspr2 --cpu=generic --pkg-config=pkg-config)
    [ $TARGET = arm ] && ARGS+=(--disable-neon)
    ARGS+=(--disable-altivec --enable-avcodec --enable-avdevice --enable-avfilter --enable-avformat
           --enable-bzlib --disable-libdav1d --disable-libfdk-aac --enable-gpl --disable-libgsm
           --disable-indev=jack --disable-libopus --disable-libv4l2 --disable-libvorbis --enable-libxml2
           --enable-lzma --disable-libmfx --disable-libmp3lame --enable-openssl --enable-postproc
           --disable-sdl2 --disable-libspeex --disable-libsrt --enable-swresample --enable-swscale
           --disable-libv4l2 --disable-vaapi --disable-vdpau --disable-libvpx --disable-libx265
           --disable-libxcb --disable-outdev=xv --enable-zlib)
    EXTRAS=(--enable-alsa --enable-libbluray --enable-protocol=bluray --enable-libfreetype --enable-librtmp
            --enable-libtheora --enable-libx264)
    PLAYERFLAGS=$ARCHFLAGS
else
    GAS=""
    if [ -x /opt/gas224/as ] && /opt/gas224/as --version > /dev/null 2>&1; then
        GAS="-B/opt/gas224/ "
        res assembler "$(gcc $GAS-print-prog-name=as): $(/opt/gas224/as --version | head -1)"
    else
        res assembler "as of binutils 2.24 not available, $(as --version | head -1) used"
    fi
    ARGS=(--prefix=/usr --arch=$FFARCH --target-os=linux --enable-shared --disable-static --enable-gpl
          --disable-autodetect --enable-zlib --enable-libxml2 --disable-doc --disable-debug
          --cc="gcc $GAS$ASMFLAGS" --extra-ldflags="-Wl,-rpath,$RPATH")
    EXTRAS=()
    PLAYERFLAGS="$GAS$ASMFLAGS"
fi

drop() {
    # removes the arguments given from ARGS, status 0 if one was there
    local found=1 a d new=()
    for a in "${ARGS[@]}"; do
        local keep=1
        for d in "$@"; do
            [ "$a" = "$d" ] && keep=0
        done
        if [ $keep = 1 ]; then new+=("$a"); else found=0; fi
    done
    ARGS=("${new[@]}")
    return $found
}

configure_loop() {
    # $1 label, $2 build directory; repeats configure, removing what it rejects
    local label=$1 dir=$2 n=0 rc msg opt lib
    mkdir -p $dir
    while :; do
        n=$((n + 1))
        ( cd $dir && env PKG_CONFIG_PATH=$PKGP $SRC/configure "${ARGS[@]}" ) > $OUT/configure.$label.$n.log 2>&1
        rc=$?
        cp $dir/ffbuild/config.log $OUT/config.$label.$n.log 2> /dev/null
        if [ $rc = 0 ]; then
            res cfg_${label} "passed in run $n"
            return 0
        fi
        msg=$(grep -E 'Unknown option|ERROR|lacks|not found|failed|Unable|requires' $OUT/configure.$label.$n.log | head -1)
        [ -z "$msg" ] && msg=$(grep -v '^$' $OUT/configure.$label.$n.log | head -1)
        res cfg_${label}_fail$n "$msg"
        echo "---- configure $label run $n failed: $msg"
        tail -25 $OUT/config.$label.$n.log 2> /dev/null
        [ $n -ge 14 ] && return 1
        opt=$(echo "$msg" | sed -n 's/^Unknown option "\(.*\)"\.*$/\1/p')
        lib=$(echo "$msg" | sed -n 's/^ERROR: \([A-Za-z0-9_.+-]*\).*not found.*/\1/p')
        if [ -n "$opt" ]; then
            drop "$opt" || return 1
        elif echo "$msg" | grep -q 'X264_BUILD'; then
            drop --enable-libx264 || return 1
        elif echo "$msg" | grep -qi openssl && [ -z "$PKGP" ]; then
            PKGP=/opt/ossl111/lib/pkgconfig
            # the libraries of /opt/ossl111 before those of the system (1.0.1t),
            # pkg-config of libxml2/zlib puts -L/usr/lib/<triplet> in front
            local a new=()
            for a in "${ARGS[@]}"; do
                case $a in
                    --extra-ldflags=*) a="--extra-ldflags=-L/opt/ossl111/lib -Wl,-rpath-link,/opt/ossl111/lib ${a#--extra-ldflags=}" ;;
                esac
                new+=("$a")
            done
            ARGS=("${new[@]}")
            res openssl_used "$(LD_LIBRARY_PATH=/opt/ossl111/lib /opt/ossl111/bin/openssl version) from /opt/ossl111 ($(cat /opt/ossl111/BUILD)), the system's $(dpkg-query -W -f='${Version}' libssl-dev) was rejected"
        elif [ -n "$lib" ]; then
            case $lib in
                freetype2) lib=libfreetype ;;
                libxml-2.0) lib=libxml2 ;;
                liblzma) lib=lzma ;;
                theora) lib=libtheora ;;
                x264) lib=libx264 ;;
            esac
            if [ $lib = libbluray ]; then drop --enable-protocol=bluray; fi
            drop --enable-$lib --enable-lib$lib || return 1
        else
            return 1
        fi
    done
}

echo "======== configure"
if ! configure_loop main $B; then
    res stage "configure failed"
    gzip -9 $OUT/*.log
    exit 0
fi
[ -z "$PKGP" ] && [ $VARIANT = sky ] && res openssl_used "system $(dpkg-query -W -f='${Version}' libssl-dev)"
LAST=$(ls $OUT/configure.main.*.log | sort -t. -k3 -n | tail -1)
grep -E '^(install prefix|ARCH|C compiler|C library|big-endian|runtime cpu detection|standalone assembly|inline assembly|threading support|ARMv|VFP|NEON|THUMB|MIPS|MMI|MSA|External libraries|License|network support|debug symbols|optimize)' $LAST
sed -n '/^External libraries:/,/^$/p' $LAST
res std "$(grep -o -- '-std=[a-z0-9+]*' $B/ffbuild/config.mak | sort -u | tr '\n' ' ')"
res cfg_c_library "$(sed -n 's/^C library  *//p' $LAST)"
res cfg_asm "$(grep -E '^(standalone assembly|inline assembly|ARMv[0-9A-Za-z]* enabled|VFP enabled|NEON enabled|THUMB enabled|MIPS FPU enabled|MIPS DSP R1 enabled|MIPS DSP R2 enabled|MIPS32R2 enabled|MMI enabled|MSA enabled) ' $LAST | sed 's/  */ /g' | tr '\n' ';')"
res cfg_extlibs "$(sed -n '/^External libraries:/,/^$/p' $LAST | tail -n +2 | tr -s ' \n' ' ')"
res cfg_stdatomic "$(grep -E '^#define HAVE_STDATOMIC|^#define HAVE_ATOMIC|^#define HAVE_SYNC_VAL' $B/config.h | tr '\n' ';') extralibs: $(grep -E '^EXTRALIBS-avutil' $B/ffbuild/config.mak | head -1)"
res cfg_components "$(grep -c '^#define CONFIG_.*_DECODER 1' $B/config_components.h 2> /dev/null || grep -c '^#define CONFIG_.*_DECODER 1' $B/config.h) decoders, $(grep -c '^#define CONFIG_.*_DEMUXER 1' $B/config_components.h 2> /dev/null || grep -c '^#define CONFIG_.*_DEMUXER 1' $B/config.h) demuxers, $(grep -c '^#define CONFIG_.*_FILTER 1' $B/config_components.h 2> /dev/null || grep -c '^#define CONFIG_.*_FILTER 1' $B/config.h) filters"
cp $B/config.h $OUT/config.h
cp $B/ffbuild/config.mak $OUT/config.mak

echo "======== make"
START=$(date +%s)
( cd $B && timeout ${MAKE_MINUTES:-250}m make -k -j$J V=1 ) > $OUT/make.log 2>&1
rc=$?
res make_minutes "$(( ($(date +%s) - START) / 60 ))"
sed -n 's/^make: \*\*\* \[\(.*: \)\{0,1\}\([^]]*\)\] Error.*/\2/p' $OUT/make.log | sort > $OUT/failed.txt
grep -E ': (fatal )?error: |undefined reference|Error: ' $OUT/make.log | sed "s#$SRC/##g" > $OUT/errors.txt
grep -E ': warning: ' $OUT/make.log | sed "s#$SRC/##g; s/:[0-9]*:[0-9]*: warning: /: warning: /" | sort | uniq -c | sort -rn > $OUT/warnings.txt
echo "---- error messages (positions removed), most frequent first"
sed 's/^[^ ]*: \(fatal \)\{0,1\}error: /error: /' $OUT/errors.txt | sort | uniq -c | sort -rn | head -30
echo "---- first errors"
head -40 $OUT/errors.txt
echo "---- targets that failed"
cat $OUT/failed.txt
LIBS=$(cd $B && ls lib*/lib*.so.* 2> /dev/null | wc -l)
PROGS=$(cd $B && echo $(ls ffmpeg ffprobe 2> /dev/null))
res make "exit $rc, $(find $B -name '*.o' | wc -l) objects, $(grep -c . $OUT/failed.txt) targets failed, $LIBS shared libraries, programs: $PROGS"
res make_failed "$(tr '\n' ' ' < $OUT/failed.txt | cut -c1-600)"
res make_first_error "$(head -1 $OUT/errors.txt | cut -c1-400)"
res warnings "$(awk '{s+=$1} END {print s+0}' $OUT/warnings.txt) in $(grep -c . $OUT/warnings.txt) distinct"

ROOT=$OUT/root
if [ -n "$PROGS" ] && [ $LIBS -gt 0 ]; then
    ( cd $B && make install DESTDIR=$ROOT ) > $OUT/install.log 2>&1
    res install "exit $?"
fi
LIBP=$ROOT/usr/lib
[ -n "$PKGP" ] && LIBP=$LIBP:/opt/ossl111/lib

run() {
    # $1 name, rest: command; result = exit status and the output in one line
    local name=$1 out rc
    shift
    out=$(env LD_LIBRARY_PATH=$LIBP "$@" 2>&1 < /dev/null)
    rc=$?
    echo "---- $name (exit $rc): $*"
    echo "$out" | head -30
    res run_$name "exit $rc: $(echo "$out" | head -12 | tr '\n' '|' | cut -c1-420)"
}

if [ -x $ROOT/usr/bin/ffmpeg ]; then
    echo "======== programs under qemu-user"
    run ffmpeg_version $ROOT/usr/bin/ffmpeg -version
    run protocols sh -c "$ROOT/usr/bin/ffmpeg -hide_banner -protocols 2>/dev/null | tr -d ' ' | grep -xE 'https|tls|http|hls|crypto|rtmp|rtmps|ffrtmp|ffrtmps|librtmp|dtls' | sort -u | tr '\n' ' '"
    run demuxers sh -c "$ROOT/usr/bin/ffmpeg -hide_banner -demuxers 2>/dev/null | grep -E '^ *D[ A-Za-z]* (hls|dash|mpegts|mov,mp4,m4a,3gp,3g2,mj2|matroska,webm) ' | awk '{print \$2}' | tr '\n' ' '"
    PROBE="$ROOT/usr/bin/ffprobe -v error -count_frames -show_entries stream=codec_name,width,nb_read_frames -of csv=p=0"
    run hls_file $PROBE /samples/hls/master.m3u8
    run dash_file $PROBE /samples/dash/manifest.mpd
    if [ $VARIANT = sky ]; then
        run hls_https $PROBE https://127.0.0.1:${HTTPS_PORT:-8443}/hls/master.m3u8
        run dash_https $PROBE https://127.0.0.1:${HTTPS_PORT:-8443}/dash/manifest.mpd
    fi
fi

if [ -d $ROOT/usr/include/libavformat ]; then
    echo "======== exteplayer3"
    cd /w
    res player "$(git -C /w log -1 --format='%h %s' skyjet-master 2> /dev/null || sed -n 's/.*"version":\([0-9]*\).*/version \1/p' main/exteplayer.c | head -1)"
    SOURCES="main/exteplayer.c tools/debug.c tools/strbuffer.c tools/pcm_export.c
        container/container.c container/container_ffmpeg.c
        manager/manager.c manager/audio.c manager/video.c manager/subtitle.c
        playback/playback.c output/output.c output/output_subtitle.c output/graphic_subtitle.c
        output/linuxdvb_mipsel.c output/linuxdvb_buffering.c
        output/writer/common/pes.c output/writer/common/misc.c output/writer/common/writer.c
        $(ls output/writer/mipsel/*.c | tr '\n' ' ') external/ffmpeg/src/bitstream.c
        external/ffmpeg/src/latmenc.c external/ffmpeg/src/mpeg4audio.c
        $(ls external/flv2mpeg4/src/*.c | tr '\n' ' ') external/plugins/src/png.c"
    PCMD="gcc $PLAYERFLAGS -O2 $SOURCES -D_FILE_OFFSET_BITS=64 -D_LARGEFILE64_SOURCE -D_LARGEFILE_SOURCE
        -DHAVE_FLV2MPEG4_CONVERTER -Iinclude -Iexternal -Iexternal/flv2mpeg4"
    PLINK="-L$ROOT/usr/lib -Wl,-rpath-link,$LIBP -Wl,-rpath,$RPATH
        -lpthread -lavformat -lavcodec -lavutil -lswresample -lswscale -ldl -lm -o $OUT/exteplayer3"
    # as in the recipe: only the installed headers
    $PCMD -I$ROOT/usr/include $PLINK > $OUT/player.log 2>&1
    prc=$?
    res player_build "exit $prc with the installed headers, $(grep -c 'warning:' $OUT/player.log) warnings, $(grep -cE 'error|undefined reference' $OUT/player.log) errors"
    if [ $prc != 0 ]; then
        grep -E 'error|undefined reference' $OUT/player.log | head -20
        res player_first_error "$(grep -E 'error|undefined reference' $OUT/player.log | head -1 | cut -c1-400)"
        # as the tests of the repository do: with the FFmpeg source tree
        $PCMD -I$ROOT/usr/include -I$SRC -I$B $PLINK > $OUT/player2.log 2>&1
        prc=$?
        res player_build_with_source_tree "exit $prc, $(grep -cE 'error|undefined reference' $OUT/player2.log) errors"
        grep -E 'error|undefined reference' $OUT/player2.log | head -20
    fi
    grep -E 'warning: (implicit declaration|.*incompatible pointer|.*makes (integer|pointer))' $OUT/player.log | head -10
    if [ -x $OUT/exteplayer3 ]; then
        run player_usage sh -c "$OUT/exteplayer3 2>&1 | head -4; $OUT/exteplayer3 2>&1 | grep -c -- '-Q'"
        run player_hls sh -c "timeout 20 $OUT/exteplayer3 /samples/hls/master.m3u8 2>&1 | tail -6"

        echo "======== package"
        P=$OUT/pkg
        mkdir -p $P/lib
        cp $OUT/exteplayer3 $ROOT/usr/bin/ffmpeg $ROOT/usr/bin/ffprobe $P/
        for f in $ROOT/usr/lib/lib*.so.*.*; do
            cp $f $P/lib/$(objdump -p $f | sed -n 's/^ *SONAME *//p')
        done
        # what the old images do not have in this version
        for f in $P/exteplayer3 $P/ffmpeg $P/ffprobe $P/lib/*; do
            env LD_LIBRARY_PATH=$LIBP ldd $f
        done 2> /dev/null | awk '$2 == "=>" && $3 ~ /^\// {print $3}' | sort -u |
            grep -E '/opt/ossl111/|/(libxml2|libz|libbz2|liblzma|libatomic)\.so' | while read f; do
            cp -L $f $P/lib/$(basename $f)
        done
        chrpath -r '$ORIGIN/lib' $P/exteplayer3 $P/ffmpeg $P/ffprobe > $OUT/chrpath.log 2>&1
        chrpath -r '$ORIGIN' $P/lib/libav*.so.* $P/lib/libsw*.so.* >> $OUT/chrpath.log 2>&1
        ls -la $P $P/lib
        objdump -p $P/exteplayer3 | grep -E 'NEEDED|RPATH|RUNPATH'
        res pkg_rpath "$(objdump -p $P/exteplayer3 | grep -E 'RPATH|RUNPATH' | tr -s ' ' | tr '\n' ';') libavformat: $(objdump -p $P/lib/libavformat.so.* | grep -E 'RPATH|RUNPATH' | tr -s ' ' | tr '\n' ';')"
        res pkg_glibc_max "$(for f in $P/exteplayer3 $P/ffmpeg $P/ffprobe $P/lib/*; do objdump -T $f; done 2> /dev/null | grep -o 'GLIBC_[0-9.]*' | sort -u -V | tail -1)"
        res pkg_libs "$(ls $P/lib | tr '\n' ' ')"
        # started from the package alone, no LD_LIBRARY_PATH
        LIBP= run pkg_selftest sh -c "cd $P && unset LD_LIBRARY_PATH && ./ffprobe -v error -show_entries stream=codec_name -of csv=p=0 /samples/dash/manifest.mpd | tr '\n' ' ' && ./ffmpeg -version | head -1 && ./exteplayer3 2>&1 | grep -c -- '-Q'"
        tar -czf $OUT/package-$TARGET-$TREE-$VARIANT.tar.gz -C $OUT pkg
        rm -rf $P
    fi
fi

if [ $VARIANT = sky ] && [ -n "$PKGP" ] && [ -f $B/ffbuild/config.mak ]; then
    echo "======== tls_openssl.c against the OpenSSL $(dpkg-query -W -f='${Version}' libssl-dev) of the system"
    cd $B
    printf 'include %s\nall:\n\t@echo $(CC) $(CPPFLAGS) $(CFLAGS)\n' "$B/ffbuild/config.mak" > /tmp/flags.mak
    FL=$(make -s -f /tmp/flags.mak | sed 's#-I/opt/ossl111/include##g')
    for hdr in old new; do
        INC=""
        [ $hdr = new ] && INC=-I/opt/ossl111/include
        CMD="$FL $INC -DHAVE_AV_CONFIG_H -I. -I$SRC -c $SRC/libavformat/tls_openssl.c -o /tmp/tls_$hdr.o"
        echo "---- $hdr: $CMD"
        sh -c "$CMD" > $OUT/tls_openssl_$hdr.log 2>&1
        trc=$?
        grep -E 'error' $OUT/tls_openssl_$hdr.log | sed "s#$SRC/##g" | head -40
        res tls_openssl_$hdr "exit $trc, $(grep -c 'error' $OUT/tls_openssl_$hdr.log) errors: $(grep 'error' $OUT/tls_openssl_$hdr.log | sed 's/^[^ ]*: error: //' | sort -u | head -12 | tr '\n' '|' | cut -c1-900)"
    done
    cd /
fi

if [ ${#EXTRAS[@]} -gt 0 ]; then
    echo "======== configure with the other external libraries of the package"
    ARGS+=("${EXTRAS[@]}")
    configure_loop extras /b2
    res cfg_extras_kept "$(for a in "${EXTRAS[@]}"; do for b in "${ARGS[@]}"; do [ "$a" = "$b" ] && echo "$a"; done; done | tr '\n' ' ')"
    if [ $TREE != sky61 ]; then
        # GnuTLS of the system instead of OpenSSL
        drop --enable-openssl "${EXTRAS[@]}"
        ARGS+=(--enable-gnutls)
        PKGSAVE=$PKGP
        PKGP=""
        if configure_loop gnutls /b3; then
            ( cd /b3 && make V=1 libavformat/tls_gnutls.o libavformat/tls.o ) > $OUT/tls_gnutls.log 2>&1
            grc=$?
            grep -E 'error' $OUT/tls_gnutls.log | sed "s#$SRC/##g" | head -20
            res tls_gnutls_compile "exit $grc, $(grep -c 'error' $OUT/tls_gnutls.log) errors: $(grep 'error' $OUT/tls_gnutls.log | sed 's/^[^ ]*: error: //' | sort -u | head -8 | tr '\n' '|' | cut -c1-600)"
        fi
        res cfg_gnutls_version "$(pkg-config --modversion gnutls)"
        PKGP=$PKGSAVE
    fi
fi

res stage done
rm -rf $ROOT
gzip -9 $OUT/*.log
ls -la $OUT
exit 0
