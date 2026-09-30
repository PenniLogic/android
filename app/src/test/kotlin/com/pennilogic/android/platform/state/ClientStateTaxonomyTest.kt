package com.pennilogic.android.platform.state

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.pennilogic.android.platform.settings.SensitiveSetting
import com.pennilogic.android.testing.RepositoryFiles
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Keeps the code's identifiers identical to the published document
 * `docs/platform/capture-health-identifiers.json` and to the pinned taxonomy: every condition and
 * reason binds to exactly one of the eight states, the client adds no state of its own
 * (`taxonomy_first`), and signals carry only the published attributes.
 */
class ClientStateTaxonomyTest {
    private val document: JsonObject by lazy {
        JsonParser
            .parseReader(
                RepositoryFiles.rootFile("docs/platform/capture-health-identifiers.json").reader(),
            ).asJsonObject
    }
    private val taxonomy: JsonObject
        get() = document.getAsJsonObject("taxonomy")

    @Test
    fun `client states are exactly the eight published identifiers with their published scopes`() {
        val published = taxonomy.getAsJsonArray("states").map { it.asString }
        assertEquals(published, ClientState.entries.map { it.id })
        assertEquals(ClientStateTaxonomy.VERSION, taxonomy.get("version").asString)
        assertEquals(ClientStateTaxonomy.SIGNAL_PREFIX, taxonomy.get("signal_prefix").asString)
        assertEquals(
            taxonomy.getAsJsonArray("permission_denied_causes").map {
                it.asString
            },
            PermissionDeniedCause.entries.map { it.id },
        )
        val scopes = taxonomy.getAsJsonObject("state_scopes")
        assertEquals(published.toSet(), scopes.keySet())
        for (state in ClientState.entries) {
            val pinned = scopes.getAsJsonArray(state.id).map { checkNotNull(StateScope.fromId(it.asString)) }.toSet()
            assertEquals(state.id, pinned, state.applicableScopes)
            assertTrue(state.id, state.applicableScopes.isNotEmpty())
        }
        assertEquals(setOf(StateScope.SURFACE), ClientState.DEGRADED.applicableScopes)
        assertEquals(setOf(StateScope.REGION, StateScope.ACTION), ClientState.QUOTA_EXCEEDED.applicableScopes)
    }

    @Test
    fun `every identifier is stable snake_case`() {
        val identifier = Regex("[a-z][a-z0-9_]*")
        val all =
            ClientState.entries.map { it.id } +
                PermissionDeniedCause.entries.map { it.id } +
                CaptureHealthCondition.entries.map { it.id } +
                PlatformCapabilityReason.entries.map { it.id }
        assertEquals(all.size, all.toSet().size)
        all.forEach { assertTrue("$it is not snake_case", identifier.matches(it)) }
    }

    @Test
    fun `conditions bind to exactly one state and match the document`() {
        val published = document.getAsJsonArray("conditions").map { it.asJsonObject }
        assertEquals(published.map { it.get("id").asString }, CaptureHealthCondition.entries.map { it.id })
        for (entry in published) {
            val condition = checkNotNull(CaptureHealthCondition.fromId(entry.get("id").asString))
            assertEquals(entry.get("binds_to").asString, condition.bindsTo.id)
            val cause = entry.get("cause").takeUnless { it.isJsonNull }?.asString
            assertEquals(cause, condition.cause?.id)
            if (condition.bindsTo == ClientState.PERMISSION_DENIED) {
                assertEquals(
                    "permission_denied conditions carry the device cause",
                    PermissionDeniedCause.DEVICE,
                    condition.cause,
                )
            } else {
                assertNull("only permission_denied has a cause", condition.cause)
            }
        }
    }

    @Test
    fun `reasons roll up to exactly one condition, match the document and carry a status`() {
        val published = document.getAsJsonArray("reasons").map { it.asJsonObject }
        assertEquals(published.map { it.get("id").asString }, PlatformCapabilityReason.entries.map { it.id })
        val statuses = setOf("implemented", "planned_pr2")
        for (entry in published) {
            val reason = checkNotNull(PlatformCapabilityReason.fromId(entry.get("id").asString))
            assertEquals(entry.get("condition").asString, reason.condition.id)
            assertEquals(reason.condition.bindsTo, reason.state)
            for (field in listOf("description", "detection", "clears_when")) {
                assertTrue("${reason.id} lacks $field", entry.get(field).asString.isNotBlank())
            }
            assertTrue("${reason.id} status", entry.get("status").asString in statuses)
            if (entry.get("status").asString == "planned_pr2") {
                assertTrue(
                    "${reason.id} detection must say it is planned",
                    entry.get("detection").asString.startsWith("Planned"),
                )
            }
        }
        assertTrue(
            published
                .single {
                    it.get("id").asString == "private_space_paused"
                }.get("privacy")
                .asString
                .contains("never joined"),
        )
        val pausedReasons =
            PlatformCapabilityReason.entries.filter {
                it.condition ==
                    CaptureHealthCondition.CAPTURE_PAUSED_BY_PLATFORM
            }
        assertTrue(PlatformCapabilityReason.FORCE_STOPPED in pausedReasons)
        assertTrue(PlatformCapabilityReason.PRIVATE_SPACE_PAUSED in pausedReasons)
        pausedReasons.forEach { assertEquals(ClientState.DEGRADED, it.state) }
        PlatformCapabilityReason.entries
            .filter { it.condition == CaptureHealthCondition.CAPTURE_BLOCKED_BY_SETTING }
            .forEach { assertEquals(ClientState.PERMISSION_DENIED, it.state) }
    }

