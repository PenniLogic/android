package com.pennilogic.android.platform.integrity

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The fail-closed decision table the api repository implements server-side: replayed, expired,
 * wrong-app, wrong-request, unrecognised, basic-only and unlicensed verdicts all deny; only a fresh,
 * correctly bound, Play-recognised, device-integral, licensed verdict allows.
 */
class IntegrityVerdictPolicyTest {
    private val now = 1_700_000_100_000
    private val expectation =
        VerdictExpectation(packageName = "com.pennilogic.android", requestHash = "hash-1", nowMillis = now)

    private fun verdict(
        packageName: String = "com.pennilogic.android",
        requestHash: String = "hash-1",
        timestampMillis: Long = now - 5_000,
        appRecognition: String = IntegrityVerdictPolicy.PLAY_RECOGNIZED,
        device: Set<String> = setOf(IntegrityVerdictPolicy.MEETS_DEVICE_INTEGRITY, "MEETS_BASIC_INTEGRITY"),
        licensing: String = IntegrityVerdictPolicy.LICENSED,
        tokenId: String = "token-1",
    ) = DecodedVerdict(packageName, requestHash, timestampMillis, appRecognition, device, licensing, tokenId)

    private fun evaluate(
        verdict: DecodedVerdict,
        expectation: VerdictExpectation = this.expectation,
        registry: ReplayRegistry = InMemoryReplayRegistry(retentionMillis = 60 * 60 * 1000),
    ) = IntegrityVerdictPolicy.evaluate(verdict, expectation, registry)

    @Test
    fun `a fresh bound recognised licensed verdict is allowed`() {
        assertEquals(IntegrityDecision.Allow, evaluate(verdict()))
    }

    @Test
    fun `wrong app and wrong request fail closed before anything else is looked at`() {
        assertEquals(
            IntegrityDecision.Deny(VerdictRejection.WRONG_PACKAGE),
            evaluate(verdict(packageName = "com.pennilogic.android.debug")),
        )
        assertEquals(IntegrityDecision.Deny(VerdictRejection.WRONG_REQUEST), evaluate(verdict(requestHash = "hash-2")))
        // Even a perfect verdict for another request is refused.
        assertEquals(IntegrityDecision.Deny(VerdictRejection.WRONG_REQUEST), evaluate(verdict(requestHash = "")))
    }

    @Test
    fun `expired and future-dated verdicts fail closed`() {
        assertEquals(
            IntegrityDecision.Deny(VerdictRejection.EXPIRED),
            evaluate(
                verdict(
                    timestampMillis =
                        now - VerdictExpectation.DEFAULT_MAX_AGE_MILLIS - 1,
                ),
            ),
        )
        assertEquals(
            IntegrityDecision.Allow,
            evaluate(
                verdict(
                    timestampMillis =
                        now - VerdictExpectation.DEFAULT_MAX_AGE_MILLIS,
                ),
            ),
        )
        assertEquals(
            IntegrityDecision.Allow,
            evaluate(
                verdict(
                    timestampMillis =
                        now + VerdictExpectation.DEFAULT_CLOCK_SKEW_MILLIS,
                ),
            ),
        )
        assertEquals(
            IntegrityDecision.Deny(VerdictRejection.FUTURE_TIMESTAMP),
            evaluate(
                verdict(
                    timestampMillis =
                        now + VerdictExpectation.DEFAULT_CLOCK_SKEW_MILLIS + 1,
                ),
            ),
        )
    }

    @Test
    fun `a replayed token fails closed and the registry forgets only after retention`() {
        val registry = InMemoryReplayRegistry(retentionMillis = 1_000)
        assertEquals(IntegrityDecision.Allow, evaluate(verdict(), registry = registry))
        assertEquals(IntegrityDecision.Deny(VerdictRejection.REPLAYED), evaluate(verdict(), registry = registry))
        assertEquals(
            "a different token for the same request is not a replay",
            IntegrityDecision.Allow,
            evaluate(verdict(tokenId = "token-2"), registry = registry),
        )
        val later = expectation.copy(nowMillis = now + 1_001)
        assertEquals(
            "after retention the token is old anyway, so freshness must catch it",
            IntegrityDecision.Allow,
            evaluate(verdict(timestampMillis = now + 1_000), later, registry),
        )
        assertTrue(later.maxAgeMillis >= 1_000)
    }

