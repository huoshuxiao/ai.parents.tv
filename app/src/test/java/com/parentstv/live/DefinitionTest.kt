package com.parentstv.live

import com.parentstv.live.data.Definition
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 验设备端那道「标清不往目录里加」的闸（用户 10-25「全频道删除标清线路，未来新增的线路也不添加标清线路」）。
 *
 * 设备侧两道：[Definition.isSdTitle] 按清单标题判档（解析公开列表时挡），
 * [Definition.isSdResolution] 按探测读到的 RESOLUTION 判档（「立即更新」探测时挡）；
 * 按实测尺寸摘线那一半在构建侧（tools/build_catalog.py 的 `is_sd_line`，
 * 夹具 tools/tests/test_definition_sd.py）。三处阈值同一把尺（高 ≤699 且 宽 <1200），
 * 所以这里既有「720×576」必须命中，也有「1280×720」「1280×576」这种不许命中的负对照——
 * 后者宽已到 1280，与构建侧 `w < SD_MIN_WIDTH` 那一支一起才判得下。
 */
class DefinitionTest {

    @Test
    fun `标题写着标清或SD的那一档不进来`() {
        assertTrue(Definition.isSdTitle("CCTV-2 财经 标清"))
        assertTrue(Definition.isSdTitle("CCTV1综合-SD"))
        assertTrue(Definition.isSdTitle("某卫视（标清）"))
        assertTrue(Definition.isSdTitle("湖南卫视 sd"))
    }

    @Test
    fun `高清蓝光与别的字样不算标清`() {
        assertFalse(Definition.isSdTitle("CCTV-2 财经 高清"))
        assertFalse(Definition.isSdTitle("CCTV-2 财经 蓝光"))
        assertFalse(Definition.isSdTitle("CCTV-4K 超高清"))
        // 「SDTV」是一整个词，不是独立一档；拿它当档名会把正常台名剪掉。
        assertFalse(Definition.isSdTitle("SDTV 新闻"))
        assertFalse(Definition.isSdTitle("CCTV-2 标清版2"))
        assertFalse(Definition.isSdTitle(""))
    }

    @Test
    fun `探测到只有标清档的那一条不留`() {
        assertTrue(Definition.isSdResolution("720×576"))
        assertTrue(Definition.isSdResolution("640x482"))
        assertTrue(Definition.isSdResolution("854×480"))
        // 阈值边界：高 699 及以下算标清，700 起不算（与构建侧 SD_MAX_HEIGHT 同值）；宽要 <1200 才算。
        assertTrue(Definition.isSdResolution("1138×699"))
        assertFalse(Definition.isSdResolution("1138×700"))
    }

    @Test
    fun `宽到高清档的竖帧与读不到尺寸都不算标清`() {
        assertFalse(Definition.isSdResolution("1280×720"))
        // 宽 1280 已到高清档：这一支靠 `w < 1200` 才不剪错（与构建侧同一把尺）。
        assertFalse(Definition.isSdResolution("1280×576"))
        assertFalse(Definition.isSdResolution("1920×1080"))
        // 读不到尺寸是"没量到"，不是"量到标清"——别拿它当判坏把线摘了。
        assertFalse(Definition.isSdResolution(null))
        assertFalse(Definition.isSdResolution(""))
        assertFalse(Definition.isSdResolution("清单没写"))
    }
}
