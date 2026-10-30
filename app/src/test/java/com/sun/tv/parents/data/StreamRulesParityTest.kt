package com.sun.tv.parents.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Kotlin 侧规则必须与生成目录用的 Python 侧（tools/stream_rules.py）逐条一致：
 * 目录是 Python 判的，设备端自动更新是 Kotlin 判的，两边口径一分叉就会一边删、一边又加回来。
 */
class StreamRulesParityTest {

    private val fixture: JsonObject = Json.parseToJsonElement(fixtureText()).jsonObject

    private fun fixtureText(): String {
        val fromClasspath = javaClass.getResourceAsStream("/stream_rules.json")?.readBytes()
        val text = fromClasspath?.toString(Charsets.UTF_8)
            ?: File("src/test/resources/stream_rules.json").readText(Charsets.UTF_8)
        assertTrue("夹具文件缺失，这一格会恒真", text.contains("nowEpochSeconds"))
        return text
    }

    @Test
    fun kotlinRulesMatchPythonOnEveryFixtureCase() {
        val now = fixture.getValue("nowEpochSeconds").jsonPrimitive.long
        val cases = fixture.getValue("cases").jsonArray
        val mismatches = mutableListOf<String>()
        val verdictCounts = mutableMapOf<String, Int>()

        cases.forEach { element ->
            val entry = element.jsonObject
            val url = entry.getValue("url").jsonPrimitive.content
            val expectedVerdict = entry["verdict"]?.jsonPrimitive?.contentOrNull
            val expectedCanonical = entry.getValue("canonical").jsonPrimitive.content
            val expectedIpv6 = entry.getValue("ipv6").jsonPrimitive.boolean

            val verdict = StreamRules.neverPlayable(url, now)
            if (verdict != expectedVerdict) {
                mismatches += "判定 $url -> 「$verdict」 而 Python 是 「$expectedVerdict」"
            }
            val canonical = StreamUrl.canonical(url)
            if (canonical != expectedCanonical) {
                mismatches += "归一键 $url -> 「$canonical」 而 Python 是 「$expectedCanonical」"
            }
            if (StreamUrl.isIpv6(url) != expectedIpv6) {
                mismatches += "IPv6 判定 $url -> ${StreamUrl.isIpv6(url)} 而 Python 是 $expectedIpv6"
            }
            val key = verdict ?: "usable"
            verdictCounts[key] = (verdictCounts[key] ?: 0) + 1
        }

        assertEquals("Python / Kotlin 规则不一致：\n" + mismatches.joinToString("\n"), emptyList<String>(), mismatches)
    }

    @Test
    fun fixtureCoversEveryRuleWithEnoughCases() {
        // 夹具若退化成只有一类，上一格会绿得没有意义。
        val cases = fixture.getValue("cases").jsonArray
        assertTrue("夹具用例太少：${cases.size}", cases.size >= 150)
        val counts = cases.map {
            it.jsonObject["verdict"]?.jsonPrimitive?.contentOrNull ?: "usable"
        }.groupingBy { it }.eachCount()
        listOf("容器不支持", "设备绑定", "鉴权已过期", "usable").forEach { kind ->
            assertTrue("$kind 至少要有 3 例，实际 ${counts[kind] ?: 0}", (counts[kind] ?: 0) >= 3)
        }
    }

    @Test
    fun nearMissesAreNotKilled() {
        val now = fixture.getValue("nowEpochSeconds").jsonPrimitive.long
        // 这几条形似「设备绑定/过期」，实际都不能误删：删掉就是少一条能看的线路。
        listOf(
            "http://h/live.m3u8?mac=",
            "http://h/live.m3u8?deviceid=unknown",
            "http://h/live.m3u8?programid=4200000611",
            "http://jxcbn.ws-cdn.gitv.tv/hls/CCTV1HD/index.m3u8?gMac=unknown&livodToken=bb80_1920507885_604800",
            "http://38.75.136.137:98/gslb/dsdqbv/cctv5p.m3u8?auth=test20251009",
        ).forEach { url ->
            assertEquals("$url 不该被判死", null, StreamRules.neverPlayable(url, now))
        }
    }

    @Test
    fun eachUnplayableClassIsCaught() {
        val now = fixture.getValue("nowEpochSeconds").jsonPrimitive.long
        assertEquals("容器不支持", StreamRules.neverPlayable("http://cm.wshls.homecdn.com/live/7cc9.flv", now))
        assertEquals("设备绑定", StreamRules.neverPlayable("http://h/a.m3u8?mac=d4:38:44:a5:72:8b", now))
        assertEquals("设备绑定", StreamRules.neverPlayable("http://h/a.m3u8?msisdn=20261020180255c16a", now))
        assertEquals("鉴权已过期", StreamRules.neverPlayable("http://h/a.m3u8?t=0a8daf_1720507885_604800", now))
        assertEquals("鉴权已过期", StreamRules.neverPlayable("http://h/a.m3u8?expire=1620507885", now))
    }
}
