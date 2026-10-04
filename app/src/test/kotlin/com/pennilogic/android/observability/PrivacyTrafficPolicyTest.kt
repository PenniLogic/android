package com.pennilogic.android.observability

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.pennilogic.android.PenniLogicApplication
import com.pennilogic.android.R
import com.pennilogic.android.config.AppConfiguration
import com.pennilogic.android.config.ConfigurationProblem
import com.pennilogic.android.config.ConfigurationResult
import com.pennilogic.android.config.Environment
import com.pennilogic.android.platform.capture.CaptureHealth
import com.pennilogic.android.platform.capture.CaptureHealthEvent
import com.pennilogic.android.platform.scheduling.StandbyBucket
import com.pennilogic.android.platform.scheduling.StopReason
import com.pennilogic.android.platform.state.PlatformCapabilityReason
import com.pennilogic.android.testing.RepositoryFiles
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowLog
import java.security.MessageDigest

@RunWith(RobolectricTestRunner::class)
class PrivacyTrafficPolicyTest {
    private val context: Context
        get() = ApplicationProvider.getApplicationContext()

    private fun startup(): String =
        StartupEvent
            .forProcessStart(
                "debug",
                "0.1.0-debug",
                1,
                ConfigurationResult.Loaded(
                    AppConfiguration(Environment.DEVELOPMENT, "https://api.dev.pennilogic.invalid", "local"),
                ),
            ).toJson()

    private fun source(): JSONObject =
        JSONObject(RepositoryFiles.appFile("src/main/res/raw/privacy_traffic_policy.json").readText())

    private fun networkFixture(): JSONObject =
        JSONObject(
            RepositoryFiles
                .rootFile("scripts/tests/fixtures/privacy_traffic/synthetic-network.json")
                .readText(),
        )

    private fun syntheticPolicy(): PrivacyTrafficPolicy {
        val policy = source()
        val fixture = networkFixture()
        policy.put("destinations", fixture.getJSONArray("destinations"))
        policy.put("network_schemas", fixture.getJSONObject("network_schemas"))
        policy.put("diagnostic_hosts", fixture.getJSONArray("diagnostic_hosts"))
        for (variant in listOf("debug", "release")) {
            val original = policy.getJSONObject("components").getJSONArray(variant)
            val entries = (0 until original.length()).map { original.getString(it) } + fixture.getString("component")
            policy.getJSONObject("components").put(variant, JSONArray(entries.sorted()))
        }
        return PrivacyTrafficPolicy.fromBytes(policy.toString().toByteArray())
    }

    private fun probe(): JSONObject =
        JSONObject("""{"event":"synthetic_probe","stage":"analytics","outcome":"ok","synthetic":true}""")

    @Test
    fun `packaged policy is byte-identical to the harness source`() {
        val bytes = context.resources.openRawResource(R.raw.privacy_traffic_policy).use { it.readBytes() }
        assertTrue(
            bytes.contentEquals(RepositoryFiles.appFile("src/main/res/raw/privacy_traffic_policy.json").readBytes()),
        )
        val sha = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        assertEquals(sha, PrivacyTrafficPolicy.fromResources(context.resources).sha256)
    }

    @Test
    fun `actual startup producer and invalid configuration conform to shared schema`() {
        val policy = PrivacyTrafficPolicy.fromResources(context.resources)
        policy.requireLocalPayload(startup())
        policy.requireLocalPayload(
            StartupEvent
                .forProcessStart(
                    "release",
                    "0.1.0",
                    1,
                    ConfigurationResult.Invalid(
                        listOf(
                            ConfigurationProblem("PENNILOGIC_ENVIRONMENT", ConfigurationProblem.Kind.MISSING),
                            ConfigurationProblem("PENNILOGIC_API_BASE_URL", ConfigurationProblem.Kind.INVALID),
                        ),
                    ),
                ).toJson(),
        )
    }

    @Test
    fun `every actual capture-health stop and bucket identifier conforms`() {
        val policy = PrivacyTrafficPolicy.fromResources(context.resources)
        for (stop in StopReason.entries) {
            for (bucket in StandbyBucket.entries) {
                policy.requireLocalPayload(CaptureHealthEvent(36, CaptureHealth.Healthy, stop, bucket, -1).toJson())
            }
        }
        for (reason in PlatformCapabilityReason.entries) {
            val health =
                if (reason in
                    listOf(
                        PlatformCapabilityReason.FORCE_STOPPED,
                        PlatformCapabilityReason.PRIVATE_SPACE_PAUSED,
                        PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED,
                        PlatformCapabilityReason.BACKGROUND_RESTRICTED,
                    )
                ) {
                    CaptureHealth.Paused(reason, 1)
                } else {
                    CaptureHealth.Blocked(reason, "android.permission.RECEIVE_SMS")
                }
            policy.requireLocalPayload(CaptureHealthEvent(36, health).toJson())
        }
    }

