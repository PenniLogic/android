package com.pennilogic.android.platform.behavior

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.pennilogic.android.platform.PlatformBaseline
import com.pennilogic.android.platform.state.CaptureHealthCondition
import com.pennilogic.android.testing.RepositoryFiles
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `docs/platform/android-behavior-matrix.json` is the API 31/33/35/36 behaviour record for T-QA-12.
 * This test keeps it honest: it covers the baseline's levels, every behaviour states all four
 * levels, every code or artifact path it cites exists, every capture-health binding is a published
 * condition, the scope bullets of android#57 are all present, and the Markdown view is rendered
 * from it verbatim.
 */
class BehaviorMatrixContractTest {
    private val matrix: JsonObject by lazy {
        JsonParser
            .parseReader(RepositoryFiles.rootFile("docs/platform/android-behavior-matrix.json").reader())
            .asJsonObject
    }
    private val behaviors: List<JsonObject>
        get() = matrix.getAsJsonArray("behaviors").map { it.asJsonObject }
    private val statuses: Set<String>
        get() = matrix.getAsJsonArray("statuses").map { it.asString }.toSet()

    @Test
    fun `matrix covers the baseline levels`() {
        assertEquals(PlatformBaseline.QA_API_LEVELS, matrix.getAsJsonArray("api_levels").map { it.asInt })
        assertEquals(PlatformBaseline.TARGET_API, matrix.get("target_api").asInt)
        assertEquals(PlatformBaseline.COMPILE_API, matrix.get("compile_api").asInt)
        assertEquals(PlatformBaseline.MIN_API, matrix.get("min_api").asInt)
        val devices = matrix.getAsJsonObject("governed_devices")
        PlatformBaseline.QA_API_LEVELS.forEach {
            assertTrue("device row for $it", devices.get(it.toString()).asString.isNotBlank())
        }
    }

    @Test
    fun `every scope bullet of the ticket has a behaviour row`() {
        val required =
            setOf(
                "edge_to_edge",
                "predictive_back",
                "large_screen_restrictions_ignored",
                "restricted_settings",
                "job_quota_and_stop_reasons",
                "standby_buckets",
                "force_stop_detection",
                "private_space",
                "developer_verification",
                "play_integrity_standard_requests",
            )
        val ids = behaviors.map { it.get("id").asString }
        assertEquals(ids.size, ids.toSet().size)
        assertTrue("missing: ${required - ids.toSet()}", ids.containsAll(required))
    }

    @Test
    fun `every behaviour states all four levels, its handling, evidence and a valid status`() {
        val identifier = Regex("[a-z][a-z0-9_]*")
        for (behavior in behaviors) {
            val id = behavior.get("id").asString
            assertTrue(id, identifier.matches(id))
            assertTrue("$id status", behavior.get("status").asString in statuses)
            val byApi = behavior.getAsJsonObject("by_api")
            assertEquals("$id levels", PlatformBaseline.QA_API_LEVELS.map { it.toString() }.toSet(), byApi.keySet())
            byApi.entrySet().forEach { (level, text) -> assertTrue("$id/$level", text.asString.isNotBlank()) }
            assertTrue("$id summary", behavior.get("summary").asString.isNotBlank())
            assertTrue("$id app_handling", behavior.get("app_handling").asString.isNotBlank())
            assertTrue("$id evidence", behavior.getAsJsonArray("evidence").size() > 0)
            assertTrue("$id source", behavior.get("source").asString.isNotBlank())
        }
    }

    @Test
    fun `capture-health bindings are published conditions`() {
        for (behavior in behaviors) {
            val binding = behavior.get("capture_health_condition")
            if (!binding.isJsonNull) {
                assertNotNull(
                    "${behavior.get("id").asString} binds to unknown condition",
                    CaptureHealthCondition.fromId(binding.asString),
                )
            }
        }
        val bound = behaviors.filter { !it.get("capture_health_condition").isJsonNull }.map { it.get("id").asString }
        val expectedBound = listOf("restricted_settings", "force_stop_detection", "private_space", "standby_buckets")
        assertTrue(bound.containsAll(expectedBound))
    }

    @Test
    fun `cited code and artifact paths exist and implemented rows cite something`() {
        val kotlinRoot = RepositoryFiles.app.resolve("src/main/kotlin/com/pennilogic/android")
        for (behavior in behaviors) {
            val id = behavior.get("id").asString
            val codePaths = behavior.getAsJsonArray("code_paths").map { it.asString }
            val artifactPaths = behavior.getAsJsonArray("artifact_paths")?.map { it.asString }.orEmpty()
            codePaths.forEach { assertTrue("$id cites missing code $it", kotlinRoot.resolve(it).isFile) }
            artifactPaths.forEach {
                assertTrue("$id cites missing artifact $it", RepositoryFiles.root.resolve(it).isFile)
            }
            if (behavior.get("status").asString == "implemented") {
                assertTrue(
                    "$id is implemented but cites no code or artifact",
                    codePaths.isNotEmpty() || artifactPaths.isNotEmpty(),
                )
            }
        }
    }

    @Test
    fun `markdown view is rendered from the json`() {
        val document =
            RepositoryFiles.rootFile("docs/platform/android-behavior-matrix.md").readText().replace("\r\n", "\n")
        assertTrue("missing markers", document.contains(BEGIN) && document.contains(END))
        val actual = document.substringAfter(BEGIN).substringBefore(END).trim()
        val expected = render().trim()
        if (actual != expected) {
            val generated = RepositoryFiles.app.resolve("build/android-behavior-matrix.generated.md")
            generated.parentFile?.mkdirs()
            generated.writeText(expected + "\n")
            assertEquals(
                "android-behavior-matrix.md is out of date; regenerated block written to ${generated.path}",
                expected,
                actual,
            )
        }
    }

    private fun render(): String =
        buildString {
            appendLine("| Behaviour | Area | Status | Capture-health condition |")
            appendLine("| --- | --- | --- | --- |")
            for (behavior in behaviors) {
                val binding =
                    behavior.get("capture_health_condition").let { if (it.isJsonNull) "—" else "`${it.asString}`" }
                appendLine(
                    "| [`${behavior.get("id").asString}`](#${behavior.get("id").asString.replace('_', '-')}) | " +
                        "${behavior.get("area").asString} | `${behavior.get("status").asString}` | $binding |",
                )
            }
            appendLine()
            for (behavior in behaviors) {
                val id = behavior.get("id").asString
                appendLine("### `$id`")
                appendLine()
                appendLine(behavior.get("summary").asString)
                appendLine()
                appendLine("| API | Platform behaviour |")
                appendLine("| --- | --- |")
                val byApi = behavior.getAsJsonObject("by_api")
                for (level in PlatformBaseline.QA_API_LEVELS) {
                    appendLine("| $level | ${byApi.get(level.toString()).asString} |")
                }
                appendLine()
                appendLine("**App handling.** ${behavior.get("app_handling").asString}")
                appendLine()
                appendLine("**Evidence.**")
                appendLine()
                behavior.getAsJsonArray("evidence").forEach { appendLine("- ${it.asString}") }
                appendLine()
                appendLine("Source: ${behavior.get("source").asString}")
                appendLine()
            }
        }

    private companion object {
        const val BEGIN = "<!-- matrix:begin (generated from android-behavior-matrix.json; do not edit by hand) -->"
        const val END = "<!-- matrix:end -->"
    }
}
