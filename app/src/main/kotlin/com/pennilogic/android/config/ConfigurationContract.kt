package com.pennilogic.android.config

/**
 * Names of the build configuration keys. Each key is an environment variable at build time and
 * a `BuildConfig` field at run time; the contract (type, default, required) is in SCAFFOLD.md.
 */
object ConfigurationKeys {
    const val ENVIRONMENT = "PENNILOGIC_ENVIRONMENT"
    const val API_BASE_URL = "PENNILOGIC_API_BASE_URL"
    const val BUILD_LABEL = "PENNILOGIC_BUILD_LABEL"

    val required: Set<String> = setOf(ENVIRONMENT, API_BASE_URL)
    val all: List<String> = listOf(ENVIRONMENT, API_BASE_URL, BUILD_LABEL)
}

/** Deployment environment the build talks to. */
enum class Environment(
    val configurationValue: String,
) {
    DEVELOPMENT("development"),
    STAGING("staging"),
    PRODUCTION("production"),
    ;

    companion object {
        fun parse(value: String): Environment? = entries.firstOrNull { it.configurationValue == value }
    }
}

/** Fully validated configuration of a running build. */
data class AppConfiguration(
    val environment: Environment,
    /** Absolute `https` URL without credentials, query or fragment. */
    val apiBaseUrl: String,
    /** Printable ASCII label identifying the build, for example a CI run identifier. */
    val buildLabel: String,
)

/** One configuration defect. Never carries the offending value, so it is safe to log and display. */
data class ConfigurationProblem(
    val key: String,
    val kind: Kind,
) {
    enum class Kind { MISSING, INVALID }

    /** Stable `KEY:kind` form used in logs and on screen. */
    val description: String
        get() = "$key:${kind.name.lowercase()}"
}

/** Outcome of loading the configuration; invalid configuration is a state, not a crash. */
sealed interface ConfigurationResult {
    data class Loaded(
        val configuration: AppConfiguration,
    ) : ConfigurationResult

    data class Invalid(
        val problems: List<ConfigurationProblem>,
    ) : ConfigurationResult
}

/** Where raw configuration values come from (BuildConfig in the app, maps in tests). */
fun interface ConfigurationSource {
    /** Raw value for [key], or null when the key is absent. */
    fun value(key: String): String?
}
