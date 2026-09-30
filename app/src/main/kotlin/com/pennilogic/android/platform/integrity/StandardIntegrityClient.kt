package com.pennilogic.android.platform.integrity

/** Why no token could be obtained; mirrors the Play error classes without exposing raw messages. */
enum class IntegrityUnavailable(
    val id: String,
    /** Whether the same request may be retried later without user action. */
    val transient: Boolean,
) {
    PLAY_NOT_AVAILABLE("play_not_available", transient = false),
    NETWORK("network", transient = true),
    TOO_MANY_REQUESTS("too_many_requests", transient = true),
    PROVIDER_INVALID("provider_invalid", transient = true),
    CLIENT_ERROR("client_error", transient = false),
    UNKNOWN("unknown", transient = true),
}

/** Result of asking Play for a token bound to a request hash. */
sealed interface IntegrityTokenResult {
    data class Token(
        val token: String,
        val requestHash: String,
        val issuedAtMillis: Long,
    ) : IntegrityTokenResult {
        /** Never includes the token. */
        override fun toString(): String = "Token(requestHash=$requestHash, issuedAtMillis=$issuedAtMillis)"
    }

    data class Unavailable(
        val cause: IntegrityUnavailable,
    ) : IntegrityTokenResult
}

/**
 * Source of standard integrity tokens: the Play adapter in production, a fake in tests. Tests never
 * call Google services.
 */
fun interface StandardIntegrityTokenProvider {
    suspend fun requestToken(requestHash: String): IntegrityTokenResult
}

/** Why a bound token could not be attached to a request; every case fails closed. */
enum class TokenRejection(
    val id: String,
) {
    /** The request is not the one the token was obtained for. */
    WRONG_REQUEST("wrong_request"),

    /** The token was already attached once; a second use is a replay. */
    ALREADY_USED("already_used"),

    /** The token is older than the client time-to-live. */
    EXPIRED("expired"),
}

/** The header value that travels with the protected request. */
data class IntegrityAttachment(
    val headerName: String,
    val token: String,
    val requestHash: String,
) {
    /** Never includes the token, so an interceptor's or caller's log line cannot leak it. */
    override fun toString(): String = "IntegrityAttachment(headerName=$headerName, requestHash=$requestHash)"
}

/**
 * A token bound to exactly one [ProtectedRequest], attachable exactly once, within [ttlMillis] of
 * issue. The server performs the authoritative checks (package, hash, freshness, replay, verdicts);
 * this class makes the client incapable of the obvious misuses, so a replayed, stale or mis-bound
 * token never leaves the device.
 */
class BoundIntegrityToken(
    private val token: String,
    val requestHash: String,
    val issuedAtMillis: Long,
    private val ttlMillis: Long = DEFAULT_TTL_MILLIS,
) {
    init {
        require(token.isNotBlank()) { "token must not be blank" }
        require(ttlMillis > 0) { "ttlMillis must be positive" }
    }

    private var consumed = false

    val isConsumed: Boolean
        get() = consumed

    fun isExpired(nowMillis: Long): Boolean = nowMillis - issuedAtMillis > ttlMillis || nowMillis < issuedAtMillis

    /**
     * Attaches the token to [request] once. Returns the rejection instead of the attachment when the
     * request differs from the bound one, the token was already used or it has expired; a rejected
     * token stays unusable, so a retry must obtain a new one.
     */
    @Synchronized
    fun attachTo(
        request: ProtectedRequest,
        nowMillis: Long,
    ): Result<IntegrityAttachment> {
        if (consumed) return Result.failure(TokenRejectedException(TokenRejection.ALREADY_USED))
        if (IntegrityRequestBinding.requestHash(request) != requestHash) {
            consumed = true
            return Result.failure(TokenRejectedException(TokenRejection.WRONG_REQUEST))
        }
        if (isExpired(nowMillis)) {
            consumed = true
            return Result.failure(TokenRejectedException(TokenRejection.EXPIRED))
        }
        consumed = true
        return Result.success(IntegrityAttachment(HEADER_NAME, token, requestHash))
    }

    /** Never includes the token. */
    override fun toString(): String =
        "BoundIntegrityToken(requestHash=$requestHash, issuedAt=$issuedAtMillis, consumed=$consumed)"

    companion object {
        const val HEADER_NAME: String = "X-PenniLogic-Integrity"

        /** Well inside Play's own freshness window and short enough that a stalled request re-binds. */
        const val DEFAULT_TTL_MILLIS: Long = 5 * 60 * 1000
    }
}

class TokenRejectedException(
    val rejection: TokenRejection,
) : IllegalStateException("integrity token rejected: ${rejection.id}")

/**
 * Standard-request client: computes the binding for a request, asks the provider for a token bound
 * to it and hands back a single-use [BoundIntegrityToken]. When Play cannot provide a token the result
 * says so and the caller's operation policy (owned by `T-SEC-05`, PenniLogic/android#27) decides what
 * the action may do without a verdict; this client never fabricates a token or downgrades to an
 * unbound one. The verdict itself is verified server-side (PenniLogic/api#86).
 */
class StandardIntegrityClient(
    private val provider: StandardIntegrityTokenProvider,
    private val clock: () -> Long,
    private val ttlMillis: Long = BoundIntegrityToken.DEFAULT_TTL_MILLIS,
) {
    sealed interface Binding {
        data class Bound(
            val token: BoundIntegrityToken,
        ) : Binding

        data class Unavailable(
            val cause: IntegrityUnavailable,
        ) : Binding
    }

    suspend fun bind(request: ProtectedRequest): Binding {
        val requestHash = IntegrityRequestBinding.requestHash(request)
        return when (val result = provider.requestToken(requestHash)) {
            is IntegrityTokenResult.Unavailable -> {
                Binding.Unavailable(result.cause)
            }

            is IntegrityTokenResult.Token -> {
                // A provider answering with a different hash, or with no token, is unavailable, never trusted.
                if (result.requestHash != requestHash || result.token.isBlank()) {
                    return Binding.Unavailable(IntegrityUnavailable.PROVIDER_INVALID)
                }
                Binding.Bound(
                    BoundIntegrityToken(
                        result.token,
                        requestHash,
                        result.issuedAtMillis.takeIf { it > 0 } ?: clock(),
                        ttlMillis,
                    ),
                )
            }
        }
    }
}
