package com.pennilogic.android.platform.integrity

/**
 * The decoded verdict fields the decision table needs, in the shapes Play documents
 * (`requestDetails`, `appIntegrity`, `deviceIntegrity`, `accountDetails`). Decoding the token is the
 * server's job (Play's decryption endpoint or the local keys); this model exists so the reference
 * policy is executable in tests and so the client can interpret a server-relayed summary.
 */
data class DecodedVerdict(
    val requestPackageName: String,
    val requestHash: String,
    val timestampMillis: Long,
    /** `PLAY_RECOGNIZED`, `UNRECOGNIZED_VERSION` or `UNEVALUATED`. */
    val appRecognitionVerdict: String,
    /**
     * `MEETS_DEVICE_INTEGRITY`, `MEETS_BASIC_INTEGRITY`, `MEETS_STRONG_INTEGRITY`, `MEETS_VIRTUAL_INTEGRITY`;
     * empty = none.
     */
    val deviceRecognitionVerdict: Set<String>,
    /** `LICENSED`, `UNLICENSED` or `UNEVALUATED`. */
    val appLicensingVerdict: String,
    /** Opaque identity of the token (for example its SHA-256) used by the replay registry; never blank. */
    val tokenId: String,
) {
    init {
        require(tokenId.isNotBlank()) { "tokenId must identify the token" }
    }
}

/** What the server expected for this verification. */
data class VerdictExpectation(
    val packageName: String,
    val requestHash: String,
    val nowMillis: Long,
    /** Maximum age of the token; Play's own freshness protection is shorter, this is the server's ceiling. */
    val maxAgeMillis: Long = DEFAULT_MAX_AGE_MILLIS,
    /** Tolerated clock skew for a timestamp slightly in the future. */
    val clockSkewMillis: Long = DEFAULT_CLOCK_SKEW_MILLIS,
    /** Whether the action requires a Play licence (`LICENSED`); most protected actions do. */
    val requiresLicence: Boolean = true,
) {
    companion object {
        const val DEFAULT_MAX_AGE_MILLIS: Long = 10 * 60 * 1000
        const val DEFAULT_CLOCK_SKEW_MILLIS: Long = 60 * 1000
    }
}

/**
 * Remembers token identities so a second presentation is refused. [retentionMillis] must cover the
 * whole freshness window, `maxAge + clockSkew`, or a token could be forgotten while it is still fresh
 * and replayed; [IntegrityVerdictPolicy.evaluate] refuses a registry that does not.
 */
interface ReplayRegistry {
    /** How long an identity is remembered after it was recorded. */
    val retentionMillis: Long

    /** True when the token was recorded now; false when it was already present. */
    fun recordIfAbsent(
        tokenId: String,
        nowMillis: Long,
    ): Boolean
}

/** In-memory registry that forgets identities older than the retention window. */
class InMemoryReplayRegistry(
    override val retentionMillis: Long,
) : ReplayRegistry {
    init {
        require(retentionMillis > 0) { "retentionMillis must be positive" }
    }

    private val seen = LinkedHashMap<String, Long>()

    @Synchronized
    override fun recordIfAbsent(
        tokenId: String,
        nowMillis: Long,
    ): Boolean {
        seen.entries.removeAll { (_, recordedAt) -> nowMillis - recordedAt > retentionMillis }
        if (tokenId in seen) return false
        seen[tokenId] = nowMillis
        return true
    }

    companion object {
        /** A registry whose retention covers exactly the freshness window of [expectation]. */
        fun covering(expectation: VerdictExpectation): InMemoryReplayRegistry =
            InMemoryReplayRegistry(expectation.maxAgeMillis + expectation.clockSkewMillis)
    }
}

