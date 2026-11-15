/* dash_subtitle_default_discard: reads a DASH stream that has a subtitle
 * representation the way a player does.
 * usage: dash_sub URL option enable
 *   option 1: open with dash_subtitle_default_discard=1
 *   enable 1: after the first 100 packets set the subtitle stream to
 *             AVDISCARD_DEFAULT (what a player does for the stream it shows)
 * prints the number of audio/video and subtitle packets before and after. */
#include <stdio.h>
#include <stdlib.h>

#include <libavformat/avformat.h>
#include <libavutil/dict.h>

static int read_some(AVFormatContext *s, int sub, int max, int *av, int *st)
{
    AVPacket *pkt = av_packet_alloc();
    int ret = 0, n;

    *av = *st = 0;
    if (!pkt)
        return AVERROR(ENOMEM);
    for (n = 0; n < max; n++) {
        ret = av_read_frame(s, pkt);
        if (ret < 0)
            break;
        if (pkt->stream_index == sub)
            (*st)++;
        else
            (*av)++;
        av_packet_unref(pkt);
    }
    av_packet_free(&pkt);
    return ret == AVERROR_EOF ? 0 : ret;
}

int main(int argc, char **argv)
{
    AVFormatContext *s = NULL;
    AVDictionary *opts = NULL;
    int option, enable, sub = -1, av, st, ret;
    unsigned i;

    if (argc != 4)
        return 2;
    option = atoi(argv[2]);
    enable = atoi(argv[3]);
    if (option)
        av_dict_set(&opts, "dash_subtitle_default_discard", "1", 0);
    ret = avformat_open_input(&s, argv[1], NULL, &opts);
    if (ret < 0) {
        fprintf(stderr, "open: %s\n", av_err2str(ret));
        return 3;
    }
    if (av_dict_count(opts)) {
        fprintf(stderr, "dash_subtitle_default_discard was not taken by the demuxer\n");
        return 4;
    }
    for (i = 0; i < s->nb_streams; i++)
        if (s->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_SUBTITLE)
            sub = i;
    if (sub < 0) {
        fprintf(stderr, "no subtitle stream among %u\n", s->nb_streams);
        return 5;
    }
    printf("streams=%u subtitle=%d discard=%d\n", s->nb_streams, sub,
           s->streams[sub]->discard);
    ret = read_some(s, sub, 100, &av, &st);
    printf("phase1 av=%d sub=%d ret=%d\n", av, st, ret);
    if (ret < 0)
        return 6;
    if (enable)
        s->streams[sub]->discard = AVDISCARD_DEFAULT;
    ret = read_some(s, sub, 100000, &av, &st);
    printf("phase2 av=%d sub=%d ret=%d\n", av, st, ret);
    avformat_close_input(&s);
    return ret < 0 ? 6 : 0;
}
