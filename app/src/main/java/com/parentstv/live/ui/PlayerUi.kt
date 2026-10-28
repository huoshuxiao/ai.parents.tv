package com.parentstv.live.ui

/**
 * 播放界面两条「看不看得见、听不听得见」的判据，抽成纯函数是为了能挂夹具。
 *
 * 来历（2026-10-28 用户现读）：`36.110.54.118` 那台只给 2–4 Mbps，而内容是 8.0–8.1 Mbps。
 * 音频轨约 0.8 Mbps 先到、视频轨到不了，屏幕上就成了「有声黑屏、也没有倒计时」——
 * 老人只会觉得坏了。两条判据分别修这两半。
 */
object PlayerUi {

    /**
     * 这一刻要不要显示「正在连接 / 还有 N 秒」。
     *
     * 旧判据只有 `缓冲中 && 没在播`：音频在播时这一串为假，于是**一帧都没有也什么都不显示**。
     * 新增一臂：**这条线还没画出过第一帧**也算等待中。
     *
     * 两条都要留：只有新那一臂的话，「播过之后又卡住」的老场景就没有提示了。
     */
    fun waitingForPicture(renderedFrame: Boolean, buffering: Boolean, playing: Boolean): Boolean =
        !renderedFrame || (buffering && !playing)

    /**
     * 这一刻音量该是多少。
     *
     * - 画面已经出来 ⇒ 1（正常播）。
     * - 还没出画面 ⇒ **0**：别让父母对着「有声黑屏」猜。
     * - 例外：纯音频流（有音轨、没有视频轨）⇒ 1，否则广播类频道会被永久静音。
     */
    fun volumeFor(renderedFrame: Boolean, hasAudioTrack: Boolean, hasVideoTrack: Boolean): Float =
        if (renderedFrame || (hasAudioTrack && !hasVideoTrack)) 1f else 0f
}
