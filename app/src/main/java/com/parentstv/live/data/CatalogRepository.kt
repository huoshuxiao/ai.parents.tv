package com.parentstv.live.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.File

class CatalogRepository(private val context: Context) {
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }
    private val cacheFile = File(context.filesDir, "channels_live.json")
    private val manualLines = ManualLineStore(context)

    suspend fun load(): ChannelCatalog = withContext(Dispatchers.IO) {
        // 手动挂的临时线只在这一层并进去，不写回缓存：写回的话下一次自动更新会把它当成
        // 目录自带的一条线，过期删掉之后又被旧缓存带回来。
        manualLines.merged(decode(readCache() ?: readAssets()))
    }

    suspend fun loadAssets(): ChannelCatalog = withContext(Dispatchers.IO) {
        decode(readAssets())
    }

    suspend fun loadCacheOrNull(): ChannelCatalog? = withContext(Dispatchers.IO) {
        readCache()?.let { runCatching { decode(it) }.getOrNull() }
    }

    private fun decode(text: String): ChannelCatalog = json.decodeFromString(text)

    private fun readCache(): String? = cacheFile
        .takeIf { it.isFile && it.length() > 1024 }
        ?.let { runCatching { it.readText() }.getOrNull() }

    private fun readAssets(): String = context.assets.open("channels.json")
        .bufferedReader(Charsets.UTF_8)
        .use { it.readText() }
}
