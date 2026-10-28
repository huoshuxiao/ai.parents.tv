package com.parentstv.live.ui

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.parentstv.live.data.BeijingTime
import com.parentstv.live.data.ManualLine
import com.parentstv.live.data.ManualLineStore
import com.parentstv.live.data.StreamProbe
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json

/**
 * 腾讯体育「手动挂」：用它自己的直播页做动态抓取。
 *
 * 为什么必须用 WebView：腾讯体育不是 24 小时频道，地址按场次签发、开赛后才出现在播放器请求里，
 * 开赛前官网详情页连 <video> 都没有（10-24 20:5x 现读）。所以这里只做「你点一下、它当场抓」，
 * 不做后台自动抓。
 *
 * 三条硬规则：
 * 1. 标着 SVIP / 用券 的场次只显示、不抓——不绕过会员；
 * 2. 抓到的地址必须先过 StreamProbe（清单＋首片都取到）才许存；
 * 3. 只收腾讯自家域名的地址，页面里别的东西一律不认。
 */
@Serializable
private data class TencentEvent(
    val mid: String,
    val league: String = "",
    val title: String = "",
    val time: String = "",
    // 官网 matchInfo.startTime（"2026-10-25 19:30:00"）。卡片上那行时间是本地文本、
    // 可能为空，排序只认这个字段（用户 10-25「列出的场次按时间排序」）。
    val start: String = "",
    val paid: Boolean = false,
    val live: Boolean = false,
    val vid: String = "",
) {
    val displayName: String
        get() = listOf(league, title).filter { it.isNotBlank() }.joinToString(" ").ifBlank { "腾讯体育场次" }
}

private const val TX_TAG = "TxBridge"

/** 面板顶部那一条提示的语气。用户 10-26 电视实测「点了挂不知道成没成」：
 *  成功／失败要整行换底色、加粗放大，不能只当一行 14sp 的灰字。 */
private enum class Notice { Info, Busy, Success, Error }

private val M3U8_IN_TEXT = Regex("""https?://[^\s"'\\]+\.m3u8[^\s"'\\]*""")

/** 只认腾讯自家的播放域名（JS 接口是对网页开放的，别让页面塞任意地址进来）。
 *  `*.v.smtcdns.net` 是腾讯视频的 CDN 前置——实测 10-25 01:40 F1 那场播放器起来后
 *  直接请求 `https://<40位哈希>.v.smtcdns.net/txycdn.video.qq.com/1397501701.m3u8`，
 *  不认这个后缀就永远挂不上。 */
private fun isTencentHost(url: String): Boolean {
    val host = hostOf(url)
    return host.endsWith("qq.com") || host.endsWith("gtimg.com") || host.endsWith("smtcdns.net")
}

// 这些播放地址后面挂着一长串 base64 样的 token，`java.net.URI` 会在非法转义处抛异常，
// 一旦抛了 host 就读成空串、地址被静默丢掉（实测 10-25 01:46 就是这么 0 条送进探针的）。
// 取主机不需要解析器：去掉协议、截到第一个 / ? # 为止。
private fun hostOf(url: String): String =
    url.substringAfter("://", url)
        .takeWhile { it != '/' && it != '?' && it != '#' }
        .substringAfterLast('@')
        .lowercase()

/** 从一条被拦到的请求里抠出真正的 m3u8 地址：
 *  它可能自己就是播放清单，也可能只是把地址编码后带在某个查询参数里。
 *  参数逐个单独解码，才不会把 m3u8 自己那串 %3Fver%3D4 解开后、又被下一个参数的 & 切断。 */
private fun streamCandidates(raw: String): List<String> {
    val out = LinkedHashSet<String>()
    val query = raw.substringAfter('?', "")
    if (query.isNotBlank()) {
        query.split('&').forEach { pair ->
            val value = pair.substringAfter('=', "")
            if (value.isBlank()) return@forEach
            var text = value
            repeat(2) {
                text = runCatching { java.net.URLDecoder.decode(text, "UTF-8") }.getOrDefault(text)
                if (text.contains(".m3u8")) M3U8_IN_TEXT.findAll(text).forEach { out.add(it.value) }
            }
        }
    }
    if (out.isEmpty() && raw.contains(".m3u8")) M3U8_IN_TEXT.findAll(raw).forEach { out.add(it.value) }
    return out.toList()
}

