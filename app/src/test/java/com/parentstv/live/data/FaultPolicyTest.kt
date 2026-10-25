package com.parentstv.live.data

import androidx.media3.common.PlaybackException
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 「可重试那一类等满、不可重试那类不等」这一档分法（用户 10-25 点档）。
 *
 * 判据本体是 [FaultPolicy.waitOutWindow]：分错一边的代价不对称——
 * 该等的没等＝父母看到「正在连接」一闪就换线（正是指令 5 要修的）；
 * 不该等的白等＝对端明明答复过「没有了」还杵着不放画面。
 * 所以四格都是**具体错误码**逐支点名，不写「随便传个整数」那种恒真断言。
 */
class FaultPolicyTest {

    @Test
    fun `网络类报错要等满设置的那一档时长`() {
        assertTrue(FaultPolicy.waitOutWindow(PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT))
        assertTrue(FaultPolicy.waitOutWindow(PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED))
        assertTrue(FaultPolicy.waitOutWindow(PlaybackException.ERROR_CODE_IO_UNSPECIFIED))
    }

    @Test
    fun `对端答过内容不对与解不了格式的两类不等`() {
        assertFalse(FaultPolicy.waitOutWindow(PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS))
        assertFalse(FaultPolicy.waitOutWindow(PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED))
        assertFalse(FaultPolicy.waitOutWindow(PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED))
        assertFalse(FaultPolicy.waitOutWindow(PlaybackException.ERROR_CODE_DECODING_FAILED))
    }

    @Test
    fun `认不出来的码一律按可重试处理`() {
        // 没有码／第三方扩展报的码：宁可多等一整档，也别把慢线误判成死线（长期口径「时刻性坏只降分」）。
        assertTrue(FaultPolicy.waitOutWindow(null))
        assertTrue(FaultPolicy.waitOutWindow(0))
        assertTrue(FaultPolicy.waitOutWindow(-12345))
    }

    @Test
    fun `两支名单不许撞码`() {
        // 同一个整数既算"等满"又算"不等"＝有一支常量其实指向别的码，分法会静默漂移。
        val wait = listOf(
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
            PlaybackException.ERROR_CODE_IO_UNSPECIFIED,
        )
        val noWait = listOf(
            PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS,
            PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
            PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED,
            PlaybackException.ERROR_CODE_DECODING_FAILED,
        )
        assertTrue("两支名单交叉了：${wait.intersect(noWait.toSet())}", wait.intersect(noWait.toSet()).isEmpty())
        assertTrue("码值本身有重复，说明其中一支不是我以为的那一支", (wait + noWait).distinct().size == 7)
    }
}
