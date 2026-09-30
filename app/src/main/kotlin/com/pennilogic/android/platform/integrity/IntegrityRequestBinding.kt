package com.pennilogic.android.platform.integrity

import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Base64

/**
 * The server request a standard Play Integrity token is bound to. Every field that changes the
 * meaning of the request is part of the binding, so a token obtained for one request cannot be
 * attached to another: method, path, the SHA-256 of the exact body bytes, an opaque account scope
 * (a server-issued identifier, never an email or name), a client nonce that makes two otherwise
 * identical requests distinct, and the issue time. Amounts and currencies travel inside the body and
 * are covered by its digest; they are never repeated here.
 */
data class ProtectedRequest(
    val method: String,
    val path: String,
    val bodySha256Hex: String,
    val accountScope: String,
    val clientNonce: String,
    val issuedAtMillis: Long,
) {
    init {
        require(method.isNotBlank() && method == method.uppercase()) { "method must be an upper-case HTTP method" }
        require(path.startsWith("/")) { "path must be absolute" }
        require(SHA256_HEX.matches(bodySha256Hex)) { "bodySha256Hex must be 64 lower-case hex characters" }
        require(accountScope.isNotBlank()) { "accountScope is required" }
        require(clientNonce.length >= MIN_NONCE_LENGTH) { "clientNonce must be at least $MIN_NONCE_LENGTH characters" }
        require(issuedAtMillis > 0) { "issuedAtMillis must be positive" }
    }

    /** Stable canonical serialization; a change in any field changes the hash. */
    fun canonical(): String =
        listOf(
            "pennilogic-integrity-v1",
            method,
            path,
            bodySha256Hex,
            accountScope,
            clientNonce,
            issuedAtMillis.toString(),
        ).joinToString("\n")

    companion object {
        const val MIN_NONCE_LENGTH: Int = 16
        private val SHA256_HEX = Regex("[0-9a-f]{64}")

        /** Hex SHA-256 of the exact request body bytes. */
        fun sha256Hex(body: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(body).joinToString("") { "%02x".format(it) }
    }
}

/**
 * Computes the `requestHash` sent with a standard request. Play returns it verbatim inside the signed
 * verdict (`requestDetails.requestHash`), and the server recomputes it from the request it actually
 * received; a mismatch means the token was obtained for a different request. The value is the
 * base64url SHA-256 of the canonical serialization: 43 characters, far below Play's 500-byte limit,
 * so long bodies never inflate the token.
 */
object IntegrityRequestBinding {
    const val MAX_REQUEST_HASH_BYTES: Int = 500

    fun requestHash(request: ProtectedRequest): String {
        val digest =
            MessageDigest
                .getInstance(
                    "SHA-256",
                ).digest(request.canonical().toByteArray(StandardCharsets.UTF_8))
        val encoded = Base64.getUrlEncoder().withoutPadding().encodeToString(digest)
        check(encoded.toByteArray(StandardCharsets.UTF_8).size <= MAX_REQUEST_HASH_BYTES)
        return encoded
    }
}
