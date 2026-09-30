package com.pennilogic.android.platform

/**
 * The Android platform baseline adopted by android#57 (T-AND-07).
 *
 * These constants are the single in-code statement of the levels the build is pinned to in
 * `gradle/libs.versions.toml`; `TargetConformanceTest` proves that the merged manifest of every
 * variant agrees with them, so a manifest merge or dependency can never lower the effective target
 * silently. Feature tickets read this object instead of hard-coding API numbers.
 */
object PlatformBaseline {
    /** Play-required target for submissions from 2026-08-31: Android 16. */
    const val TARGET_API: Int = 36

    /** SDK the sources compile against; Compose 1.12 needs 37, so it moves with the Compose BOM. */
    const val COMPILE_API: Int = 36

    /** Provisional minimum; android#2 confirms or raises it. Never lowered by this baseline. */
    const val MIN_API: Int = 26

    /**
     * API levels whose behaviour differences are recorded for T-QA-12 in
     * `docs/platform/android-behavior-matrix.json` and exercised on the governed emulator matrix.
     */
    val QA_API_LEVELS: List<Int> = listOf(31, 33, 35, 36)

    /**
     * Smallest width, in dp, from which Android 16 ignores orientation, resizability and aspect-ratio
     * restrictions for apps targeting API 36. Every root surface must remain usable at and above it.
     */
    const val LARGE_SCREEN_MIN_WIDTH_DP: Int = 600
}
