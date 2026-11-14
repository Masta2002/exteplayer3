#!/bin/sh
# Compiles the libraries and programs of the FFmpeg trees given
# (directories under /w) with the compiler of the container, out of tree,
# with a minimal configure. Never fails: writes /w/<tree>.result (one line)
# and /w/<tree>.failed (the objects that did not compile).
# usage: old_gcc.sh <tree>...
gcc --version | head -1
ldd --version | head -1
make --version | head -1
for t in "$@"; do
    echo "======== $t"
    : > /w/$t.failed
    mkdir -p /tmp/build_$t
    cd /tmp/build_$t || exit 0
    if ! /w/$t/configure --disable-autodetect --disable-doc --disable-x86asm > configure.log 2>&1; then
        tail -5 configure.log
        tail -40 ffbuild/config.log
        echo "configure failed: $(grep -v '^$' configure.log | head -1)" > /w/$t.result
        continue
    fi
    std=$(grep -o -- '-std=[a-z0-9+]*' ffbuild/config.mak | sort -u | tr '\n' ' ')
    grep -E '^(C compiler|C library|standalone assembly|threading support) ' configure.log
    make -k -j"$(nproc)" > make.log 2>&1
    rc=$?
    sed -n 's/^make: \*\*\* \[\(.*: \)\{0,1\}\([^]]*\.o\)\] Error.*/\2/p' make.log | sort > /w/$t.failed
    echo "---- error messages (file names and positions removed), most frequent first"
    grep -E ': (fatal )?error: ' make.log | sed 's/^[^ ]*: \(fatal \)\{0,1\}error: /error: /' | sort | uniq -c | sort -rn | head -25
    echo "---- first errors"
    grep -E ': (fatal )?error: ' make.log | head -25
    echo "---- objects that did not compile"
    cat /w/$t.failed
    built=$(find . -name '*.o' | wc -l)
    libs=$(ls lib*/lib*.a 2>/dev/null | wc -l)
    progs=$(ls ffmpeg ffprobe 2>/dev/null | tr '
' ' ')
    echo "configure passed ($std), make exit $rc, $built objects compiled, $(grep -c . /w/$t.failed) failed, $libs libraries, programs: $progs" > /w/$t.result
    cd /
    rm -rf /tmp/build_$t
done
exit 0
