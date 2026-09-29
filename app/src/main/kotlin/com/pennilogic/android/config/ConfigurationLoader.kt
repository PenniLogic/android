package com.pennilogic.android.config

import java.net.URI
import java.net.URISyntaxException

/**
 * Validates raw configuration values against the key contract. All problems are collected in one
 * pass so a misconfigured build reports every defect at once instead of failing key by key.
 */
object ConfigurationLoader {
    const val DEFAULT_BUILD_LABEL = "local"
    const val MAX_BUILD_LABEL_LENGTH = 64
    private const val HTTPS_SCHEME = "https"

    fun load(source: ConfigurationSource): ConfigurationResult {
        val problems = mutableListOf<ConfigurationProblem>()
        val environment = required(source, ConfigurationKeys.ENVIRONMENT, problems, Environment::parse)
        val apiBaseUrl = required(source, ConfigurationKeys.API_BASE_URL, problems, ::parseHttpsUrl)
        val buildLabel =
            optional(source, ConfigurationKeys.BUILD_LABEL, DEFAULT_BUILD_LABEL, problems, ::parseBuildLabel)
        return if (environment != null && apiBaseUrl != null && buildLabel != null && problems.isEmpty()) {
            ConfigurationResult.Loaded(AppConfiguration(environment, apiBaseUrl, buildLabel))
        } else {
            ConfigurationResult.Invalid(problems.toList())
        }
    }

    /** Accepts only absolute `https` URLs with a host and without credentials, query or fragment. */
    internal fun parseHttpsUrl(value: String): String? {
        val uri =
            try {
                URI(value)
            } catch (malformed: URISyntaxException) {
                return null
            }
        val accepted =
            uri.isAbsolute &&
                uri.scheme.equals(HTTPS_SCHEME, ignoreCase = true) &&
                !uri.host.isNullOrEmpty() &&
                uri.userInfo == null &&
                uri.rawQuery == null &&
                uri.rawFragment == null
        return if (accepted) value else null
    }

    /** Printable ASCII of bounded length, so the label is safe to display and to log. */
    internal fun parseBuildLabel(value: String): String? =
        value.takeIf { label -> label.length <= MAX_BUILD_LABEL_LENGTH && label.all { it in ' '..'~' } }

    private fun <T : Any> required(
        source: ConfigurationSource,
        key: String,
        problems: MutableList<ConfigurationProblem>,
        parse: (String) -> T?,
    ): T? {
        val raw = present(source, key)
        if (raw == null) {
            problems += ConfigurationProblem(key, ConfigurationProblem.Kind.MISSING)
            return null
        }
        return parsed(key, raw, problems, parse)
    }

    private fun <T : Any> optional(
        source: ConfigurationSource,
        key: String,
        default: T,
        problems: MutableList<ConfigurationProblem>,
        parse: (String) -> T?,
    ): T? {
        val raw = present(source, key) ?: return default
        return parsed(key, raw, problems, parse)
    }

    private fun <T : Any> parsed(
        key: String,
        raw: String,
        problems: MutableList<ConfigurationProblem>,
        parse: (String) -> T?,
    ): T? {
        val value = parse(raw)
        if (value == null) {
            problems += ConfigurationProblem(key, ConfigurationProblem.Kind.INVALID)
        }
        return value
    }

    /** Blank values count as absent: an empty BuildConfig field means the variable was not set. */
    private fun present(
        source: ConfigurationSource,
        key: String,
    ): String? = source.value(key)?.trim()?.takeIf { it.isNotEmpty() }
}