    @Test
    fun `real application startup uses the shared policy logger`() {
        val application: PenniLogicApplication = ApplicationProvider.getApplicationContext()
        ShadowLog.clear()
        application.onCreate()
        val event = ShadowLog.getLogsForTag(LOG_TAG).last { it.msg.contains("\"event\":\"app_start\"") }
        PrivacyTrafficPolicy.fromResources(context.resources).requireLocalPayload(event.msg)
        assertFalse(event.msg.contains("api.dev.pennilogic.invalid"))
    }

    @Test
    fun `refused local event logs an explicit code without the original payload`() {
        ShadowLog.clear()
        val payload = JSONObject(startup()).put("version_name", "SYNTHETIC_RAW_MESSAGE_DO_NOT_EGRESS_ANDROID_16")
        assertFalse(PrivacyEventLogger.log(context.resources, payload.toString()))
        val event = ShadowLog.getLogsForTag(LOG_TAG).last()
        assertEquals("""{"event":"privacy_payload_refused","code":"payload_value_refused"}""", event.msg)
        PrivacyTrafficPolicy.fromResources(context.resources).requireLocalPayload(event.msg)
    }

    @Test
    fun `analytics route cannot masquerade as api or local logging`() {
        for (category in listOf("api", "local_log")) {
            val policy = source()
            val fixture = networkFixture()
            fixture.getJSONObject("network_schemas").getJSONObject("synthetic_probe").put("category", category)
            policy.put("destinations", fixture.getJSONArray("destinations"))
            policy.put("network_schemas", fixture.getJSONObject("network_schemas"))
            for (variant in listOf("debug", "release")) {
                policy.getJSONObject("components").getJSONArray(variant).put(fixture.getString("component"))
            }
            assertThrows(
                PrivacyPayloadRefused::class.java,
            ) { PrivacyTrafficPolicy.fromBytes(policy.toString().toByteArray()) }
        }
    }

    @Test
    fun `changing shared schema without changing actual producer fails`() {
        val policy = source()
        policy
            .getJSONObject("local_events")
            .getJSONObject("app_start")
            .getJSONObject("fields")
            .remove("version_code")
        val drifted = PrivacyTrafficPolicy.fromBytes(policy.toString().toByteArray())
        val refusal = assertThrows(PrivacyPayloadRefused::class.java) { drifted.requireLocalPayload(startup()) }
        assertEquals("undeclared_payload_field", refusal.code)
    }

    @Test
    fun `scaffold publishes no analytics network or journey implementation`() {
        val document = source()
        assertEquals(0, document.getJSONArray("destinations").length())
        assertEquals(0, document.getJSONArray("diagnostic_hosts").length())
        assertEquals(0, document.getJSONObject("network_schemas").length())
        assertEquals(0, document.getJSONObject("journey_providers").length())
        val refusal =
            assertThrows(PrivacyPayloadRefused::class.java) {
                PrivacyTrafficPolicy
                    .fromResources(context.resources)
                    .requireNetworkPayload("analytics.synthetic.invalid", "/probe/analytics", probe().toString())
            }
        assertEquals("undeclared_route", refusal.code)
    }

    @Test
    fun `DNS grammar cannot authorize retained host metadata`() {
        val policy = syntheticPolicy()
        assertEquals("unlisted.synthetic.invalid", policy.metadataHost("UNLISTED.SYNTHETIC.INVALID"))
        assertEquals("analytics.synthetic.invalid", policy.metadataHost("analytics.synthetic.invalid"))
        assertNull(policy.metadataHost("private-synthetic-identifier.synthetic.invalid"))
        assertNull(policy.metadataHost("https://unlisted.synthetic.invalid/private"))
        assertNull(policy.metadataHost("unlisted%2esynthetic.invalid"))
        assertNull(PrivacyTrafficPolicy.fromResources(context.resources).metadataHost("unlisted.synthetic.invalid"))
        val digest =
            MessageDigest
                .getInstance("SHA-256")
                .digest("SYNTHETIC_RAW_MESSAGE_DO_NOT_EGRESS_ANDROID_16".toByteArray())
                .joinToString("") { "%02x".format(it) }
        val host = "${digest.take(32)}.${digest.drop(32)}.synthetic.invalid"
        assertTrue("raw-derived host metadata must be withheld", policy.metadataHost(host) == null)
    }

