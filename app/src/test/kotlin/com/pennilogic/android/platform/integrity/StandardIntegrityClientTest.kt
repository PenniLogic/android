package com.pennilogic.android.platform.integrity

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.charset.StandardCharsets

private fun request(
    method: String = "POST",
    path: String = "/v1/transactions",
    body: String = "{\"amount_minor\":123400,\"currency\":\"INR\"}",
    accountScope: String = "acct_3f9c",
    nonce: String = "nonce-0123456789abcdef",
    issuedAt: Long = 1_700_000_000_000,
) = ProtectedRequest(
    method,
    path,
    ProtectedRequest.sha256Hex(body.toByteArray(StandardCharsets.UTF_8)),
    accountScope,
    nonce,
    issuedAt,
)

class IntegrityRequestBindingTest {
    @Test
    fun `the hash is deterministic, short and base64url`() {
        val hash = IntegrityRequestBinding.requestHash(request())
        assertEquals(hash, IntegrityRequestBinding.requestHash(request()))
        assertEquals(43, hash.length)
        assertTrue(hash, Regex("[A-Za-z0-9_-]+").matches(hash))
        assertTrue(hash.toByteArray(StandardCharsets.UTF_8).size <= IntegrityRequestBinding.MAX_REQUEST_HASH_BYTES)
    }

    @Test
    fun `any change to the request changes the hash`() {
        val base = IntegrityRequestBinding.requestHash(request())
        val variants =
            listOf(
                request(method = "PUT"),
                request(path = "/v1/transactions/1"),
                request(body = "{\"amount_minor\":123401,\"currency\":\"INR\"}"),
                request(body = "{\"amount_minor\":123400,\"currency\":\"USD\"}"),
                request(accountScope = "acct_other"),
                request(nonce = "nonce-fedcba9876543210"),
                request(issuedAt = 1_700_000_000_001),
            )
        variants.forEach { assertNotEquals("$it", base, IntegrityRequestBinding.requestHash(it)) }
        assertEquals(variants.size, variants.map { IntegrityRequestBinding.requestHash(it) }.toSet().size)
    }

    @Test
    fun `the binding covers the body through its digest and never repeats amounts`() {
        val canonical = request().canonical()
        assertFalse(canonical.contains("123400"))
        assertTrue(
            canonical.contains(
                ProtectedRequest.sha256Hex("{\"amount_minor\":123400,\"currency\":\"INR\"}".toByteArray()),
            ),
        )
        assertTrue(canonical.startsWith("pennilogic-integrity-v1\n"))
    }

    @Test
    fun `malformed requests are refused before any token is asked for`() {
        assertThrows(IllegalArgumentException::class.java) { request(method = "post") }
        assertThrows(IllegalArgumentException::class.java) { request(path = "v1/x") }
        assertThrows(IllegalArgumentException::class.java) { request(nonce = "short") }
        assertThrows(IllegalArgumentException::class.java) { request(accountScope = " ") }
        assertThrows(IllegalArgumentException::class.java) {
            ProtectedRequest("POST", "/x", "not-a-digest", "acct", "nonce-0123456789abcdef", 1)
        }
    }
}

class BoundIntegrityTokenTest {
    private val bound = request()
    private val hash = IntegrityRequestBinding.requestHash(bound)

    @Test
    fun `a token attaches once to the request it was bound to`() {
        val token = BoundIntegrityToken("tok", hash, issuedAtMillis = 1_000, ttlMillis = 10_000)
        val attachment = token.attachTo(bound, nowMillis = 2_000).getOrThrow()
        assertEquals(BoundIntegrityToken.HEADER_NAME, attachment.headerName)
        assertEquals("tok", attachment.token)
        assertEquals(hash, attachment.requestHash)
        assertTrue(token.isConsumed)
        val replay = token.attachTo(bound, nowMillis = 2_001)
        assertEquals(TokenRejection.ALREADY_USED, (replay.exceptionOrNull() as TokenRejectedException).rejection)
    }

