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
        registry: ReplayRegistry = InMemoryReplayRegistry.covering(expectation),
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
    fun `a replayed token fails closed for as long as it is fresh`() {
        val registry = InMemoryReplayRegistry.covering(expectation)
        val presented = verdict()
        assertEquals(IntegrityDecision.Allow, evaluate(presented, registry = registry))
        assertEquals(IntegrityDecision.Deny(VerdictRejection.REPLAYED), evaluate(presented, registry = registry))
        assertEquals(
            "a different token for the same request is not a replay",
            IntegrityDecision.Allow,
            evaluate(verdict(tokenId = "token-2"), registry = registry),
        )
        // The same token again at the very end of its freshness window: still fresh, still refused.
        val lastFreshMoment = expectation.copy(nowMillis = presented.timestampMillis + expectation.maxAgeMillis)
        assertEquals(
            IntegrityDecision.Deny(VerdictRejection.REPLAYED),
            evaluate(presented, lastFreshMoment, registry),
        )
        // Once the token is stale, freshness refuses it, whether or not the registry still remembers it.
        val stale = expectation.copy(nowMillis = presented.timestampMillis + expectation.maxAgeMillis + 1)
        assertEquals(IntegrityDecision.Deny(VerdictRejection.EXPIRED), evaluate(presented, stale, registry))
    }

    @Test
    fun `a registry whose retention does not cover the freshness window is refused before any row`() {
        val tooShort = InMemoryReplayRegistry(retentionMillis = 1_000)
        assertThrows(IllegalArgumentException::class.java) { evaluate(verdict(), registry = tooShort) }
        val justShort =
            InMemoryReplayRegistry(retentionMillis = expectation.maxAgeMillis + expectation.clockSkewMillis - 1)
        assertThrows(IllegalArgumentException::class.java) { evaluate(verdict(), registry = justShort) }
        val exact = InMemoryReplayRegistry(retentionMillis = expectation.maxAgeMillis + expectation.clockSkewMillis)
        assertEquals(IntegrityDecision.Allow, evaluate(verdict(), registry = exact))
        assertEquals(
            "the covering registry is exactly the freshness window",
            expectation.maxAgeMillis + expectation.clockSkewMillis,
            InMemoryReplayRegistry.covering(expectation).retentionMillis,
        )
        assertThrows(IllegalArgumentException::class.java) { InMemoryReplayRegistry(retentionMillis = 0) }
    }

    @Test
    fun `a verdict without a token identity cannot reach the registry`() {
        assertThrows(IllegalArgumentException::class.java) { verdict(tokenId = "") }
        assertThrows(IllegalArgumentException::class.java) { verdict(tokenId = "   ") }
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
