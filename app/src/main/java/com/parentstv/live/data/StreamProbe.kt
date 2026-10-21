package com.parentstv.live.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import javax.net.ssl.SSLException

object StreamProbe {
    data class Result(
        val responseMs: Long?,
        val checkedAt: Long,
        val error: String?,
    )

    suspend fun probe(url: String): Result = withContext(Dispatchers.IO) {
        probeBlocking(url)
    }

    private fun probeBlocking(url: String): Result {
        val startedAt = System.currentTimeMillis()
        var connection: HttpURLConnection? = null
        return try {
            var target = URL(url)
            var redirects = 0
            var outcome: Result? = null
            while (outcome == null) {
                connection = (target.openConnection() as HttpURLConnection).apply {
                    connectTimeout = 5_000
                    readTimeout = 7_000
                    instanceFollowRedirects = false
                    setRequestProperty("User-Agent", USER_AGENT)
                    setRequestProperty("Range", "bytes=0-1023")
                    setRequestProperty("Accept", "*/*")
                }
                val code = connection.responseCode
                if (code in 300..399) {
                    val location = connection.getHeaderField("Location")
                    connection.disconnect()
                    connection = null
                    if (location.isNullOrBlank() || redirects >= 4) {
                        outcome = result(startedAt, "跳转异常")
                    } else {
                        target = URL(target, location)
                        redirects += 1
                    }
                    continue
                }
                if (code !in 200..299) {
                    outcome = result(startedAt, "HTTP $code")
                    continue
                }
                val responseAt = System.currentTimeMillis()
                val bytes = ByteArray(512)
                val read = connection.inputStream.use { stream ->
                    stream.read(bytes)
                }
                if (read <= 0) {
                    outcome = result(startedAt, "无数据")
                    continue
                }
                val beginning = String(bytes, 0, read, Charsets.ISO_8859_1)
                    .removePrefix("\uFEFF")
                    .trimStart()
                val looksLikeHtml = beginning.startsWith("<") ||
                    beginning.contains("<html", ignoreCase = true)
                val looksLikeHls = beginning.startsWith("#EXTM3U")
                outcome = if (looksLikeHtml && !looksLikeHls) {
                    result(startedAt, "返回网页")
                } else {
                    Result(
                        responseMs = (responseAt - startedAt).coerceAtLeast(0),
                        checkedAt = responseAt,
                        error = null,
                    )
                }
            }
            outcome ?: result(startedAt, "检测失败")
        } catch (_: SocketTimeoutException) {
            result(startedAt, "响应超时")
        } catch (_: UnknownHostException) {
            result(startedAt, "域名解析失败")
        } catch (_: SSLException) {
            result(startedAt, "证书错误")
        } catch (_: ConnectException) {
            result(startedAt, "连接失败")
        } catch (_: Throwable) {
            result(startedAt, "检测失败")
        } finally {
            connection?.disconnect()
        }
    }

    private fun result(startedAt: Long, error: String) = Result(
        responseMs = null,
        checkedAt = System.currentTimeMillis().coerceAtLeast(startedAt),
        error = error,
    )

    private const val USER_AGENT =
        "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