/** WebView 的 JS 回调跑在 JavaBridge 线程，所有界面状态都转回主线程再改。 */
private class TencentBridge(
    private val onEvents: (String) -> Unit,
    private val onStream: (String) -> Unit,
    private val onNote: (String) -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())

    // 属性与方法同名，调用点一律写 .invoke：写成 onEvents(json) 会落到自己这个方法上，
    // 结果是回调永远到不了界面（10-25 00:06 那场「列表一直是空的」就是这么来的）。
    @JavascriptInterface
    fun onEvents(json: String) {
        main.post { onEvents.invoke(json) }
    }

    @JavascriptInterface
    fun onStream(url: String) {
        main.post { onStream.invoke(url) }
    }

    @JavascriptInterface
    fun onNote(text: String) {
        main.post { onNote.invoke(text) }
    }
}

@Composable
fun TencentSportsDialog(
    onDismiss: () -> Unit,
    onLinesChanged: () -> Unit,
) {
    val context = LocalContext.current
    val store = remember { ManualLineStore(context) }
    val scope = rememberCoroutineScope()
    val json = remember { Json { ignoreUnknownKeys = true; isLenient = true } }

    var events by remember { mutableStateOf<List<TencentEvent>>(emptyList()) }
    var listLoading by remember { mutableStateOf(true) }
    var status by remember { mutableStateOf("正在打开腾讯体育直播页…") }
    var statusKind by remember { mutableStateOf(Notice.Busy) }
    fun say(message: String, kind: Notice = Notice.Info) {
        status = message
        statusKind = kind
    }
    var capturing by remember { mutableStateOf<TencentEvent?>(null) }
    var verifying by remember { mutableStateOf(false) }
    var pastedUrl by remember { mutableStateOf("") }
    var manualLines by remember { mutableStateOf(store.load()) }
    // 同一条抓坏的地址只试一次，别让播放器反复重试把 45 秒耗光。
    val rejected = remember { mutableSetOf<String>() }
    // 读赛程的定时器读到结果就该收手。Runnable 定在第一次组合上、读不到 Compose 最新状态，
    // 所以拿一个裸数组当信箱，由 onEvents 往里写。
    val listReady = remember { booleanArrayOf(false) }

    // 状态行只有一个：读赛程的定时器与「挂 / 验地址」不能互相盖对方的话，
    // 所以凡是要写状态的地方，先看用户是不是已经点了挂、或正在验证。
    fun idle(): Boolean = capturing == null && !verifying

    fun lineTitle(event: TencentEvent): String {
        val raw = "腾讯体育 ${event.displayName}".trim()
        return if (raw.length <= 28) raw else raw.take(27) + "…"
    }

    fun attach(url: String, title: String) {
        val line = ManualLine.create(title = title, url = url)
        manualLines = store.add(line)
        onLinesChanged()
        say("已挂上「$title」——回首页切到「腾讯体育」组就能看到它（${BeijingTime.format(line.expiresAt, "HH:mm")} 自动摘线）",
            Notice.Success)
        Log.i(TX_TAG, "attached id=${line.id} title=$title")
        capturing = null
        verifying = false
    }

    // 抓到候选地址后统一走这里：先探活，再存；失败就继续等播放器发下一条。
    // 实测（10-25 00:22）腾讯播放器把 m3u8 请求藏在 hls.js worker 里，WebView 侧只看得到
    // h.trace.qq.com 的埋点，真正的播放地址以 URL 编码塞在它的 vurl= 参数里，所以要先抠出来。
    fun handleCaptured(raw: String) {
        val event = capturing ?: return
        if (verifying) return
        for (candidate in streamCandidates(raw)) {
            if (!isTencentHost(candidate)) continue
            if (!rejected.add(candidate)) continue
            verifying = true
            say("抓到播放地址，正在验证能不能播…", Notice.Busy)
            scope.launch {
                val result = runCatching { StreamProbe.probe(candidate) }.getOrNull()
                if (result?.playable == true) {
                    attach(candidate, lineTitle(event))
                } else {
                    Log.i(TX_TAG, "probe rejected ${result?.error ?: "超时"} url=${candidate.take(90)}")
                    say("这条地址取不到画面（${result?.error ?: "超时"}），继续等播放器发下一条…", Notice.Error)
                    verifying = false
                }
            }
            return
        }
    }

    // 两个 WebView 各拿一个桥，只有「抓取页」那条允许提交播放地址。
    // 共用一个桥会被首页自己自动播的预告片抢走：实测 10-25 00:32 点「意甲 那不勒斯 vs 罗马」，
    // 挂上的却是首页预告片（画面是 WNBA），因为那条 m3u8 是从 sports.qq.com 首页的埋点里抠出来的。
    fun newBridge(acceptsStream: Boolean) = TencentBridge(
        onEvents = { text ->
            val rows = runCatching { json.decodeFromString<List<TencentEvent>>(text) }
                .getOrDefault(emptyList())
            // 已经在直播的排最前，其余按开赛时间从早到晚（用户 10-25「列出的场次按时间排序」）。
            // start 是官网的 "yyyy-MM-dd HH:mm:ss"，同格式下按字符串比就是按时间比；
            // 拿不到时间的排最后，不许因为它缺字段就挤到第一场。
            events = rows.sortedWith(
                compareByDescending<TencentEvent> { it.live }
                    .thenBy { it.start.ifBlank { "9999" } }
                    .thenBy { it.time },
            )
            listLoading = false
            if (rows.isNotEmpty()) listReady[0] = true
            // 成功／失败那条横幅是给人看的结论，别让赛程刷新把它盖掉（用户 10-26 电视实测）。
            if (idle() && statusKind != Notice.Success && statusKind != Notice.Error) {
                if (rows.isEmpty()) {
                    say("没读到赛程：这台设备的网页组件可能打不开腾讯体育，或此刻没有视频直播。" +
                        "可以直接在下面粘贴播放地址。", Notice.Error)
                } else {
                    say("共 ${rows.size} 场视频场次（正在直播 ${rows.count { it.live }} 场，" +
                        "待开赛 ${rows.count { !it.live }} 场），其中 ${rows.count { !it.paid }} 场免费可挂",
                        Notice.Info)
                }
            }
        },
        onStream = { url ->
            // JS 接口是对网页开放的：只认腾讯自家域名，别让页面塞任意地址进来。
            if (acceptsStream && isTencentHost(url)) handleCaptured(url)
        },
        onNote = { text ->
            // DIAG 只进日志：它不是给人看的结论，挂在面板上会把「已挂上」挤掉（10-26 实测）。
            Log.i(TX_TAG, "note ${text.take(200)}")
        },
    )

    val listBridge = remember { newBridge(acceptsStream = false) }
    val captureBridge = remember { newBridge(acceptsStream = true) }

    @SuppressLint("SetJavaScriptEnabled")
    fun makeWebView(source: TencentBridge, autoplay: Boolean, onFinished: (WebView) -> Unit): WebView =
        WebView(context).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            // 赛程页不许自动播预告片：那会发出带真实播放地址的埋点，还会在背后出声。
            settings.mediaPlaybackRequiresUserGesture = !autoplay
            settings.userAgentString =
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            setBackgroundColor(android.graphics.Color.TRANSPARENT)
            isFocusable = false
            isFocusableInTouchMode = false
            importantForAccessibility = android.view.View.IMPORTANT_FOR_ACCESSIBILITY_NO
            addJavascriptInterface(source, "ParentsTvBridge")
            webViewClient = object : WebViewClient() {
                override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                    super.onPageStarted(view, url, favicon)
                    // 文档一建立就打钩子，赶在 hls.js 发请求之前。
                    view?.evaluateJavascript(CAPTURE_HOOK_JS, null)
                }

                override fun onPageFinished(view: WebView?, url: String?) {
                    super.onPageFinished(view, url)
                    view?.evaluateJavascript(CAPTURE_HOOK_JS, null)
                    view?.let(onFinished)
                }

                override fun shouldInterceptRequest(
                    view: WebView?,
                    request: WebResourceRequest?,
                ): android.webkit.WebResourceResponse? {
                    val url = request?.url?.toString().orEmpty()
                    if (url.contains(".m3u8")) source.onStream(url)
                    return null
                }
            }
        }

    val listView = remember {
        makeWebView(listBridge, autoplay = false) { view ->
            view.evaluateJavascript(DIAGNOSE_JS, null)
            view.evaluateJavascript(EXTRACT_EVENTS_JS, null)
        }
    }
    val captureView = remember {
        makeWebView(captureBridge, autoplay = true) { view ->
            view.evaluateJavascript(CAPTURE_HOOK_JS, null)
            view.evaluateJavascript(AUTOPLAY_JS, null)
        }
    }

    // 直播页有长连接，onPageFinished 可能迟迟不来（实测一次都没回调）；按时间点反复读赛程。
    val ticker = remember { Handler(Looper.getMainLooper()) }
    val readTries = remember { intArrayOf(0) }
    val readList = remember {
        object : Runnable {
            override fun run() {
                if (listReady[0]) return
                listView.evaluateJavascript(DIAGNOSE_JS, null)
                listView.evaluateJavascript(EXTRACT_EVENTS_JS, null)
                // 这里读不到 Compose 的最新 events（Runnable 在第一次组合时就定住了），
                // 所以改成固定重试 12 次（36 秒）封顶，够了就停。
                if (++readTries[0] >= 12) {
                    if (idle() && statusKind != Notice.Success && statusKind != Notice.Error) {
                        say("没读到赛程：这台设备的网页组件没打开腾讯体育（或此刻没有视频直播）。" +
                            "可以在下面直接粘贴播放地址。", Notice.Error)
                    }
                } else ticker.postDelayed(this, 3_000)
            }
        }
    }
    fun startListRead() {
        listReady[0] = false
        readTries[0] = 0
        ticker.removeCallbacks(readList)
        ticker.postDelayed(readList, 3_000)
    }

    DisposableEffect(Unit) {
        listView.loadUrl(TENCENT_LIVE_URL)
        startListRead()
        onDispose {
            ticker.removeCallbacksAndMessages(null)
            listView.stopLoading()
            listView.destroy()
            captureView.stopLoading()
            captureView.destroy()
        }
    }

    // 点「挂」之后：打开这场详情页，每 3 秒催一次播放再等 45 秒。
    // 催播放必须自己按表跑——详情页有长连接，onPageFinished 迟迟不来（实测 10-25 00:22 一次都没回调）。
    LaunchedEffect(capturing) {
        val event = capturing ?: return@LaunchedEffect
        captureView.loadUrl("https://sports.qq.com/kbsweb/game.htm?mid=${event.mid}")
        var tick = 0
        while (capturing === event && ++tick <= 15) {
            delay(3_000)
            captureView.evaluateJavascript(CAPTURE_HOOK_JS, null)
            captureView.evaluateJavascript(AUTOPLAY_JS, null)
            // 倒计时要让人看得见：电视上「点了没反应」多半只是还在等这 45 秒。
            if (capturing === event && !verifying) {
                say("正在抓取「${event.displayName}」… 还剩 ${(15 - tick) * 3} 秒", Notice.Busy)
            }
        }
        if (capturing === event && !verifying) {
            say("没挂上：45 秒内没抓到播放地址。多半是这场还没开播、或需要会员。" +
                "可以换一场再按「挂」，或用下面「粘贴地址」。", Notice.Error)
            capturing = null
        }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Box {
            // 两个 WebView 只负责跑页面和抓请求，不显示也不抢焦点：放在面板 Column 之前绘制，
            // 被那块不透明的 1000×720 背景整个盖住。抓取页给的是真实尺寸——播放器在 1dp 的
            // 容器里可能压根不初始化（实测 10-25 01:31 F1 那场 45 秒内一条媒体请求都没发出来）。
            AndroidView(factory = { listView }, modifier = Modifier.size(1.dp))
            AndroidView(
                factory = { captureView },
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .size(width = 480.dp, height = 270.dp),
            )

            Column(
                // 以前写死 1000×720dp：1920×1080 的电视在 density 2 下只有 960×540dp，
                // 面板比屏幕大一圈，最下面那排「粘贴地址」和赛程行的「挂」被切在屏幕外
                //（用户 10-26 电视实测：点了挂没反应，其实是按钮被裁）。改按屏幕比例留边。
                modifier = Modifier
                    .fillMaxWidth(0.94f)
                    .fillMaxHeight(0.92f)
                    .clip(RoundedCornerShape(22.dp))
                    .background(TvColors.BackgroundAlt)
                    .border(1.dp, TvColors.Divider, RoundedCornerShape(22.dp))
                    .padding(horizontal = 28.dp, vertical = 20.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "腾讯体育 · 手动挂",
                            color = TvColors.TextPrimary,
                            fontSize = 26.sp,
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            text = "只抓免费场次；标 SVIP/用券 的不抓。挂上的线 6 小时后自动摘掉。",
                            color = TvColors.TextSecondary,
                            fontSize = 12.sp,
                        )
                    }
                    PlayerActionButton(
                        icon = Icons.Rounded.Refresh,
                        label = "刷新赛程",
                        onClick = {
                            listLoading = true
                            events = emptyList()
                            say("正在刷新赛程…", Notice.Busy)
                            listView.loadUrl(TENCENT_LIVE_URL)
                            // 刷新也要重新起那个定时器：只靠 onPageFinished 的话，
                            // 这个页面的长连接会让它一直不回调（实测过一次都没有）。
                            startListRead()
                        },
                    )
                    Spacer(Modifier.width(12.dp))
                    PlayerActionButton(
                        icon = Icons.Rounded.Check,
                        label = "关闭",
                        onClick = onDismiss,
                    )
                }

                Spacer(Modifier.height(10.dp))
                // 成功／失败整行换底色＋加粗放大：电视上隔两三米，「已挂上」必须一眼看见。
                val noticeBg = when (statusKind) {
                    Notice.Success -> Color(0xFF1C4A2E)
                    Notice.Error -> Color(0xFF4C1E1E)
                    Notice.Busy -> TvColors.Surface
                    Notice.Info -> TvColors.Surface.copy(alpha = 0.6f)
                }
                val noticeFg = when (statusKind) {
                    Notice.Success -> Color(0xFFA9F0C1)
                    Notice.Error -> Color(0xFFFFC2C2)
                    else -> TvColors.Accent
                }
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .background(noticeBg)
                        .border(1.dp, noticeFg.copy(alpha = 0.45f), RoundedCornerShape(10.dp))
                        .padding(horizontal = 14.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = if (statusKind == Notice.Success) "✓  $status"
                        else if (statusKind == Notice.Error) "！  $status" else status,
                        color = noticeFg,
                        fontSize = if (statusKind == Notice.Success || statusKind == Notice.Error) 18.sp
                        else 15.sp,
                        fontWeight = if (statusKind == Notice.Success || statusKind == Notice.Error)
                            FontWeight.Bold else FontWeight.Normal,
                        modifier = Modifier.weight(1f),
                    )
                    Text(
                        text = "已挂 ${manualLines.size} 条",
                        color = noticeFg,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Spacer(Modifier.height(10.dp))

                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .clip(RoundedCornerShape(14.dp))
                        .background(TvColors.Surface)
                        .border(1.dp, TvColors.Divider, RoundedCornerShape(14.dp)),
                ) {
                    if (listLoading && events.isEmpty()) {
                        Text(
                            text = "正在读取腾讯体育的今日赛程…",
                            color = TvColors.TextSecondary,
                            fontSize = 14.sp,
                            modifier = Modifier.padding(18.dp),
                        )
                    } else {
                        LazyColumn(
                            modifier = Modifier.padding(vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            // 已经挂上的排最前：遥控器要能一眼看到、一按就摘，
                            // 不能让人从 60 场赛程里翻到底去找「摘掉」。
                            if (manualLines.isNotEmpty()) {
                                item {
                                    Text(
                                        text = "已挂上的临时线",
                                        color = TvColors.Accent,
                                        fontSize = 14.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        modifier = Modifier.padding(start = 16.dp, top = 4.dp, bottom = 4.dp),
                                    )
                                }
                                items(manualLines, key = { it.id }) { line ->
                                    ManualLineRow(
                                        line = line,
                                        onRemove = {
                                            manualLines = store.remove(line.id)
                                            onLinesChanged()
                                            say("已摘掉「${line.title}」", Notice.Info)
                                        },
                                    )
                                }
                            }
                            items(events, key = { it.mid }) { event ->
                                EventRow(
                                    event = event,
                                    busy = capturing?.mid == event.mid,
                                    onAttach = {
                                        capturing = event
                                        verifying = false
                                        say("正在打开「${event.displayName}」等播放器发地址…（最多 45 秒）",
                                            Notice.Busy)
                                    },
                                )
                            }
                        }
                    }
                }

                Spacer(Modifier.height(12.dp))
                PasteLineRow(
                    value = pastedUrl,
                    onValueChange = { pastedUrl = it },
                    onAttach = {
                        val url = pastedUrl.trim()
                        if (url.startsWith("http://") || url.startsWith("https://")) {
                            verifying = true
                            say("正在验证粘贴的地址…", Notice.Busy)
                            scope.launch {
                                val result = runCatching { StreamProbe.probe(url) }.getOrNull()
                                if (result?.playable == true) {
                                    val title = "腾讯体育 手动 ${BeijingTime.format(System.currentTimeMillis(), "HH:mm")}"
                                    attach(url, title)
                                    pastedUrl = ""
                                } else {
                                    say("这条地址取不到画面（${result?.error ?: "超时"}）", Notice.Error)
                                    verifying = false
                                }
                            }
                        } else {
                            say("地址要以 http:// 或 https:// 开头", Notice.Error)
                        }
                    },
                )
            }
        }
    }
}

