package com.pennilogic.android.platform.state

import com.google.gson.JsonParser
import com.pennilogic.android.testing.RepositoryFiles
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** In-memory refusal controls for the source binding; none is a registered or rendered surface. */
class ClientStateGateTest {
    @Test
    fun `accepted source records full immutable provenance and additive history`() {
        val provider = AcceptedClientStateTaxonomy()
        assertEquals("a700e639585c61a4610e7b99dbd02b2dab28bdcc", provider.source.get("source_commit").asString)
        assertEquals("3879d3893a3f289bd6b0d4474f09dbebf1fef195", provider.source.get("source_tree").asString)
        assertEquals(1394134442, provider.source.get("repository_id").asInt)
        assertEquals(335295566, provider.source.get("organization_id").asInt)
        assertEquals("1.1.0", provider.data.get("taxonomy_version").asString)
        val history = provider.data.getAsJsonArray("version_history").map { it.asJsonObject }
        assertEquals(listOf("1.0.0", "1.1.0"), history.map { it.get("version").asString })
        assertEquals(listOf("initial", "minor"), history.map { it.get("bump").asString })
    }

    @Test
    fun `altered provider data is refused even with the original byte count`() {
        val bytes = dataBytes()
        bytes[bytes.lastIndex - 1] = ' '.code.toByte()
        val failure =
            assertThrows(IllegalArgumentException::class.java) {
                AcceptedClientStateTaxonomy(dataBytes = bytes)
            }
        assertTrue(failure.message.orEmpty().contains("provider data SHA-256 mismatch"))
    }

    @Test
    fun `altered provider schema is refused even with the original byte count`() {
        val bytes = schemaBytes()
        bytes[bytes.lastIndex - 1] = ' '.code.toByte()
        val failure =
            assertThrows(IllegalArgumentException::class.java) {
                AcceptedClientStateTaxonomy(schemaBytes = bytes)
            }
        assertTrue(failure.message.orEmpty().contains("provider schema SHA-256 mismatch"))
    }

    @Test
    fun `source metadata cannot silently change identity hashes or version`() {
        val original = sourceBytes().toString(Charsets.UTF_8)
        val changes =
            listOf(
                "source_commit" to "0000000000000000000000000000000000000000",
                "taxonomy_version" to "1.0.0",
                "unpublished_field" to "synthetic",
            ).map { (key, value) ->
                JsonParser
                    .parseString(original)
                    .asJsonObject
                    .apply { addProperty(key, value) }
                    .toString()
                    .toByteArray()
            } +
                listOf(
                    original.replace("\"sha256\": \"040d", "\"sha256\": \"0000").toByteArray(),
                    original.replace("\"taxonomy_version\": \"1.1.0\",", "").toByteArray(),
                    original
                        .replace(
                            "\"taxonomy_version\": \"1.1.0\",",
                            "\"taxonomy_version\": \"1.1.0\", \"taxonomy_version\": \"1.0.0\",",
                        ).toByteArray(),
                    "{".toByteArray(),
                    byteArrayOf(0x80.toByte()),
                )
        for (bytes in changes) {
            assertThrows(IllegalArgumentException::class.java) {
                AcceptedClientStateTaxonomy(sourceBytes = bytes)
            }
        }
    }

    @Test
    fun `missing provider artifacts fail instead of falling back to Android lists`() {
        assertThrows(IllegalArgumentException::class.java) { AcceptedClientStateTaxonomy(dataBytes = byteArrayOf()) }
        assertThrows(IllegalArgumentException::class.java) { AcceptedClientStateTaxonomy(schemaBytes = byteArrayOf()) }
        assertThrows(IllegalArgumentException::class.java) { AcceptedClientStateTaxonomy(sourceBytes = byteArrayOf()) }
        assertThrows(IllegalStateException::class.java) {
            RepositoryFiles.rootFile("docs/platform/client-state-taxonomy/absent.json")
        }
    }

    @Test
    fun `code cannot keep a stale taxonomy version while adopting new provider bytes`() {
        val failure =
            assertThrows(IllegalArgumentException::class.java) {
                AcceptedClientStateTaxonomy(clientVersion = "1.0.0")
            }
        assertTrue(failure.message.orEmpty().contains("Client taxonomy version"))
    }

