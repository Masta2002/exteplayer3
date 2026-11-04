# exteplayer3

<p align="center">
  <img alt="GitHub repo size" src="https://img.shields.io/github/repo-size/oe-alliance/exteplayer3.svg">
  <a href="https://visitorbadge.io/status?path=https://github.com/oe-alliance/exteplayer3"><img alt="Views" src="https://api.visitorbadge.io/api/visitors?path=https://github.com/oe-alliance/exteplayer3&label=Views&countColor=blue&style=flat"></a>
  <a href="https://visitorbadge.io/status?path=https://github.com/oe-alliance/exteplayer3"><img alt="Visitors today" src="https://api.visitorbadge.io/api/daily?path=https://github.com/oe-alliance/exteplayer3&label=Visitors%20Today&countColor=blue&style=flat"></a>
</p>

Core of the movie player for Enigma2, based on libeplayer with FFmpeg as
demuxer. It reads files and streams (HTTP, HLS, DASH, RTMP, RTSP, local
files) through FFmpeg and writes the elementary streams as PES to the box's
decoders (`/dev/dvb/adapter0/video0`, `audio0`), or to the Amlogic backend of
the DreamNextGen boxes (dreamone, dreamtwo).

It is used by ServiceApp (service 5002), E2iPlayer, ArchivCZSK and other
plugins. The OE-Alliance images build it with the `exteplayer3` recipe of
oe-alliance-core against `ffmpeg-ext` (FFmpeg 6.1 with the playback fixes of
[skyjet18/FFmpeg](https://github.com/skyjet18/FFmpeg), installed in
`/usr/lib/ffmpeg-ext`).

## Github status

[![Build](https://github.com/oe-alliance/exteplayer3/actions/workflows/build.yml/badge.svg)](https://github.com/oe-alliance/exteplayer3/actions/workflows/build.yml)
[![Github last commit](https://img.shields.io/github/last-commit/oe-alliance/exteplayer3)](https://github.com/oe-alliance/exteplayer3/commits)
[![GitHub Activity](https://img.shields.io/github/commit-activity/y/oe-alliance/exteplayer3.svg?label=commits)](https://github.com/oe-alliance/exteplayer3/commits)
[![Issues](https://img.shields.io/github/issues/oe-alliance/exteplayer3?color=blue)](https://github.com/oe-alliance/exteplayer3/issues)
![Version](https://img.shields.io/badge/version-186-blue)
![Platform](https://img.shields.io/badge/Platform-Enigma2-orange.svg)

## SonarCloud status

[![Vulnerabilities](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=vulnerabilities)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=bugs)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)

---

## Features

- Video: H.264, H.265/HEVC, MPEG-1/2/4, VC-1/WMV, VP6/8/9, MJPEG, DivX 3, FLV
  (to MPEG-4), AVS/AVS2 (DreamNextGen). A variant whose codec has no writer
  (AV1, VVC) is never chosen.
- Audio: AAC/LATM, AC3, E-AC3, DTS, MP2/MP3, LPCM/PCM, WMA, AMR, Vorbis, Opus,
  FLAC; AC-4, xHE-AAC and everything else decoded in software as PCM. DTS /
  DTS-HD and TrueHD can be transcoded to AC3 5.1, HE-AAC to AAC-LC.
- HDR10 / HLG detection of HEVC streams, live channel count and live Dolby
  Atmos detection of the audio track.
- Subtitles: SRT, ASS/SSA, WebVTT, MOV_TEXT as text; PGS, DVB and XSUB as PNG
  files through the `exteplayer3png.so` plugin (`-G`). Forced tracks are
  reported.
- HLS: segments with any file name (`.jpg`, `.html`, none), playlists named
  `.txt` or `.css`, no Range request (some CDNs answer it with 410), three
  retries of a broken segment, gzip playlists. The first variant plays by
  default, the best one with `-Q` (DreamNextGen always takes the best).
- DASH with CENC keys (`-5`), a separate audio file or stream (`-x`), RTMP
  (native or librtmp), live TS mode (`-v`).
- Progressive playback of a file that is still downloaded (`-o 1`), with the
  moov atom in a file of its own (`-F`, `-O`, `-S`).
- Network streams: a read error is not taken as the end of the file (10 s
  grace), a read that stalls ends after 3x the http timeout (60 s at least),
  MPEG-TS over HTTP seeks by a byte estimate instead of a binary search.
- Box specific: DD+ restart for Vu+ Ultimo 4K / Duo 4K SE, write order and an
  optional software clock for HiSilicon receivers, VP9 chunks, MP2 as MPEG and
  a live TS audio hold for Dreambox.

## Usage

`exteplayer3 [options] url`

```
-b size     Linux DVB output buffer size in MB
-a 0|1|2|3  AAC software decoding - 1 bit AAC ADTS, 2 bit AAC LATM
-e          EAC3 software decoding
-3          AC3 software decoding
-d          DTS software decoding
-D          DTS / DTS-HD transcoding to AC3 5.1
-X          TrueHD transcoding to AC3 5.1
-Y          HE-AAC transcoding to AAC-LC
-Q          play the best HLS variant instead of the first
-m          MP3 software decoding
-A 0|1      disable|enable AMR software decoding
-V 0|1      disable|enable Vorbis software decoding
-U 0|1      disable|enable Opus software decoding
-w          WMA2, WMA/PRO software decoding
-l          software decoder uses LPCM for injection (otherwise WAV PCM)
-s          software decoding as stereo (downmix)
-4 0|1      disable/enable the flv2mpeg4 converter
-C          disable the tracks info cache
-T          http timeout
-L desc     debug level to change, NAME:INT_VALUE
-i          play in an endless loop
-v          live TS stream mode
-n 0|1|2    rtmp implementation: auto (0), native/ffmpeg (1), librtmp (2)
-o 0|1      progressive download
-p value    nice value
-P value    program ID of a multi-service stream
-t id       audio track ID switched on at start
-9 id       subtitle track ID switched on at start
-h headers  custom HTTP headers "Name: value\r\nName: value\r\n"
-u agent    custom HTTP User-Agent
-x uri      separate audio uri
-5 key      MPEG-DASH CENC decryption key
-f opt=val  any other FFmpeg option
-F path     file with the moov atom (MP4 in progressive download mode)
-O offset   moov atom offset in the original file
-S size     remote file size
-G path     directory for graphic subtitle frames (PNG)
-W / -H     OSD window width / height for graphic subtitles
```

Commands are read from stdin, one per line: `q` quit, `p` pause, `c`
continue, `k` relative and `g` absolute seek (`kc<sec>` / `gc<sec>` checked
against the length, `kf` / `gf` forced), `al` / `ac` / `ai<id>` list, current
and switch audio track, the same with `s` for subtitles, `vc` the video track.
Status and track information go to stderr, one JSON object per line.

### Track fields

| Field | Meaning |
|---|---|
| `id` | track ID |
| `e` | codec ID (`A_AC3`, `V_MPEG4/ISO/AVC`, `S_TEXT/SUBRIP` ...) |
| `n` | name / language |
| `c` | audio: channel count, live from the decoded frames |
| `d` | audio: description (`Dolby Atmos`, `HE-AAC v2`, `DTS-HD MA` ...) |
| `f` | subtitle: 1 for a forced track; video: frame rate |
| `w`, `h`, `p`, `an`, `ad` | video: size, progressive, aspect ratio |
| `hdr` | video: 0 SDR, 1 HDR10, 2 HLG, 3 other HDR |

### Environment

| Variable | Effect |
|---|---|
| `EP3_OUTPUT_PACING_MS=1500` | software playback clock for HiSilicon receivers whose picture and sound run too fast |
| `EP3_PES_DTS=1` | write the DTS into the H.264 PES header (B frames) |
| `EP3_LIVE_SYNC=0`, `EP3_LIVE_SYNC_WATCH=0` | Dreambox: no audio hold for live TS |
| `EP3_LIVE_SYNC_MARGIN=ms` | Dreambox: margin of the live TS audio hold (400) |

## Building

```
./autogen.sh
./configure [--enable-dreamnextgen] [--disable-flv2mpeg4]
make
```

Needs the FFmpeg libraries (`libavformat`, `libavcodec`, `libavutil`,
`libswresample`, `libswscale`), libxml2, OpenSSL, libbluray, librtmp,
libudfread, zlib, bzip2 and liblzma; ALSA for `--enable-dreamnextgen`. The
build workflow compiles it against the FFmpeg of Ubuntu, the `ffmpeg-ext` of
the images and FFmpeg 9. [exteplayer3_git.bb](exteplayer3_git.bb) is the
oe-alliance-core recipe, for building this repository in an OE tree.

---

### 📜 License Information [![License: GPL v2](https://img.shields.io/badge/License-GPLv2-blue.svg)](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)

exteplayer3 is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, version 2 or later. Vendored code keeps its own license.

| Part | Origin |
|---|---|
| player core | libeplayer3 of the Duckbox team, exteplayer3 by samsamsam ([e2iplayer/exteplayer3](https://github.com/e2iplayer/exteplayer3)), continued by [skyjet18/exteplayer3](https://github.com/skyjet18/exteplayer3) |
| DreamNextGen backend | oe-alliance |
| HDR detection, transcoding, codec descriptions, Atmos detection, DD+ restart | [BlackHole/exteplayer3](https://github.com/BlackHole/exteplayer3), [OpenViX/exteplayer3](https://github.com/OpenViX/exteplayer3) |
| network, HLS and subtitle fixes | [boingbasti/e2-serviceapp-vti](https://github.com/boingbasti/e2-serviceapp-vti) |
| H.264 slices, VP9 chunks, MP2, live TS hold (Dreambox) | [Blacker91/exteplayer3-dreamos](https://github.com/Blacker91/exteplayer3-dreamos) |
| HiSilicon detection, write order, software clock | [OpenVu/exteplayer3](https://github.com/OpenVu/exteplayer3) |
| `external/ffmpeg` | FFmpeg (LGPL 2.1+) |
| `external/flv2mpeg4` | vixy project |

<img width="127" height="51" alt="GPLv2 logo" src="https://www.gnu.org/graphics/gplv2-127x51.png" />

---

### 🤝 Contributing

Pull requests welcome. Fork, branch, commit with a clear message, open a PR.
The build workflow runs on every push and PR. The binary goes to hundreds of
receivers: a change that alters what every box gets should come with a test
on more than one chipset, or stay behind an option.
