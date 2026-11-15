#!/bin/bash
# Builds the environment of the test on the runner: a Debian 8 "jessie" userland
# of the target architecture (GCC 4.9.2, glibc 2.19, binutils 2.25,
# OpenSSL 1.0.1t, libxml2 2.9.1, kernel headers 3.16) that runs under
# qemu-user, the generation of the OpenPLi 4 build environment (GCC 4.9.1,
# glibc 2.20, binutils 2.24, OpenSSL 1.0.1j, libxml2 2.9.2, headers 3.17).
# Added to it:
#   /opt/ossl111   OpenSSL 1.1.1w compiled with the GCC of the environment
#   /opt/gas224/as the assembler of binutils 2.24 (static x86_64 program, cross)
#   /samples       a small HLS and DASH stream, certificate for the HTTPS test
# usage: rootfs.sh <armhf|mipsel> <directory> <archive.tar.zst>
set -eux
ARCH=$1
R=$2
TARBALL=$3
HERE=$(cd "$(dirname "$0")" && pwd)

case $ARCH in
    armhf)  Q=arm;    GNU=arm-linux-gnueabihf; OSSL=linux-armv4
            ARCHFLAGS="-march=armv7-a -mfloat-abi=hard -marm -mfpu=vfp" ;;
    mipsel) Q=mipsel; GNU=mipsel-linux-gnu;    OSSL=linux-mips32
            ARCHFLAGS="-mel -mabi=32 -mhard-float -march=mips32" ;;
    *) echo "unknown architecture $ARCH"; exit 1 ;;
esac

sudo apt-get update -q
sudo apt-get install -y -q qemu-user-static debootstrap zstd ffmpeg texinfo
bash "$HERE/binfmt.sh" $Q

sudo debootstrap --arch=$ARCH --variant=minbase --no-check-gpg jessie "$R" http://archive.debian.org/debian
bash "$HERE/binfmt.sh" $Q "$R"

echo 'deb http://archive.debian.org/debian jessie main' | sudo tee "$R/etc/apt/sources.list"
sudo tee "$R/etc/apt/apt.conf.d/99old" <<'EOF'
Acquire::Check-Valid-Until "false";
APT::Get::AllowUnauthenticated "true";
APT::Install-Recommends "false";
EOF
sudo cp -L /etc/resolv.conf "$R/etc/resolv.conf"
sudo mount -t proc proc "$R/proc"
sudo chroot "$R" apt-get update -q
sudo chroot "$R" env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --force-yes \
    build-essential pkg-config perl file chrpath binutils \
    zlib1g-dev libbz2-dev liblzma-dev libxml2-dev libssl-dev \
    librtmp-dev libbluray-dev libfreetype6-dev libasound2-dev libtheora-dev libx264-dev \
    libgnutls28-dev
sudo chroot "$R" apt-get clean

# OpenSSL 1.1.1w, compiled in the environment
curl -sL --retry 3 -o /tmp/openssl.tar.gz https://github.com/openssl/openssl/releases/download/OpenSSL_1_1_1w/openssl-1.1.1w.tar.gz
echo "cf3098950cb4d853ad95c0841f1f9c6d3dc102dccfcacd521d93925208b76ac8  /tmp/openssl.tar.gz" | sha256sum -c
sudo mkdir -p "$R/build"
sudo tar -xzf /tmp/openssl.tar.gz -C "$R/build"
sudo tee "$R/build/openssl.sh" <<EOF
set -ex
cd /build/openssl-1.1.1w
build() {
    ./Configure $OSSL shared no-tests \$1 --prefix=/opt/ossl111 --openssldir=/opt/ossl111/ssl --libdir=lib $ARCHFLAGS -O2
    make -j\$(nproc) > /build/openssl-make.log 2>&1 && make install_sw > /build/openssl-install.log 2>&1
}
if build ""; then
    echo "with assembler code" > /opt/ossl111/BUILD
else
    tail -30 /build/openssl-make.log
    make clean > /dev/null 2>&1 || true
    build no-asm
    echo "no-asm (the build with assembler code failed)" > /opt/ossl111/BUILD
fi
LD_LIBRARY_PATH=/opt/ossl111/lib /opt/ossl111/bin/openssl version
EOF
sudo chroot "$R" /bin/bash /build/openssl.sh
sudo rm -rf "$R/build"

