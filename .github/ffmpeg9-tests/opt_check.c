/* Opens an input with the options given as key=value and lists the ones the
 * demuxer (or the protocol) did not take: avformat_open_input() leaves them
 * in the dictionary. A player that sets an option the library does not know
 * gets no error, the option is just ignored.
 * usage: opt_check URL key=value ...   exit 0: every option was taken */
#include <stdio.h>
#include <string.h>

#include <libavformat/avformat.h>
#include <libavutil/dict.h>

int main(int argc, char **argv)
{
    AVFormatContext *s = NULL;
    AVDictionary *opts = NULL;
    const AVDictionaryEntry *e = NULL;
    int left = 0, ret, i;

    if (argc < 2)
        return 2;
    for (i = 2; i < argc; i++) {
        char *eq = strchr(argv[i], '=');
        if (!eq)
            return 2;
        *eq = 0;
        av_dict_set(&opts, argv[i], eq + 1, 0);
    }
    ret = avformat_open_input(&s, argv[1], NULL, &opts);
    if (ret < 0) {
        fprintf(stderr, "open %s: %s\n", argv[1], av_err2str(ret));
        return 3;
    }
    printf("format=%s streams=%u\n", s->iformat->name, s->nb_streams);
    while ((e = av_dict_iterate(opts, e))) {
        printf("unused %s\n", e->key);
        left++;
    }
    avformat_close_input(&s);
    av_dict_free(&opts);
    return left ? 1 : 0;
}
