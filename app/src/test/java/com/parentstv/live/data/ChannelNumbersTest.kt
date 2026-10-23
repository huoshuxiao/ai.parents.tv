package com.parentstv.live.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 数字键台号表（用户 2026-10-23 指令：全频道从 1 开始，CCTV-1…17 对键 1…17，
 * CCTV-5+ 对键 18，其余频道依次追加）。
 *
 * 这一把尺要挡的是三种说谎：
 *  - 台号又退回「本组第几台」——在央视组里按 5 会跳错台，而屏幕上还写着台号；
 *  - 央视前缀按目录里的位置数——目录生成侧或设备端合并把 CCTV-5+ 放到第 6 位时，键 18 就不是它；
 *  - 编号表里有空洞——按 300 时报「没有第 300 台」，但实际 302 个台。
 */
class ChannelNumbersTest {

    private fun channel(id: String, name: String, category: String = "cctv") = TvChannel(
        id = id,
        name = name,
        category = category,
        streams = listOf("http://example.invalid/$id.m3u8"),
    )

    private val cctv17 = (1..17).map { index ->
        channel("cctv$index", "CCTV-$index 频道$index")
    }

    private val fivePlus = channel("cctv-5plus", "CCTV-5+ 体育赛事")
    private val cctv4k = channel("cctv-4k", "CCTV-4K 超高清")
    private val cctv8k = channel("cctv-8k", "CCTV-8K 超高清")
    private val beijing = channel("bjws", "北京卫视", "cctv")
    private val liaoning = channel("lnTy", "辽宁体育", "sports")

    /** 一份与现网目录同形的样本：央视 1-17 之后紧跟 4K，再是 CGTN/卫视/体育。 */
    private val catalog = cctv17 + listOf(cctv4k, fivePlus, beijing, liaoning)

    @Test
    fun keysOneToSeventeenAreCctvOneToSeventeen() {
        val numbered = ChannelNumbers.numbered(catalog)
        repeat(17) { key ->
            assertEquals("键 ${key + 1}", "CCTV-${key + 1} 频道${key + 1}", numbered[key].name)
        }
    }

    @Test
    fun fivePlusTakesKeyEighteenWhereverTheCatalogPutsIt() {
        assertEquals("18", "CCTV-5+ 体育赛事", ChannelNumbers.numbered(catalog)[17].name)
        // 目录把 5+ 写在最前面（设备端合并完全可能这么排），键 18 还得是它。
        val scrambled = listOf(fivePlus) + cctv17 + listOf(cctv4k, beijing, liaoning)
        val numbered = ChannelNumbers.numbered(scrambled)
        assertEquals("CCTV-5+ 体育赛事", numbered[17].name)
        assertEquals("CCTV-1 频道1", numbered[0].name)
    }

    @Test
    fun otherChannelsAreAppendedInCatalogOrderWithoutHoles() {
        val numbered = ChannelNumbers.numbered(catalog)
        assertEquals(catalog.size, numbered.size)
        assertEquals("19 该是 4K", "CCTV-4K 超高清", numbered[18].name)
        assertEquals("20 往后依次追加", listOf("北京卫视", "辽宁体育"), numbered.drop(19).map { it.name })
        val table = ChannelNumbers.table(catalog)
        assertEquals("每个台都有一个号", catalog.size, table.size)
        assertEquals(
            "号要从 1 连到 n，中间不许跳",
            (1..catalog.size).toList(),
            numbered.map { table.getValue(it.id) }.sorted(),
        )
    }

    @Test
    fun absentFivePlusLeavesNoHoleBehind() {
        // 现在装机的目录里就没有 CCTV-5+（四条线全部「连接超时」被删台），
        // 这一档要保证键 18 仍然有台可跳，而不是报「没有第 18 台」。
        val withoutPlus = cctv17 + listOf(cctv4k, beijing, liaoning)
        val numbered = ChannelNumbers.numbered(withoutPlus)
        assertEquals("CCTV-4K 超高清", numbered[17].name)
        assertEquals(withoutPlus.size, numbered.size)
    }

