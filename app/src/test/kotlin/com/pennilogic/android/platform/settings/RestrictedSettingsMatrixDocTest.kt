package com.pennilogic.android.platform.settings

import com.pennilogic.android.testing.RepositoryFiles
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Keeps `docs/platform/restricted-settings-matrix.md` identical to the matrix the code defines: the
 * block between the markers is rendered from [RestrictedSettingsMatrix] and compared verbatim. When
 * the code changes, the regenerated block is written to `app/build/restricted-settings-matrix.generated.md`
 * for the author to paste, so the document can never drift from what the app actually does.
 */
class RestrictedSettingsMatrixDocTest {
    @Test
    fun `document renders the matrix exactly as the code defines it`() {
        val document =
            RepositoryFiles
                .rootFile(
                    "docs/platform/restricted-settings-matrix.md",
                ).readText()
                .replace("\r\n", "\n")
        assertTrue("missing markers", document.contains(BEGIN) && document.contains(END))
        val actual = document.substringAfter(BEGIN).substringBefore(END).trim()
        val expected = render().trim()
        if (actual != expected) {
            val generated = RepositoryFiles.app.resolve("build/restricted-settings-matrix.generated.md")
            generated.parentFile?.mkdirs()
            generated.writeText(expected + "\n")
            assertEquals(
                "restricted-settings-matrix.md is out of date; regenerated block written to ${generated.path}",
                expected,
                actual,
            )
        }
    }

    @Test
    fun `document explains every identifier and recovery path outside the generated block`() {
        val document = RepositoryFiles.rootFile("docs/platform/restricted-settings-matrix.md").readText()
        val prose = document.substringBefore(BEGIN) + document.substringAfter(END)
        for (source in InstallSource.entries) assertTrue(source.id, prose.contains("`${source.id}`"))
        for (availability in SettingAvailability.entries) {
            assertTrue(
                availability.id,
                prose.contains("`${availability.id}`"),
            )
        }
        for (path in RecoveryPath.entries) assertTrue(path.id, prose.contains("`${path.id}`"))
        assertTrue(prose.contains("Allow restricted settings"))
    }

    companion object {
        const val BEGIN: String = "<!-- matrix:begin (generated from RestrictedSettingsMatrix; do not edit by hand) -->"
        const val END: String = "<!-- matrix:end -->"

        fun render(): String =
            buildString {
                for (source in InstallSource.entries) {
                    appendLine("### `${source.id}`")
                    appendLine()
                    appendLine(
                        "${source.description} Restricted-settings lock on API 33+: ${yesNo(
                            source.restrictedOnApi33Plus,
                        )}. " +
                            "Restored build: ${yesNo(source.restored)}.",
                    )
                    appendLine()
                    appendLine(
                        "| Setting | Used | " + RestrictedSettingsMatrix.API_LEVELS.joinToString(" | ") +
                            " | Not granted on 36 |",
                    )
                    appendLine(
                        "| --- | --- | " + RestrictedSettingsMatrix.API_LEVELS.joinToString(" | ") { "---" } +
                            " | --- |",
                    )
                    for (setting in SensitiveSetting.entries) {
                        val cells =
                            RestrictedSettingsMatrix.API_LEVELS.map {
                                RestrictedSettingsMatrix.availability(
                                    source,
                                    setting,
                                    it,
                                )
                            }
                        val api36 = RestrictedSettingsMatrix.cell(source, setting, 36)
                        val binding =
                            when {
                                api36.conditionWhenNotGranted == null -> {
                                    "n/a"
                                }

                                api36.reasonWhenNotGranted != null -> {
                                    "`${api36.conditionWhenNotGranted?.id}` / `${api36.reasonWhenNotGranted?.id}`"
                                }

                                else -> {
                                    "`${api36.conditionWhenNotGranted?.id}`"
                                }
                            }
                        appendLine(
                            "| `${setting.id}` | ${yesNo(setting.usedByPenniLogic)} | " +
                                cells.joinToString(" | ") { "`${it.id}`" } + " | $binding |",
                        )
                    }
                    appendLine()
                }
                appendLine("### Recovery path per availability")
                appendLine()
                appendLine("| Availability | Meaning | QA recovery path |")
                appendLine("| --- | --- | --- |")
                for (availability in SettingAvailability.entries) {
                    val path =
                        RestrictedSettingsCell(
                            InstallSource.PLAY_STORE,
                            SensitiveSetting.NOTIFICATION_LISTENER,
                            36,
                            availability,
                        ).recoveryPath
                    appendLine("| `${availability.id}` | ${availability.summary} | `${path.id}` |")
                }
                appendLine()
                for (path in RecoveryPath.entries) {
                    appendLine("#### `${path.id}`")
                    appendLine()
                    path.steps.forEachIndexed { index, step -> appendLine("${index + 1}. $step") }
                    appendLine()
                }
            }

        private fun yesNo(value: Boolean): String = if (value) "yes" else "no"
    }
}
