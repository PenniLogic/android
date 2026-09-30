package com.pennilogic.android.platform.integrity

import android.content.Context
import com.google.android.gms.tasks.Task
import com.google.android.play.core.integrity.IntegrityManagerFactory
import com.google.android.play.core.integrity.StandardIntegrityException
import com.google.android.play.core.integrity.StandardIntegrityManager
import com.google.android.play.core.integrity.StandardIntegrityManager.PrepareIntegrityTokenRequest
import com.google.android.play.core.integrity.StandardIntegrityManager.StandardIntegrityTokenRequest
import com.google.android.play.core.integrity.model.StandardIntegrityErrorCode
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlin.coroutines.cancellation.CancellationException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Play-backed [StandardIntegrityTokenProvider]. The token provider is prepared once per process
 * (Play caches it and refreshes it on its own) and each request carries the request hash. Not
 * exercised by unit tests, which never call Google services; feature tickets wire it with the Cloud
 * project number of the Play Console project (configuration, never a secret) and exercise it in
 * their instrumented runs.
 */
class PlayStandardIntegrityTokenProvider(
    context: Context,
    private val cloudProjectNumber: Long,
    private val clock: () -> Long = System::currentTimeMillis,
) : StandardIntegrityTokenProvider {
    private val manager: StandardIntegrityManager = IntegrityManagerFactory.createStandard(context.applicationContext)
    private val prepared = Mutex()
    private var tokenProvider: StandardIntegrityManager.StandardIntegrityTokenProvider? = null

    override suspend fun requestToken(requestHash: String): IntegrityTokenResult =
        try {
            val provider = preparedProvider()
            val token =
                provider
                    .request(
                        StandardIntegrityTokenRequest.builder().setRequestHash(requestHash).build(),
                    ).await()
            IntegrityTokenResult.Token(token.token(), requestHash, clock())
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (failure: Exception) {
            val cause = classify(failure)
            if (cause == IntegrityUnavailable.PROVIDER_INVALID) prepared.withLock { tokenProvider = null }
            IntegrityTokenResult.Unavailable(cause)
        }

    private suspend fun preparedProvider(): StandardIntegrityManager.StandardIntegrityTokenProvider =
        prepared.withLock {
            tokenProvider ?: manager
                .prepareIntegrityToken(
                    PrepareIntegrityTokenRequest.builder().setCloudProjectNumber(cloudProjectNumber).build(),
                ).await()
                .also { tokenProvider = it }
        }

    private fun classify(failure: Exception): IntegrityUnavailable {
        val code = (failure as? StandardIntegrityException)?.errorCode ?: return IntegrityUnavailable.UNKNOWN
        return when (code) {
            StandardIntegrityErrorCode.API_NOT_AVAILABLE,
            StandardIntegrityErrorCode.PLAY_STORE_NOT_FOUND,
            StandardIntegrityErrorCode.PLAY_SERVICES_NOT_FOUND,
            StandardIntegrityErrorCode.PLAY_STORE_VERSION_OUTDATED,
            StandardIntegrityErrorCode.PLAY_SERVICES_VERSION_OUTDATED,
            StandardIntegrityErrorCode.APP_NOT_INSTALLED,
            StandardIntegrityErrorCode.APP_UID_MISMATCH,
            -> IntegrityUnavailable.PLAY_NOT_AVAILABLE

            StandardIntegrityErrorCode.NETWORK_ERROR,
            StandardIntegrityErrorCode.GOOGLE_SERVER_UNAVAILABLE,
            StandardIntegrityErrorCode.CANNOT_BIND_TO_SERVICE,
            StandardIntegrityErrorCode.CLIENT_TRANSIENT_ERROR,
            -> IntegrityUnavailable.NETWORK

            StandardIntegrityErrorCode.TOO_MANY_REQUESTS -> IntegrityUnavailable.TOO_MANY_REQUESTS

            StandardIntegrityErrorCode.INTEGRITY_TOKEN_PROVIDER_INVALID -> IntegrityUnavailable.PROVIDER_INVALID

            StandardIntegrityErrorCode.CLOUD_PROJECT_NUMBER_IS_INVALID,
            StandardIntegrityErrorCode.REQUEST_HASH_TOO_LONG,
            -> IntegrityUnavailable.CLIENT_ERROR

            else -> IntegrityUnavailable.UNKNOWN
        }
    }
}

/** Suspends on a Play `Task` without pulling in the coroutines Play-services artifact. */
private suspend fun <T> Task<T>.await(): T =
    suspendCancellableCoroutine { continuation ->
        addOnSuccessListener { continuation.resume(it) }
        addOnFailureListener { continuation.resumeWithException(it) }
        addOnCanceledListener { continuation.cancel() }
    }
