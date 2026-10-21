package com.parentstv.live.data

import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import kotlin.concurrent.thread

class StreamProbeTest {
    private lateinit var server: ServerSocket
    private lateinit var serverThread: Thread
    private var baseUrl: String = ""

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
                    val (code, headers, body) = when (path) {
                        "/ok" -> Triple(200, "Content-Type: video/mp2t", byteArrayOf(0x47, 0x40, 0x11, 0x10))
                        "/html" -> Triple(
                            200,
                            "Content-Type: text/html; charset=utf-8",
                            "<html><body>blocked</body></html>".toByteArray(),
                        )
                        "/hls-mislabeled" -> Triple(
                            200,
                            "Content-Type: text/html; charset=utf-8",
                            "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:5,\nsegment.ts\n".toByteArray(),
                        )
                        "/redirect" -> Triple(302, "Location: /ok", ByteArray(0))
                        else -> Triple(404, "Content-Type: text/plain", "not found".toByteArray())
                    }
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

    @After
    fun tearDown() {
        server.close()
        serverThread.join(2_000)
    }

    @Test
    fun readableStreamReportsResponseTimeAndBeijingCheckTime() = runBlocking {
        val before = System.currentTimeMillis()
        val result = StreamProbe.probe("$baseUrl/ok")
        assertNull(result.error)
        assertNotNull(result.responseMs)
        assertTrue(result.responseMs!! >= 0)
        assertTrue(result.checkedAt >= before)
    }

    @Test
    fun redirectIsFollowed() = runBlocking {
        val result = StreamProbe.probe("$baseUrl/redirect")
        assertNull(result.error)
        assertNotNull(result.responseMs)
    }

    @Test
    fun httpErrorIsReported() = runBlocking {
        assertEquals("HTTP 404", StreamProbe.probe("$baseUrl/missing").error)
    }

    @Test
    fun htmlBlockPageIsNotCountedAsAStream() = runBlocking {
        assertEquals("返回网页", StreamProbe.probe("$baseUrl/html").error)
    }

    @Test
    fun validHlsWithMislabeledHtmlContentTypeIsAccepted() = runBlocking {
        val result = StreamProbe.probe("$baseUrl/hls-mislabeled")
        assertNull(result.error)
        assertNotNull(result.responseMs)
    }
}
