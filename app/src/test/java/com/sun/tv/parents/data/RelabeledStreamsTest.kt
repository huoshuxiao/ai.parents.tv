package com.sun.tv.parents.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 台标实测挂错频道的线路：自动更新会不会又把它带回错的频道，是这次真机反馈暴露的第二层问题。
 * 内置目录改一次挡不住下一次 CatalogUpdater，所以改挂规则必须跑在合并那一步。
 */
class RelabeledStreamsTest {

    private val xizangUrl =
        "http://112.27.5.218:9901/tsfile/live/0111_1.m3u8?key=txiptv&playlive=1&authid=0"

    /** 同一台主机上的另一个节目号（江西卫视那条），抽帧台标是对的，绝不该被这条规则牵连。 */
    private val jiangxiUrl =
        "http://112.27.5.218:9901/tsfile/live/faacts/0138_1.m3u8?key=txiptv&playlive=1&authid=0"

    @Test
    fun mislabeledLineBelongsToSichuanNotTibet() {
        assertEquals("四川卫视", RelabeledStreams.trueChannel(xizangUrl))
        assertTrue("挂在西藏卫视名下要摘掉", RelabeledStreams.belongsElsewhere("西藏卫视", xizangUrl))
        assertFalse("挂在四川卫视名下要留下", RelabeledStreams.belongsElsewhere("四川卫视", xizangUrl))
        assertEquals(listOf(xizangUrl), RelabeledStreams.linesFor("四川卫视"))
        assertTrue("西藏卫视不该再拿到这条线", RelabeledStreams.linesFor("西藏卫视").isEmpty())
    }

    @Test
    fun sameHostDifferentProgramIsLeftAlone() {
        assertNull("同主机的江西卫视那条没有改挂记录", RelabeledStreams.trueChannel(jiangxiUrl))
        assertFalse(RelabeledStreams.belongsElsewhere("江西卫视", jiangxiUrl))
        assertFalse("也不许被当成四川卫视的改挂线塞进来", RelabeledStreams.linesFor("江西卫视").contains(jiangxiUrl))
    }

    @Test
    fun addressVariantsResolveToTheSameRule() {
        val escaped = "HTTP://112.27.5.218:9901/tsfile/live/0111_1.m3u8?key=txiptv&playlive=1&authid=0"
        assertEquals("大写写法要认得是同一条", RelabeledStreams.trueChannel(escaped), "四川卫视")
        assertTrue(RelabeledStreams.belongsElsewhere("西藏卫视", escaped))
    }

    @Test
    fun channelNameVariantsResolveToTheSameChannel() {
        // ChannelName.normalize 会剥掉「高清/频道」等字样，所以这些写法都算同一个台。
        assertEquals(listOf(xizangUrl), RelabeledStreams.linesFor("四川卫视高清"))
        assertTrue("西藏卫视高清同样不许留", RelabeledStreams.belongsElsewhere("西藏卫视高清", xizangUrl))
    }

    // ── 10-24「保留有台标的线路」：#96 乙 从目录里删掉的三对挂错台，要在合并这一步也挡住。 ──
    // 不写进这张表的话，公开源下一次把它们带给错的台名时，删掉的台会自动回来、继续播别人的节目。

    private val andaUrl = "https://liveout.xntv.tv/a65jur/96iln2.m3u8"

    @Test
    fun xntvLineBelongsToXiningNotAnduo() {
        assertEquals("西宁新闻综合", RelabeledStreams.trueChannel(andaUrl))
        assertTrue("安多卫视名下这条必须摘", RelabeledStreams.belongsElsewhere("安多卫视", andaUrl))
        assertFalse("挂在西宁新闻综合名下要留", RelabeledStreams.belongsElsewhere("西宁新闻综合", andaUrl))
        assertEquals(listOf(andaUrl), RelabeledStreams.linesFor("西宁新闻综合"))
    }

    /** 内置目录里那条写的是 `tvfile.jyrmt.cn:80`；canonical 会去掉默认端口，两种写法是同一条。 */
    private val jiaxingUrl = "http://tvfile.jyrmt.cn:80/nmip-media/channellive/channel104452/playlist.m3u8"

    @Test
    fun jyrmtLineBelongsToJingyanNotJiaxing() {
        assertEquals("井研综合", RelabeledStreams.trueChannel(jiaxingUrl))
        assertTrue("嘉兴新闻综合名下这条必须摘", RelabeledStreams.belongsElsewhere("嘉兴新闻综合", jiaxingUrl))
        assertFalse("挂在井研综合名下要留", RelabeledStreams.belongsElsewhere("井研综合", jiaxingUrl))
        assertEquals(listOf("http://tvfile.jyrmt.cn/nmip-media/channellive/channel104452/playlist.m3u8"),
            RelabeledStreams.linesFor("井研综合"))
    }

    private val muchuanUrl = "http://file.ysxtv.cn/cms/videos/nmip-media/channellive/channel4/playlist.m3u8?zsicd"

    @Test
    fun ysxtvLineBelongsToYingshanNotMuchuan() {
        assertTrue(RelabeledStreams.belongsElsewhere("沐川综合", muchuanUrl))
        assertFalse("营山电视台自己的线不许摘", RelabeledStreams.belongsElsewhere("营山电视台", muchuanUrl))
        // 内置目录里营山那条写成了「两条地址粘在一起」的样子，`#` 之后会被 canonical 剥掉 ⇒ 认成同一条。
        val glued = muchuanUrl +
            "#http://file.ysxtv.cn/cms/videos/nmip-media/channellive/channel4/playlist.m3u8"
        assertTrue("粘串写法也要认得", RelabeledStreams.belongsElsewhere("沐川综合", glued))
        assertEquals(listOf(muchuanUrl), RelabeledStreams.linesFor("营山电视台"))
    }

    @Test
    fun unknownOwnerIsNotGuessed() {
        // 新疆卫视那条 218.84.12.186 的帧上写的是「KTTV-1」，真身属于哪个台还没认出来 ⇒ 不进改挂表。
        // 拿"别的台"当占位名会让 linesFor 造出一个目录里不存在的台，比不写更坏。
        val xinjiang = "http://218.84.12.186:8001/hls/main/playlist.m3u8?zxinjd;" +
            "http://218.84.12.186:8001/hls/main/playlist.m3u8zxinjd"
        assertNull(RelabeledStreams.trueChannel(xinjiang))
        assertFalse(RelabeledStreams.belongsElsewhere("新疆卫视", xinjiang))
    }
}
