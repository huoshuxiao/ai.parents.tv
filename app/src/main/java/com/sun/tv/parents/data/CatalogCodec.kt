package com.sun.tv.parents.data

import java.io.ByteArrayInputStream
import java.util.zip.GZIPInputStream

/**
 * 目录资产编解码（用户 10-29：「本地开发 channels.json 还是现状，打包运行时转码，读 url 时解码」）。
 *
 * 分工：
 *  · 源码 `app/src/main/assets/channels.json` **保持明文**，构建工具链（tools 下的 python 脚本）照旧读它；
 *  · 打包时 Gradle 任务 `packCatalog` 把整份 JSON 压成 gzip、再逐字节异或，前置 4 字节 magic，
 *    写成 `assets/catalog.dat` 打进 APK；明文那份不进包（assets 源目录被换成生成目录）。
 *
 * 这样：解压 APK 拿不到 channels.json、`strings` 搜不到一条 http 地址、直接复制也无从下手。
 * 挡的是「解压瞄一眼 / strings 抄一遍 / 把文件拷走」这三类，不挡逆向与抓包——那是另一档。
 *
 * 没有 magic 的输入一律按明文 UTF-8 处理：开发期、或只跑了普通构建没生成转码产物时，
 * 读旧资产也不会把首启搞崩。
 */
object CatalogCodec {
    const val ASSET_NAME = "catalog.dat"
    const val LEGACY_ASSET_NAME = "channels.json"

    private val MAGIC = byteArrayOf(0x50, 0x54, 0x56, 0x31) // "PTV1"

    /** 必须与 `app/build.gradle.kts` 里 packCatalog 的 KEY 逐字节相同。 */
    private val KEY = byteArrayOf(
        0x37, 0x9A.toByte(), 0xC1.toByte(), 0x4E, 0x88.toByte(), 0x2D, 0x6B, 0xF0.toByte(),
        0x15, 0xA7.toByte(), 0x3C, 0xE2.toByte(), 0x59, 0x06, 0xBD.toByte(), 0x71,
    )

    fun looksEncoded(bytes: ByteArray): Boolean =
        bytes.size > MAGIC.size && MAGIC.indices.all { bytes[it] == MAGIC[it] }

    fun decode(bytes: ByteArray): String {
        if (!looksEncoded(bytes)) return String(bytes, Charsets.UTF_8)
        val body = ByteArray(bytes.size - MAGIC.size)
        for (i in body.indices) {
            body[i] = (bytes[MAGIC.size + i].toInt() xor KEY[i % KEY.size].toInt()).toByte()
        }
        return GZIPInputStream(ByteArrayInputStream(body))
            .bufferedReader(Charsets.UTF_8)
            .use { it.readText() }
    }
}