# assembler of binutils 2.24 for the target, static, runs on the runner's CPU
sudo mkdir -p "$R/opt/gas224"
(
    set -e
    mkdir /tmp/binutils && cd /tmp/binutils
    curl -sL --retry 3 -o binutils.tar.bz2 https://ftp.gnu.org/gnu/binutils/binutils-2.24.tar.bz2
    tar -xjf binutils.tar.bz2
    mkdir build && cd build
    ../binutils-2.24/configure --target=$GNU --disable-nls --disable-werror --disable-gdb \
        --disable-ld --disable-gold --disable-gprof --disable-binutils \
        CFLAGS="-O2 -w -std=gnu89" LDFLAGS=-static > configure.log 2>&1
    make -j"$(nproc)" configure-host MAKEINFO=true > make-configure.log 2>&1
    make -j"$(nproc)" all-gas MAKEINFO=true LDFLAGS=-all-static > make.log 2>&1 || { tail -40 make.log; exit 1; }
    file gas/as-new
    sudo install -m 755 gas/as-new "$R/opt/gas224/as"
) || echo "::warning::$ARCH: the assembler of binutils 2.24 could not be built"
if [ -x "$R/opt/gas224/as" ]; then
    sudo chroot "$R" /opt/gas224/as --version | head -1
fi

# samples (made with the FFmpeg of the runner) and the certificate
S=/tmp/samples
mkdir -p $S/hls $S/dash
ffmpeg -v error -f lavfi -i testsrc2=size=320x180:rate=25 -f lavfi -i sine=frequency=440:sample_rate=48000 \
    -t 6 -c:v libx264 -preset veryfast -g 25 -pix_fmt yuv420p -c:a aac -b:a 64k \
    -f hls -hls_time 2 -hls_playlist_type vod -master_pl_name master.m3u8 $S/hls/stream.m3u8
ffmpeg -v error -f lavfi -i testsrc2=size=320x180:rate=25 -f lavfi -i sine=frequency=440:sample_rate=48000 \
    -t 6 -c:v libx264 -preset veryfast -g 25 -pix_fmt yuv420p -c:a aac -b:a 64k \
    -f dash -seg_duration 2 $S/dash/manifest.mpd
openssl req -x509 -newkey rsa:2048 -nodes -subj /CN=localhost -days 30 -keyout $S/key.pem -out $S/crt.pem 2>/dev/null
cat $S/key.pem $S/crt.pem > $S/cert.pem
ls -la $S $S/hls $S/dash
sudo cp -r $S "$R/samples"

sudo tee "$R/versions.sh" <<'EOF'
echo "gcc:      $(gcc --version | head -1)"
echo "binutils: $(ld --version | head -1)"
echo "glibc:    $(ldd --version | head -1)"
echo "make:     $(make --version | head -1)"
echo "openssl:  $(openssl version 2>/dev/null || dpkg-query -W -f='${Version}' libssl-dev) (system), $(LD_LIBRARY_PATH=/opt/ossl111/lib /opt/ossl111/bin/openssl version) in /opt/ossl111, $(cat /opt/ossl111/BUILD)"
echo "libxml2:  $(pkg-config --modversion libxml-2.0)"
echo "headers:  linux-libc-dev $(dpkg-query -W -f='${Version}' linux-libc-dev)"
echo "packages: $(dpkg-query -W -f='${Package} ${Version}, ' libssl-dev librtmp-dev libbluray-dev libfreetype6-dev libasound2-dev libtheora-dev libx264-dev libgnutls28-dev zlib1g-dev libbz2-dev liblzma-dev)"
echo "gcc -v:   $(gcc -v 2>&1 | grep 'Configured with' | grep -o -- '--with-[a-z0-9-]*=[^ ]*' | tr '\n' ' ')"
[ -x /opt/gas224/as ] && echo "gas 2.24: $(/opt/gas224/as --version | head -1)"
uname -a
EOF
sudo chroot "$R" /bin/bash /versions.sh

sudo umount "$R/proc"
sudo tar --numeric-owner -C "$R" -c . | zstd -T0 -6 > "$TARBALL"
ls -la "$TARBALL"
