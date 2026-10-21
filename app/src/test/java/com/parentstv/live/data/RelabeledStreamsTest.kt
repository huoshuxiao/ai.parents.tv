package com.parentstv.live.data

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
}
