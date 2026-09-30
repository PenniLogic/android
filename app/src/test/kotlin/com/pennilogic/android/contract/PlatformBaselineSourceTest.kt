package com.pennilogic.android.contract

import com.pennilogic.android.testing.RepositoryFiles
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Applies [SourceRules] to the real main source tree: legacy back handling is absent, back is
 * intercepted only through the predictive-back primitive (by import and by call), the window-insets
 * API is imported and used only by the root surface, every composition root calls `RootSurface`, every
 * `PenniLogicBackHandler` call states `enabled` explicitly, and no code requests an orientation or opts
 * out of edge-to-edge. `SourceRulesSelfTest` proves each rule on planted snippets.
 */
class PlatformBaselineSourceTest {
    private val packageRoot = RepositoryFiles.app.resolve("src/main/kotlin/com/pennilogic/android")
    private val sources: Map<String, String> =
        RepositoryFiles.mainKotlinSources().associate { it.relative() to it.readText() }

    @Test
    fun `main sources exist and include the primitives under test`() {
        assertTrue(sources.isNotEmpty())
        assertTrue(sources.keys.any { it.endsWith(SourceRules.ROOT_SURFACE_FILE) })
        assertTrue(sources.keys.any { it.endsWith(SourceRules.PREDICTIVE_BACK_FILE) })
        assertTrue(
            "at least one composition root is expected",
            sources.values.any {
                Regex("""\bsetContent\s*[({]""").containsMatchIn(it)
            },
        )
    }

    @Test
    fun `the main source tree satisfies every baseline source rule`() {
        assertEquals(emptyList<SourceRules.Violation>(), SourceRules.violations(sources))
    }

    private fun File.relative(): String = relativeTo(packageRoot).path.replace('\\', '/')
}