    @Test
    fun cctvNumberParsingIgnoresPlusAndResolutionSuffixes() {
        assertEquals(13, ChannelNumbers.cctvNumber("CCTV-13 新闻"))
        assertEquals(16, ChannelNumbers.cctvNumber("CCTV-16 奥林匹克"))
        assertEquals(1, ChannelNumbers.cctvNumber("  CCTV-1 综合 "))
        assertNull("5+ 不是纯号，不能占 CCTV-5 那一档", ChannelNumbers.cctvNumber("CCTV-5+ 体育赛事"))
        assertNull(ChannelNumbers.cctvNumber("CCTV-4K 超高清"))
        assertNull(ChannelNumbers.cctvNumber("CCTV-8K 超高清"))
        assertNull("CGTN 不带号", ChannelNumbers.cctvNumber("CGTN"))
        assertNull(ChannelNumbers.cctvNumber("北京卫视"))
    }

    @Test
    fun eighteenThroughNineteenAreDifferentChannelsNotTheSameOneTwice() {
        // 负对照：CCTV-5 与 CCTV-5+ 是同一个前缀的两个台，5 不许被 5+ 顶掉，也不许共用一号。
        val numbered = ChannelNumbers.numbered(catalog)
        assertEquals("CCTV-5 频道5", numbered[4].name)
        assertEquals("CCTV-5+ 体育赛事", numbered[17].name)
        assertEquals("两个台各占一号", 1, numbered.count { it.id == "cctv5" })
        assertEquals(1, numbered.count { it.id == "cctv-5plus" })
    }

    @Test
    fun channelBeyondSeventeenIsNotTreatedAsPrefix() {
        // 「CCTV-18」这种名字真要出现了，它只能进「依次追加」那一队，不许挤掉 5+ 的 18 号。
        val with18 = cctv17 + listOf(
            channel("cctv18", "CCTV-18 农业农村"),
            fivePlus,
            cctv4k,
        )
        val numbered = ChannelNumbers.numbered(with18)
        assertEquals("CCTV-5+ 体育赛事", numbered[17].name)
        assertEquals("CCTV-18 农业农村", numbered[18].name)
        assertEquals("CCTV-4K 超高清", numbered[19].name)
    }

    @Test
    fun resolutionSuffixNeverStealsACctvNumber() {
        // CCTV-8K 排在 CCTV-8 前面（4K/8K 那一批就在央视组前后晃，合并进来的目录完全可能这样排）：
        // 键 8 必须是 CCTV-8 电视剧，不许是 8K，也不许让 8 落进「依次追加」那一队。
        val swapped = listOf(cctv8k, cctv4k) + cctv17 + listOf(fivePlus, beijing)
        val numbered = ChannelNumbers.numbered(swapped)
        assertEquals("CCTV-8 频道8", numbered[7].name)
        assertEquals("CCTV-5+ 体育赛事", numbered[17].name)
        assertEquals("CCTV-8K 超高清", numbered[18].name)
        assertEquals("CCTV-4K 超高清", numbered[19].name)
        assertEquals("北京卫视", numbered[20].name)
        assertEquals(swapped.size, numbered.size)
    }

    @Test
    fun catalogSizedTableIsContiguousAndTotal() {
        // 装机目录那一份是 324 个台（10-23 卫视扩容两批后，兵团卫视按用户点档删掉）：台号必须正好铺满 1…324，一个不丢、一个不重。
        val big = (1..17).map { index -> channel("cctv$index", "CCTV-$index 频道$index") } +
            channel("cctv5plus", "CCTV-5+ 体育赛事") +
            (19..324).map { index -> channel("ch$index", "第 $index 个台") }
        val numbered = ChannelNumbers.numbered(big)
        val table = ChannelNumbers.table(big)
        assertEquals(big.size, numbered.size)
        assertEquals(big.size, table.size)
        assertEquals((1..big.size).toList(), numbered.map { table.getValue(it.id) })
        assertEquals("CCTV-5+ 体育赛事", numbered[17].name)
        assertEquals("第 19 个台", numbered[18].name)
    }
}
