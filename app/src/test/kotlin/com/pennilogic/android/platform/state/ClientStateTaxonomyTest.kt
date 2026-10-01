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
import java.security.MessageDigest

/**
 * Binds actual native identifiers to the byte-pinned accepted Docs provider, then checks the
 * Android platform document against that same source. This is source adoption, not UI coverage.
 */
class ClientStateTaxonomyTest {
    private val document: JsonObject by lazy {
        JsonParser
            .parseString(
                RepositoryFiles.rootFile("docs/platform/capture-health-identifiers.json").readText(),
            ).asJsonObject
    }
    private val provider by lazy { AcceptedClientStateTaxonomy() }
    private val taxonomy: JsonObject
        get() = document.getAsJsonObject("taxonomy")

    @Test
    fun `taxonomy_first binds native states scopes and causes to the accepted provider`() {
        val published = provider.states.map { it.get("id").asString }
        provider.assertIdentifiers("client states", ClientState.entries.map { it.id }, published)
        assertEquals(published, ClientState.entries.map { it.id })
        assertEquals(published, taxonomy.getAsJsonArray("states").map { it.asString })
        assertEquals(provider.source.get("source_commit").asString, taxonomy.get("source_commit").asString)
        assertEquals(AcceptedClientStateTaxonomy.SOURCE_PATH, taxonomy.get("source_metadata").asString)
        assertEquals(provider.data.get("taxonomy_version").asString, ClientStateTaxonomy.VERSION)
        assertEquals(ClientStateTaxonomy.VERSION, taxonomy.get("version").asString)
        assertEquals(provider.signals.get("prefix").asString, ClientStateTaxonomy.SIGNAL_PREFIX)
        assertEquals(ClientStateTaxonomy.SIGNAL_PREFIX, taxonomy.get("signal_prefix").asString)
        val causes =
            provider.state(ClientState.PERMISSION_DENIED.id).getAsJsonArray("causes").map {
                it.asJsonObject.get("id").asString
            }
        provider.assertIdentifiers("permission causes", PermissionDeniedCause.entries.map { it.id }, causes)
        assertEquals(
            causes,
            PermissionDeniedCause.entries.map { it.id },
        )
        assertEquals(causes, taxonomy.getAsJsonArray("permission_denied_causes").map { it.asString })
        provider.assertIdentifiers(
            "state scopes",
            StateScope.entries.map {
                it.id
            },
            provider.data
                .getAsJsonObject("scopes")
                .keySet()
                .toList(),
        )
        assertEquals(
            provider.signalAttributes.map { it.get("id").asString },
            taxonomy.getAsJsonArray("signal_attributes").map { it.asString },
        )
        val scopes = taxonomy.getAsJsonObject("state_scopes")
        assertEquals(published.toSet(), scopes.keySet())
        for (state in ClientState.entries) {
            val pinned = provider.state(state.id).getAsJsonArray("scopes").map { it.asString }
            provider.assertIdentifiers("${state.id} scopes", state.applicableScopes.map { it.id }, pinned)
            provider.assertIdentifiers(
                "${state.id} document scopes",
                scopes.getAsJsonArray(state.id).map { it.asString },
                pinned,
            )
            assertTrue(state.id, state.applicableScopes.isNotEmpty())
        }
        assertEquals(setOf(StateScope.SURFACE), ClientState.DEGRADED.applicableScopes)
        assertEquals(setOf(StateScope.REGION, StateScope.ACTION), ClientState.QUOTA_EXCEEDED.applicableScopes)
    }

