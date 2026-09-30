package com.pennilogic.android.platform.scheduling

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** The contract every [DurableQueue] implementation must keep, proven on the reference implementation. */
class DurableQueueContractTest {
    private val queue = InMemoryDurableQueue<String>()

    @Test
    fun `a key is never queued twice`() =
        runTest {
            assertTrue(queue.enqueue("k1", "a", nowMillis = 1))
            assertFalse("second copy refused", queue.enqueue("k1", "b", nowMillis = 2))
            val claimed = queue.claim("owner", 10, nowMillis = 3, leaseMillis = 100)
            assertEquals(listOf("k1"), claimed.map { it.key })
            assertEquals("a", claimed.single().payload)
            assertFalse("still refused while in flight", queue.enqueue("k1", "c", nowMillis = 4))
            assertTrue(queue.deadLetter("k1", "owner", "rejected", nowMillis = 5))
            assertFalse("refused after dead-lettering too", queue.enqueue("k1", "d", nowMillis = 5))
        }

    @Test
    fun `claims lease items to one owner and a failed send attempt honours its retry time`() =
        runTest {
            queue.enqueue("k1", "a", 1)
            queue.enqueue("k2", "b", 1)
            queue.enqueue("k3", "c", 1)
            val first = queue.claim("A", limit = 2, nowMillis = 10, leaseMillis = 100)
            assertEquals(listOf("k1", "k2"), first.map { it.key })
            val second = queue.claim("B", limit = 10, nowMillis = 10, leaseMillis = 100)
            assertEquals("B only gets what A did not lease", listOf("k3"), second.map { it.key })
            assertTrue(queue.retryLater("k1", "A", notBeforeMillis = 500))
            assertEquals(
                "k1 waits for its retry time",
                emptyList<String>(),
                queue
                    .claim(
                        "B",
                        10,
                        nowMillis = 400,
                        leaseMillis = 100,
                    ).map {
                        it.key
                    },
            )
            val retried = queue.claim("B", 10, nowMillis = 500, leaseMillis = 100)
            assertEquals(listOf("k1"), retried.map { it.key })
            assertEquals("a failed send attempt counts", 1, retried.single().attempts)
            assertEquals(0, retried.single().leaseExpiries)
        }

    @Test
    fun `an unsent give-back counts no attempt and applies no backoff, however often it happens`() =
        runTest {
            queue.enqueue("k1", "a", 1)
            repeat(50) { round ->
                val claimed = queue.claim("run-$round", 10, nowMillis = 10L + round, leaseMillis = 100)
                assertEquals("k1", claimed.single().key)
                assertEquals("stops never spend the attempt budget", 0, claimed.single().attempts)
                assertTrue(queue.release("k1", "run-$round"))
                assertEquals(setOf("k1"), queue.snapshot().pending)
            }
            assertEquals(
                "pending at once, no retry time",
                listOf("k1"),
                queue.claim("next", 10, nowMillis = 60, leaseMillis = 100).map { it.key },
            )
        }

    @Test
    fun `ack, release, retry and dead-letter are accepted only from the lease holder`() =
        runTest {
            queue.enqueue("k1", "a", 1)
            queue.claim("A", 10, nowMillis = 1, leaseMillis = 100)
            assertFalse(queue.ack("k1", "B"))
            assertFalse(queue.release("k1", "B"))
            assertFalse(queue.retryLater("k1", "B", 50))
            assertFalse(queue.deadLetter("k1", "B", "x", nowMillis = 2))
            assertEquals(setOf("k1"), queue.snapshot().inFlight)
            assertTrue(queue.ack("k1", "A"))
            assertTrue(queue.snapshot().isEmpty)
            assertFalse("acking twice is a no-op", queue.ack("k1", "A"))
        }

    @Test
    fun `expired leases return to pending, count an expiry not an attempt, and refuse the old owner`() =
        runTest {
            queue.enqueue("k1", "a", 1)
            queue.claim("A", 10, nowMillis = 1, leaseMillis = 100)
            assertEquals(0, queue.expireLeases(nowMillis = 100))
            assertEquals(1, queue.expireLeases(nowMillis = 101))
            assertEquals(setOf("k1"), queue.snapshot().pending)
            val takeover = queue.claim("B", 10, nowMillis = 102, leaseMillis = 100)
            assertEquals("the item may never have been sent", 0, takeover.single().attempts)
            assertEquals(1, takeover.single().leaseExpiries)
            assertFalse("A lost the lease with the expiry", queue.ack("k1", "A"))
            assertTrue(queue.ack("k1", "B"))
        }