@Composable
private fun EventRow(
    event: TencentEvent,
    busy: Boolean,
    onAttach: () -> Unit,
) {
    var focused by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 10.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(if (focused) TvColors.SurfaceFocused else Color.Transparent)
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = event.displayName,
                color = TvColors.TextPrimary,
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium,
            )
            Text(
                text = buildString {
                    append(event.time.ifBlank { "时间未知" })
                    append(" · ")
                    if (event.live) {
                        append("正在直播 · ")
                    }
                    append(if (event.paid) "会员/用券场次" else "免费场次")
                },
                color = if (event.paid) Color(0xFFFF9E6D) else TvColors.TextSecondary,
                fontSize = 12.sp,
            )
        }
        Spacer(Modifier.width(12.dp))
        if (event.paid) {
            Text("会员不抓", color = Color(0xFFFF9E6D), fontSize = 12.sp)
        } else {
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(9.dp))
                    .background(if (focused) TvColors.Accent else TvColors.Surface)
                    .border(1.dp, TvColors.Accent.copy(alpha = 0.6f), RoundedCornerShape(9.dp))
                    .onFocusChanged { focused = it.isFocused }
                    .clickable(onClick = onAttach)
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = if (busy) "抓取中…" else "挂",
                    color = if (focused) Color(0xFF1A1200) else TvColors.TextPrimary,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }
    }
}

