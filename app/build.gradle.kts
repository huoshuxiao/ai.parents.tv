import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.GZIPOutputStream

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "com.sun.tv.parents"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.sun.tv.parents"
        minSdk = 23
        targetSdk = 34
        versionCode = 14
        versionName = "1.3.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs += listOf(
            "-opt-in=androidx.media3.common.util.UnstableApi",
            "-opt-in=androidx.compose.foundation.ExperimentalFoundationApi",
        )
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    // 用户 10-29：源码目录里的 channels.json 保持明文（工具链照旧读它），
    // 只有打进 APK 的那一份是转码后的 catalog.dat —— 解压/strings/复制都拿不到线路地址。
    sourceSets["main"].assets.setSrcDirs(
        listOf(layout.buildDirectory.dir("generated/catalogAssets").get().asFile.absolutePath),
    )
}

/**
 * 把明文目录转成 `catalog.dat`：gzip → 逐字节异或 → 前置 magic "PTV1"。
 * 必须与 `CatalogCodec`（data/CatalogCodec.kt）里的 MAGIC / KEY 逐字节一致。
 */
val packCatalog = tasks.register("packCatalog") {
    val plainFile = layout.projectDirectory.file("src/main/assets/channels.json").asFile
    val allowlistFile = layout.projectDirectory.file("src/main/assets/line_allowlist.json").asFile
    val outDir = layout.buildDirectory.dir("generated/catalogAssets")
    inputs.file(plainFile)
    inputs.file(allowlistFile)
    outputs.dir(outDir)
    doLast {
        val dir = outDir.get().asFile.apply { mkdirs() }
        val magic = byteArrayOf(0x50, 0x54, 0x56, 0x31) // "PTV1"
        val key = byteArrayOf(
            0x37, 0x9A.toByte(), 0xC1.toByte(), 0x4E, 0x88.toByte(), 0x2D, 0x6B, 0xF0.toByte(),
            0x15, 0xA7.toByte(), 0x3C, 0xE2.toByte(), 0x59, 0x06, 0xBD.toByte(), 0x71,
        )
        val gz = ByteArrayOutputStream().also { bos ->
            GZIPOutputStream(bos).use { it.write(plainFile.readBytes()) }
        }.toByteArray()
        val out = ByteArray(magic.size + gz.size)
        magic.copyInto(out, 0)
        for (i in gz.indices) {
            out[magic.size + i] = (gz[i].toInt() xor key[i % key.size].toInt()).toByte()
        }
        File(dir, "catalog.dat").writeBytes(out)
        // 本地也留一份（用户 10-29：「本地也留一份转码后的文件」→ 指定放 assets）。
        // 注意：assets 源目录已被换成上面的生成目录，所以这份**不会**被打进 APK，只是工作区可见的副本。
        val localCopy = layout.projectDirectory.file("src/main/assets/catalog.dat").asFile
        localCopy.writeBytes(out)
        // assets 源目录已经换成这个生成目录，白名单这类小文件要一起拷过去，缺了就是构建期硬错。
        check(allowlistFile.isFile) { "缺少 ${allowlistFile.path}" }
        allowlistFile.copyTo(File(dir, "line_allowlist.json"), overwrite = true)
        println("[packCatalog] catalog.dat ${out.size} B ← channels.json ${plainFile.length()} B")
    }
}

tasks.matching { it.name == "preBuild" }.configureEach { dependsOn(packCatalog) }

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.animation:animation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    implementation("androidx.media3:media3-exoplayer:1.5.1")
    implementation("androidx.media3:media3-exoplayer-hls:1.5.1")
    implementation("androidx.media3:media3-exoplayer-dash:1.5.1")
    implementation("androidx.media3:media3-ui:1.5.1")

    // 盒子没有 audio/mpeg-L2 解码器，MP2 音轨靠这份纯 Java 软解（链子里 gradle 是 --offline，故吃仓库内 jar）。
    implementation(files("libs/jlayer-1.0.1.jar"))

    implementation("io.coil-kt:coil-compose:2.7.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")

    testImplementation("junit:junit:4.13.2")

    debugImplementation("androidx.compose.ui:ui-tooling")
}
