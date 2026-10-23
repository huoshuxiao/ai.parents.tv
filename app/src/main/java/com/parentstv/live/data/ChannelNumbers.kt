package com.parentstv.live.data

/**
 * 遥控器数字键的台号表。
 *
 * 用户 2026-10-23 指令：全频道从 1 开始编号，CCTV-1…CCTV-17 对键 1…17，
 * CCTV-5+ 对键 18，其余频道依次追加。
 *
 * 两条与生俱来的取舍，写在代码里而不是只在文档里：
 *  - 台号按**整份目录**编，不按「本组第几台」编，所以在央视组里输 25 也能跳到第 25 台；
 *    目录里台数会变（公开源合并、删台），台号跟着目录走，中间不留空洞。
 *  - 央视那 18 位显式排在最前面，不依赖目录里它们恰好排在哪一位：设备端会把
 *    公开源合并进来的新目录落盘，那份目录里 CCTV-5+ 落在第几位由合并结果决定，
 *    而用户要的 18 号是 CCTV-5+。目录生成侧的同一条规则在 tools/build_catalog.py 的 CCTV_NAMES。
 */
object ChannelNumbers {

    /** 央视的台号上限：CCTV-1…CCTV-17 占 1…17。 */
    private const val CCTV_LAST = 17

    /**
     * 「CCTV-5 体育」→ 5，「CCTV-13 新闻」→ 13。
     * 号后面紧跟的必须是空白或结尾：`CCTV-4K 超高清`、`CCTV-8K 超高清` 里的 4 和 8
     * 是分辨率不是台号，`CCTV-5+` 的 5 后面是加号，三者都不算纯号。
     */
    private val CCTV_PLAIN_NUMBER = Regex("^CCTV-(\\d{1,2})(?![\\dA-Za-z+])")

    private const val FIVE_PLUS_PREFIX = "CCTV-5+"

    /** 下标 i 的那个台 = 数字键 i+1。入参一般是 `ChannelCatalog.playableChannels()`（目录原序）。 */
    fun numbered(playable: List<TvChannel>): List<TvChannel> {
        val prefixIndexes = (1..CCTV_LAST).map { number ->
            playable.indexOfFirst { cctvNumber(it.name) == number }
        }.filter { it >= 0 }.toSet()
        val fivePlusIndex = playable.indexOfFirst { isFivePlus(it.name) }
        val reserved = prefixIndexes + (if (fivePlusIndex >= 0) setOf(fivePlusIndex) else emptySet())
        val head = (1..CCTV_LAST).mapNotNull { number ->
            playable.firstOrNull { cctvNumber(it.name) == number }
        }
        val tail = playable.filterIndexed { position, _ -> position !in reserved }
        return head + listOfNotNull(fivePlusIndex.takeIf { it >= 0 }?.let { playable[it] }) + tail
    }

    /** id → 台号（从 1 起）。选台面板和播放界面都念这一份，免得两处各数各的。 */
    fun table(playable: List<TvChannel>): Map<String, Int> =
        numbered(playable).mapIndexed { position, channel -> channel.id to position + 1 }.toMap()

    /** 频道名里的央视台号；取不到就返回 null（CGTN、CCTV-4K/8K、CCTV-5+ 这些都算取不到）。 */
    fun cctvNumber(name: String): Int? =
        CCTV_PLAIN_NUMBER.find(name.trim())?.groupValues?.get(1)?.toIntOrNull()

    /** 「CCTV-5+ 体育赛事」这类加号台，与 CCTV-5 是两个台。 */
    fun isFivePlus(name: String): Boolean = name.trim().startsWith(FIVE_PLUS_PREFIX)
}
