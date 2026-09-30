package com.pennilogic.android.platform.capture

import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.pennilogic.android.platform.state.ClientStateSignal
import com.pennilogic.android.platform.state.PlatformCapabilityReason

/** Whether a runtime permission is granted to this app; fakeable in tests. */
fun interface PermissionStateReader {
    fun isGranted(permission: String): Boolean
}

/** Platform-backed [PermissionStateReader]: `ContextCompat.checkSelfPermission`, which never throws. */
class AndroidPermissionStateReader(
    private val context: Context,
) : PermissionStateReader {
    override fun isGranted(permission: String): Boolean =
        ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
}

/**
 * Detection behind the `capture_permission_not_granted` reason: the capture feature names the
 * runtime permission it needs (for example `android.permission.RECEIVE_SMS`) and the probe reports
 * whether capture is blocked by it. Reading a permission state requests nothing and shows no prompt;
 * this baseline declares and requests no capture permission, so on this build every capture
 * permission reads as not granted, which is the truthful answer for a build without a capture feature.
 */
class CapturePermissionProbe(
    private val reader: PermissionStateReader,
) {
    /**
     * Null when [permission] is granted; `capture_permission_not_granted` otherwise. [permission] must
     * be a platform permission name, the same shape the taxonomy signal accepts.
     */
    fun reason(permission: String): PlatformCapabilityReason? {
        require(ClientStateSignal.PLATFORM_PERMISSION.matches(permission)) {
            "permission must be a platform permission name"
        }
        return if (reader.isGranted(permission)) null else PlatformCapabilityReason.CAPTURE_PERMISSION_NOT_GRANTED
    }

    /** The blocked capture health for [permission], or null when it is granted. */
    fun blocked(permission: String): CaptureHealth.Blocked? =
        reason(permission)?.let { CaptureHealth.Blocked(it, permission) }
}
