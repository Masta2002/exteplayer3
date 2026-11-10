/*
 * LinuxDVB Output handling.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
 *
 */

/* ***************************** */
/* Includes                      */
/* ***************************** */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <linux/dvb/video.h>
#include <linux/dvb/audio.h>

#include <memory.h>
#include <asm/types.h>
#include <pthread.h>
#include <errno.h>
#include <time.h>

#include "stm_ioctls.h"
#include "bcm_ioctls.h"

#include "common.h"
#include "output.h"
#include "debug.h"
#include "misc.h"

/* ***************************** */
/* Makros/Constants              */
/* ***************************** */

/* ***************************** */
/* Types                         */
/* ***************************** */

/* ***************************** */
/* Varaibles                     */
/* ***************************** */

/* ***************************** */
/* Prototypes                    */
/* ***************************** */

/* ***************************** */
/* MISC Functions                */
/* ***************************** */

void PutBits(BitPacker_t * ld, unsigned int code, unsigned int length)
{
    unsigned int bit_buf;
    unsigned int bit_left;

    bit_buf = ld->BitBuffer;
    bit_left = ld->Remaining;

#ifdef DEBUG_PUTBITS
    if (ld->debug)
        dprintf("code = %d, length = %d, bit_buf = 0x%x, bit_left = %d\n", code, length, bit_buf, bit_left);
#endif /* DEBUG_PUTBITS */

    if (length < bit_left)
    {
        /* fits into current buffer */
        bit_buf = (bit_buf << length) | code;
        bit_left -= length;
    }
    else
    {
        /* doesn't fit */
        bit_buf <<= bit_left;
        bit_buf |= code >> (length - bit_left);
        ld->Ptr[0] = (char)(bit_buf >> 24);
        ld->Ptr[1] = (char)(bit_buf >> 16);
        ld->Ptr[2] = (char)(bit_buf >> 8);
        ld->Ptr[3] = (char)bit_buf;
        ld->Ptr   += 4;
        length    -= bit_left;
        bit_buf    = code & ((1 << length) - 1);
        bit_left   = 32 - length;
        bit_buf = code;
    }

#ifdef DEBUG_PUTBITS
    if (ld->debug)
        dprintf("bit_left = %d, bit_buf = 0x%x\n", bit_left, bit_buf);
#endif /* DEBUG_PUTBITS */

    /* writeback */
    ld->BitBuffer = bit_buf;
    ld->Remaining = bit_left;
}

void FlushBits(BitPacker_t * ld)
{
    ld->BitBuffer <<= ld->Remaining;
    while (ld->Remaining < 32)
    {
#ifdef DEBUG_PUTBITS
        if (ld->debug)
            dprintf("flushing 0x%2.2x\n", ld->BitBuffer >> 24);
#endif /* DEBUG_PUTBITS */
        *ld->Ptr++ = ld->BitBuffer >> 24;
        ld->BitBuffer <<= 8;
        ld->Remaining += 8;
    }
    ld->Remaining = 32;
    ld->BitBuffer = 0;
}

/* true when the first line of the file names a HiSilicon SoC,
 * e.g. "hi3798mv200" or "Hi3716MV430" in /proc/stb/info/chipset */
static bool IsHiSiliconChipsetFile(const char *path)
{
    char buf[64];
    size_t i;
    FILE *f = fopen(path, "r");

    if (NULL == f)
    {
        return false;
    }
    if (NULL == fgets(buf, (int)sizeof(buf), f))
    {
        fclose(f);
        return false;
    }
    fclose(f);

    for (i = 0; buf[i] != '\0'; ++i)
    {
        if (buf[i] >= 'A' && buf[i] <= 'Z')
        {
            buf[i] = (char)(buf[i] - 'A' + 'a');
        }
    }
    return 0 == strncmp(buf, "hi3", 3) || NULL != strstr(buf, "hisi");
}

/* The device tree node is missing on a number of HiSilicon receivers, so
 * also accept what the vendor SDK creates and the chipset name of the box. */
static bool IsHiSiliconSTB(void)
{
    static const char *const markers[] = {
        "/sys/firmware/devicetree/base/soc/hisilicon_clock/name",
        "/proc/hisi",
        "/dev/hi_mem",
        "/dev/hi_dmx",
        "/sys/module/hi_media",
        NULL
    };
    int i;

    for (i = 0; markers[i] != NULL; ++i)
    {
        if (access(markers[i], F_OK) != -1)
        {
            return true;
        }
    }
    return IsHiSiliconChipsetFile("/proc/stb/info/chipset");
}

stb_type_t GetSTBType()
{
    static stb_type_t type = STB_UNKNOWN;
    if (type == STB_UNKNOWN) {
        struct stat buffer;
        if (access("/proc/stb/tpm/0/serial", F_OK) != -1) {
            type = STB_DREAMBOX;
        }
        else if (access("/proc/stb/info/vumodel", F_OK) != -1 && \
                 access("/proc/stb/info/boxtype", F_OK) == -1 ) {
            // some STB like Octagon SF4008 has also /proc/stb/info/vumodel
            // but VU PLUS does not have /proc/stb/info/boxtype
            // please see: https://gitlab.com/e2i/e2iplayer/issues/282
            type = STB_VUPLUS;
        }
        else if (IsHiSiliconSTB()) {
            type = STB_HISILICON;
        }
        else {
            type = STB_OTHER;
        }
    }

    return type;
}

