/* Reads a stream the way skyjet18/exteplayer3 does, without the outputs:
 * avformat_open_input(), avformat_find_stream_info(), then commands.
 * usage: boxplay URL [key=value ...] -- command ...
 *   rN    read N packets (as fast as they come, the player buffers ahead)
 *   e     read to the end of the stream (or 100000 packets)
 *   tS    read until a packet is S seconds after the start time (or the end)
 *   sS    seek to second S: avformat_seek_file(ctx, -1, INT64_MIN,
 *         start_time + S, INT64_MAX, 0), the call of the player
 *   wS    for S seconds call av_read_frame() every 100 ms (the player goes on
 *         reading after the end of the stream until its finish timeout)
 *   d     print duration and seekability
 * After every command one line: what was read (packets per media type, the
 * first and last timestamp in ms relative to the start time) and the return
 * value of the last call; before it a line for every jump of the timestamps
 * of a stream by more than 1.5 s. The audio stream used for the timestamps is the
 * first one, the others are discarded as the player does. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavformat/avformat.h>
#include <libavutil/dict.h>
#include <libavutil/time.h>

static AVFormatContext *s;
static int vstream = -1, astream = -1;
static int64_t start;

static int64_t ms(const AVPacket *pkt)
{
    AVRational tb = s->streams[pkt->stream_index]->time_base;
    int64_t ts = pkt->pts != AV_NOPTS_VALUE ? pkt->pts : pkt->dts;

    if (ts == AV_NOPTS_VALUE)
        return -1;
    return av_rescale_q(ts, tb, (AVRational){1, 1000}) - start / 1000;
}

/* mode 0: n packets, 1: to the end, 2: until n ms */
static void read_packets(const char *cmd, int mode, int64_t n)
{
    AVPacket *pkt = av_packet_alloc();
    int64_t first[2] = {-1, -1}, last[2] = {-1, -1};
    int count[3] = {0}, seen[2] = {0}, jumps = 0, ret = 0;
    int64_t i;

    for (i = 0; mode ? i < 100000 : i < n; i++) {
        int k;
        ret = av_read_frame(s, pkt);
        if (ret < 0)
            break;
        k = pkt->stream_index == vstream ? 0 : pkt->stream_index == astream ? 1 : 2;
        count[k]++;
        if (k < 2) {
            int64_t t = ms(pkt);
            if (!seen[k])
                first[k] = t;
            else if ((t < last[k] - 1500 || t > last[k] + 1500) && jumps++ < 12)
                printf("  %s jumps from %"PRId64" to %"PRId64" ms (packet %"PRId64")\n",
                       k ? "audio" : "video", last[k], t, i);
            seen[k] = 1;
            last[k] = t;
            if (mode == 2 && t >= n) {
                av_packet_unref(pkt);
                break;
            }
        }
        av_packet_unref(pkt);
    }
    printf("%s: video %d (%"PRId64"..%"PRId64" ms) audio %d (%"PRId64"..%"PRId64" ms) other %d ret=%d%s\n",
           cmd, count[0], first[0], last[0], count[1], first[1], last[1], count[2], ret,
           ret == AVERROR_EOF ? " (end)" : "");
    fflush(stdout);
    av_packet_free(&pkt);
}

int main(int argc, char **argv)
{
    AVDictionary *opts = NULL;
    int i, ret;

    if (argc < 3)
        return 2;
    for (i = 2; i < argc && strcmp(argv[i], "--"); i++) {
        char *eq = strchr(argv[i], '=');
        if (!eq)
            return 2;
        *eq = 0;
        av_dict_set(&opts, argv[i], eq + 1, 0);
    }
    ret = avformat_open_input(&s, argv[1], NULL, &opts);
    av_dict_free(&opts);
    if (ret < 0) {
        printf("open: ret=%d %s\n", ret, av_err2str(ret));
        return 3;
    }
    s->flags |= AVFMT_FLAG_GENPTS;
    ret = avformat_find_stream_info(s, NULL);
    for (unsigned j = 0; j < s->nb_streams; j++) {
        enum AVMediaType t = s->streams[j]->codecpar->codec_type;
        if (t == AVMEDIA_TYPE_VIDEO && vstream < 0)
            vstream = j;
        else if (t == AVMEDIA_TYPE_AUDIO && astream < 0)
            astream = j;
    }
    for (unsigned j = 0; j < s->nb_streams; j++)
        s->streams[j]->discard = (int)j == vstream || (int)j == astream ?
                                 AVDISCARD_DEFAULT : AVDISCARD_ALL;
    start = s->start_time != AV_NOPTS_VALUE ? s->start_time : 0;
    printf("open: ret=%d format=%s streams=%u video=%d audio=%d start_ms=%"PRId64" duration_ms=%"PRId64"\n",
           ret, s->iformat->name, s->nb_streams, vstream, astream, start / 1000,
           s->duration == AV_NOPTS_VALUE ? -1 : s->duration / 1000);
    fflush(stdout);
    for (i++; i < argc; i++) {
        const char *cmd = argv[i];
        double v = cmd[0] ? atof(cmd + 1) : 0;

        switch (cmd[0]) {
        case 'r':
            read_packets(cmd, 0, (int64_t)v);
            break;
        case 'e':
            read_packets(cmd, 1, 0);
            break;
        case 't':
            read_packets(cmd, 2, (int64_t)(v * 1000));
            break;
        case 's':
            ret = avformat_seek_file(s, -1, INT64_MIN, start + (int64_t)(v * 1000000), INT64_MAX, 0);
            printf("%s: ret=%d\n", cmd, ret);
            fflush(stdout);
            break;
        case 'w': {
            AVPacket *pkt = av_packet_alloc();
            int64_t end = av_gettime_relative() + (int64_t)(v * 1000000);
            int got = 0, calls = 0;
            ret = 0;
            while (av_gettime_relative() < end) {
                ret = av_read_frame(s, pkt);
                calls++;
                if (ret >= 0) {
                    got++;
                    av_packet_unref(pkt);
                } else {
                    av_usleep(100000);
                }
            }
            av_packet_free(&pkt);
            printf("%s: %d calls, %d packets, last ret=%d%s\n", cmd, calls, got, ret,
                   ret == AVERROR_EOF ? " (end)" : "");
            fflush(stdout);
            break;
        }
        case 'd':
            printf("d: duration_ms=%"PRId64" unseekable=%d\n",
                   s->duration == AV_NOPTS_VALUE ? -1 : s->duration / 1000,
                   !!(s->ctx_flags & AVFMTCTX_UNSEEKABLE));
            fflush(stdout);
            break;
        default:
            return 2;
        }
    }
    avformat_close_input(&s);
    printf("closed\n");
    return 0;
}
