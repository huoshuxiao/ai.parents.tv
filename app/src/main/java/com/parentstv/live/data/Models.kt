package com.parentstv.live.data

import kotlinx.serialization.Serializable

@Serializable
data class ChannelCatalog(
    val schemaVersion: Int = 1,
    val generatedAt: String = "",
    val sourceNote: String = "",
    // 目录生成时按实测证据判坏的那些**地址**（归一键 -> 对端给过的结论）。
    // 公开源会一遍遍把它们捞回来，只删内置目录挡不住下一次自动更新（10-22 模拟器现读：
    // 一次开机后台更新就让设备缓存比目录多出 151 条判坏的线），所以黑名单要跟目录一起装进 APK。
    val blockedLines: Map<String, String> = emptyMap(),
    // 构建时实测到的画面尺寸（规范地址 -> "宽x高"，取自抽帧的原生尺寸，其次清单自报）。
    // 选线面板要给每条线标清晰度，而设备端现场只探得到正在播的那一条（用户 10-25 指令：
    // 「线路选择画面没看到每条线路的清晰度」），尺寸必须随目录一起打进 APK。
    val lineQuality: Map<String, String> = emptyMap(),
    // 按台标改挂的那几条地址（规范地址 -> 从哪个台名搬到哪个台名，用户 10-25「台标与频道不一致的，
    // 根据台标移入对应卫视频道」）。设备端只用得上「挡回错误台名」这一半：构建已经把这条地址搬进
    // 台标对应的那一台，可公开列表明天还会按旧台名再送一遍过来（与 blockedLines 同一个道理）。
    val lineMoves: Map<String, LineMove> = emptyMap(),
    val categories: List<CategoryDef> = emptyList(),
    val channels: List<TvChannel> = emptyList(),
) {
    fun playableChannels(): List<TvChannel> = channels.filter { it.streams.isNotEmpty() }

    fun categoryName(id: String): String =
        categories.firstOrNull { it.id == id }?.name ?: id

    /** 这条线构建时实测到的尺寸（"宽x高"）；没有实测读数返回 null。 */
    fun qualityOf(stream: String): String? = lineQuality[StreamUrl.canonical(stream)]
}

@Serializable
data class CategoryDef(
    val id: String,
    val name: String,
    val subgroups: List<String> = emptyList(),
)

/** 一条「按台标改挂」的记录：键是规范地址，值写它从哪个台名搬到哪个台名＋结论出处。 */
@Serializable
data class LineMove(
    val from: String = "",
    val to: String = "",
    val evidence: String = "",
)

@Serializable
data class TvChannel(
    val id: String,
    val name: String,
    val category: String,
    val subgroup: String = "",
    val logo: String = "",
    val streams: List<String> = emptyList(),
)
