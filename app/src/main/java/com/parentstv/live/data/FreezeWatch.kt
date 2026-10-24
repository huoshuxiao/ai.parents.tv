package com.parentstv.live.data

/**
 * 画面冻住这条尺：整幅像素连续多少毫秒不变，才算「这个台没在给新画面」。
 *
 * 为什么现有一堆看门狗抓不到它：源在喂同一帧的时候，分片照到、解码器照 render 新帧、
 * position 照走、playbackState 一直是 READY——播放器侧样样"正常"，只有屏幕上的画面不动。
 * 目录侧那条长窗尺（tools/freeze_scan.py）能抓到这种读数，但它只能判「这条地址坏了」；
 * 整台所有线一起冻住（县台半夜自动播出挂同一帧，10-24 凌晨实测到 38 条）删地址也修不好，
 * 这一格就只剩设备侧能看见。
 *
 * 判据本体只有 observe 一个函数：采样（PixelCopy）与动作（原地重连）都在外面，
 * 这样 JVM 里能拿合成哈希正反各验一遍，不用真起播放器。
 */
object FreezeWatch {

    /** 采样间隔：3 秒取一帧。 */
    const val SAMPLE_MS = 3_000L

    /** 静止满多少毫秒才算冻。比这短会误伤真节目里的静止镜头（电影里一个定格十几秒很常见），
     *  比这长就是让看电视的人多盯一段木头画面。 */
    const val SILENCE_MS = 30_000L

    /** 每个通道留高 4 位：编码噪声、台标呼吸、字幕抗锯齿都吃掉，不然静止画面也会因为
     *  逐位不同而被读成"在动"。 */
    private const val QUANT = 4

    private const val FNV_OFFSET = -0x340d631b1f19b4b9L  // 14695981039346656037
    private const val FNV_PRIME = 0x100000001b3L         // 1099511628211

    /** 一帧像素（ARGB_8888 的 int 数组）→ 64 位哈希。FNV-1a，量化后逐像素累加。 */
    fun hash(pixels: IntArray): Long {
        var h = FNV_OFFSET
        for (p in pixels) {
            val r = ((p shr 16) and 0xFF) shr QUANT
            val g = ((p shr 8) and 0xFF) shr QUANT
            val b = (p and 0xFF) shr QUANT
            h = h xor (r * 65536L + g * 256L + b)
            h *= FNV_PRIME
        }
        return h
    }

    class State {
        var lastHash: Long = 0L
        var sinceMs: Long = -1L
        var fired: Boolean = false
    }

    /**
     * 喂进一帧的哈希，返回「静止已满 silenceMs」这件事是不是刚被凑齐。
     *
     * 三条规矩：哈希一变就把计时起点挪到此刻（并允许下一轮再报一次）；
     * 一次停留里只报一次（fired），否则每 3 秒就会重连一次、电视上就是一直在黑屏重启;
     * 第一帧只记起点不判（读数还不够长，与目录侧「帧数不足不作判」同一个口径）。
     */
    fun observe(st: State, h: Long, nowMs: Long, silenceMs: Long = SILENCE_MS): Boolean {
        if (st.sinceMs < 0L || h != st.lastHash) {
            st.lastHash = h
            st.sinceMs = nowMs
            st.fired = false
            return false
        }
        if (st.fired) return false
        if (nowMs - st.sinceMs >= silenceMs) {
            st.fired = true
            return true
        }
        return false
    }

    /** 换线/换台/重连之后清一遍，让下一轮能重新报。 */
    fun reset(st: State) {
        st.sinceMs = -1L
        st.fired = false
    }
}