/** Why a verdict was refused; every value fails the protected action closed. */
enum class VerdictRejection(
    val id: String,
) {
    WRONG_PACKAGE("wrong_package"),
    WRONG_REQUEST("wrong_request"),
    EXPIRED("expired"),
    FUTURE_TIMESTAMP("future_timestamp"),
    REPLAYED("replayed"),
    APP_NOT_RECOGNIZED("app_not_recognized"),
    DEVICE_INTEGRITY_NOT_MET("device_integrity_not_met"),
    UNLICENSED("unlicensed"),
}

sealed interface IntegrityDecision {
    data object Allow : IntegrityDecision

    data class Deny(
        val rejection: VerdictRejection,
    ) : IntegrityDecision
}

/**
 * Reference decision table for verifying a standard integrity verdict. **The authoritative
 * implementation belongs to the server: PenniLogic/api#86** (server-side verdict verification —
 * nonce issuance, request binding, replay registry and fail-closed policy; blocked by android#57): the
 * server decrypts the token, applies exactly these checks in this order and stores the replay
 * registry. It is kept here, executable, so the fail-closed behaviour required by android#57 is
 * tested and so the client interprets a relayed summary the same way. Order: request details first
 * (package, hash, freshness, replay), then the verdicts; an `UNEVALUATED` verdict denies, because
 * Play sets verdicts to `UNEVALUATED` when its own replay protection triggers.
 *
 * Invariant the replay row depends on, enforced before any row is evaluated: the registry retains
 * identities for at least `maxAge + clockSkew`. A registry with shorter retention could forget a token
 * that is still inside the freshness window, and the same token would pass every row a second time;
 * such a registry is refused with an [IllegalArgumentException], so a misconfigured server can never
 * allow anything. The client nonce in the binding is not a replay defence — it only makes two
 * otherwise identical requests distinct; replay defence is this registry plus Play's own protection.
 */
object IntegrityVerdictPolicy {
    const val PLAY_RECOGNIZED: String = "PLAY_RECOGNIZED"
    const val MEETS_DEVICE_INTEGRITY: String = "MEETS_DEVICE_INTEGRITY"
    const val LICENSED: String = "LICENSED"

    fun evaluate(
        verdict: DecodedVerdict,
        expectation: VerdictExpectation,
        replays: ReplayRegistry,
    ): IntegrityDecision {
        require(replays.retentionMillis >= expectation.maxAgeMillis + expectation.clockSkewMillis) {
            "replay registry retention (${replays.retentionMillis} ms) must cover the freshness window " +
                "(${expectation.maxAgeMillis} + ${expectation.clockSkewMillis} ms)"
        }
        if (verdict.requestPackageName !=
            expectation.packageName
        ) {
            return IntegrityDecision.Deny(VerdictRejection.WRONG_PACKAGE)
        }
        if (verdict.requestHash !=
            expectation.requestHash
        ) {
            return IntegrityDecision.Deny(VerdictRejection.WRONG_REQUEST)
        }
        val age = expectation.nowMillis - verdict.timestampMillis
        if (age > expectation.maxAgeMillis) return IntegrityDecision.Deny(VerdictRejection.EXPIRED)
        if (age < -expectation.clockSkewMillis) return IntegrityDecision.Deny(VerdictRejection.FUTURE_TIMESTAMP)
        if (!replays.recordIfAbsent(
                verdict.tokenId,
                expectation.nowMillis,
            )
        ) {
            return IntegrityDecision.Deny(VerdictRejection.REPLAYED)
        }
        if (verdict.appRecognitionVerdict !=
            PLAY_RECOGNIZED
        ) {
            return IntegrityDecision.Deny(VerdictRejection.APP_NOT_RECOGNIZED)
        }
        if (MEETS_DEVICE_INTEGRITY !in verdict.deviceRecognitionVerdict) {
            return IntegrityDecision.Deny(VerdictRejection.DEVICE_INTEGRITY_NOT_MET)
        }
        if (expectation.requiresLicence &&
            verdict.appLicensingVerdict != LICENSED
        ) {
            return IntegrityDecision.Deny(VerdictRejection.UNLICENSED)
        }
        return IntegrityDecision.Allow
    }
}
