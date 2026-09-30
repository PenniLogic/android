package com.pennilogic.android.contract

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Proves that each [SourceRules] rule bites on the form developers actually write, including the
 * forms the round-1 review showed slipping past the first version (trailing-lambda `BackHandler {`,
 * a dispatcher held in a local, a comment mentioning `RootSurface`, `.setContent {` roots).
 */
class SourceRulesSelfTest {
    private fun rulesFor(vararg files: Pair<String, String>): Set<String> =
        SourceRules.violations(files.toMap()).map { it.rule }.toSet()

    @Test
    fun `a clean activity through the root surface has no violations`() {
        val clean =
            """
            package com.pennilogic.android
            import androidx.activity.compose.setContent
            import com.pennilogic.android.platform.ui.RootSurface
            class MainActivity : ComponentActivity() {
                override fun onCreate(savedInstanceState: Bundle?) {
                    setContent { PenniLogicTheme { RootSurface { ScaffoldScreen(state) } } }
                }
            }
            """.trimIndent()
        assertEquals(emptySet<String>(), rulesFor("MainActivity.kt" to clean))
    }

    @Test
    fun `trailing-lambda BackHandler and a dispatcher held in a local are caught`() {
        val trailingLambda =
            "import androidx.compose.runtime.Composable\n@Composable fun Screen() { BackHandler { finish() } }"
        assertTrue("direct back registration" in rulesFor("ui/Screen.kt" to trailingLambda))
        val predictive = "@Composable fun Screen() { PredictiveBackHandler(true) { progress -> } }"
        assertTrue("direct back registration" in rulesFor("ui/Screen.kt" to predictive))
        val local = "fun register() { val d = onBackPressedDispatcher; d.addCallback(this) { } }"
        assertTrue("direct back registration" in rulesFor("ui/Screen.kt" to local))
        val safeCall = "fun register() { onBackPressedDispatcher?.addCallback(callback) }"
        assertTrue("direct back registration" in rulesFor("ui/Screen.kt" to safeCall))
        val platform = "fun register() { window.onBackInvokedDispatcher.registerOnBackInvokedCallback(0, callback) }"
        assertTrue("direct back registration" in rulesFor("ui/Screen.kt" to platform))
    }

    @Test
    fun `a fully qualified back registration without an import is caught by the call net`() {
        // #66 core review F7: no import line, so only the call net can see it.
        val fullyQualified =
            "fun bind(view: ComposeView) { view.setContent { " +
                "androidx.activity.compose.BackHandler(enabled = true) { finish() }; RootSurface { } } }"
        assertEquals(setOf("direct back registration"), rulesFor("ui/Screen.kt" to fullyQualified))
        val trailingLambda = "@Composable fun Screen() { androidx.activity.compose.BackHandler { finish() } }"
        assertEquals(setOf("direct back registration"), rulesFor("ui/Screen.kt" to trailingLambda))
        val predictive =
            "@Composable fun Screen() { androidx.activity.compose.PredictiveBackHandler(true) { progress -> } }"
        assertEquals(setOf("direct back registration"), rulesFor("ui/Screen.kt" to predictive))
        val callback =
            "val callback = object : androidx.activity.OnBackPressedCallback(true) { " +
                "override fun handleOnBackPressed() {} }"
        assertEquals(setOf("direct back registration"), rulesFor("ui/Screen.kt" to callback))
        val invoked = "val callback = android.window.OnBackInvokedCallback { finish() }"
        assertEquals(setOf("direct back registration"), rulesFor("ui/Screen.kt" to invoked))
        assertEquals(
            "the primitive itself may use the fully qualified form",
            emptySet<String>(),
            rulesFor(SourceRules.PREDICTIVE_BACK_FILE to predictive),
        )
        assertEquals(
            "a comment naming the fully qualified form is not a registration",
            emptySet<String>(),
            rulesFor(
                "ui/Screen.kt" to "/** never androidx.activity.compose.BackHandler(enabled = true) { } */ class X",
            ),
        )
    }

    @Test
    fun `direct back imports are caught form-independently, except in the primitive itself`() {
        for (
        import in
        listOf(
            "import androidx.activity.compose.BackHandler",
            "import androidx.activity.compose.PredictiveBackHandler",
            "import androidx.activity.OnBackPressedCallback",
            "import android.window.OnBackInvokedCallback",
            "import android.window.OnBackInvokedDispatcher",
        )
        ) {
            assertTrue(import, "direct back import" in rulesFor("ui/Screen.kt" to "$import\nclass X"))
            assertEquals(import, emptySet<String>(), rulesFor(SourceRules.PREDICTIVE_BACK_FILE to "$import\nclass X"))
        }
    }

