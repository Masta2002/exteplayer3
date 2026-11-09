/*
 * CI check of the FFmpeg patches 0051 (hls) and 0052 (dashdec): a track
 * that is switched in during playback.
 *
 * Opens <url>, which has at least two audio streams. The last one (B) is
 * set to AVDISCARD_ALL before the first packet, the first one (A) is read
 * for <switch_sec> seconds. Then the discard flags are swapped, as a player
 * does when the user selects another audio track, and the reading goes on.
 *
 *   near  packets of B arrive and the first one is within <tol_sec> of the
 *         last packet of A that was read (the playing position)
 *   far   they do not arrive, or not there
 *   none  no packet of B arrives until the end of the stream
 *
 * usage: track_switch <url> <switch_sec> near|far|none <tol_sec>
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavformat/avformat.h>
#include <libavutil/avutil.h>

#define B_PACKETS 40 /* packets of B to read after the switch */

int main(int argc, char **argv)
{
    AVFormatContext *ic = NULL;
    AVPacket *pkt;
    int a = -1, b = -1, ret, switched = 0, ok;
    int n_b = 0, n_b_before = 0, n_a_after = 0, n_other = 0, backwards = 0;
    double switch_sec, tol, t0 = NAN, playhead = NAN, first_b = NAN, last_b = NAN;

    if (argc != 5) {
        fprintf(stderr, "usage: %s <url> <switch_sec> near|far|none <tol_sec>\n", argv[0]);
        return 2;
    }
    switch_sec = atof(argv[2]);
    tol        = atof(argv[4]);

    ret = avformat_open_input(&ic, argv[1], NULL, NULL);
    if (ret < 0) {
        fprintf(stderr, "open %s: %s\n", argv[1], av_err2str(ret));
        return 2;
    }
    for (unsigned i = 0; i < ic->nb_streams; i++) {
        if (ic->streams[i]->codecpar->codec_type != AVMEDIA_TYPE_AUDIO)
            continue;
        if (a < 0)
            a = i;
        b = i;
    }
    if (a < 0 || a == b) {
        fprintf(stderr, "%s: two audio streams needed (%u streams)\n", argv[1], ic->nb_streams);
        return 2;
    }
    ic->streams[b]->discard = AVDISCARD_ALL;

    pkt = av_packet_alloc();
    if (!pkt)
        return 2;
    while ((ret = av_read_frame(ic, pkt)) >= 0) {
        const AVStream *st = ic->streams[pkt->stream_index];
        int64_t t = pkt->pts != AV_NOPTS_VALUE ? pkt->pts : pkt->dts;
        double ts = t == AV_NOPTS_VALUE ? NAN : t * av_q2d(st->time_base);

        if (pkt->stream_index == a && !switched) {
            if (!isnan(ts)) {
                if (isnan(t0))
                    t0 = ts;
                playhead = ts;
                if (ts - t0 >= switch_sec) {
                    ic->streams[a]->discard = AVDISCARD_ALL;
                    ic->streams[b]->discard = AVDISCARD_DEFAULT;
                    switched = 1;
                }
            }
        } else if (pkt->stream_index == a) {
            n_a_after++;
        } else if (pkt->stream_index == b && !switched) {
            n_b_before++;
        } else if (pkt->stream_index == b) {
            if (!isnan(ts)) {
                if (isnan(first_b))
                    first_b = ts;
                else if (ts < last_b)
                    backwards++;
                last_b = ts;
            }
            n_b++;
        } else {
            n_other++;
        }
        av_packet_unref(pkt);
        if (n_b >= B_PACKETS)
            break;
    }
    av_packet_free(&pkt);
    avformat_close_input(&ic);

    printf("%s: A=#%d B=#%d start %.3f switch at %.3f; B: %d packets, first %.3f last %.3f, "
           "%d backwards; before the switch %d of B, after it %d of A, %d of others; %s\n",
           argv[1], a, b, t0, playhead, n_b, first_b, last_b, backwards,
           n_b_before, n_a_after, n_other,
           ret < 0 ? av_err2str(ret) : "stopped");

    if (!switched) {
        fprintf(stderr, "FAIL: the stream ended before the switch\n");
        return 1;
    }
    ok = n_b >= B_PACKETS && !backwards && !isnan(first_b) && fabs(first_b - playhead) <= tol;
    if (!strcmp(argv[3], "none"))
        ok = n_b == 0;
    else if (!strcmp(argv[3], "far"))
        ok = !ok;
    else if (strcmp(argv[3], "near"))
        return 2;
    printf("%s: expected %s\n", ok ? "OK" : "FAIL", argv[3]);
    return !ok;
}
