package com.parentstv.live.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.InputStream
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import javax.net.ssl.SSLException

/**
 * 线路体检：先取清单，再顺着清单取**一片真正的画面字节**，两步都通才叫「可用」。
 *
 * 只测清单会把死线报成可用——对端可以给 200 + `#EXTM3U`，而里面列的分片 404／403／
 * 早就滚出直播窗口（真机上「辽宁体育线路 1 显示可用却播不出来」就是这一类）。
 * 分片的基址必须用**跟完重定向之后**的那条 URL：这类源的清单常被 302 到另一台主机，
 * 分片是相对那一台写的，用原始地址拼会把能播的线一律读成 404。
 */
object StreamProbe {
    data class Result(
        val responseMs: Long?,
        val segmentMs: Long?,
        val checkedAt: Long,
        val error: String?,
    ) {
        /** 界面只许对这一格说「可用」；清单通了但画面取不到的是另一种颜色。 */
        val playable: Boolean get() = responseMs != null && segmentMs != null && error == null
    }

    /** 一步取数的结果：[error] 非空就是没取到。 */
    private data class Step(
        val error: String?,
        val finalUrl: String,
        val body: ByteArray,
        val ms: Long,
        val at: Long,
    )

    suspend fun probe(url: String): Result = withContext(Dispatchers.IO) { probeBlocking(url) }

    private fun probeBlocking(url: String): Result {
        val manifest = fetch(url, MANIFEST_LIMIT, CONNECT_MS, READ_MS)
        val manifestAt = System.currentTimeMillis()
        if (manifest.error != null) return Result(null, null, manifest.at, manifest.error)

        val text = manifest.body.toString(Charsets.ISO_8859_1).removePrefix("\uFEFF")
        return when (classify(text)) {
            Kind.PLAYLIST -> probeMediaFromPlaylist(manifest, text, manifestAt)
            Kind.HTML -> Result(null, null, manifestAt, "返回网页")
            Kind.EMPTY -> Result(null, null, manifestAt, "无数据")
            // 裸 TS／MP4 直连型：这一把读到的就是媒体字节，不必再取第二层。
            else -> {
                if (manifest.body.size < MIN_MEDIA_BYTES) {
                    Result(manifest.ms, null, manifestAt, "首包太小")
                } else {
                    Result(manifest.ms, manifest.ms, manifestAt, null)
                }
            }
        }
    }

    private fun probeMediaFromPlaylist(manifest: Step, text: String, manifestAt: Long): Result {
        var base = manifest.finalUrl
        var playlist = text
        val variants = variantRefs(playlist)
        if (variants.isNotEmpty()) {
            val variant = fetch(resolve(base, variants.last()), MANIFEST_LIMIT, CONNECT_MS, READ_MS)
            if (variant.error != null) return Result(manifest.ms, null, variant.at, "变体${variant.error}")
            val variantText = variant.body.toString(Charsets.ISO_8859_1).removePrefix("\uFEFF")
            if (classify(variantText) != Kind.PLAYLIST) return Result(manifest.ms, null, variant.at, "变体非清单")
            base = variant.finalUrl
            playlist = variantText
        }

        val refs = segmentRefs(playlist)
        if (refs.isEmpty()) return Result(manifest.ms, null, manifestAt, "清单无分片")

        var lastError = "分片取不到"
        for (ref in refs) {
            val segment = fetch(resolve(base, ref), SEGMENT_LIMIT, SEGMENT_CONNECT_MS, SEGMENT_READ_MS)
            if (segment.error != null) {
                lastError = "分片${segment.error}"
                continue
            }
            when (classify(segment.body.toString(Charsets.ISO_8859_1))) {
                Kind.HTML -> {
                    lastError = "分片是网页"
                    continue
                }
                // 分片里再套一层清单：跟着它再走一次，别把能播的线判死。
                Kind.PLAYLIST -> {
                    val nested = probeMediaFromPlaylist(segment, segment.body.toString(Charsets.ISO_8859_1), segment.at)
                    if (nested.playable) return Result(manifest.ms, nested.segmentMs, nested.checkedAt, null)
                    lastError = nested.error ?: "分片是清单"
                    continue
                }
                else -> if (segment.body.size >= MIN_MEDIA_BYTES) {
                    return Result(manifest.ms, segment.ms, segment.at, null)
                } else {
                    lastError = "分片太小"
                }
            }
        }
        return Result(manifest.ms, null, System.currentTimeMillis(), lastError)
    }

