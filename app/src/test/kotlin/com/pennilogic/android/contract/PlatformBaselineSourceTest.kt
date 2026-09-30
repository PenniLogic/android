package com.pennilogic.android.contract

import com.pennilogic.android.testing.RepositoryFiles
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Source-level rules of the Android 16 baseline that no runtime test can see: legacy back handling
 * is absent, back is intercepted only through the predictive-back primitive, insets are applied only
 * by the root surface, every composition root goes through it, and no code requests an orientation.
 */
class PlatformBaselineSourceTest {
    private val sources = RepositoryFiles.mainKotlinSources()
    private val rootSurfaceFile = "platform/ui/RootSurface.kt"
    private val predictiveBackFile = "platform/ui/PredictiveBack.kt"

    @Test
    fun `main sources exist and include the primitives under test`() {
        assertTrue(sources.isNotEmpty())
        assertTrue(sources.any { it.relative().endsWith(rootSurfaceFile) })
        assertTrue(sources.any { it.relative().endsWith(predictiveBackFile) })
    }

    @Test
    fun `no legacy back callback survives`() {
        assertNoMatch(Regex("""override\s+fun\s+onBackPressed\s*\("""), "onBackPressed() override")
        assertNoMatch(Regex("""KEYCODE_BACK"""), "KEYCODE_BACK handling")
        assertNoMatch(Regex("""override\s+fun\s+onKeyDown\s*\("""), "onKeyDown() override")
        assertNoMatch(Regex("""override\s+fun\s+onKeyUp\s*\("""), "onKeyUp() override")
    }

    @Test
    fun `back is intercepted only through the predictive-back primitive`() {
        val forbidden =
            listOf(
                Regex("""(?<![A-Za-z])PredictiveBackHandler\s*\("""),
                Regex("""(?<![A-Za-z])BackHandler\s*\("""),
                Regex("""onBackPressedDispatcher\s*\.\s*addCallback"""),
                Regex("""onBackInvokedDispatcher\s*\.\s*registerOnBackInvokedCallback"""),
            )
        for (pattern in forbidden) {
            assertNoMatch(pattern, "direct back registration ($pattern)", except = setOf(predictiveBackFile))
        }
    }

    @Test
    fun `window insets are applied only by the root surface`() {
        val insetModifiers =
            Regex(
                """\.(windowInsetsPadding|safeDrawingPadding|safeContentPadding|systemBarsPadding|statusBarsPadding|navigationBarsPadding|displayCutoutPadding|safeGesturesPadding)\s*\(""",
            )
        assertNoMatch(insetModifiers, "inset padding outside RootSurface", except = setOf(rootSurfaceFile))
    }

    @Test
    fun `every composition root goes through the root surface`() {
        val roots = sources.filter { Regex("""(?<![A-Za-z.])setContent\s*\{""").containsMatchIn(it.readText()) }
        assertTrue("at least one setContent root is expected", roots.isNotEmpty())
        val missing = roots.filterNot { it.readText().contains("RootSurface") }.map { it.relative() }
        assertEquals("setContent roots without RootSurface", emptyList<String>(), missing)
    }

    @Test
    fun `no code requests an orientation or opts out of edge-to-edge`() {
        assertNoMatch(Regex("""setRequestedOrientation\s*\("""), "setRequestedOrientation()")
        assertNoMatch(Regex("""requestedOrientation\s*="""), "requestedOrientation assignment")
        assertNoMatch(Regex("""windowOptOutEdgeToEdgeEnforcement"""), "edge-to-edge opt-out")
    }

    private fun assertNoMatch(
        pattern: Regex,
        description: String,
        except: Set<String> = emptySet(),
    ) {
        val offenders =
            sources
                .filterNot { file -> except.any { file.relative().endsWith(it) } }
                .filter { pattern.containsMatchIn(it.codeWithoutComments()) }
                .map { it.relative() }
        assertEquals("$description found in", emptyList<String>(), offenders)
    }

    /** Source text with block and line comments removed, so documentation may name what code may not. */
    private fun File.codeWithoutComments(): String =
        readText()
            .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("""//[^\n]*"""), "")

    private fun File.relative(): String =
        relativeTo(RepositoryFiles.app.resolve("src/main/kotlin/com/pennilogic/android")).path.replace('\\', '/')
}