    @Test
    fun `unrecognised, unevaluated, basic-only and unlicensed verdicts fail closed`() {
        assertEquals(
            IntegrityDecision.Deny(VerdictRejection.APP_NOT_RECOGNIZED),
            evaluate(verdict(appRecognition = "UNRECOGNIZED_VERSION")),
        )
        assertEquals(
            IntegrityDecision.Deny(VerdictRejection.APP_NOT_RECOGNIZED),
            evaluate(verdict(appRecognition = "UNEVALUATED")),
        )
        assertEquals(
            IntegrityDecision.Deny(VerdictRejection.DEVICE_INTEGRITY_NOT_MET),
            evaluate(verdict(device = setOf("MEETS_BASIC_INTEGRITY"))),
        )
        assertEquals(
            IntegrityDecision.Deny(VerdictRejection.DEVICE_INTEGRITY_NOT_MET),
            evaluate(verdict(device = emptySet())),
        )
        assertEquals(IntegrityDecision.Deny(VerdictRejection.UNLICENSED), evaluate(verdict(licensing = "UNLICENSED")))
        assertEquals(IntegrityDecision.Deny(VerdictRejection.UNLICENSED), evaluate(verdict(licensing = "UNEVALUATED")))
        assertEquals(
            "an action that needs no licence still needs everything else",
            IntegrityDecision.Allow,
            evaluate(verdict(licensing = "UNLICENSED"), expectation.copy(requiresLicence = false)),
        )
        assertEquals(
            IntegrityDecision.Deny(VerdictRejection.DEVICE_INTEGRITY_NOT_MET),
            evaluate(verdict(licensing = "UNLICENSED", device = emptySet()), expectation.copy(requiresLicence = false)),
        )
    }

    @Test
    fun `play replay protection shape denies through the verdict checks`() {
        // When Play detects a replay it empties the device verdict and sets the others to UNEVALUATED.
        val replayedByPlay = verdict(appRecognition = "UNEVALUATED", device = emptySet(), licensing = "UNEVALUATED")
        assertEquals(IntegrityDecision.Deny(VerdictRejection.APP_NOT_RECOGNIZED), evaluate(replayedByPlay))
    }

    @Test
    fun `every rejection has a stable identifier`() {
        val identifier = Regex("[a-z][a-z0-9_]*")
        val vocabularies =
            listOf(
                VerdictRejection.entries.map { it.id },
                TokenRejection.entries.map { it.id },
                IntegrityUnavailable.entries.map { it.id },
            )
        for (ids in vocabularies) {
            assertEquals(ids.size, ids.toSet().size)
            ids.forEach { assertTrue(it, identifier.matches(it)) }
        }
    }
}

class ClassicRequestRegistryTest {
    @Test
    fun `this baseline reserves no classic request`() {
        assertEquals(emptyList<ClassicRequestReservation>(), ClassicRequestRegistry.reservations)
        assertEquals(0, ClassicRequestRegistry.totalDailyBudget())
        assertTrue(!ClassicRequestRegistry.isReserved("sync_transactions"))
    }

    @Test
    fun `a reservation must name its risk, budget and approval`() {
        val valid = ClassicRequestReservation("payout_release", "irreversible payout of pooled funds", 200, "T-SEC-05")
        assertEquals("payout_release", valid.actionId)
        assertThrows(IllegalArgumentException::class.java) { ClassicRequestReservation("Payout", "risk", 1, "x") }
        assertThrows(IllegalArgumentException::class.java) { ClassicRequestReservation("payout", " ", 1, "x") }
        assertThrows(IllegalArgumentException::class.java) { ClassicRequestReservation("payout", "risk", 0, "x") }
        assertThrows(IllegalArgumentException::class.java) { ClassicRequestReservation("payout", "risk", 1, "") }
    }

    @Test
    fun `reservations may never spend more than half of the default quota`() {
        val ceiling = ClassicRequestRegistry.DEFAULT_DAILY_QUOTA * ClassicRequestRegistry.RESERVABLE_SHARE_PERCENT / 100
        assertEquals(5_000, ceiling)
        assertTrue(ClassicRequestRegistry.totalDailyBudget() <= ceiling)
        val ids = ClassicRequestRegistry.reservations.map { it.actionId }
        assertEquals(ids.size, ids.toSet().size)
    }
}
