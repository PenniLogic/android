package com.pennilogic.android.contract

import com.pennilogic.android.platform.PlatformBaseline
import com.pennilogic.android.platform.settings.SensitiveSetting
import com.pennilogic.android.testing.RepositoryFiles
import com.pennilogic.android.testing.RepositoryFiles.androidAttribute
import com.pennilogic.android.testing.RepositoryFiles.elements
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Document
import org.w3c.dom.Element

/**
 * Proves the Android 16 / API 36 baseline of android#57 on what AGP actually packages for this
 * variant: the merged manifest (source manifests, library manifests and Gradle values combined).
 * A dependency or source-set manifest that lowered the target, opted out of predictive back or
 * edge-to-edge, or restricted orientation, resizability or aspect ratio fails here, in both variants.
 */
class TargetConformanceTest {
    private val merged: Document by lazy { RepositoryFiles.parseXml(RepositoryFiles.mergedManifest()) }

    @Test
    fun `merged manifest targets the api 36 baseline and keeps the provisional minimum`() {
        val usesSdk = merged.elements("uses-sdk")
        assertEquals("exactly one <uses-sdk> after merging", 1, usesSdk.size)
        assertEquals(PlatformBaseline.TARGET_API, usesSdk.single().androidAttribute("targetSdkVersion")?.toInt())
        assertEquals(PlatformBaseline.MIN_API, usesSdk.single().androidAttribute("minSdkVersion")?.toInt())
        usesSdk.single().androidAttribute("maxSdkVersion")?.let { fail("maxSdkVersion must not be declared: $it") }
    }

    @Test
    fun `version catalogue pins the same levels as the code baseline`() {
        val catalogue = RepositoryFiles.rootFile("gradle/libs.versions.toml").readText()
        assertEquals(PlatformBaseline.TARGET_API, catalogueVersion(catalogue, "android-targetSdk"))
        assertEquals(PlatformBaseline.COMPILE_API, catalogueVersion(catalogue, "android-compileSdk"))
        assertEquals(PlatformBaseline.MIN_API, catalogueVersion(catalogue, "android-minSdk"))
        assertTrue(
            "compileSdk must not be below targetSdk",
            PlatformBaseline.COMPILE_API >= PlatformBaseline.TARGET_API,
        )
    }

    @Test
    fun `no source-set manifest declares uses-sdk or a library override`() {
        val sourceManifests =
            RepositoryFiles.app
                .resolve("src")
                .listFiles()
                .orEmpty()
                .map { it.resolve("AndroidManifest.xml") }
                .filter { it.isFile }
        assertTrue("the main source manifest must exist", sourceManifests.any { it.parentFile?.name == "main" })
        for (manifest in sourceManifests) {
            val document = RepositoryFiles.parseXml(manifest)
            assertEquals(
                "${manifest.path} must leave SDK levels to the catalogue",
                0,
                document.elements("uses-sdk").size,
            )
            assertTrue(
                "${manifest.path} must not override a library's minimum with tools:overrideLibrary",
                !manifest.readText().contains("overrideLibrary"),
            )
        }
    }

    @Test
    fun `predictive back is enabled for the application and disabled for no activity`() {
        assertEquals("true", application().androidAttribute("enableOnBackInvokedCallback"))
        for (activity in merged.elements("activity")) {
            val value = activity.androidAttribute("enableOnBackInvokedCallback")
            assertTrue(
                "${activity.androidAttribute("name")} opts out of predictive back",
                value == null || value == "true",
            )
        }
    }

    @Test
    fun `no activity restricts orientation, resizability or aspect ratio`() {
        for (activity in merged.elements("activity")) {
            val name = activity.androidAttribute("name")
            assertNull("$name declares screenOrientation", activity.androidAttribute("screenOrientation"))
            assertNull("$name declares minAspectRatio", activity.androidAttribute("minAspectRatio"))
            assertNull("$name declares maxAspectRatio", activity.androidAttribute("maxAspectRatio"))
            assertTrue("$name is not resizeable", activity.androidAttribute("resizeableActivity") != "false")
        }
        assertTrue("application is not resizeable", application().androidAttribute("resizeableActivity") != "false")
    }

    @Test
    fun `no compatibility property opts out of the android 16 large-screen behaviour`() {
        val properties = merged.elements("property").mapNotNull { it.androidAttribute("name") }
        val compatOptOuts = properties.filter { it.startsWith("android.window.PROPERTY_COMPAT_") }
        assertEquals("compat properties found: $compatOptOuts", emptyList<String>(), compatOptOuts)
    }

    @Test
    fun `themes never opt out of edge-to-edge enforcement`() {
        val themeFiles =
            RepositoryFiles.app
                .resolve("src/main/res")
                .walkTopDown()
                .filter { it.isFile && it.parentFile?.name?.startsWith("values") == true && it.name == "themes.xml" }
                .toList()
        assertTrue("theme resources must exist", themeFiles.isNotEmpty())
        for (file in themeFiles) {
            val items = RepositoryFiles.parseXml(file).elements("item").map { it.getAttribute("name") }
            assertTrue("${file.path} opts out of edge-to-edge", "android:windowOptOutEdgeToEdgeEnforcement" !in items)
        }
    }

    @Test
    fun `merged manifest requests only the normal permissions its libraries declare`() {
        val requested =
            merged
                .elements("uses-permission")
                .mapNotNull { it.androidAttribute("name") }
                .filterNot { it.endsWith(".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION") }
                .toSet()
        assertEquals("permissions requested: $requested", LIBRARY_NORMAL_PERMISSIONS, requested)
        val sensitive = SensitiveSetting.entries.map { it.platformName }.toSet()
        assertEquals("sensitive permissions in the merged manifest", emptySet<String>(), requested intersect sensitive)
    }

    private fun application(): Element = merged.elements("application").single()

    private fun catalogueVersion(
        catalogue: String,
        key: String,
    ): Int {
        val match = Regex("""^$key\s*=\s*"(\d+)"""", RegexOption.MULTILINE).find(catalogue)
        return checkNotNull(match) { "$key is not pinned in gradle/libs.versions.toml" }.groupValues[1].toInt()
    }

    private fun fail(message: String): Nothing = throw AssertionError(message)

    private companion object {
        /**
         * Install-time (normal) permissions declared by WorkManager's own manifest: no runtime prompt,
         * no restricted setting, no special access. Any addition to this set is a reviewed decision.
         */
        val LIBRARY_NORMAL_PERMISSIONS =
            setOf(
                "android.permission.WAKE_LOCK",
                "android.permission.ACCESS_NETWORK_STATE",
                "android.permission.RECEIVE_BOOT_COMPLETED",
                "android.permission.FOREGROUND_SERVICE",
            )
    }
}
