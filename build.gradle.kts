// Root build: declares plugin versions from the catalogue and wires the formatter gate.
plugins {
    // `base` gives the root project `check`/`build` lifecycle tasks so `spotlessCheck`
    // participates in `./gradlew build` and `./gradlew check`.
    base
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.spotless)
}

// Formatting gate: `spotlessCheck` fails the build on any violation; `spotlessApply` fixes it.
spotless {
    kotlin {
        target("app/src/**/*.kt")
        targetExclude("**/build/**")
        ktlint(libs.versions.ktlint.get())
        trimTrailingWhitespace()
        endWithNewline()
    }
    kotlinGradle {
        target("*.gradle.kts", "app/*.gradle.kts")
        ktlint(libs.versions.ktlint.get())
        trimTrailingWhitespace()
        endWithNewline()
    }
}