    @Test
    fun `every identifier is stable snake_case`() {
        val identifier = provider.identifierPattern
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
        val sourceConditions = provider.data.getAsJsonArray("contract_conditions").map { it.asJsonObject }
        provider.assertIdentifiers(
            "capture conditions",
            CaptureHealthCondition.entries.map { it.id },
            sourceConditions.map { it.get("id").asString },
            complete = false,
        )
        for (entry in published) {
            val condition = checkNotNull(CaptureHealthCondition.fromId(entry.get("id").asString))
            val accepted = sourceConditions.single { it.get("id").asString == condition.id }
            assertFalse("capture conditions are client-determined", accepted.get("contract_level").asBoolean)
            assertEquals(accepted.get("binds_to").asString, condition.bindsTo.id)
            assertEquals(entry.get("binds_to").asString, condition.bindsTo.id)
            val cause = entry.get("cause").takeUnless { it.isJsonNull }?.asString
            assertEquals(accepted.get("cause")?.asString, cause)
            assertEquals(cause, condition.cause?.id)
            assertEquals(
                provider
                    .state(condition.bindsTo.id)
                    .getAsJsonObject("recovery_action")
                    .get("id")
                    .asString,
                entry.get("recovery_action").asString,
            )
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
        val sourceReasons =
            provider.data
                .getAsJsonArray("contract_conditions")
                .flatMap { condition ->
                    condition.asJsonObject
                        .getAsJsonArray("reasons")
                        ?.map { reason ->
                            reason.asJsonObject to condition.asJsonObject.get("id").asString
                        }.orEmpty()
                }.filter { (reason, _) -> reason.get("client").asString == ClientStateTaxonomy.CLIENT }
        provider.assertIdentifiers(
            "platform reasons",
            PlatformCapabilityReason.entries.map { it.id },
            sourceReasons.map { (reason, _) -> reason.get("id").asString },
        )
        val statuses = setOf("implemented", "planned_pr2")
        for (entry in published) {
            val reason = checkNotNull(PlatformCapabilityReason.fromId(entry.get("id").asString))
            assertEquals(
                sourceReasons.single { (sourceReason, _) -> sourceReason.get("id").asString == reason.id }.second,
                reason.condition.id,
            )
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
        val publishedAttributes = provider.signalAttributes.map { it.get("id").asString }.toSet()
        val denied =
            ClientStateSignal(
                state = ClientState.PERMISSION_DENIED,
                surfaceId = "example_transactions_home",
                scope = StateScope.REGION,
                cause = PermissionDeniedCause.DEVICE,
                permission = "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
            )
        assertEquals("client_state.permission_denied", denied.name)
        provider.assertSignal(denied.name, denied.attributes())
        assertEquals(publishedAttributes, denied.attributes().keys)
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
        provider.assertSignal(degraded.name, degraded.attributes())
        assertEquals(
            publishedAttributes - setOf("cause", "permission"),
            degraded.attributes().keys,
        )
    }

    @Test
    fun `signals conform for every native state scope and permission cause`() {
        for (state in ClientState.entries) {
            val causes =
                if (state ==
                    ClientState.PERMISSION_DENIED
                ) {
                    listOf(null) + PermissionDeniedCause.entries
                } else {
                    listOf(null)
                }
            for (scope in state.applicableScopes) {
                for (cause in causes) {
                    for (recoveryActionTaken in listOf(false, true)) {
                        val signal =
                            ClientStateSignal(
                                state,
                                "synthetic_taxonomy_probe",
                                scope,
                                cause,
                                if (cause == PermissionDeniedCause.DEVICE) "android.permission.RECEIVE_SMS" else null,
                                recoveryActionTaken,
                            )
                        assertEquals(provider.state(state.id).get("signal").asString, signal.name)
                        provider.assertSignal(signal.name, signal.attributes())
                    }
                }
            }
        }
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
        // Per-state scope applicability is unchanged in v1.1.0: degraded is surface-only, quota_exceeded
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
    fun `implementation paths named by the document exist`() {
        val implementation = document.getAsJsonObject("implementation")
        val kotlinRoot = RepositoryFiles.app.resolve("src/main/kotlin/com/pennilogic/android")
        val paths = implementation.getAsJsonArray("code_paths").map { it.asString }
        assertTrue(paths.isNotEmpty())
        paths.forEach { assertTrue("missing $it", kotlinRoot.resolve(it).isFile) }
        assertTrue(RepositoryFiles.root.resolve(implementation.get("document").asString).isFile)
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

/** A test-only reader, not a renderer, surface registry or general-purpose schema validator. */
internal class AcceptedClientStateTaxonomy(
    sourceBytes: ByteArray = RepositoryFiles.rootFile(SOURCE_PATH).readBytes(),
    dataBytes: ByteArray = RepositoryFiles.rootFile("$DIRECTORY/client-state-taxonomy.json").readBytes(),
    schemaBytes: ByteArray = RepositoryFiles.rootFile("$DIRECTORY/client-state-taxonomy.schema.json").readBytes(),
    clientVersion: String = ClientStateTaxonomy.VERSION,
) {
    val source: JsonObject = verifiedObject("source metadata", sourceBytes, 1308, SOURCE_SHA256)
    val data: JsonObject = verifiedArtifact("data", dataBytes)
    val schema: JsonObject = verifiedArtifact("schema", schemaBytes)
    val states: List<JsonObject> = data.getAsJsonArray("states").map { it.asJsonObject }
    val signals: JsonObject = data.getAsJsonObject("signals")
    val signalAttributes: List<JsonObject> = signals.getAsJsonArray("attributes").map { it.asJsonObject }
    val identifierPattern: Regex =
        Regex(
            schema
                .getAsJsonObject("definitions")
                .getAsJsonObject("identifier")
                .get("pattern")
                .asString,
        )

    init {
        val version = data.get("taxonomy_version").asString
        require(clientVersion == version) { "Client taxonomy version does not match the accepted provider" }
        require(source.get("taxonomy_version").asString == version) { "Source metadata taxonomy version mismatch" }
        require(
            data
                .getAsJsonArray("version_history")
                .last()
                .asJsonObject
                .get("version")
                .asString == version,
        ) {
            "Published taxonomy version history mismatch"
        }
        val schemaVersion = data.get("schema_version").asInt
        require(
            source.get("provider_schema_version").asInt == schemaVersion,
        ) { "Source metadata schema version mismatch" }
        require(
            schema
                .getAsJsonObject("properties")
                .getAsJsonObject("schema_version")
                .get("const")
                .asInt == schemaVersion,
        ) {
            "Accepted provider schema version mismatch"
        }
    }

    fun state(id: String): JsonObject = states.single { it.get("id").asString == id }

    fun assertIdentifiers(
        kind: String,
        actual: List<String>,
        published: List<String>,
        complete: Boolean = true,
    ) {
        require(actual.all(identifierPattern::matches)) { "$kind contains malformed identifiers" }
        require(actual.size == actual.toSet().size) { "$kind contains duplicate identifiers" }
        require(published.size == published.toSet().size) { "Accepted provider contains duplicate $kind" }
        require(actual.all { it in published }) { "$kind contains identifiers absent from the accepted provider" }
        require(
            !complete || actual.toSet() == published.toSet(),
        ) { "$kind omits identifiers from the accepted provider" }
    }

    fun assertSignal(
        name: String,
        attributes: Map<String, String>,
    ) {
        val state =
            requireNotNull(
                states.singleOrNull {
                    it.get("signal").asString == name
                },
            ) { "Signal is absent from the accepted provider" }
        val allowed = signalAttributes.map { it.get("id").asString }.toSet()
        require(attributes.keys.all { it in allowed }) { "Signal carries unpublished attributes" }
        require(
            attributes.keys.containsAll(allowed - setOf("cause", "permission")),
        ) { "Signal omits required attributes" }
        for (attribute in signalAttributes) {
            val value = attributes[attribute.get("id").asString] ?: continue
            val values = attribute.getAsJsonArray("values") ?: continue
            require(
                value in values.map { it.asString },
            ) { "Signal attribute value is absent from the accepted provider" }
        }
        require(attributes["client"] == ClientStateTaxonomy.CLIENT) { "Signal must identify the Android client" }
        require(identifierPattern.matches(requireNotNull(attributes["surface_id"]))) {
            "Signal surface identifier is malformed"
        }
        require(
            attributes["scope"] in
                state.getAsJsonArray("scopes").map {
                    it.asString
                },
        ) { "Signal scope is not applicable to the state" }
        require(
            attributes["taxonomy_version"] == data.get("taxonomy_version").asString,
        ) { "Signal taxonomy version mismatch" }
        require("cause" !in attributes || state.get("id").asString == ClientState.PERMISSION_DENIED.id) {
            "Signal cause is only defined for permission_denied"
        }
        require("permission" !in attributes || attributes["cause"] == PermissionDeniedCause.DEVICE.id) {
            "Signal permission is only defined for the device cause"
        }
        require(
            "permission" !in attributes ||
                ClientStateSignal.PLATFORM_PERMISSION.matches(requireNotNull(attributes["permission"])),
        ) {
            "Signal permission must be a platform permission or role name"
        }
    }

    private fun verifiedArtifact(
        name: String,
        bytes: ByteArray,
    ): JsonObject {
        val pin = source.getAsJsonObject("artifacts").getAsJsonObject(name)
        val value = verifiedObject("provider $name", bytes, pin.get("byte_count").asInt, pin.get("sha256").asString)
        val gitBlob =
            MessageDigest
                .getInstance("SHA-1")
                .apply {
                    update("blob ${bytes.size}\u0000".toByteArray(Charsets.US_ASCII))
                    update(bytes)
                }.digest()
                .joinToString("") { "%02x".format(it) }
        require(gitBlob == pin.get("git_blob").asString) { "Provider $name Git blob mismatch" }
        return value
    }

    companion object {
        private const val DIRECTORY = "docs/platform/client-state-taxonomy"
        const val SOURCE_PATH: String = "$DIRECTORY/source.json"
        private const val SOURCE_SHA256 = "cfe9cb49620d7da7d305138da02667a81b45b070c8828f78c2d4822bd90c6191"

        private fun verifiedObject(
            name: String,
            bytes: ByteArray,
            byteCount: Int,
            sha256: String,
        ): JsonObject {
            require(bytes.size == byteCount) { "$name byte count mismatch" }
            val actual = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            require(actual == sha256) { "$name SHA-256 mismatch" }
            return JsonParser.parseString(bytes.toString(Charsets.UTF_8)).asJsonObject
        }
    }
}