@Composable
private fun ManualLineRow(line: ManualLine, onRemove: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(line.title, color = TvColors.TextPrimary, fontSize = 14.sp)
            Text(
                text = "有效至 ${BeijingTime.format(line.expiresAt, "HH:mm")}",
                color = TvColors.TextSecondary,
                fontSize = 11.sp,
            )
        }
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(8.dp))
                .background(if (focused) TvColors.SurfaceFocused else TvColors.Surface)
                .border(1.dp, if (focused) TvColors.Accent else TvColors.Divider, RoundedCornerShape(8.dp))
                .onFocusChanged { focused = it.isFocused }
                .clickable(onClick = onRemove)
                .padding(horizontal = 14.dp, vertical = 6.dp),
        ) {
            Text("摘掉", color = TvColors.TextPrimary, fontSize = 13.sp)
        }
    }
}

@Composable
private fun PasteLineRow(
    value: String,
    onValueChange: (String) -> Unit,
    onAttach: () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text("粘贴地址（抓不到时的备用）", color = TvColors.TextSecondary, fontSize = 12.sp)
            Spacer(Modifier.height(4.dp))
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(38.dp)
                    .clip(RoundedCornerShape(9.dp))
                    .background(TvColors.Surface)
                    .border(1.dp, TvColors.Divider, RoundedCornerShape(9.dp))
                    .padding(horizontal = 12.dp),
                contentAlignment = Alignment.CenterStart,
            ) {
                BasicTextField(
                    value = value,
                    onValueChange = onValueChange,
                    singleLine = true,
                    textStyle = TextStyle(color = TvColors.TextPrimary, fontSize = 13.sp),
                    modifier = Modifier.fillMaxWidth(),
                )
                if (value.isEmpty()) {
                    Text(
                        text = "http://…m3u8（电视上接键盘或用手机遥控输入）",
                        color = TvColors.TextSecondary.copy(alpha = 0.6f),
                        fontSize = 12.sp,
                    )
                }
            }
        }
        Spacer(Modifier.width(12.dp))
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(9.dp))
                .background(TvColors.Surface)
                .border(1.dp, TvColors.Accent.copy(alpha = 0.6f), RoundedCornerShape(9.dp))
                .clickable(onClick = onAttach)
                .padding(horizontal = 16.dp, vertical = 9.dp),
        ) {
            Text("挂这条", color = TvColors.TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

private const val TENCENT_LIVE_URL = "https://sports.qq.com/live/"

/** 读不到赛程时，把现场数字打在状态行上：页面改版和「此刻确实没比赛」是两种要分开说的故障。 */
private const val DIAGNOSE_JS = """
(function(){
  var msg;
  try {
    var a = document.querySelectorAll('a[href*="kbsweb/game"]');
    var n = a.length, video = 0, midOk = 0;
    for (var i = 0; i < n; i++) {
      if ((a[i].innerText || '').indexOf('视频直播') >= 0) video++;
      if (/mid=([0-9]+:[0-9]+)/.test(a[i].getAttribute('href') || '')) midOk++;
    }
    msg = 'DIAG 卡片=' + n + ' 可挂=' + video + ' 场次号=' + midOk +
      ' 桥=' + (typeof ParentsTvBridge) + ' 页=' + location.href.slice(0, 40);
  } catch (e) {
    msg = 'DIAG failed ' + e;
  }
  try { ParentsTvBridge.onNote(msg); } catch (e) { console.log('DIAG call-failed ' + e); }
})();
"""

// 官网自己轮询的 multiUpdate 里有权威字段：matchInfo.liveType / scheduleStyle.desc
// 写着「视频直播」「图文直播」「已结束」，isPay 与 videoInfo.isPay 管会员，videoInfo.vid 是直播流 id。
// 现读 10-25 20:1x 的页面数据：liveType 3（NBA 等）与 4（F1/赛车/部分球类）都是有视频的场次，
// 0/1 才是图文或没有视频——旧版只认 3，F1 三场全被过滤掉，列表里只剩 NBA（用户当天反馈）。
private const val EXTRACT_EVENTS_JS = """
(function(){
  var API = 'https://matchweb.sports.qq.com/match/multiUpdate?mids=';
  var DEAD = /已结束|回放|集锦|图文直播/;
  var VIDEO_TYPES = {'3': 1, '4': 1};

  // 不再只扫 DOM 卡片：首页只渲染当前那个 tab 的卡片，F1/赛车在别的 tab 里根本不在 DOM 中。
  // 页面自己的 __INITIAL_STATE__ 里有今天到明天的全部场次（现读：80 场，含 F1 三场）。
  var root = window.__INITIAL_STATE__;
  if (root && root.__v_isRef) root = root._rawValue || root;
  var found = [], seenMid = {}, visited = new Set();
  (function walk(o, d){
    if (!o || typeof o !== 'object' || d > 14 || visited.has(o)) return;
    visited.add(o);
    if (Object.prototype.toString.call(o) === '[object Array]') {
      for (var i = 0; i < o.length; i++) {
        var v = o[i];
        if (v && v.matchInfo && v.matchInfo.mid && !seenMid[v.matchInfo.mid]) {
          seenMid[v.matchInfo.mid] = 1;
          found.push(v);
        }
        walk(v, d + 1);
      }
      return;
    }
    for (var k in o) { try { walk(o[k], d + 1); } catch (e) {} }
  })(root, 0);

  var ids = found.map(function(v){ return v.matchInfo.mid; });
  var parts = [];
  for (var i = 0; i < ids.length; i += 40) parts.push(ids.slice(i, i + 40));

  var run = function(){
    if (!ids.length) return Promise.resolve(false);
    return parts.reduce(function(chain, part){
      return chain.then(function(info){
        return fetch(API + part.join(','))
          .then(function(r){ return r.json(); })
          .then(function(j){
            var d = (j && j.data) || {};
            Object.keys(d).forEach(function(k){ info[k] = d[k]; });
            return info;
          })
          .catch(function(){ return info; });
      });
    }, Promise.resolve({})).then(function(info){
      var rows = [], now = Date.now(), WINDOW = 6 * 3600 * 1000;
      found.forEach(function(v){
        var mi = v.matchInfo || {mid: ''}, x = info[mi.mid] || {};
        var xi = x.matchInfo || {}, vi = x.videoInfo || {};
        var lt = String(xi.liveType || mi.liveType || '');
        var desc = (xi.scheduleStyle && xi.scheduleStyle.desc) || '';
        if (!VIDEO_TYPES[lt] && desc.indexOf('视频直播') < 0) return;
        if (DEAD.test(desc)) return;
        var start = xi.startTime || mi.startTime || '';
        var st = Date.parse(start.replace(/-/g, '/'));
        // 已经结束超过 6 小时的场次不再列（回放/集锦在上面已经被 DEAD 挡掉）。
        if (!isNaN(st) && st < now - WINDOW) return;
        var left = mi.leftName || mi.title || '';
        var right = mi.rightName || '';
        var title = (mi.title || (right ? (left + ' vs ' + right) : left)).trim();
        rows.push({
          mid: mi.mid,
          league: mi.matchDesc || xi.matchDesc || '',
          title: title,
          time: start ? start.slice(5, 16) : '',
          start: start,
          paid: String(x.isPay) === '1' || String(vi.isPay) === '1',
          live: !isNaN(st) && st <= now && now - st < WINDOW,
          vid: vi.vid || ''
        });
      });
      // 正在直播的排最前；其余按开赛时间从早到晚（用户 10-25「列出的场次按时间排序」）。
      rows.sort(function(a, b){
        if (a.live !== b.live) return a.live ? -1 : 1;
        var sa = a.start || '9999', sb = b.start || '9999';
        return sa < sb ? -1 : (sa > sb ? 1 : 0);
      });
      ParentsTvBridge.onEvents(JSON.stringify(rows));
      return rows.length > 0;
    });
  };

  var tries = 0;
  var tick = function(){
    run().then(function(ok){ if (!ok && ++tries < 6) setTimeout(tick, 1500); });
  };
  tick();
})();
"""

/** 在页面里接住播放器发出的 m3u8 请求：hls.js 走 XHR，有的播放器直接改 video.src。 */
private const val CAPTURE_HOOK_JS = """
(function(){
  if (window.__parentsTvHooked) return;
  window.__parentsTvHooked = true;
  var report = function(u){
    try {
      if (!u) return;
      u = String(u);
      if (u.indexOf('.m3u8') >= 0) ParentsTvBridge.onStream(u);
    } catch (e) {}
  };
  var origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url){
    report(url);
    return origOpen.apply(this, arguments);
  };
  if (window.fetch) {
    var origFetch = window.fetch;
    window.fetch = function(input){
      try { report(typeof input === 'string' ? input : (input && input.url)); } catch (e) {}
      return origFetch.apply(this, arguments);
    };
  }
  try {
    var d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
    if (d && d.set) {
      Object.defineProperty(HTMLMediaElement.prototype, 'src', {
        set: function(v){ report(v); return d.set.call(this, v); },
        get: d.get
      });
    }
  } catch (e) {}
})();
"""

// 单发一次「催播放」：找得到 <video> 就 play()，找得到播放键就点一下。
// 重复调用由外面的定时器负责（每次注入都新建 setInterval 会把定时器叠爆）。
private const val AUTOPLAY_JS = """
(function(){
  try {
    var v = document.querySelector('video');
    if (v) { v.muted = true; if (v.play) v.play().catch(function(){}); }
    var btn = document.querySelector('.txp_btn_play,[class*=play-button],[class*=play_btn]');
    if (btn) btn.click();
  } catch (e) {}
})();
"""
