package com.pennilogic.android.observability

import android.content.res.Resources
import android.util.JsonReader
import android.util.JsonToken
import android.util.Log
import com.pennilogic.android.R
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.io.StringReader
import java.security.MessageDigest

class PrivacyPayloadRefused internal constructor(
    val code: String,
) : IllegalArgumentException("privacy_payload_refused:$code")

/** One packaged source for the local logger and the external inspection harness. */
class PrivacyTrafficPolicy private constructor(
    private val document: JSONObject,
    val sha256: String,
) {
    fun requireLocalPayload(json: String) {
        val payload = parse(json)
        val name = payload.opt("event") as? String ?: throw PrivacyPayloadRefused("undeclared_payload_schema")
        val schemas = document.getJSONObject("local_events")
        refuseUnless(schemas.has(name), "undeclared_payload_schema")
        validate(schemas.getJSONObject(name), payload)
    }

    fun requireNetworkPayload(
        host: String,
        path: String,
        json: String,
    ) {
        val destinations = document.getJSONArray("destinations")
        val route =
            (0 until destinations.length())
                .map { destinations.getJSONObject(it) }
                .singleOrNull { it.getString("host") == host && it.getString("path") == path }
        refuseUnless(route != null, "undeclared_route")
        val schema =
            document
                .getJSONObject("network_schemas")
                .getJSONObject(checkNotNull(route).getString("schema"))
        val payload = parse(json)
        refuseUnless(
            payload.keys().asSequence().none { it in DEVICE_ONLY_FIELDS || it.endsWith("_bidx") },
            "device_only_artifact_egress",
        )
        refuseUnless(!payload.has("source_event_id"), "source_event_origin_unverified")
        if (schema.getString("category") == "analytics") {
            refuseUnless(
                payload.keys().asSequence().none { MONEY_FIELD.matches(it) || payload.get(it) is Number },
                "monetary_analytics",
            )
        }
        validate(schema, payload)
    }

    private fun validate(
        schema: JSONObject,
        payload: JSONObject,
    ) {
        val fields = schema.getJSONObject("fields")
        refuseUnless(
            payload.keys().asSequence().toSet() == fields.keys().asSequence().toSet(),
            "undeclared_payload_field",
        )
        for (name in fields.keys()) {
            refuseUnless(accepts(payload.get(name), fields.getJSONObject(name)), "payload_value_refused")
        }
    }

    private fun accepts(
        value: Any,
        rule: JSONObject,
    ): Boolean {
        if (value == JSONObject.NULL) return rule.getBoolean("nullable")
        return when (rule.getString("kind")) {
            "enum" -> {
                val values = rule.getJSONArray("values")
                value is String && (0 until values.length()).any { values.getString(it) == value }
            }

            "integer" -> {
                (value is Int || value is Long) &&
                    (value as Number).toLong() in rule.getLong("minimum")..rule.getLong("maximum")
            }

            "boolean" -> {
                value is Boolean
            }

            "pattern" -> {
                value is String &&
                    value.length <= rule.getInt("max_length") &&
                    value.all { it.code < 128 } &&
                    Regex(rule.getString("pattern")).matches(value)
            }

            "array" -> {
                value is JSONArray &&
                    value.length() <= rule.getInt("max_items") &&
                    (0 until value.length()).all { accepts(value.get(it), rule.getJSONObject("items")) }
            }

            else -> {
                throw PrivacyPayloadRefused("invalid_field_rule")
            }
        }
    }

    companion object {
        private const val MAX_BYTES = 262144
        private val MONEY_FIELD =
            Regex(
                "amount|amount_minor|balance|currency|money|monetary|price|subtotal|total|value|revenue|cost",
                RegexOption.IGNORE_CASE,
            )
        private val IDENTIFIER = Regex("[a-z][a-z0-9_]{0,63}")
        private val COMPONENT = Regex("[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[A-Za-z0-9_.+-]+")
        private val HOST =
            Regex("(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
        private val DEVICE_ONLY_FIELDS =
            setOf(
                "raw_event_digest",
                "raw_hash",
                "message_digest",
                "message_hash",
                "raw_message_hash",
                "raw_message_digest",
            )
        private val KEYS =
            setOf(
                "schema_version",
                "required_journeys",
                "limits",
                "components",
                "destinations",
                "network_schemas",
                "journey_providers",
                "local_events",
            )

        fun fromResources(resources: Resources): PrivacyTrafficPolicy =
            resources.openRawResource(R.raw.privacy_traffic_policy).use { stream ->
                val bytes = ArrayList<Byte>()
                val buffer = ByteArray(4096)
                var count = stream.read(buffer)
                while (count != -1) {
                    refuseUnless(bytes.size + count <= MAX_BYTES, "document_size_refused")
                    for (index in 0 until count) bytes.add(buffer[index])
                    count = stream.read(buffer)
                }
                fromBytes(bytes.toByteArray())
            }

        internal fun fromBytes(bytes: ByteArray): PrivacyTrafficPolicy {
            refuseUnless(bytes.isNotEmpty() && bytes.size <= MAX_BYTES, "document_size_refused")
            val document = parse(bytes.toString(Charsets.UTF_8))
            try {
                validateDocument(document)
            } catch (_: JSONException) {
                throw PrivacyPayloadRefused("invalid_policy")
            }
            val sha256 =
                MessageDigest
                    .getInstance("SHA-256")
                    .digest(bytes)
                    .joinToString("") { "%02x".format(it) }
            return PrivacyTrafficPolicy(document, sha256)
        }

        private fun parse(json: String): JSONObject {
            refuseUnless(json.toByteArray(Charsets.UTF_8).size <= MAX_BYTES, "document_size_refused")
            return try {
                JsonReader(StringReader(json)).use { reader ->
                    reader.isLenient = false
                    var nodes = 0

                    fun read(depth: Int): Any {
                        nodes++
                        refuseUnless(depth <= 12 && nodes <= 4096, "document_complexity_refused")
                        return when (reader.peek()) {
                            JsonToken.BEGIN_OBJECT -> {
                                val result = JSONObject()
                                reader.beginObject()
                                while (reader.hasNext()) {
                                    val name = reader.nextName()
                                    refuseUnless(!result.has(name), "duplicate_json_key")
                                    result.put(name, read(depth + 1))
                                }
                                reader.endObject()
                                result
                            }

                            JsonToken.BEGIN_ARRAY -> {
                                val result = JSONArray()
                                reader.beginArray()
                                while (reader.hasNext()) result.put(read(depth + 1))
                                reader.endArray()
                                result
                            }

                            JsonToken.STRING -> {
                                reader.nextString()
                            }

                            JsonToken.BOOLEAN -> {
                                reader.nextBoolean()
                            }

                            JsonToken.NULL -> {
                                reader.nextNull()
                                JSONObject.NULL
                            }

                            JsonToken.NUMBER -> {
                                val token = reader.nextString()
                                if (Regex("-?(0|[1-9][0-9]*)").matches(token)) {
                                    val number =
                                        token.toLongOrNull() ?: throw PrivacyPayloadRefused("numeric_value_refused")
                                    if (number in Int.MIN_VALUE..Int.MAX_VALUE) number.toInt() else number
                                } else {
                                    val number =
                                        token.toDoubleOrNull() ?: throw PrivacyPayloadRefused("numeric_value_refused")
                                    refuseUnless(number.isFinite(), "non_finite_json")
                                    number
                                }
                            }

                            else -> {
                                throw PrivacyPayloadRefused("malformed_document")
                            }
                        }
                    }
                    val result = read(0) as? JSONObject ?: throw PrivacyPayloadRefused("document_object_required")
                    refuseUnless(reader.peek() == JsonToken.END_DOCUMENT, "malformed_document")
                    result
                }
            } catch (_: IOException) {
                throw PrivacyPayloadRefused("malformed_document")
            } catch (_: IllegalStateException) {
                throw PrivacyPayloadRefused("malformed_document")
            } catch (_: JSONException) {
                throw PrivacyPayloadRefused("malformed_document")
            }
        }

        private fun validateDocument(document: JSONObject) {
            refuseUnless(
                document.keys().asSequence().toSet() == KEYS && document.opt("schema_version") == 1,
                "invalid_policy",
            )
            refuseUnless(
                strings(
                    document.getJSONArray("required_journeys"),
                ) == listOf("ingestion", "clarification", "analytics"),
                "required_journey_drift",
            )
            val expectedLimits =
                mapOf(
                    "max_requests" to 64,
                    "max_body_bytes" to 65536,
                    "max_header_bytes" to 8192,
                    "max_capture_bytes" to 1048576,
                    "run_timeout_seconds" to 20,
                    "max_retention_seconds" to 86400,
                )
            val limits = document.getJSONObject("limits")
            refuseUnless(
                limits.keys().asSequence().toSet() == expectedLimits.keys &&
                    expectedLimits.all { (key, value) -> limits.opt(key) == value },
                "capture_budget_drift",
            )
            val components = document.getJSONObject("components")
            refuseUnless(
                components.keys().asSequence().toSet() == setOf("debug", "release"),
                "invalid_component_registry",
            )
            for (variant in listOf("debug", "release")) {
                val entries = strings(components.getJSONArray(variant))
                refuseUnless(
                    entries.size <= 256 && entries == entries.distinct().sorted() &&
                        entries.all { COMPONENT.matches(it) },
                    "invalid_component_registry",
                )
            }
            val local = document.getJSONObject("local_events")
            val network = document.getJSONObject("network_schemas")
            refuseUnless(local.keys().asSequence().none { network.has(it) }, "payload_schema_overlap")
            for (schemas in listOf(local, network)) {
                for (name in schemas.keys()) {
                    refuseUnless(IDENTIFIER.matches(name), "invalid_payload_schema")
                    val schema = schemas.getJSONObject(name)
                    refuseUnless(
                        schema.keys().asSequence().toSet() == setOf("category", "fields"),
                        "invalid_payload_schema",
                    )
                    val category = schema.getString("category")
                    refuseUnless(
                        if (schemas === local) category == "local_log" else category in setOf("analytics", "api"),
                        "payload_category_drift",
                    )
                    val fields = schema.getJSONObject("fields")
                    refuseUnless(fields.length() in 1..32, "invalid_payload_schema")
                    for (field in fields.keys()) {
                        refuseUnless(IDENTIFIER.matches(field), "invalid_payload_schema")
                        val rule = fields.getJSONObject(field)
                        validateRule(rule)
                        if (schemas === network) {
                            refuseUnless(
                                field !in DEVICE_ONLY_FIELDS && !field.endsWith("_bidx") && field != "source_event_id",
                                "device_only_schema_refused",
                            )
                        }
                        if (category == "analytics") {
                            refuseUnless(
                                !MONEY_FIELD.matches(field) && rule.getString("kind") in setOf("enum", "boolean"),
                                "unsafe_analytics_schema",
                            )
                        }
                    }
                }
            }
            val providers = document.getJSONObject("journey_providers")
            refuseUnless(
                providers.keys().asSequence().all {
                    it in setOf("ingestion", "clarification", "analytics")
                },
                "invalid_journey_provider",
            )
            val destinations = document.getJSONArray("destinations")
            refuseUnless(destinations.length() <= 32, "invalid_destination_registry")
            val routes = mutableSetOf<Pair<String, String>>()
            for (index in 0 until destinations.length()) {
                val route = destinations.getJSONObject(index)
                refuseUnless(
                    route.keys().asSequence().toSet() ==
                        setOf("host", "component", "path", "schema", "journey", "synthetic") &&
                        HOST.matches(route.getString("host")) &&
                        route.getString("component") in strings(components.getJSONArray("release")) &&
                        Regex("/[a-z/_]{1,64}").matches(route.getString("path")) &&
                        network.has(route.getString("schema")) &&
                        route.getString("journey") in setOf("ingestion", "clarification", "analytics") &&
                        route.opt("synthetic") is Boolean,
                    "invalid_route",
                )
                refuseUnless(routes.add(route.getString("host") to route.getString("path")), "duplicate_route")
                refuseUnless(
                    route.getString("journey") != "analytics" ||
                        network.getJSONObject(route.getString("schema")).getString("category") == "analytics",
                    "analytics_route_drift",
                )
            }
        }

        private fun validateRule(rule: JSONObject) {
            val kind = rule.getString("kind")
            val keys =
                when (kind) {
                    "enum" -> setOf("kind", "values", "nullable")
                    "integer" -> setOf("kind", "minimum", "maximum", "nullable")
                    "boolean" -> setOf("kind", "nullable")
                    "pattern" -> setOf("kind", "pattern", "max_length", "nullable")
                    "array" -> setOf("kind", "items", "max_items", "nullable")
                    else -> throw PrivacyPayloadRefused("invalid_field_rule")
                }
            refuseUnless(
                rule.keys().asSequence().toSet() == keys && rule.opt("nullable") is Boolean,
                "invalid_field_rule",
            )
            when (kind) {
                "enum" -> {
                    val values = strings(rule.getJSONArray("values"))
                    refuseUnless(
                        values.size in 1..64 && values.size == values.distinct().size &&
                            values.all { it.length in 1..96 && it.all { character -> character.code < 128 } },
                        "invalid_field_rule",
                    )
                }

                "integer" -> {
                    refuseUnless(
                        rule.opt("minimum") is Int && rule.opt("maximum") is Int &&
                            rule.getInt("minimum") <= rule.getInt("maximum"),
                        "invalid_field_rule",
                    )
                }

                "pattern" -> {
                    val pattern = rule.getString("pattern")
                    refuseUnless(
                        pattern.length in 1..96 && rule.opt("max_length") is Int && rule.getInt("max_length") in 1..96,
                        "invalid_field_rule",
                    )
                    try {
                        Regex(pattern)
                    } catch (_: java.util.regex.PatternSyntaxException) {
                        throw PrivacyPayloadRefused("invalid_field_rule")
                    }
                }

                "array" -> {
                    refuseUnless(
                        rule.opt("max_items") is Int && rule.getInt("max_items") in 1..16,
                        "invalid_field_rule",
                    )
                    validateRule(rule.getJSONObject("items"))
                }
            }
        }

        private fun strings(array: JSONArray): List<String> =
            (0 until array.length()).map {
                array.opt(it) as? String ?: throw PrivacyPayloadRefused("invalid_policy")
            }

        private fun refuseUnless(
            condition: Boolean,
            code: String,
        ) {
            if (!condition) throw PrivacyPayloadRefused(code)
        }
    }
}

object PrivacyEventLogger {
    /** Refused events are explicit errors containing only a fixed reason, never the original JSON. */
    fun log(
        resources: Resources,
        json: String,
    ): Boolean =
        try {
            PrivacyTrafficPolicy.fromResources(resources).requireLocalPayload(json)
            Log.i(LOG_TAG, json)
            true
        } catch (refused: PrivacyPayloadRefused) {
            Log.e(LOG_TAG, """{"event":"privacy_payload_refused","code":"${refused.code}"}""")
            false
        }
}
