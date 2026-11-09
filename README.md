# exteplayer3
Core of movie player for E2 based on the libeplayer using the ffmpeg solution

[![Build](https://github.com/oe-alliance/exteplayer3/actions/workflows/build.yml/badge.svg)](https://github.com/oe-alliance/exteplayer3/actions/workflows/build.yml)


## SonarCloud status
[![Vulnerabilities](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=vulnerabilities)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=bugs)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=oe-alliance_exteplayer3&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=oe-alliance_exteplayer3)

## Version 185

Integrated from [skyjet18/exteplayer3](https://github.com/skyjet18/exteplayer3/tree/3e87bef42b8d233ae11d0527548308b84a6d2a2b), retaining the OE Alliance Autotools build and Dreambox backends.

The update improves HLS/DASH audio switching, stream identity, timestamp handling,
HTTP MPEG-TS seeking, subtitle selection and radio metadata. Dreambox raw-video
output, kernel audio synchronization and keyframe trick play remain supported.

### Adaptive stream selection

| Option | Behavior |
| --- | --- |
| `-Q 0/1/2` | Auto (default, highest advertised bandwidth), lowest or highest HLS/DASH quality. |
| `-D 0/1` | Keep all HLS audio renditions (default), or prefer default audio. |
| `-M WIDTHxHEIGHT` | Limit automatic H.264 selection; `0x0` disables the limit. HEVC is unaffected. |
| `-P ID` | Select an explicit positive program ID, bypassing automatic selection limits. |

Quality selection chooses a representation; it does not change quality dynamically
according to available bandwidth. HLS groups without default audio retain their
available tracks.

Use the companion [FFmpeg 6.1 patched branch](https://github.com/skyjet18/FFmpeg/tree/0da0223cd1add40eabaad04e1ce609f05670a1de).
The player uses its `hls_quality_mode`, `hls_audio_default_only` and
`dash_subtitle_default_discard` AVOptions. The OpenATV ffmpeg-ext recipe supplies
this version together with the downstream decoder and decryption changes.

### Optional decoded PCM export

`-K` enables `/tmp/exteplayer3_pcm_audio.fifo`; export is disabled by default.
It forces software decoding for the supported audio codecs and therefore uses
additional CPU time. Normal playback continues if the consumer is absent or slow.

Each chunk starts with a 16-byte little-endian header: magic `0x504d4341`
(bytes `ACMP`), sample rate (32 bits), channel count (16 bits), sample depth
(16 bits, always 16), and payload size (32 bits). The payload is interleaved
signed S16LE audio. Rate and channel count can change between chunks.

Header and payload are written atomically in complete sample frames within
`PIPE_BUF`. Consumers must read complete chunks and tolerate dropped chunks.
Use one reader; multiple readers divide the bytes instead of receiving copies.
Only a FIFO owned by the player user is accepted; newly created FIFOs use mode
0600. The FIFO remains on disk between sessions and must not be replaced while
playback is active.
