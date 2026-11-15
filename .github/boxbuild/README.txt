ep3ff9 - test bundle: FFmpeg 9 patch series + exteplayer3 for a Vu+ Uno 4K SE
(openATV 8.0, ARMv7 hard-float, glibc 2.44). Nothing is installed: the folder
is self-contained and does not touch /usr/lib, /usr/lib/ffmpeg-ext or
/usr/bin/exteplayer3. MANIFEST.txt says what it was built from.

  ep3_sky186_ff9   exteplayer3 of skyjet18 master (version 186)
  ep3_oea_ff9      exteplayer3 of oe-alliance master (version 185)
  ffmpeg, ffprobe  the tools of the series
  lib/             libavutil.so.61 libswresample.so.7 libswscale.so.10
                   libavcodec.so.63 libavformat.so.63 libavfilter.so.12
                   libavdevice.so.63 (the series; same SONAMEs as the
                   image's FFmpeg 9.0.2 in /usr/lib)

The programs carry DT_RPATH $ORIGIN/lib and the libraries DT_RPATH $ORIGIN:
the FFmpeg libraries are taken from lib/ next to the program, before
LD_LIBRARY_PATH and before /usr/lib. Everything else (libssl.so.4,
libxml2.so.16, libbluray.so.3, librtmp.so.1, libz.so.1, ...) is the image's
own library in /usr/lib. Keep the programs and lib/ together; a copy of a
program alone somewhere else would load the image's FFmpeg 9.0.2.

Unpack on the box (any folder on a file system that allows executables):

  mkdir -p /home/root/ep3test && cd /home/root/ep3test
  tar xzf /tmp/ep3ff9-*.tar.gz && mv ep3ff9 ff9 && cd ff9
  # only if the files came unpacked over a Windows share:
  chmod 755 ep3_sky186_ff9 ep3_oea_ff9 ffmpeg ffprobe lib/*

Check that it is the series and that the private libraries are used:

  ./ffmpeg -version | head -2
      # not "ffmpeg version 9.0.2"; configuration has --prefix=/opt/ep3ff9
  LD_DEBUG=libs ./ffmpeg -version 2>&1 | grep 'calling init.*lib\(av\|sw\)'
  LD_DEBUG=libs ./ep3_sky186_ff9 2>&1 | grep 'calling init.*lib\(av\|sw\)'
  LD_DEBUG=libs ./ep3_oea_ff9 2>&1 | grep 'calling init.*lib\(av\|sw\)'
      # every line names /home/root/ep3test/ff9/lib/..., none /usr/lib/libav*
  # while a player runs:
  grep -E 'lib(av|sw)' /proc/$(pidof ep3_sky186_ff9)/maps | awk '{print $6}' | sort -u

Use:

  ./ffprobe -hide_banner 'URL'
  ./ep3_sky186_ff9                 # no arguments: {"EPLAYER3_EXTENDED":{"version":186}} + usage
  ./ep3_oea_ff9                    # the same with version 185
  ./ep3_sky186_ff9 [options] 'URL or file'
  ./ep3_oea_ff9 [options] 'URL or file'
      # options and commands on stdin as with /usr/bin/exteplayer3
