package com.pennilogic.android.platform.settings

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import com.pennilogic.android.platform.state.PlatformCapabilityReason

/**
 * What the platform reports about this install through `PackageManager.getInstallSourceInfo`.
 * Fields are null when the platform does not say; [packageSource] is a `PackageInstaller.PACKAGE_SOURCE_*`
 * value and exists from API 33.
 */
data class InstallSourceSnapshot(
    val installingPackage: String?,
    val initiatingPackage: String?,
    val packageSource: Int?,
)

/** Reads the install source of the running build; null below API 30 or when the lookup fails. */
fun interface InstallSourceReader {
    fun read(): InstallSourceSnapshot?
}

/** Reads whether the notification listener [listener] currently has notification access. */
fun interface ListenerAccessReader {
    fun isGranted(listener: ComponentName): Boolean
}

/**
 * Maps the platform's install-source report onto [InstallSource]. Pure JVM so the mapping is unit
 * tested exhaustively; the platform constants are inlined from `PackageInstaller` (API 33).
 */
object InstallSourceClassifier {
    const val PLAY_STORE_PACKAGE: String = "com.android.vending"
    const val SHELL_PACKAGE: String = "com.android.shell"
    val PACKAGE_INSTALLER_PACKAGES: Set<String> =
        setOf("com.google.android.packageinstaller", "com.android.packageinstaller")

    const val PACKAGE_SOURCE_UNSPECIFIED: Int = 0
    const val PACKAGE_SOURCE_OTHER: Int = 1
    const val PACKAGE_SOURCE_STORE: Int = 2
    const val PACKAGE_SOURCE_LOCAL_FILE: Int = 3
    const val PACKAGE_SOURCE_DOWNLOADED_FILE: Int = 4

    /**
     * Classifies a snapshot; null when the platform gave nothing to classify. A declared file source
     * always wins; any package-installer app (the AOSP, Google or a vendor build of it, which is the
     * intent-based sideload path) is a legacy sideload; any other installing package that used an
     * install session is a store, because the platform only locks file-sourced and legacy installs.
     */
    fun classify(snapshot: InstallSourceSnapshot?): InstallSource? {
        if (snapshot == null) return null
        val installing = snapshot.installingPackage
        val initiating = snapshot.initiatingPackage
        val throughPackageInstaller = isPackageInstaller(installing) || isPackageInstaller(initiating)
        return when {
            snapshot.packageSource == PACKAGE_SOURCE_LOCAL_FILE -> InstallSource.SESSION_FILE
            snapshot.packageSource == PACKAGE_SOURCE_DOWNLOADED_FILE -> InstallSource.SESSION_FILE
            installing == PLAY_STORE_PACKAGE -> InstallSource.PLAY_STORE
            throughPackageInstaller -> InstallSource.LEGACY_SIDELOAD
            installing == null && (initiating == null || initiating == SHELL_PACKAGE) -> InstallSource.ADB
            installing == null -> InstallSource.LEGACY_SIDELOAD
            snapshot.packageSource == PACKAGE_SOURCE_STORE -> InstallSource.SESSION_STORE
            else -> InstallSource.SESSION_STORE
        }
    }

    /** The system package installer under any vendor package name (`com.miui.packageinstaller`, ...). */
    fun isPackageInstaller(packageName: String?): Boolean =
        packageName != null && (packageName in PACKAGE_INSTALLER_PACKAGES || packageName.endsWith(".packageinstaller"))
}

/** Platform-backed [InstallSourceReader]. */
class AndroidInstallSourceReader(
    private val context: Context,
) : InstallSourceReader {
    override fun read(): InstallSourceSnapshot? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        val info =
            try {
                context.packageManager.getInstallSourceInfo(context.packageName)
            } catch (missing: PackageManager.NameNotFoundException) {
                return null
            }
        val packageSource =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) info.packageSource else null
        return InstallSourceSnapshot(info.installingPackageName, info.initiatingPackageName, packageSource)
    }
}

/** Platform-backed [ListenerAccessReader]; the compat call covers API 26 as well. */
class AndroidListenerAccessReader(
    private val context: Context,
) : ListenerAccessReader {
    override fun isGranted(listener: ComponentName): Boolean =
        NotificationManagerCompat.getEnabledListenerPackages(context).contains(listener.packageName)
}

/**
 * Decides which platform-capability reason the capture surface shows for the notification listener
 * of this build, combining the install source, the API level and the matrix. The app never reads or
 * changes the platform's restricted-settings state; a lock is inferred from what the matrix says
 * about this install source, so the recovery copy can point at the supported unlock path.
 */
class CaptureSettingsProbe(
    private val installSourceReader: InstallSourceReader,
    private val listenerAccessReader: ListenerAccessReader,
    private val apiLevel: Int = Build.VERSION.SDK_INT,
) {
    /** The install source of this build, or null when the platform did not report one. */
    fun installSource(): InstallSource? = InstallSourceClassifier.classify(installSourceReader.read())

    /**
     * Null when [listener] has notification access. Otherwise `restricted_setting_locked` when the
     * matrix says this install source is locked on this API level, else `listener_access_not_granted`.
     * An unknown install source is treated as unlocked: the user is sent to the normal settings page,
     * where the platform itself shows the restricted-setting dialog if the lock applies.
     */
    fun notificationListenerReason(listener: ComponentName): PlatformCapabilityReason? {
        if (listenerAccessReader.isGranted(listener)) return null
        val source = installSource() ?: return PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED
        val availability =
            RestrictedSettingsMatrix.availability(source, SensitiveSetting.NOTIFICATION_LISTENER, apiLevel)
        return if (availability.isLocked) {
            PlatformCapabilityReason.RESTRICTED_SETTING_LOCKED
        } else {
            PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED
        }
    }
}
