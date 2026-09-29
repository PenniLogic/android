import org.gradle.api.tasks.testing.logging.TestExceptionFormat
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

// ---------------------------------------------------------------------------------------------
// Build-time configuration. Values come from the process environment only; the key contract
// (type, default, required) is documented in SCAFFOLD.md. Nothing here is read from files.
// ---------------------------------------------------------------------------------------------

/** Trimmed value of an environment variable, or null when it is unset or blank. */
fun environmentValue(name: String): String? =
    providers
        .environmentVariable(name)
        .orNull
        ?.trim()
        ?.takeIf { it.isNotEmpty() }

/** Escapes a value as a Java string literal for a BuildConfig field. */
fun buildConfigString(value: String): String = "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

val environmentName = environmentValue("PENNILOGIC_ENVIRONMENT")
val apiBaseUrl = environmentValue("PENNILOGIC_API_BASE_URL")
val buildLabel = environmentValue("PENNILOGIC_BUILD_LABEL") ?: "local"

/**
 * Release signing material, present only when every PENNILOGIC_RELEASE_* variable is set.
 * Deliberately not a data class: no generated toString() that could print passwords.
 */
class ReleaseSigning(
    val storePath: String,
    val storePassword: String,
    val keyAlias: String,
    val keyPassword: String,
)

val releaseSigningVariables =
    listOf(
        "PENNILOGIC_RELEASE_KEYSTORE_PATH",
        "PENNILOGIC_RELEASE_KEYSTORE_PASSWORD",
        "PENNILOGIC_RELEASE_KEY_ALIAS",
        "PENNILOGIC_RELEASE_KEY_PASSWORD",
    )

val releaseSigning: ReleaseSigning? =
    run {
        val values = releaseSigningVariables.associateWith { environmentValue(it) }
        val missing = values.filterValues { it == null }.keys
        when (missing.size) {
            releaseSigningVariables.size -> {
                null
            }

            0 -> {
                ReleaseSigning(
                    storePath = values.getValue(releaseSigningVariables[0])!!,
                    storePassword = values.getValue(releaseSigningVariables[1])!!,
                    keyAlias = values.getValue(releaseSigningVariables[2])!!,
                    keyPassword = values.getValue(releaseSigningVariables[3])!!,
                )
            }

            // A partial set is a misconfiguration; fail instead of silently producing an unsigned build.
            else -> {
                throw GradleException(
                    "Release signing needs all of ${releaseSigningVariables.joinToString()}; missing: ${missing.joinToString()}",
                )
            }
        }
    }

android {
    namespace = "com.pennilogic.android"
    compileSdk =
        libs.versions.android.compileSdk
            .get()
            .toInt()
    buildToolsVersion =
        libs.versions.android.buildTools
            .get()

    defaultConfig {
        applicationId = "com.pennilogic.android"
        minSdk =
            libs.versions.android.minSdk
                .get()
                .toInt()
        targetSdk =
            libs.versions.android.targetSdk
                .get()
                .toInt()
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "BUILD_LABEL", buildConfigString(buildLabel))
    }

    signingConfigs {
        releaseSigning?.let { signing ->
            create("release") {
                storeFile = file(signing.storePath)
                storePassword = signing.storePassword
                keyAlias = signing.keyAlias
                keyPassword = signing.keyPassword
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            // AGP's locally generated debug keystore (~/.android/debug.keystore); never committed.
            signingConfig = signingConfigs.getByName("debug")
            enableUnitTestCoverage = true
            buildConfigField("String", "ENVIRONMENT", buildConfigString(environmentName ?: "development"))
            buildConfigField(
                "String",
                "API_BASE_URL",
                buildConfigString(apiBaseUrl ?: "https://api.dev.pennilogic.invalid"),
            )
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Unsigned unless the environment provides the full release signing set.
            signingConfig = releaseSigning?.let { signingConfigs.getByName("release") }
            // Release carries no defaults: required keys must come from the environment.
            buildConfigField("String", "ENVIRONMENT", buildConfigString(environmentName ?: ""))
            buildConfigField("String", "API_BASE_URL", buildConfigString(apiBaseUrl ?: ""))
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        // Every warning fails the build; there is no baseline file on purpose.
        abortOnError = true
        warningsAsErrors = true
        checkReleaseBuilds = true
        // targetSdk is deliberately pinned to the agreed Android 16 / API 36 baseline; raising it is
        // the behaviour-baseline ticket's decision, so the "newer target exists" check is disabled.
        disable += "OldTargetApi"
        // "A newer version is available" checks depend on the release calendar and the network, so
        // they would fail a pinned build non-deterministically. Versions are owned by the catalogue.
        disable += setOf("AndroidGradlePluginVersion", "GradleDependency")
    }

    testOptions {
        unitTests.all { test ->
            test.useJUnit()
            test.testLogging {
                events("passed", "skipped", "failed")
                exceptionFormat = TestExceptionFormat.FULL
            }
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = JvmTarget.JVM_17
        allWarningsAsErrors = true
    }
}

dependencies {
    implementation(platform(libs.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.compose.material3)
    implementation(libs.compose.ui.tooling.preview)
    debugImplementation(libs.compose.ui.tooling)

    testImplementation(libs.junit4)
}