    @Test
    fun `a token never attaches to a different request and is burnt by the attempt`() {
        val token = BoundIntegrityToken("tok", hash, 1_000, 10_000)
        val other = request(body = "{\"amount_minor\":1,\"currency\":\"INR\"}")
        val rejected = token.attachTo(other, nowMillis = 2_000)
        assertEquals(TokenRejection.WRONG_REQUEST, (rejected.exceptionOrNull() as TokenRejectedException).rejection)
        assertTrue("a mis-bound attempt burns the token", token.isConsumed)
        assertEquals(
            TokenRejection.ALREADY_USED,
            (token.attachTo(bound, 2_000).exceptionOrNull() as TokenRejectedException).rejection,
        )
    }

    @Test
    fun `a stale token or one from the future is refused`() {
        val token = BoundIntegrityToken("tok", hash, issuedAtMillis = 1_000, ttlMillis = 10_000)
        assertEquals(
            TokenRejection.EXPIRED,
            (token.attachTo(bound, nowMillis = 11_001).exceptionOrNull() as TokenRejectedException).rejection,
        )
        val fromFuture = BoundIntegrityToken("tok", hash, issuedAtMillis = 5_000, ttlMillis = 10_000)
        assertTrue(fromFuture.isExpired(nowMillis = 4_999))
        assertFalse(BoundIntegrityToken("tok", hash, 1_000, 10_000).isExpired(nowMillis = 11_000))
    }

    @Test
    fun `the token never appears in diagnostics`() {
        val token = BoundIntegrityToken("secret-token-value", hash, 1_000)
        assertFalse(token.toString().contains("secret-token-value"))
        assertThrows(IllegalArgumentException::class.java) { BoundIntegrityToken(" ", hash, 1_000) }
    }
}

class StandardIntegrityClientTest {
    private val now = 50_000L

    @Test
    fun `binding asks the provider for exactly the request hash and wraps the token`() =
        runTest {
            val asked = mutableListOf<String>()
            val provider =
                StandardIntegrityTokenProvider { hash ->
                    asked += hash
                    IntegrityTokenResult.Token("tok", hash, issuedAtMillis = 49_000)
                }
            val binding =
                StandardIntegrityClient(
                    provider,
                    { now },
                ).bind(request()) as StandardIntegrityClient.Binding.Bound
            assertEquals(listOf(IntegrityRequestBinding.requestHash(request())), asked)
            assertEquals(49_000, binding.token.issuedAtMillis)
            assertTrue(binding.token.attachTo(request(), now).isSuccess)
        }

    @Test
    fun `an unavailable provider yields no token and no fallback`() =
        runTest {
            val provider =
                StandardIntegrityTokenProvider {
                    IntegrityTokenResult.Unavailable(
                        IntegrityUnavailable.PLAY_NOT_AVAILABLE,
                    )
                }
            val binding = StandardIntegrityClient(provider, { now }).bind(request())
            assertEquals(StandardIntegrityClient.Binding.Unavailable(IntegrityUnavailable.PLAY_NOT_AVAILABLE), binding)
        }

    @Test
    fun `a provider answering for another hash is not trusted`() =
        runTest {
            val provider = StandardIntegrityTokenProvider { IntegrityTokenResult.Token("tok", "some-other-hash", now) }
            val binding = StandardIntegrityClient(provider, { now }).bind(request())
            assertEquals(StandardIntegrityClient.Binding.Unavailable(IntegrityUnavailable.PROVIDER_INVALID), binding)
        }

    @Test
    fun `a token without an issue time is stamped with the client clock`() =
        runTest {
            val provider =
                StandardIntegrityTokenProvider { hash ->
                    IntegrityTokenResult.Token("tok", hash, issuedAtMillis = 0)
                }
            val binding =
                StandardIntegrityClient(
                    provider,
                    { now },
                ).bind(request()) as StandardIntegrityClient.Binding.Bound
            assertEquals(now, binding.token.issuedAtMillis)
        }
}
