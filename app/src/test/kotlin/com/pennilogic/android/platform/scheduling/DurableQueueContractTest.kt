package com.pennilogic.android.platform.scheduling

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
            assertTrue(queue.deadLetter("k1", "owner", "rejected"))
            assertFalse("refused after dead-lettering too", queue.enqueue("k1", "d", nowMillis = 5))
        }

    @Test
    fun `claims lease items to one owner and honour retry times`() =
        runTest {
            queue.enqueue("k1", "a", 1)
            queue.enqueue("k2", "b", 1)
            queue.enqueue("k3", "c", 1)
            val first = queue.claim("A", limit = 2, nowMillis = 10, leaseMillis = 100)
            assertEquals(listOf("k1", "k2"), first.map { it.key })
            val second = queue.claim("B", limit = 10, nowMillis = 10, leaseMillis = 100)
            assertEquals("B only gets what A did not lease", listOf("k3"), second.map { it.key })
            assertTrue(queue.release("k1", "A", retryNotBeforeMillis = 500))
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
            assertEquals("attempts count the release", 1, retried.single().attempts)
        }

    @Test
    fun `ack and release are accepted only from the lease holder`() =
        runTest {
            queue.enqueue("k1", "a", 1)
            queue.claim("A", 10, nowMillis = 1, leaseMillis = 100)
            assertFalse(queue.ack("k1", "B"))
            assertFalse(queue.release("k1", "B", null))
            assertFalse(queue.deadLetter("k1", "B", "x"))
            assertEquals(setOf("k1"), queue.snapshot().inFlight)
            assertTrue(queue.ack("k1", "A"))
            assertTrue(queue.snapshot().isEmpty)
            assertFalse("acking twice is a no-op", queue.ack("k1", "A"))
        }

    @Test
    fun `expired leases return to pending and a late ack from the old owner is refused`() =
        runTest {
            queue.enqueue("k1", "a", 1)
            queue.claim("A", 10, nowMillis = 1, leaseMillis = 100)
            assertEquals(0, queue.expireLeases(nowMillis = 100))
            assertEquals(1, queue.expireLeases(nowMillis = 101))
            assertEquals(setOf("k1"), queue.snapshot().pending)
            val takeover = queue.claim("B", 10, nowMillis = 102, leaseMillis = 100)
            assertEquals(1, takeover.single().attempts)
            assertFalse("A lost the lease with the expiry", queue.ack("k1", "A"))
            assertTrue(queue.ack("k1", "B"))
        }

    @Test
    fun `snapshot separates pending, in-flight and dead-lettered keys`() =
        runTest {
            queue.enqueue("p", "1", 1)
            queue.enqueue("f", "2", 1)
            queue.enqueue("d", "3", 1)
            queue.claim("A", 1, 1, 100)
            val second = queue.claim("A", 1, 1, 100)
            queue.deadLetter(second.single().key, "A", "bad")
            val snapshot = queue.snapshot()
            assertEquals(setOf("d"), snapshot.pending)
            assertEquals(setOf("p"), snapshot.inFlight)
            assertEquals(setOf("f"), snapshot.deadLettered)
            assertEquals("bad", queue.deadLetterReason("f"))
            assertFalse(snapshot.isEmpty)
        }
}
