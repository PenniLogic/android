package com.pennilogic.android.platform.scheduling

import android.content.Context
import android.util.Log
import androidx.test.core.app.ApplicationProvider
import androidx.work.ListenableWorker
import androidx.work.WorkerParameters
import androidx.work.testing.TestListenableWorkerBuilder
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowLog

class WorkerResultMapperTest {
    @Test
    fun `drained work succeeds unless items wait for a retry`() {
        assertEquals(ListenableWorker.Result.success(), WorkerResultMapper.map(DrainOutcome.Drained(3, 0, 0)))
        assertEquals(ListenableWorker.Result.success(), WorkerResultMapper.map(DrainOutcome.Drained(0, 2, 0)))
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(DrainOutcome.Drained(3, 0, 1)))
    }

    @Test
    fun `stopped work follows the disposition and never fails the chain`() {
        fun stopped(reason: StopReason) = DrainOutcome.Stopped(reason, StopReasonPolicy.disposition(reason), 1, 0, 2)
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(stopped(StopReason.QUOTA)))
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(stopped(StopReason.APP_STANDBY)))
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(stopped(StopReason.TIMEOUT)))
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(stopped(StopReason.TIMEOUT_ABANDONED)))
        assertEquals(
            ListenableWorker.Result.success(),
            WorkerResultMapper.map(stopped(StopReason.BACKGROUND_RESTRICTION)),
        )
        assertEquals(ListenableWorker.Result.success(), WorkerResultMapper.map(stopped(StopReason.CANCELLED_BY_APP)))
        assertEquals(ListenableWorker.Result.success(), WorkerResultMapper.map(stopped(StopReason.NOT_STOPPED)))
        StopReason.entries.forEach { reason ->
            assertTrue(
                "$reason must never end the chain with failure",
                WorkerResultMapper.map(stopped(reason)) != ListenableWorker.Result.failure(),
            )
        }
    }

    @Test
    fun `platform stop reason is read only while stopped`() {
        assertNull(WorkerResultMapper.stopReason(isStopped = false, platformStopReason = 10))
        assertEquals(StopReason.QUOTA, WorkerResultMapper.stopReason(isStopped = true, platformStopReason = 10))
        assertEquals(StopReason.UNKNOWN, WorkerResultMapper.stopReason(isStopped = true, platformStopReason = -512))
    }
}

/** A concrete drain worker over an in-memory queue, as a feature ticket would write it. */
class TestQueueDrainWorker(
    context: Context,
    parameters: WorkerParameters,
) : QueueDrainWorker(context, parameters) {
    override fun createDrainer(owner: String): QueueDrainer<*> {
        owners += owner
        return QueueDrainer(queue, {
            sent += it.key
            SendResult.Sent
        }, owner, { 10_000L }, leaseMillis = 60_000)
    }

    companion object {
        val queue = InMemoryDurableQueue<String>()
        val sent = mutableListOf<String>()
        val owners = mutableListOf<String>()
    }
}

@RunWith(RobolectricTestRunner::class)
class QueueDrainWorkerTest {
    @Test
    fun `the worker drains the queue, succeeds and logs the capture health event without content`() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        runBlocking {
            TestQueueDrainWorker.queue.enqueue("txn-1", "payload", 1)
            TestQueueDrainWorker.queue.enqueue("txn-2", "payload", 1)
        }
        ShadowLog.clear()
        val worker = TestListenableWorkerBuilder<TestQueueDrainWorker>(context).build()

        val result = runBlocking { worker.doWork() }

        assertEquals(ListenableWorker.Result.success(), result)
        assertEquals(listOf("txn-1", "txn-2"), TestQueueDrainWorker.sent)
        assertTrue(runBlocking { TestQueueDrainWorker.queue.snapshot().isEmpty })
        assertTrue("owner is attributable to the run", TestQueueDrainWorker.owners.single().startsWith("work:"))
        val event =
            ShadowLog
                .getLogsForTag(
                    "PenniLogic",
                ).map { it.msg }
                .single { it.contains("\"event\":\"capture_health\"") }
        assertTrue(event, event.contains("\"api_level\":36"))
        assertTrue(event, event.contains("\"stop_reason\":null"))
        assertTrue(event, event.contains("\"capture_health\":null"))
        assertTrue("no payload or key in the event: $event", !event.contains("payload") && !event.contains("txn-"))
        assertEquals(Log.INFO, ShadowLog.getLogsForTag("PenniLogic").single { it.msg == event }.type)
    }
}