    @Test
    fun `a dead-lettered item is parked with its payload, counts and reason until clear`() =
        runTest {
            queue.enqueue("k1", "payload-1", nowMillis = 1)
            queue.claim("A", 10, nowMillis = 2, leaseMillis = 100)
            assertTrue(queue.retryLater("k1", "A", notBeforeMillis = 3))
            queue.claim("A", 10, nowMillis = 3, leaseMillis = 100)
            assertTrue(queue.deadLetter("k1", "A", "max_attempts", nowMillis = 4))

            val parked = queue.deadLetters().single()
            assertEquals("k1", parked.item.key)
            assertEquals("payload-1", parked.item.payload)
            assertEquals(1, parked.item.enqueuedAtMillis)
            assertEquals(1, parked.item.attempts)
            assertEquals("max_attempts", parked.reason)
            assertEquals(4, parked.deadLetteredAtMillis)
            assertEquals("max_attempts", queue.deadLetterReason("k1"))
            val snapshot = queue.snapshot()
            assertEquals(setOf("k1"), snapshot.deadLettered)
            assertTrue("no longer live", snapshot.isEmpty)
            assertFalse("its key stays refused", queue.enqueue("k1", "again", nowMillis = 5))
            assertFalse("a dead letter is not leased again", queue.claim("B", 10, 6, 100).any { it.key == "k1" })
        }

    @Test
    fun `no item or dead letter ever prints its payload`() =
        runTest {
            val payload = "{\"amount_minor\":123400,\"merchant\":\"ACME\"}"
            queue.enqueue("txn-1", payload, nowMillis = 1)
            val item = queue.claim("A", 10, nowMillis = 2, leaseMillis = 100).single()
            assertEquals(payload, item.payload)
            assertFalse("item: $item", item.toString().contains("123400") || item.toString().contains("ACME"))
            assertTrue(item.toString().contains("txn-1"))
            assertTrue(queue.deadLetter("txn-1", "A", "schema", nowMillis = 3))
            val parked = queue.deadLetters().single()
            assertEquals(payload, parked.item.payload)
            val printed = parked.toString()
            assertFalse("dead letter: $printed", printed.contains("123400") || printed.contains("ACME"))
            assertTrue(printed.contains("schema") && printed.contains("txn-1"))
            val plan = SendResult.Rejected("schema")
            assertFalse(plan.toString().contains("ACME"))
        }

    @Test
    fun `clear erases pending, in-flight and dead-lettered items with their payloads`() =
        runTest {
            queue.enqueue("pending", "p", 1)
            queue.enqueue("flying", "f", 1)
            queue.enqueue("parked", "d", 1)
            queue.claim("A", 1, 2, 100)
            val second = queue.claim("A", 1, 2, 100).single()
            queue.deadLetter(second.key, "A", "schema", nowMillis = 3)
            assertEquals(setOf("parked"), queue.snapshot().pending)
            assertEquals(setOf("pending"), queue.snapshot().inFlight)
            assertEquals(setOf("flying"), queue.snapshot().deadLettered)

            queue.clear()

            val snapshot = queue.snapshot()
            assertEquals(QueueSnapshot(emptySet(), emptySet(), emptySet()), snapshot)
            assertEquals(emptyList<DeadLetter<String>>(), queue.deadLetters())
            assertNull(queue.deadLetterReason("flying"))
            assertFalse("the old lease is gone with the item", queue.ack("pending", "A"))
            assertTrue("keys are free again after erasure", queue.enqueue("flying", "again", nowMillis = 4))
        }

    @Test
    fun `reasons are identifiers, never free server text`() =
        runTest {
            queue.enqueue("k1", "a", 1)
            queue.claim("A", 10, 1, 100)
            for (bad in listOf("", "Schema", "amount 123400 too large", "merchant:ACME", "a".repeat(65), "1x")) {
                assertThrows(bad, IllegalArgumentException::class.java) { SendResult.Rejected(bad) }
                val thrown = runCatching { queue.deadLetter("k1", "A", bad, 2) }.exceptionOrNull()
                assertTrue(bad, thrown is IllegalArgumentException)
            }
            assertEquals("still in flight after the refused calls", setOf("k1"), queue.snapshot().inFlight)
            SendResult.Rejected("schema_v2")
            assertTrue(queue.deadLetter("k1", "A", "schema_v2", 2))
        }

    @Test
    fun `snapshot separates pending, in-flight and dead-lettered keys`() =
        runTest {
            queue.enqueue("p", "1", 1)
            queue.enqueue("f", "2", 1)
            queue.enqueue("d", "3", 1)
            queue.claim("A", 1, 1, 100)
            val second = queue.claim("A", 1, 1, 100)
            queue.deadLetter(second.single().key, "A", "bad", nowMillis = 2)
            val snapshot = queue.snapshot()
            assertEquals(setOf("d"), snapshot.pending)
            assertEquals(setOf("p"), snapshot.inFlight)
            assertEquals(setOf("f"), snapshot.deadLettered)
            assertEquals("bad", queue.deadLetterReason("f"))
            assertFalse(snapshot.isEmpty)
        }
}