    @Test
    fun `legacy back callbacks are caught but their mention in comments is not`() {
        assertTrue("onBackPressed() override" in rulesFor("A.kt" to "class A { override fun onBackPressed() {} }"))
        assertTrue("KEYCODE_BACK handling" in rulesFor("A.kt" to "if (keyCode == KeyEvent.KEYCODE_BACK) return true"))
        assertTrue("onKeyDown() override" in rulesFor("A.kt" to "override fun onKeyDown(k: Int, e: KeyEvent?) = false"))
        assertEquals(
            emptySet<String>(),
            rulesFor("A.kt" to "/** no onBackPressed() and no KEYCODE_BACK here */ class A"),
        )
        assertEquals(emptySet<String>(), rulesFor("A.kt" to "// override fun onBackPressed()\nclass A"))
    }

    @Test
    fun `window-insets imports and calls outside RootSurface are caught`() {
        for (
        import in
        listOf(
            "import androidx.compose.foundation.layout.WindowInsets",
            "import androidx.compose.foundation.layout.safeDrawing",
            "import androidx.compose.foundation.layout.systemBarsPadding",
            "import androidx.compose.foundation.layout.asPaddingValues",
            "import androidx.compose.foundation.layout.consumeWindowInsets",
            "import androidx.compose.foundation.layout.imePadding",
            "import androidx.compose.foundation.layout.windowInsetsPadding",
        )
        ) {
            assertTrue(
                import,
                "window-insets import outside RootSurface" in rulesFor("ui/Screen.kt" to "$import\nclass X"),
            )
            assertEquals(import, emptySet<String>(), rulesFor(SourceRules.ROOT_SURFACE_FILE to "$import\nclass X"))
        }
        assertEquals(
            "the plain padding modifier is allowed",
            emptySet<String>(),
            rulesFor("ui/Screen.kt" to "import androidx.compose.foundation.layout.padding\nclass X"),
        )
        assertTrue(
            "inset consumption outside RootSurface" in rulesFor("ui/Screen.kt" to "Modifier.statusBarsPadding()"),
        )
        assertTrue(
            "inset consumption outside RootSurface" in
                rulesFor("ui/Screen.kt" to "LazyColumn(contentPadding = insets.asPaddingValues())"),
        )
        assertTrue(
            "inset consumption outside RootSurface" in
                rulesFor("ui/Screen.kt" to "Modifier.consumeWindowInsets(insets)"),
        )
    }

    @Test
    fun `a composition root needs a RootSurface call, not a comment, in every setContent form`() {
        val commentOnly =
            "/** bypasses RootSurface deliberately */\nclass Second { fun go() { setContent { Text(\"planted\") } } }"
        assertTrue("composition root without RootSurface" in rulesFor("Second.kt" to commentOnly))
        val viewRoot = "fun bind(view: ComposeView) { view.setContent { Text(\"planted\") } }"
        assertTrue("composition root without RootSurface" in rulesFor("Second.kt" to viewRoot))
        val parentRoot = "fun go() { setContent(parent) { Text(\"planted\") } }"
        assertTrue("composition root without RootSurface" in rulesFor("Second.kt" to parentRoot))
        val ok = "fun go() { setContent { RootSurface { Text(\"ok\") } } }"
        assertEquals(emptySet<String>(), rulesFor("Second.kt" to ok))
        val okView = "fun bind(view: ComposeView) { view.setContent { RootSurface(insets = WindowInsets(0)) { } } }"
        assertEquals(emptySet<String>(), rulesFor("Second.kt" to okView))
    }

    @Test
    fun `a PenniLogicBackHandler call must state enabled explicitly`() {
        assertTrue(
            "PenniLogicBackHandler without an explicit enabled argument" in
                rulesFor("ui/Screen.kt" to "PenniLogicBackHandler(onBack = { close() })"),
        )
        assertTrue(
            "PenniLogicBackHandler without an explicit enabled argument" in
                rulesFor("ui/Screen.kt" to "PenniLogicBackHandler(onProgress = { }, onBack = { close() })"),
        )
        assertEquals(
            emptySet<String>(),
            rulesFor("ui/Screen.kt" to "PenniLogicBackHandler(enabled = sheetOpen, onBack = { sheetOpen = false })"),
        )
        assertEquals(
            "the declaration in the primitive is not a call site",
            emptySet<String>(),
            rulesFor(
                SourceRules.PREDICTIVE_BACK_FILE to
                    "fun PenniLogicBackHandler(\n    enabled: Boolean,\n    onBack: () -> Unit,\n) {}",
            ),
        )
    }

    @Test
    fun `orientation requests and the edge-to-edge opt-out are caught`() {
        assertTrue(
            "setRequestedOrientation()" in
                rulesFor("A.kt" to "setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT)"),
        )
        assertTrue("requestedOrientation assignment" in rulesFor("A.kt" to "requestedOrientation = 1"))
        assertTrue("edge-to-edge opt-out" in rulesFor("A.kt" to "theme.windowOptOutEdgeToEdgeEnforcement"))
    }
}