    @Test
    fun `capture health values bind to the published conditions`() {
        val values = document.getAsJsonObject("capture_health").getAsJsonArray("values").map { it.asJsonObject }
        assertEquals(listOf("healthy", "paused", "blocked"), values.map { it.get("id").asString })
        assertTrue(values.single { it.get("id").asString == "healthy" }.get("condition").isJsonNull)
        assertEquals(
            "capture_paused_by_platform",
            values
                .single {
                    it.get("id").asString == "paused"
                }.get("condition")
                .asString,
        )
        assertEquals(
            "capture_blocked_by_setting",
            values
                .single {
                    it.get("id").asString == "blocked"
                }.get("condition")
                .asString,
        )
    }

    @Test
    fun `signal carries only the published attributes`() {
        val publishedAttributes = taxonomy.getAsJsonArray("signal_attributes").map { it.asString }.toSet()
        val denied =
            ClientStateSignal(
                state = ClientState.PERMISSION_DENIED,
                surfaceId = "example_transactions_home",
                scope = StateScope.REGION,
                cause = PermissionDeniedCause.DEVICE,
                permission = "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
            )
        assertEquals("client_state.permission_denied", denied.name)
        assertTrue(denied.attributes().keys.all { it in publishedAttributes })
        assertEquals("android", denied.attributes()["client"])
        assertEquals("device", denied.attributes()["cause"])
        assertEquals("false", denied.attributes()["recovery_action_taken"])
        assertEquals(ClientStateTaxonomy.VERSION, denied.attributes()["taxonomy_version"])

        val degraded =
            ClientStateSignal(
                ClientState.DEGRADED,
                "example_transactions_home",
                StateScope.SURFACE,
                recoveryActionTaken = true,
            )
        assertEquals("client_state.degraded", degraded.name)
        assertEquals(
            setOf("client", "surface_id", "scope", "recovery_action_taken", "taxonomy_version"),
            degraded.attributes().keys,
        )
    }

    @Test
    fun `signal rejects attributes the taxonomy does not define for the state`() {
        assertThrows(IllegalArgumentException::class.java) {
            ClientStateSignal(
                ClientState.DEGRADED,
                "example_home",
                StateScope.SURFACE,
                cause = PermissionDeniedCause.DEVICE,
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            ClientStateSignal(
                ClientState.PERMISSION_DENIED,
                "example_home",
                StateScope.REGION,
                cause = PermissionDeniedCause.PLAN,
                permission = "x",
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            ClientStateSignal(ClientState.EMPTY, "Not Snake", StateScope.SURFACE)
        }
        // Per-state scope applicability (taxonomy v1.0.0): degraded is surface-only, quota_exceeded
        // never surface, empty and stale never action.
        assertThrows(IllegalArgumentException::class.java) {
            ClientStateSignal(ClientState.DEGRADED, "example_home", StateScope.REGION)
        }
        assertThrows(IllegalArgumentException::class.java) {
            ClientStateSignal(ClientState.QUOTA_EXCEEDED, "example_home", StateScope.SURFACE)
        }
        assertThrows(IllegalArgumentException::class.java) {
            ClientStateSignal(ClientState.EMPTY, "example_home", StateScope.ACTION)
        }
        assertThrows(IllegalArgumentException::class.java) {
            ClientStateSignal(ClientState.STALE, "example_home", StateScope.ACTION)
        }
        ClientStateSignal(ClientState.QUOTA_EXCEEDED, "example_home", StateScope.ACTION)
        // The permission attribute is the platform's name or nothing: no message content, no free text.
        val pattern = Regex(taxonomy.get("permission_attribute_pattern").asString)
        val bad =
            listOf(
                "Your OTP is 123456",
                "sms",
                "android.permission.",
                "com.example.PERMISSION",
                "android.permission.receive_sms",
            )
        for (value in bad) {
            assertFalse(value, pattern.matches(value))
            assertThrows(value, IllegalArgumentException::class.java) {
                ClientStateSignal(
                    ClientState.PERMISSION_DENIED,
                    "example_home",
                    StateScope.REGION,
                    PermissionDeniedCause.DEVICE,
                    value,
                )
            }
        }
        val good =
            listOf(
                "android.permission.RECEIVE_SMS",
                "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
                "android.app.role.SMS",
            )
        for (value in good) {
            assertTrue(value, pattern.matches(value))
            ClientStateSignal(
                ClientState.PERMISSION_DENIED,
                "example_home",
                StateScope.REGION,
                PermissionDeniedCause.DEVICE,
                value,
            )
        }
        SensitiveSetting.entries.forEach { assertTrue(it.platformName, pattern.matches(it.platformName)) }
    }

    @Test
    fun `observability event never records identifying or financial content and names its sink`() {
        val observability = document.getAsJsonObject("observability")
        val attributes = observability.getAsJsonArray("attributes").map { it.asString }
        assertTrue(attributes.containsAll(listOf("api_level", "stop_reason", "standby_bucket", "capture_health")))
        val forbidden = listOf("device_id", "android_id", "amount", "balance", "message", "imei", "account_id")
        forbidden.forEach { term ->
            assertTrue("$term must not be an attribute", attributes.none { it.contains(term) })
        }
        assertTrue(observability.getAsJsonArray("never_recorded").size() >= 3)
        assertTrue(observability.get("sink").asString.contains("no telemetry export"))
        assertTrue(observability.get("retention").asString.isNotBlank())
    }
}
