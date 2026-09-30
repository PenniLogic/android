package com.pennilogic.android.contract

/**
 * The Android 16 baseline's source rules, as pure functions over file text so they can be proven on
 * planted snippets (`SourceRulesSelfTest`) as well as applied to the real tree
 * (`PlatformBaselineSourceTest`). Every rule strips comments first, so documentation may name what
 * code may not.
 */
object SourceRules {
    const val ROOT_SURFACE_FILE: String = "platform/ui/RootSurface.kt"
    const val PREDICTIVE_BACK_FILE: String = "platform/ui/PredictiveBack.kt"

    /** A rule violation: which rule, in which file. */
    data class Violation(
        val rule: String,
        val file: String,
    )

    /** Source text with block and line comments removed. */
    fun codeWithoutComments(source: String): String =
        source
            .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
            .replace(Regex("""//[^\n]*"""), "")

    /** Legacy back handling that Android 16 no longer calls for apps targeting API 36. */
    private val legacyBack =
        mapOf(
            "onBackPressed() override" to Regex("""override\s+fun\s+onBackPressed\s*\("""),
            "KEYCODE_BACK handling" to Regex("""\bKEYCODE_BACK\b"""),
            "onKeyDown() override" to Regex("""override\s+fun\s+onKeyDown\s*\("""),
            "onKeyUp() override" to Regex("""override\s+fun\s+onKeyUp\s*\("""),
        )

    /**
     * Direct back registration, by import (form-independent) and by receiver-less call as a second
     * net; both forms of the Kotlin call (`BackHandler(...)` and the trailing lambda `BackHandler {`)
     * are matched, and so is the fully qualified form that needs no import
     * (`androidx.activity.compose.BackHandler(...) {`, #66 core review F7).
     */
    private val directBackImports =
        Regex(
            """^\s*import\s+(androidx\.activity\.compose\.BackHandler|""" +
                """androidx\.activity\.compose\.PredictiveBackHandler|""" +
                """androidx\.activity\.OnBackPressedCallback|android\.window\.OnBackInvokedCallback|""" +
                """android\.window\.OnBackInvokedDispatcher)\b""",
            RegexOption.MULTILINE,
        )
    private val directBackCalls =
        listOf(
            Regex("""(?<![A-Za-z0-9_.])PredictiveBackHandler\s*[({]"""),
            Regex("""(?<![A-Za-z0-9_.])BackHandler\s*[({]"""),
            Regex(
                """\b(androidx\.activity\.compose\.(Predictive)?BackHandler|""" +
                    """androidx\.activity\.OnBackPressedCallback|android\.window\.OnBackInvokedCallback)\s*[({]""",
            ),
            Regex("""\baddCallback\s*\("""),
            Regex("""\bregisterOnBackInvokedCallback\s*\("""),
        )

    /**
     * Window-insets API, by import: any `androidx.compose.foundation.layout` import that names the
     * insets types, the inset-derived padding modifiers, `asPaddingValues` or inset consumption. The
     * plain `padding` modifier is not an inset API and stays allowed.
     */
    private val insetImports =
        Regex(
            """^\s*import\s+androidx\.compose\.foundation\.layout\.(WindowInsets\w*|windowInsets\w*|""" +
                """asPaddingValues|""" +
                """consumeWindowInsets|onConsumedWindowInsetsChanged|safeDrawing\w*|safeContent\w*|safeGestures\w*|""" +
                """systemBars\w*|statusBars\w*|navigationBars\w*|displayCutout\w*|ime\w*|captionBar\w*|""" +
                """mandatorySystemGestures\w*|systemGestures\w*|tappableElement\w*|waterfall\w*)\b""",
            RegexOption.MULTILINE,
        )
    private val insetCalls =
        Regex(
            """\.(windowInsetsPadding|safeDrawingPadding|safeContentPadding|safeGesturesPadding|systemBarsPadding|""" +
                """statusBarsPadding|navigationBarsPadding|displayCutoutPadding|imePadding|captionBarPadding|""" +
                """consumeWindowInsets|asPaddingValues)\s*\(""",
        )

    /** Every composition root: `setContent {`, `.setContent {`, `setContent(parent) {`. */
    private val compositionRoot = Regex("""\bsetContent\s*[({]""")
    private val rootSurfaceCall = Regex("""\bRootSurface\s*[({]""")

    /** A `PenniLogicBackHandler` call whose `enabled` argument is not named explicitly. */
    private val backHandlerCall = Regex("""\bPenniLogicBackHandler\s*\(([^)]*)\)""", RegexOption.DOT_MATCHES_ALL)

    private val orientation =
        mapOf(
            "setRequestedOrientation()" to Regex("""\bsetRequestedOrientation\s*\("""),
            "requestedOrientation assignment" to Regex("""\brequestedOrientation\s*="""),
            "edge-to-edge opt-out" to Regex("""\bwindowOptOutEdgeToEdgeEnforcement\b"""),
        )

    /**
     * Applies every rule to [sources] (path relative to the package root → file text) and returns
     * the violations. Paths use `/` separators.
     */
    fun violations(sources: Map<String, String>): List<Violation> {
        val found = mutableListOf<Violation>()
        for ((path, raw) in sources) {
            val code = codeWithoutComments(raw)
            legacyBack.forEach { (rule, pattern) -> if (pattern.containsMatchIn(code)) found += Violation(rule, path) }
            if (!path.endsWith(PREDICTIVE_BACK_FILE)) {
                if (directBackImports.containsMatchIn(code)) found += Violation("direct back import", path)
                if (directBackCalls.any { it.containsMatchIn(code) }) {
                    found +=
                        Violation("direct back registration", path)
                }
            }
            if (!path.endsWith(ROOT_SURFACE_FILE)) {
                if (insetImports.containsMatchIn(code)) {
                    found +=
                        Violation("window-insets import outside RootSurface", path)
                }
                if (insetCalls.containsMatchIn(code)) found += Violation("inset consumption outside RootSurface", path)
            }
            if (compositionRoot.containsMatchIn(code) && !rootSurfaceCall.containsMatchIn(code)) {
                found += Violation("composition root without RootSurface", path)
            }
            backHandlerCall.findAll(code).forEach { call ->
                // The declaration itself lives in PredictiveBack.kt; only call sites are checked.
                if (!path.endsWith(PREDICTIVE_BACK_FILE) &&
                    !Regex("""\benabled\s*=""").containsMatchIn(call.groupValues[1])
                ) {
                    found += Violation("PenniLogicBackHandler without an explicit enabled argument", path)
                }
            }
            orientation.forEach { (rule, pattern) -> if (pattern.containsMatchIn(code)) found += Violation(rule, path) }
        }
        return found
    }
}
