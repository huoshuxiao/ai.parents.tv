package com.parentstv.live.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class StreamUrlTest {

    private val rawChinese = "http://dassby.qqff.top:99/live/辽宁体育/index.m3u8"
    private val encodedChinese =
        "http://dassby.qqff.top:99/live/%E8%BE%BD%E5%AE%81%E4%BD%93%E8%82%B2/index.m3u8"

    @Test
    fun percentEncodedChineseIsTheSameLineAsRawChinese() {
        assertEquals(StreamUrl.canonical(rawChinese), StreamUrl.canonical(encodedChinese))
        assertTrue(StreamUrl.sameLine(rawChinese, encodedChinese))
    }

    @Test
    fun deadListCoversEverySpellingOfOneAddress() {
        listOf(
            "http://59.44.102.18:8888/newlive/live/hls/45/live.m3u8",
            "http://59.44.102.18:8888/newlive/live/hls/45/live.m3u8/",
            "HTTP://59.44.102.18:8888/newlive/live/hls/45/live.m3u8",
            "  http://59.44.102.18:8888/newlive/live/hls/45/live.m3u8  ",
            "http://59.44.102.18:8888/newlive/live/hls/45/live.m3u8 ",
            "\uFEFFhttp://59.44.102.18:8888/newlive/live/hls/45/live.m3u8",
        ).forEach { url ->
            assertTrue("这一种写法应判死：$url", DeadStreams.isDead(url))
        }
    }

    @Test
    fun `用户在电视上实测能播的线路不许被拉黑`() {
        // 这条曾在「本机测不通」时被拉黑，结果把唯一能在电视上出画面的线路清掉了。
        assertFalse(DeadStreams.isDead(rawChinese))
        assertFalse(DeadStreams.isDead(encodedChinese))
    }

    @Test
    fun defaultPortIsDroppedButOtherPortsAreKept() {
        assertTrue(StreamUrl.sameLine("http://example.com:80/a.m3u8", "http://example.com/a.m3u8"))
        assertTrue(StreamUrl.sameLine("https://example.com:443/a.m3u8", "https://example.com/a.m3u8"))
        assertFalse(StreamUrl.sameLine("http://example.com:80/a.m3u8", "http://example.com:8080/a.m3u8"))
    }

    @Test
    fun schemeAndHostAreCaseInsensitiveWhilePathIsNot() {
        assertTrue(StreamUrl.sameLine("HTTP://Example.COM/A.m3u8", "http://example.com/A.m3u8"))
        assertFalse("路径大小写要保留，服务端通常区分大小写",
            StreamUrl.sameLine("http://example.com/A.m3u8", "http://example.com/a.m3u8"))
    }

    @Test
    fun differentProgramIdsOnOneHostStayDifferentLines() {
        val blocked = "http://42.176.185.28:9901/tsfile/live/1007_1.m3u8"
        val sibling = "http://42.176.185.28:9901/tsfile/live/1009_1.m3u8"
        assertTrue("1007_1 在黑名单里", DeadStreams.isDead(blocked))
        assertFalse("同主机其他节目号不跟着一起拉黑", DeadStreams.isDead(sibling))
        assertNotEquals(StreamUrl.canonical(blocked), StreamUrl.canonical(sibling))
    }

    @Test
    fun authTokensInQueryStillTellLinesApart() {
        assertFalse(StreamUrl.sameLine(
            "http://h/g.m3u8?Authinfo=AAA",
            "http://h/g.m3u8?Authinfo=BBB",
        ))
        assertTrue(StreamUrl.sameLine(
            "http://h/g.m3u8?Authinfo=a%2Fb",
            "http://h/g.m3u8?Authinfo=a/b",
        ))
    }

    @Test
    fun emptyQueryIsTheSameLineAsNoQuery() {
        // 内置目录里两种写法并存（cztv 蓝田 channel002/006），旧口径会当成两条线路。
        assertTrue(StreamUrl.sameLine(
            "https://ali-m-l.cztv.com/channels/lantian/channel002/1080p.m3u8?",
            "https://ali-m-l.cztv.com/channels/lantian/channel002/1080p.m3u8",
        ))
        assertFalse("有值的查询串不能当成空", StreamUrl.sameLine(
            "https://ali-m-l.cztv.com/channels/lantian/channel002/1080p.m3u8?zhejiang",
            "https://ali-m-l.cztv.com/channels/lantian/channel002/1080p.m3u8",
        ))
    }

    @Test
    fun ipv6IsDetectedAcrossSchemeAndCase() {
        assertTrue(StreamUrl.isIpv6("http://[2409:8087:1e03:21::2]:6060/a.m3u8"))
        assertTrue(StreamUrl.isIpv6("https://[2409:8087:1e03:21::2]:6060/a.m3u8"))
        assertTrue(StreamUrl.isIpv6("HTTP://[2409:8087:1e03:21::2]:6060/a.m3u8"))
        assertFalse(StreamUrl.isIpv6("http://42.176.185.28:9901/a.m3u8"))
    }

    @Test
    fun canonicalOfCanonicalIsStable() {
        val once = StreamUrl.canonical("HTTP://Example.COM:80/live/%E8%BE%BD/index.m3u8/")
        assertEquals(once, StreamUrl.canonical(once))
    }

    @Test
    fun exactStringCompareMissesWhatCanonicalizationCatches() {
        // 这一格钉住改动前后的差异本身：旧口径是整串相等，下面是同一地址的多种写法，
        // 整串相等全部判成不同线路，而归一化后的比较必须全部判成同一条。
        val pairs = listOf(
            rawChinese to encodedChinese,
            "http://example.com:80/a.m3u8" to "http://example.com/a.m3u8",
            "HTTP://Example.COM/a.m3u8" to "http://example.com/a.m3u8",
            "http://example.com/live/x.m3u8/" to "http://example.com/live/x.m3u8",
            "http://h/g.m3u8?Authinfo=a%2Fb" to "http://h/g.m3u8?Authinfo=a/b",
            "http://h/g.m3u8?" to "http://h/g.m3u8",
        )
        pairs.forEach { (first, second) ->
            assertFalse("整串相等本来就是错的：$first", first == second)
            assertTrue("归一化后应判为同一条：$first", StreamUrl.sameLine(first, second))
        }
    }

    @Test
    fun junkInputFallsBackToTrimmedLowercaseWithoutThrowing() {
        mapOf(
            "" to "",
            "   " to "",
            "Not A URL" to "not a url",
            "http://" to "http://",
            "://x" to "://x",
            "http://[unclosed/a.m3u8" to "http://[unclosed/a.m3u8",
            "http://ex ample.com/a.m3u8" to "http://ex ample.com/a.m3u8",
        ).forEach { (input, expected) ->
            assertEquals("脏输入不该被改写：$input", expected, StreamUrl.canonical(input))
        }
        assertFalse(StreamUrl.isIpv6("not a url"))
    }
}
