package com.pennilogic.android.observability

import com.pennilogic.android.config.ConfigurationResult

/** Log tag shared by the scaffold's structured events. */
const val LOG_TAG = "PenniLogic"

/**
 * Structured process-start event. It carries build metadata and the configuration outcome only:
 * no configuration values, no user data, no payload of any kind.
 */
data class StartupEvent(
    val buildType: String,
    val versionName: String,
    val versionCode: Int,
    val configurationStatus: String,
    val configurationProblems: List<String>,
) {
    /** Single-line JSON object with a fixed key order, suitable for one logcat entry. */
    fun toJson(): String =
        buildString {
            append('{')
            append("\"event\":").append(quote(EVENT_NAME)).append(',')
            append("\"build_type\":").append(quote(buildType)).append(',')
            append("\"version_name\":").append(quote(versionName)).append(',')
            append("\"version_code\":").append(versionCode).append(',')
            append("\"configuration\":").append(quote(configurationStatus)).append(',')
            append("\"problems\":[")
            configurationProblems.forEachIndexed { index, problem ->
                if (index > 0) append(',')
                append(quote(problem))
            }
            append("]}")
        }

    companion object {
        const val EVENT_NAME = "app_start"
        const val STATUS_LOADED = "loaded"
        const val STATUS_INVALID = "invalid"

        fun forProcessStart(
            buildType: String,
            versionName: String,
            versionCode: Int,
            configuration: ConfigurationResult,
        ): StartupEvent =
            when (configuration) {
                is ConfigurationResult.Loaded -> {
                    StartupEvent(buildType, versionName, versionCode, STATUS_LOADED, emptyList())
                }

                is ConfigurationResult.Invalid -> {
                    StartupEvent(
                        buildType,
                        versionName,
                        versionCode,
                        STATUS_INVALID,
                        configuration.problems.map { it.description },
                    )
                }
            }

        /** Escapes a string as a JSON string literal (RFC 8259). */
        internal fun quote(value: String): String =
            buildString(value.length + 2) {
                append('"')
                for (character in value) {
                    when {
                        character == '"' -> append("\\\"")
                        character == '\\' -> append("\\\\")
                        character == '\n' -> append("\\n")
                        character == '\r' -> append("\\r")
                        character == '\t' -> append("\\t")
                        character < ' ' -> append("\\u%04x".format(character.code))
                        else -> append(character)
                    }
                }
                append('"')
            }
    }
}
