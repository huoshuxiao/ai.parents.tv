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

    suspend fun load(): ChannelCatalog = withContext(Dispatchers.IO) {
        decode(readCache() ?: readAssets())
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