/* Optional software playback clock.
 *
 * The player pushes data as fast as the device takes it and relies on the
 * write blocking (or reporting "not writable") while the decoder is full.
 * The devices of some HiSilicon receivers take everything; picture and
 * sound then run too fast. With EP3_OUTPUT_PACING_MS=<ms> in the environment
 * a chunk is held back until it is at most <ms> ahead of real time (audio
 * 500 ms more, so that its buffer does not run dry before the picture).
 * Unset, empty, 0 or negative: off, nothing here runs. */
static pthread_mutex_t g_pacing_mtx = PTHREAD_MUTEX_INITIALIZER;
static int32_t g_pacing_lead_ms = -1;   /* -1: environment not read yet */
static int64_t g_pacing_wall_us = -1;   /* anchor: monotonic time ...   */
static int64_t g_pacing_pts = -1;       /* ... at which this PTS is due */
static int64_t g_pacing_last_pts[PACING_STREAM_MAX] = { -1, -1 };

static int64_t PacingNowUs(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000LL + (int64_t)ts.tv_nsec / 1000LL;
}

int32_t OutputPacingLeadMs(void)
{
    if (g_pacing_lead_ms < 0)
    {
        const char *env = getenv("EP3_OUTPUT_PACING_MS");
        int32_t lead = env ? atoi(env) : 0;
        g_pacing_lead_ms = lead > 0 ? lead : 0;
    }
    return g_pacing_lead_ms;
}

/* forget the anchor, the next chunk sets a new one (seek, trick play) */
void OutputPacingReset(void)
{
    int32_t i;
    pthread_mutex_lock(&g_pacing_mtx);
    g_pacing_wall_us = -1;
    g_pacing_pts = -1;
    for (i = 0; i < PACING_STREAM_MAX; ++i)
    {
        g_pacing_last_pts[i] = -1;
    }
    pthread_mutex_unlock(&g_pacing_mtx);
}

/* pts in 90 kHz units; returns when the chunk may be written */
void OutputPacingWait(void *_context, int32_t stream, int64_t pts)
{
    Context_t *context = (Context_t *)_context;
    int32_t leadMs = OutputPacingLeadMs();
    int64_t now, last, target, ahead, maxAhead;

    if (leadMs <= 0 || pts < 0 || pts == (int64_t)INVALID_PTS_VALUE || NULL == context ||
        stream < 0 || stream >= PACING_STREAM_MAX)
    {
        return;
    }

    /* trick play steps through key frames, real time means nothing there */
    if (context->playback->isForwarding || context->playback->BackWard || context->playback->SlowMotion)
    {
        OutputPacingReset();
        return;
    }

    /* the clock stands during a pause, or the delay bursts out after it */
    if (context->playback->isPaused)
    {
        int64_t pauseStart = PacingNowUs();
        while (context->playback->isPaused && context->playback->isPlaying &&
               !context->playback->isSeeking && 0 == PlaybackDieNow(0))
        {
            usleep(10000);
        }
        pthread_mutex_lock(&g_pacing_mtx);
        if (g_pacing_wall_us >= 0)
        {
            g_pacing_wall_us += PacingNowUs() - pauseStart;
        }
        pthread_mutex_unlock(&g_pacing_mtx);
    }

    now = PacingNowUs();
    maxAhead = (int64_t)leadMs * 1000LL;
    if (stream == PACING_STREAM_AUDIO)
    {
        maxAhead += 500000LL;
    }

    pthread_mutex_lock(&g_pacing_mtx);
    if (g_pacing_pts < 0)
    {
        g_pacing_pts = pts;
        g_pacing_wall_us = now;
    }
    last = g_pacing_last_pts[stream];
    g_pacing_last_pts[stream] = pts;

    if (last >= 0 && (pts - last < -90000LL * 2LL || pts - last > 90000LL * 10LL))
    {
        /* this stream jumped: anchor again. What was written before the
         * jump is still being played, so the new position is due a lead later */
        g_pacing_pts = pts;
        g_pacing_wall_us = now + (int64_t)leadMs * 1000LL;
        pthread_mutex_unlock(&g_pacing_mtx);
        return;
    }

    target = g_pacing_wall_us + ((pts - g_pacing_pts) * 100LL) / 9LL;
    ahead = target - now;
    if (ahead > maxAhead + 2000000LL || ahead < -10000000LL)
    {
        /* far off: the other stream jumped and moved the anchor, or the
         * source stalled for long. Anchor here instead of sleeping it out */
        g_pacing_pts = pts;
        g_pacing_wall_us = now;
        pthread_mutex_unlock(&g_pacing_mtx);
        return;
    }
    pthread_mutex_unlock(&g_pacing_mtx);

    while (ahead > maxAhead)
    {
        int64_t sleepUs = ahead - maxAhead;

        if (!context->playback->isPlaying || context->playback->isSeeking || 0 != PlaybackDieNow(0))
        {
            break;
        }
        usleep(sleepUs > 5000LL ? 5000 : (useconds_t)sleepUs);
        ahead = target - PacingNowUs();
    }
}
