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
#include <stdbool.h>
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

static bool ReadFirstLineLower(const char *path, char *buf, size_t bufSize)
{
    FILE *f = fopen(path, "r");
    size_t i = 0;

    if (NULL == f)
    {
        return false;
    }
    if (NULL == fgets(buf, (int)bufSize, f))
    {
        fclose(f);
        return false;
    }
    fclose(f);

    for (i = 0; buf[i] != '\0'; ++i)
    {
        if (buf[i] == '\n' || buf[i] == '\r')
        {
            buf[i] = '\0';
            break;
        }
        if (buf[i] >= 'A' && buf[i] <= 'Z')
        {
            buf[i] = (char)(buf[i] - 'A' + 'a');
        }
    }
    return true;
}

/* Not all HiSilicon receivers (Qviart Dual/Lunix 4K, Zgemma H9/H11, Octagon
 * SX8x, Edision OS Mio+ 4K, ...) have the device tree node. */
static bool IsHiSiliconSTB()
{
    static const char *markers[] = {
        "/sys/firmware/devicetree/base/soc/hisilicon_clock/name",
        "/proc/hisi",
        "/dev/hi_mem",
        "/dev/hi_dmx",
        "/sys/module/hi_media",
        NULL
    };
    char buf[128];
    int i = 0;

    for (i = 0; markers[i] != NULL; ++i)
    {
        if (access(markers[i], F_OK) != -1)
        {
            return true;
        }
    }

    /* e.g. "hi3798mv200" */
    if (ReadFirstLineLower("/proc/stb/info/chipset", buf, sizeof(buf)))
    {
        if (0 == strncmp(buf, "hi3", 3) || NULL != strstr(buf, "hisi"))
        {
            return true;
        }
    }

    return false;
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

/* Output pacing. The player pushes data as fast as the driver takes it and
 * relies on the write blocking when the decoder is full. The emulated devices
 * of some HiSilicon receivers swallow everything, picture and sound then run
 * too fast. This holds the writes to at most <lead> ms ahead of real time, as
 * the clock of a GstBaseSink does for gstplayer.
 *
 * Off by default, not all of these boxes need it: EP3_OUTPUT_PACING_MS=1500
 * switches it on. With the RAM buffer it holds the filling of the buffer. */
typedef struct
{
    int64_t wall_us;    /* clock at the anchor               */
    int64_t pts;        /* PTS at the anchor                 */
    int64_t last_pts;   /* PTS of the last chunk             */
} PacingClock_t;

static int32_t g_pacing_lead_ms = -1;
static pthread_mutex_t g_pacing_mtx = PTHREAD_MUTEX_INITIALIZER;
static int64_t g_shared_wall_us = -1;
static int64_t g_shared_pts     = -1;

static PacingClock_t g_pacing[PACING_STREAM_MAX] =
{
    { -1, -1, -1 },
    { -1, -1, -1 },
};

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

void OutputPacingReset(void)
{
    int32_t i = 0;
    pthread_mutex_lock(&g_pacing_mtx);
    g_shared_wall_us = -1;
    g_shared_pts     = -1;
    for (i = 0; i < PACING_STREAM_MAX; ++i)
    {
        g_pacing[i].wall_us  = -1;
        g_pacing[i].pts      = -1;
        g_pacing[i].last_pts = -1;
    }
    pthread_mutex_unlock(&g_pacing_mtx);
}

static int64_t monotonic_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000LL + (int64_t)ts.tv_nsec / 1000LL;
}

void OutputPacingWait(void *_context, int32_t stream, int64_t pts)
{
    Context_t *context = (Context_t *)_context;
    int32_t leadMs = OutputPacingLeadMs();
    PacingClock_t *clk = NULL;
    int64_t now, jump, target, ahead, maxAhead;

    if (leadMs <= 0 || pts < 0 || pts == (int64_t)INVALID_PTS_VALUE || NULL == context ||
        stream < 0 || stream >= PACING_STREAM_MAX)
    {
        return;
    }

    clk = &g_pacing[stream];
    maxAhead = (int64_t)leadMs * 1000LL;
    /* audio some more ahead, so that its buffer does not run dry */
    if (stream == PACING_STREAM_AUDIO)
    {
        maxAhead += 500000LL;
    }

    /* the clock stands during a pause, or the delay bursts out after it */
    if (context->playback->isPaused && clk->wall_us >= 0)
    {
        int64_t pauseStart = monotonic_us();
        int64_t paused;
        while (context->playback->isPaused && context->playback->isPlaying &&
               !context->playback->isSeeking && 0 == PlaybackDieNow(0))
        {
            usleep(10000);
        }
        paused = monotonic_us() - pauseStart;
        pthread_mutex_lock(&g_pacing_mtx);
        clk->wall_us += paused;
        if (stream == PACING_STREAM_VIDEO && g_shared_wall_us >= 0)
        {
            g_shared_wall_us += paused;
        }
        pthread_mutex_unlock(&g_pacing_mtx);
    }

    now = monotonic_us();

    pthread_mutex_lock(&g_pacing_mtx);
    if (g_shared_pts < 0)
    {
        g_shared_pts     = pts;
        g_shared_wall_us = now;
    }

    if (clk->pts < 0)
    {
        clk->pts      = g_shared_pts;
        clk->wall_us  = g_shared_wall_us;
        clk->last_pts = pts;
        pthread_mutex_unlock(&g_pacing_mtx);
        return;
    }

    jump          = pts - clk->last_pts;
    clk->last_pts = pts;

    /* a real jump: anchor again, keeping the lead */
    if (jump < -90000LL * 2LL || jump > 90000LL * 10LL)
    {
        clk->pts         = pts;
        clk->wall_us     = now + (int64_t)leadMs * 1000LL;
        g_shared_pts     = clk->pts;
        g_shared_wall_us = clk->wall_us;
        pthread_mutex_unlock(&g_pacing_mtx);
        return;
    }

    target = g_shared_wall_us + ((pts - g_shared_pts) * 1000LL) / 90LL;
    pthread_mutex_unlock(&g_pacing_mtx);

    ahead = target - now;
    while (ahead > maxAhead)
    {
        int64_t sleep_us = ahead - maxAhead;

        if (!context->playback->isPlaying || context->playback->isSeeking || 0 != PlaybackDieNow(0))
        {
            break;
        }
        usleep(sleep_us > 5000LL ? 5000 : (useconds_t)sleep_us);
        ahead = target - monotonic_us();
    }
}