    @Test
    fun `taxonomy_first refuses unknown duplicate missing and malformed native states`() {
        val provider = AcceptedClientStateTaxonomy()
        val actual = ClientState.entries.map { it.id }
        val published = provider.states.map { it.get("id").asString }
        provider.assertIdentifiers("client states", actual, published)
        val plants =
            listOf(
                actual + "synthetic_unpublished_state",
                actual + actual.first(),
                actual.dropLast(1),
                actual.dropLast(1) + "Not a state",
            )
        for (plant in plants) {
            assertThrows(IllegalArgumentException::class.java) {
                provider.assertIdentifiers("client states", plant, published)
            }
        }
    }

    @Test
    fun `taxonomy_first also refuses changed cause and scope vocabularies`() {
        val provider = AcceptedClientStateTaxonomy()
        val publishedCauses =
            provider.state(ClientState.PERMISSION_DENIED.id).getAsJsonArray("causes").map {
                it.asJsonObject.get("id").asString
            }
        val publishedScopes =
            provider.data
                .getAsJsonObject("scopes")
                .keySet()
                .toList()
        for ((kind, values) in listOf("causes" to publishedCauses, "scopes" to publishedScopes)) {
            for (plant in listOf(
                values + "synthetic_unpublished",
                values + values.first(),
                values.dropLast(1),
                values + "",
            )) {
                assertThrows(IllegalArgumentException::class.java) {
                    provider.assertIdentifiers(kind, plant, values)
                }
            }
        }
    }

    @Test
    fun `signal contract refuses extra missing malformed and mismatched attributes`() {
        val provider = AcceptedClientStateTaxonomy()
        val signal =
            ClientStateSignal(
                ClientState.PERMISSION_DENIED,
                "synthetic_taxonomy_probe",
                StateScope.REGION,
                PermissionDeniedCause.DEVICE,
                "android.permission.RECEIVE_SMS",
            )
        val attributes = signal.attributes()
        val plants =
            listOf(
                attributes + ("reason" to "force_stopped"),
                attributes + ("device_id" to "SYNTHETIC_PRIVATE_VALUE"),
                attributes - "surface_id",
                attributes + ("surface_id" to "Not a surface"),
                attributes + ("client" to "web"),
                attributes + ("scope" to "synthetic_scope"),
                attributes + ("cause" to "synthetic_cause"),
                attributes + ("permission" to "SYNTHETIC_MESSAGE_CONTENT"),
                attributes + ("recovery_action_taken" to "sometimes"),
                attributes + ("taxonomy_version" to "1.0.0"),
            )
        for (plant in plants) {
            assertThrows(IllegalArgumentException::class.java) { provider.assertSignal(signal.name, plant) }
        }
        assertThrows(IllegalArgumentException::class.java) {
            provider.assertSignal("client_state.synthetic_unpublished_state", attributes)
        }
        val degraded = ClientStateSignal(ClientState.DEGRADED, "synthetic_taxonomy_probe", StateScope.SURFACE)
        assertThrows(IllegalArgumentException::class.java) {
            provider.assertSignal(degraded.name, degraded.attributes() + ("scope" to StateScope.REGION.id))
        }
        assertThrows(IllegalArgumentException::class.java) {
            provider.assertSignal(degraded.name, degraded.attributes() + ("cause" to PermissionDeniedCause.DEVICE.id))
        }
        assertThrows(IllegalArgumentException::class.java) {
            provider.assertSignal(
                degraded.name,
                degraded.attributes() + ("permission" to "android.permission.RECEIVE_SMS"),
            )
        }
    }

    @Test
    fun `published gate names do not imply registered product surface coverage`() {
        val provider = AcceptedClientStateTaxonomy()
        val gates =
            provider.data.getAsJsonObject("adoption").getAsJsonArray("coverage_assertions").map {
                it.asJsonObject.get("id").asString
            }
        assertTrue("taxonomy_first" in gates)
        assertTrue("client_state_coverage" in gates)
        println("client_state_coverage: not_exercised; no registered product surfaces or renderer observations")
    }

    private fun sourceBytes(): ByteArray = RepositoryFiles.rootFile(AcceptedClientStateTaxonomy.SOURCE_PATH).readBytes()

    private fun dataBytes(): ByteArray =
        RepositoryFiles.rootFile("docs/platform/client-state-taxonomy/client-state-taxonomy.json").readBytes()

    private fun schemaBytes(): ByteArray =
        RepositoryFiles.rootFile("docs/platform/client-state-taxonomy/client-state-taxonomy.schema.json").readBytes()
}