    private enum class Kind { PLAYLIST, HTML, EMPTY, MEDIA }

    private fun classify(body: String): Kind = when {
        body.isBlank() -> Kind.EMPTY
        body.trimStart().startsWith("#EXTM3U") -> Kind.PLAYLIST
        body.trimStart().startsWith("<") || body.contains("<html", ignoreCase = true) -> Kind.HTML
        else -> Kind.MEDIA
    }

    /** 直播清单的最后几枚才是当前画面，最后那一枚可能刚写一半 ⇒ 倒数第二优先。 */
    internal fun segmentRefs(text: String): List<String> {
        val refs = text.lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() && !it.startsWith("#") }
            .filter { !it.endsWith(".m3u8", ignoreCase = true) }
            .toList()
        if (refs.isEmpty()) return emptyList()
        val picks = listOfNotNull(refs.getOrNull(refs.size - 2), refs.last(), refs[refs.size / 2])
        return picks.distinct()
    }

    internal fun variantRefs(text: String): List<String> =
        Regex("^#EXT-X-STREAM-INF:[^\\n]*\\n([^\\n#]+)", RegexOption.MULTILINE)
            .findAll(text)
            .map { it.groupValues[1].trim() }
            .filter { it.isNotEmpty() }
            .toList()

    /** 相对引用一律按 [base] 解，[base] 必须是跟完重定向之后的那条地址。 */
    internal fun resolve(base: String, ref: String): String = try {
        if (ref.startsWith("http://", true) || ref.startsWith("https://", true)) ref
        else URL(URL(base), ref).toString()
    } catch (_: Throwable) {
        ref
    }

    private fun fetch(url: String, limit: Int, connectMs: Int, readMs: Int): Step {
        val startedAt = System.currentTimeMillis()
        var target = url
        var redirects = 0
        while (true) {
            var connection: HttpURLConnection? = null
            try {
                connection = (URL(target).openConnection() as HttpURLConnection).apply {
                    connectTimeout = connectMs
                    readTimeout = readMs
                    instanceFollowRedirects = false
                    setRequestProperty("User-Agent", USER_AGENT)
                    setRequestProperty("Range", "bytes=0-${limit - 1}")
                    setRequestProperty("Accept", "*/*")
                }
                val code = connection.responseCode
                if (code in 300..399) {
                    val location = connection.getHeaderField("Location")
                    if (location.isNullOrBlank() || redirects >= MAX_REDIRECTS) {
                        return step("跳转异常", target, startedAt)
                    }
                    target = URL(URL(target), location).toString()
                    redirects += 1
                    continue
                }
                if (code !in 200..299) return step("HTTP $code", target, startedAt)
                val body = connection.inputStream.use { readUpTo(it, limit) }
                val at = System.currentTimeMillis()
                if (body.isEmpty()) return step("无数据", target, startedAt)
                return Step(null, target, body, at - startedAt, at)
            } catch (_: SocketTimeoutException) {
                return step("响应超时", target, startedAt)
            } catch (_: UnknownHostException) {
                return step("域名解析失败", target, startedAt)
            } catch (_: SSLException) {
                return step("证书错误", target, startedAt)
            } catch (_: ConnectException) {
                return step("连接失败", target, startedAt)
            } catch (_: Throwable) {
                return step("检测失败", target, startedAt)
            } finally {
                connection?.disconnect()
            }
        }
    }

    private fun step(error: String, url: String, startedAt: Long) =
        Step(error, url, ByteArray(0), System.currentTimeMillis() - startedAt, System.currentTimeMillis())

    private fun readUpTo(stream: InputStream, limit: Int): ByteArray {
        val buffer = ByteArray(limit)
        var total = 0
        while (total < limit) {
            val read = try {
                stream.read(buffer, total, limit - total)
            } catch (_: SocketTimeoutException) {
                if (total >= MIN_MEDIA_BYTES) break else throw SocketTimeoutException("读取超时")
            } catch (_: Exception) {
                break
            }
            if (read <= 0) break
            total += read
        }
        return if (total == limit) buffer else buffer.copyOf(total)
    }

    private const val MAX_REDIRECTS = 4
    private const val CONNECT_MS = 5_000
    private const val READ_MS = 7_000
    private const val SEGMENT_CONNECT_MS = 5_000
    private const val SEGMENT_READ_MS = 6_000
    private const val MANIFEST_LIMIT = 16_384
    private const val SEGMENT_LIMIT = 8_192
    private const val MIN_MEDIA_BYTES = 1_024

    private const val USER_AGENT =
        "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
