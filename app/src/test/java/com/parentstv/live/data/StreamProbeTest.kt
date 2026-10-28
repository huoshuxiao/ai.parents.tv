package com.parentstv.live.data

import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import kotlin.concurrent.thread

/**
 * 「可用」必须等于清单和画面两步都取到。
 *
 * 2026-10-21 真机反馈：辽宁体育线路 1 显示可用、实际播不出来——旧探测只取清单首包，
 * 对端给一份 200 + `#EXTM3U` 的僵尸清单就会被报成可用。这里每一格都钉住一种说谎方式。
 */
class StreamProbeTest {
    private lateinit var server: ServerSocket
    private lateinit var serverThread: Thread
    private var baseUrl: String = ""

    private val ts = ByteArray(2_048) { index -> if (index == 0) 0x47 else (index % 251).toByte() }

    /** huya 那种代理直连：地址里没有 `.flv`，正文却是 FLV 头（本机闻出容器不支持的 53 条里有 13 条是这种）。 */
    private val flv: ByteArray = byteArrayOf('F'.code.toByte(), 'L'.code.toByte(), 'V'.code.toByte(), 0x08, 0x04) +
        ByteArray(2_048) { (it % 251).toByte() }

    @Before
    fun setUp() {
        server = ServerSocket(0, 20, InetAddress.getByName("127.0.0.1"))
        baseUrl = "http://127.0.0.1:${server.localPort}"
        serverThread = thread(isDaemon = true, name = "stream-probe-test-server") {
            while (!server.isClosed) {
                val socket = try {
                    server.accept()
                } catch (_: IOException) {
                    break
                }
                socket.use { connection ->
                    val reader = connection.getInputStream().bufferedReader()
                    val requestLine = reader.readLine() ?: return@use
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) break
                    }
                    val path = requestLine.split(' ').getOrNull(1).orEmpty()
                    val (code, headers, body) = respond(path)
                    val output = connection.getOutputStream()
                    output.write(
                        (
                            "HTTP/1.1 $code Test\r\n" +
                                "$headers\r\n" +
                                "Content-Length: ${body.size}\r\n" +
                                "Connection: close\r\n\r\n"
                            ).toByteArray(),
                    )
                    output.write(body)
                    output.flush()
                }
            }
        }
    }

    /** 路径 → (状态码, 头, 正文)。清单里的相对分片一律按「请求它的那份清单所在的地址」解。 */
    private fun respond(path: String): Triple<Int, String, ByteArray> = when (path) {
        "/ok" -> Triple(200, "Content-Type: video/mp2t", ts)
        "/html" -> Triple(200, "Content-Type: text/html; charset=utf-8", "<html><body>blocked</body></html>".toByteArray())
        "/redirect" -> Triple(302, "Location: /ok", ByteArray(0))
        // 清单被 302 到 /live/ 下面：分片只有 /live/seg.ts 存在，根下的 /seg.ts 故意 404。
        "/redirect-hls" -> Triple(302, "Location: /live/index.m3u8", ByteArray(0))
        "/live/index.m3u8" -> Triple(
            200,
            "Content-Type: application/vnd.apple.mpegurl",
            "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nseg.ts\n#EXTINF:10,\nseg2.ts\n".toByteArray(),
        )
        "/live/seg.ts", "/live/seg2.ts" -> Triple(200, "Content-Type: video/mp2t", ts)
        "/master.m3u8" -> Triple(
            200,
            "Content-Type: application/vnd.apple.mpegurl",
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4000000\nlive/index.m3u8\n".toByteArray(),
        )
        "/hls-dead.m3u8" -> Triple(
            200,
            "Content-Type: application/vnd.apple.mpegurl",
            "#EXTM3U\n#EXTINF:10,\nnope.ts\n".toByteArray(),
        )
        "/hls-htmlseg.m3u8" -> Triple(
            200,
            "Content-Type: application/vnd.apple.mpegurl",
            "#EXTM3U\n#EXTINF:10,\nblocked.ts\n".toByteArray(),
        )
        "/blocked.ts" -> Triple(200, "Content-Type: text/html", "<html><body>403 by proxy</body></html>".toByteArray())
        "/empty.m3u8" -> Triple(200, "Content-Type: application/vnd.apple.mpegurl", "#EXTM3U\n".toByteArray())
        // 内容型标成 text/html、正文却是 #EXTM3U 的源：清单这一层要认。
        "/hls-mislabeled" -> Triple(
            200,
            "Content-Type: application/vnd.apple.mpegurl",
            "#EXTM3U\n#EXTINF:5,\nsegment.ts\n".toByteArray(),
        )
        // 地址里根本没有 .flv，正文是 FLV 头：扩展名那把尺看不见，只有闻字节才拦得住。
        "/huya/11342412" -> Triple(200, "Content-Type: video/x-flv", flv)
        "/hls-flv.m3u8" -> Triple(
            200,
            "Content-Type: application/vnd.apple.mpegurl",
            "#EXTM3U\n#EXTINF:10,\nclip\n".toByteArray(),
        )
        "/clip" -> Triple(200, "Content-Type: application/octet-stream", flv)
        else -> Triple(404, "Content-Type: text/plain", "not found".toByteArray())
    }

    @After
    fun tearDown() {
        server.close()
        serverThread.join(2_000)
    }

    @Test
    fun bareStreamWithRealBytesIsPlayable() = runBlocking {
        val before = System.currentTimeMillis()
        val result = StreamProbe.probe("$baseUrl/ok")
        assertTrue("裸流应当判可用", result.playable)
        assertNotNull(result.responseMs)
        assertNotNull(result.segmentMs)
        assertTrue(result.checkedAt >= before)
    }

    @Test
    fun redirectIsFollowed() = runBlocking {
        assertTrue(StreamProbe.probe("$baseUrl/redirect").playable)
    }

    @Test
    fun httpErrorIsReported() = runBlocking {
        val result = StreamProbe.probe("$baseUrl/missing")
        assertEquals("HTTP 404", result.error)
        assertFalse(result.playable)
    }

    @Test
    fun htmlBlockPageIsNotCountedAsAStream() = runBlocking {
        assertEquals("返回网页", StreamProbe.probe("$baseUrl/html").error)
    }

    /**
     * 这一格钉的是「清单通、画面取不到」不能报成可用——用户真机撞到的就是它。
     * 旧实现只看清单首包，这里会给出 `responseMs != null` 的绿。
     */
    @Test
    fun zombiePlaylistIsNotReportedAsAvailable() = runBlocking {
        val result = StreamProbe.probe("$baseUrl/hls-dead.m3u8")
        assertNotNull("清单确实取到了", result.responseMs)
        assertNull("分片没取到就不许有画面耗时", result.segmentMs)
        assertFalse("绝不能判成可用", result.playable)
        assertTrue("错误要落在分片那一层，实际=${result.error}", result.error?.startsWith("分片") == true)
    }

    @Test
    fun segmentServingHtmlIsReportedAsPage() = runBlocking {
        val result = StreamProbe.probe("$baseUrl/hls-htmlseg.m3u8")
        assertEquals("分片是网页", result.error)
        assertFalse(result.playable)
    }

    @Test
    fun playlistWithoutAnySegmentIsReported() = runBlocking {
        assertEquals("清单无分片", StreamProbe.probe("$baseUrl/empty.m3u8").error)
    }

    /**
     * 302 之后分片要按**跳转后**的地址解：`/redirect-hls` → `/live/index.m3u8`，
     * 分片只在 `/live/` 下存在，按原始地址拼会打到根下的 `/seg.ts`（404）。
     */
    @Test
    fun segmentsResolveAgainstTheRedirectTarget() = runBlocking {
        val result = StreamProbe.probe("$baseUrl/redirect-hls")
        assertTrue("按跳转后的基址取片，这条线判可用；实际=$result", result.playable)
        // 同一份清单按原始地址解就会打空——把「错基址」这条路本身钉死，别让它悄悄变绿。
        val wrongBase = StreamProbe.resolve("$baseUrl/redirect-hls", "seg.ts")
        assertEquals("$baseUrl/seg.ts", wrongBase)
        assertFalse("根下那枚分片必须不存在，否则这格没有牙", StreamProbe.probe(wrongBase).playable)
    }

    @Test
    fun masterPlaylistDescendsToOneVariant() = runBlocking {
        assertTrue(StreamProbe.probe("$baseUrl/master.m3u8").playable)
    }

    @Test
    fun contentTypeMislabelDoesNotKillARealPlaylist() = runBlocking {
        // 清单这一层仍然认（正文是 #EXTM3U），但它指的那枚分片不存在 ⇒ 不能判可用。
        val result = StreamProbe.probe("$baseUrl/hls-mislabeled")
        assertNotNull(result.responseMs)
        assertFalse(result.playable)
    }

    @Test
    fun segmentRefsPrefersTheSecondToLastAndSkipsPlaylistRefs() {
        val five = "#EXTM3U\n#EXTINF:10,\na.ts\n#EXTINF:10,\nb.ts\n#EXTINF:10,\nc.ts\n#EXTINF:10,\nd.ts\n#EXTINF:10,\ne.ts\n"
        assertEquals("倒数第二优先、最后其次、中间第三", listOf("d.ts", "e.ts", "c.ts"), StreamProbe.segmentRefs(five))
        val three = "#EXTM3U\n#EXTINF:10,\na.ts\n#EXTINF:10,\nb.ts\n#EXTINF:10,\nc.ts\n"
        assertEquals("中间那枚与倒数第二重合时不许重复取", listOf("b.ts", "c.ts"), StreamProbe.segmentRefs(three))
        assertEquals(
            "变体清单不该被当成分片",
            listOf<String>(),
            StreamProbe.segmentRefs("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n"),
        )
    }

    @Test
    fun variantRefsReadsStreamInfLines() {
        val text = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=4000\nhigh.m3u8\n"
        assertEquals(listOf("low.m3u8", "high.m3u8"), StreamProbe.variantRefs(text))
    }

    /**
     * 探测取到字节 ≠ 电视上有画面：ExoPlayer 没有 FLV/MKV/ASF/RM/AVI 的解复用器。
     * 这一格钉的是**没有扩展名**的那一类 —— 本机闻出容器不支持的 53 条里 13 条是这种
     * （`https://live.metshop.top/huya/11342412`），只看 URL 结尾的 `StreamRules` 挡不住，
     * 少了它，按一次「立即更新」会把这种黑屏线报成「可用」并顶到第 1 位。
     */
    @Test
    fun flvWithoutExtensionIsNotReportedAsPlayable() = runBlocking {
        val result = StreamProbe.probe("$baseUrl/huya/11342412")
        assertNotNull("清单这一层确实取到了字节", result.responseMs)
        assertNull("FLV 不许算画面耗时", result.segmentMs)
        assertFalse("解不开的容器绝不能判成可用", result.playable)
        assertTrue("错误要落在容器这一层，实际=${result.error}",
            result.error?.startsWith("容器不支持") == true)
    }

    @Test
    fun flvSegmentBehindAPlaylistIsNotReportedAsPlayable() = runBlocking {
        val result = StreamProbe.probe("$baseUrl/hls-flv.m3u8")
        assertNotNull("清单是通的", result.responseMs)
        assertFalse("分片是 FLV ⇒ 不许判可用", result.playable)
        assertTrue("错误要写出容器，实际=${result.error}", result.error?.startsWith("容器不支持") == true)
    }

    @Test
    fun containerSniffingKeepsWhatExoPlayerCanDemux() {
        // 负对照：TS（0x47）与 MP4（ftyp）是 ExoPlayer 解得开的，闻错成"不支持"就会把能播的线打死。
        assertNull(StreamProbe.undemuxableContainer(ts))
        val mp4 = ByteArray(64)
        mp4[4] = 'f'.code.toByte(); mp4[5] = 't'.code.toByte()
        mp4[6] = 'y'.code.toByte(); mp4[7] = 'p'.code.toByte()
        assertNull(StreamProbe.undemuxableContainer(mp4))
        assertNull(StreamProbe.undemuxableContainer(ByteArray(0)))
        // 正对照：五种解不开的头部各钉一枚。
        assertEquals("FLV", StreamProbe.undemuxableContainer(flv))
        assertEquals("MKV", StreamProbe.undemuxableContainer(
            byteArrayOf(0x1A, 0x45, 0xDF.toByte(), 0xA3.toByte()) + ByteArray(32)))
        assertEquals("RMVB", StreamProbe.undemuxableContainer(".RMF".toByteArray() + ByteArray(32)))
        assertEquals("ASF/WMV", StreamProbe.undemuxableContainer(
            byteArrayOf(0x30, 0x82.toByte(), 0xB2.toByte(), 0x7B) + ByteArray(32)))
        val avi = ByteArray(64)
        "RIFF".toByteArray().copyInto(avi, 0); "AVI ".toByteArray().copyInto(avi, 8)
        assertEquals("AVI", StreamProbe.undemuxableContainer(avi))
    }

    /**
     * 「线路清晰度计算一次就好，不需要每次打开线路时都重算」（用户 10-26 指令）。
     *
     * 盯四种会说谎的情形：
      · A 算过了还重探——面板每开一次就把本台每条线重探一遍，抢正在播那条的首片带宽（＝「缓冲中」）。
      · B 按了「重新检测」却不重探——用户主动要新读数，缓存不许挡。
      · C 缓存串台——同一条地址的两种写法要认得是同一枚（按规范地址存），但别的地址不许蹭。
      · D 记了却读不到——rememberResult 与 cached 必须用同一把归一尺，否则缓存永远不命中（恒探）。
     */
    @Test
    fun clarityIsComputedOncePerLine() {
        val fresh = "http://10.20.30.40/live/once-a.m3u8"
        val other = "http://10.20.30.40/live/once-b.m3u8"
        val streams = listOf(fresh, other)
        val hit = StreamProbe.Result(12L, 34L, 0L, null, "1920×1080")

        // A 的正对照：两条都没算过 ⇒ 全都要探。
        assertEquals(listOf(0 to fresh, 1 to other), StreamProbe.linesToProbe(streams, force = false))
        // D：记一笔之后必须读得回来，并且规划器不再安排它。
        StreamProbe.rememberResult(fresh, hit)
        assertEquals(hit, StreamProbe.cached(fresh))
        assertEquals(listOf(1 to other), StreamProbe.linesToProbe(streams, force = false))
        // B：按了「重新检测」⇒ 算过的那条也要重探。
        assertEquals(2, StreamProbe.linesToProbe(streams, force = true).size)
        // C：缓存不许蹭到别的地址上（other 仍然没算过；单独成表时它的下标是 0）。
        assertNull(StreamProbe.cached(other))
        assertEquals(listOf(0 to other), StreamProbe.linesToProbe(listOf(other), force = false))
        // A 的负对照：同一枚地址换一种写法（多一个无害的查询串顺序）仍算"已算过"。
        val sameUrl = "$fresh?x=1"
        StreamProbe.rememberResult(sameUrl, hit)
        if (StreamUrl.canonical(sameUrl) == StreamUrl.canonical(fresh)) {
            assertTrue(StreamProbe.linesToProbe(listOf(fresh), force = false).isEmpty())
        }
        // 负对照（同一把尺的另一侧）：完全没见过的地址必须安排探，不许被缓存误判成"算过"。
        assertEquals(
            listOf(0 to "http://10.20.30.40/live/never-seen.m3u8"),
            StreamProbe.linesToProbe(listOf("http://10.20.30.40/live/never-seen.m3u8"), force = false),
        )
    }
}
