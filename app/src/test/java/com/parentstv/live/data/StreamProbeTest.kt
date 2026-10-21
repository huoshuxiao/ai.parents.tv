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
            "Content-Type: text/html; charset=utf-8",
            "#EXTM3U\n#EXTINF:5,\nsegment.ts\n".toByteArray(),
        )
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
}