    @Test
    fun `diagnostic metadata policy cannot admit URLs or aliases and never grants a network route`() {
        val policy = syntheticPolicy()
        val refused =
            assertThrows(PrivacyPayloadRefused::class.java) {
                policy.requireNetworkPayload("unlisted.synthetic.invalid", "/probe/analytics", probe().toString())
            }
        assertEquals("undeclared_route", refused.code)
        for (host in listOf(
            "https://synthetic.invalid/private",
            "synthetic@synthetic.invalid",
            "*.synthetic.invalid",
        )) {
            val malformed = source().put("diagnostic_hosts", JSONArray(listOf(host)))
            assertThrows(PrivacyPayloadRefused::class.java) {
                PrivacyTrafficPolicy.fromBytes(malformed.toString().toByteArray())
            }
        }
    }

    @Test
    fun `native consumer uses the same synthetic schema fixture as actual proxy tests`() {
        syntheticPolicy().requireNetworkPayload("analytics.synthetic.invalid", "/probe/analytics", probe().toString())
        val unknown =
            assertThrows(PrivacyPayloadRefused::class.java) {
                syntheticPolicy().requireNetworkPayload(
                    "unlisted.synthetic.invalid",
                    "/probe/analytics",
                    probe().toString(),
                )
            }
        assertEquals("undeclared_route", unknown.code)
    }

    @Test
    fun `monetary field and value fail native analytics boundary`() {
        for (payload in listOf(
            probe().put("amount_minor", 12345).put("currency", "USD"),
            probe().put("outcome", 12345),
        )) {
            val refusal =
                assertThrows(PrivacyPayloadRefused::class.java) {
                    syntheticPolicy().requireNetworkPayload(
                        "analytics.synthetic.invalid",
                        "/probe/analytics",
                        payload.toString(),
                    )
                }
            assertEquals("monetary_analytics", refusal.code)
        }
    }

    @Test
    fun `blind index and raw-derived digest fields never cross native boundary`() {
        for (name in listOf("merchant_bidx", "raw_event_digest", "raw_hash", "message_digest")) {
            val payload = probe().put(name, "SYNTHETIC_DEVICE_ONLY_VALUE")
            val refusal =
                assertThrows(PrivacyPayloadRefused::class.java) {
                    syntheticPolicy().requireNetworkPayload(
                        "analytics.synthetic.invalid",
                        "/probe/analytics",
                        payload.toString(),
                    )
                }
            assertEquals("device_only_artifact_egress", refusal.code)
            assertFalse(refusal.message.orEmpty().contains("SYNTHETIC_DEVICE_ONLY_VALUE"))
        }
    }

    @Test
    fun `opaque source-event identifier requires a real origin provider not only a shape`() {
        val refusal =
            assertThrows(PrivacyPayloadRefused::class.java) {
                syntheticPolicy().requireNetworkPayload(
                    "analytics.synthetic.invalid",
                    "/probe/analytics",
                    probe().put("source_event_id", "synthetic_opaque_id").toString(),
                )
            }
        assertEquals("source_event_origin_unverified", refusal.code)
    }

    @Test
    fun `duplicate malformed and oversized JSON fails closed`() {
        val policy = PrivacyTrafficPolicy.fromResources(context.resources)
        for (json in listOf("""{"event":"app_start","event":"capture_health"}""", "{", " ".repeat(262145))) {
            assertThrows(PrivacyPayloadRefused::class.java) { policy.requireLocalPayload(json) }
        }
    }

    @Test
    fun `unsafe analytics source-schema additions are refused`() {
        for (name in listOf("amount_minor", "merchant_bidx", "raw_hash", "source_event_id")) {
            val policy = source()
            val fixture = networkFixture()
            policy.put("network_schemas", fixture.getJSONObject("network_schemas"))
            policy
                .getJSONObject("network_schemas")
                .getJSONObject("synthetic_probe")
                .getJSONObject("fields")
                .put(name, JSONObject("""{"kind":"boolean","nullable":false}"""))
            assertThrows(
                PrivacyPayloadRefused::class.java,
            ) { PrivacyTrafficPolicy.fromBytes(policy.toString().toByteArray()) }
        }
    }

    @Test
    fun `required journey and capture budgets cannot be lowered or enlarged`() {
        val policy = source()
        policy.put("required_journeys", JSONArray(listOf("analytics")))
        assertThrows(
            PrivacyPayloadRefused::class.java,
        ) { PrivacyTrafficPolicy.fromBytes(policy.toString().toByteArray()) }
        val budget = source()
        budget.getJSONObject("limits").put("max_body_bytes", 65537)
        assertThrows(
            PrivacyPayloadRefused::class.java,
        ) { PrivacyTrafficPolicy.fromBytes(budget.toString().toByteArray()) }
    }
}
