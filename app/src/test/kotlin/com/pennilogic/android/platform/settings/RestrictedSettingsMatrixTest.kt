package com.pennilogic.android.platform.settings

import com.pennilogic.android.platform.state.CaptureHealthCondition
import com.pennilogic.android.platform.state.ClientState
import com.pennilogic.android.platform.state.PlatformCapabilityReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The platform facts the restricted-settings matrix must state: which install sources are locked,
 * from which API level, for which settings, that restored builds re-grant, and that every recovery
 * path is a user action on the device or an install method rather than a change to app behaviour.
 */
class RestrictedSettingsMatrixTest {
    private val unlockedSources = listOf(InstallSource.PLAY_STORE, InstallSource.SESSION_STORE, InstallSource.ADB)
    private val sideloadSources =
        listOf(InstallSource.LEGACY_SIDELOAD, InstallSource.SESSION_FILE, InstallSource.RESTORED_BY_TRANSFER)
    private val api33Set = setOf(SensitiveSetting.NOTIFICATION_LISTENER, SensitiveSetting.ACCESSIBILITY_SERVICE)
    private val api35Set =
        api33Set +
            setOf(
                SensitiveSetting.DEVICE_ADMIN,
                SensitiveSetting.DISPLAY_OVER_OTHER_APPS,
                SensitiveSetting.USAGE_ACCESS,
                SensitiveSetting.SMS_PERMISSION,
                SensitiveSetting.DEFAULT_SMS_ROLE,
                SensitiveSetting.DEFAULT_DIALER_ROLE,
            )

    @Test
    fun `matrix covers every source, setting and qa api level exactly once`() {
        assertEquals(listOf(31, 33, 34, 35, 36), RestrictedSettingsMatrix.API_LEVELS)
        val cells = RestrictedSettingsMatrix.cells()
        assertEquals(InstallSource.entries.size * SensitiveSetting.entries.size * 5, cells.size)
        assertEquals(cells.size, cells.map { Triple(it.source, it.setting, it.apiLevel) }.toSet().size)
        assertEquals(SettingAvailability.entries.toSet(), cells.map { it.availability }.toSet())
    }

    @Test
    fun `play, other store sessions and adb are never locked`() {
        for (source in unlockedSources) {
            for (apiLevel in RestrictedSettingsMatrix.API_LEVELS) {
                assertEquals(
                    "$source on $apiLevel",
                    emptyList<SensitiveSetting>(),
                    RestrictedSettingsMatrix.lockedSettings(source, apiLevel),
                )
                for (setting in SensitiveSetting.entries) {
                    val availability = RestrictedSettingsMatrix.availability(source, setting, apiLevel)
                    val expected =
                        if (apiLevel <
                            setting.existsFromApi
                        ) {
                            SettingAvailability.NOT_APPLICABLE
                        } else {
                            SettingAvailability.USER_ENABLEABLE
                        }
                    assertEquals("$source $setting $apiLevel", expected, availability)
                }
            }
        }
    }

    @Test
    fun `api 31 has no restricted settings for any source`() {
        for (source in InstallSource.entries) {
            assertEquals("$source", emptyList<SensitiveSetting>(), RestrictedSettingsMatrix.lockedSettings(source, 31))
        }
        assertEquals(
            SettingAvailability.NOT_APPLICABLE,
            RestrictedSettingsMatrix.availability(
                InstallSource.LEGACY_SIDELOAD,
                SensitiveSetting.POST_NOTIFICATIONS,
                31,
            ),
        )
    }

    @Test
    fun `api 33 and 34 lock the listener and accessibility for sideloads`() {
        for (source in sideloadSources) {
            for (apiLevel in listOf(33, 34)) {
                assertEquals(
                    "$source on $apiLevel",
                    api33Set,
                    RestrictedSettingsMatrix.lockedSettings(source, apiLevel).toSet(),
                )
            }
        }
    }

    @Test
    fun `api 35 and 36 lock the android 15 cdd set for sideloads and never the notification permission`() {
        for (source in sideloadSources) {
            for (apiLevel in listOf(35, 36)) {
                assertEquals(
                    "$source on $apiLevel",
                    api35Set,
                    RestrictedSettingsMatrix.lockedSettings(source, apiLevel).toSet(),
                )
                assertFalse(
                    RestrictedSettingsMatrix
                        .availability(
                            source,
                            SensitiveSetting.POST_NOTIFICATIONS,
                            apiLevel,
                        ).isLocked,
                )
            }
        }
    }

    @Test
    fun `restored builds always grant again and play restores are never locked`() {
        for (setting in SensitiveSetting.entries) {
            for (apiLevel in RestrictedSettingsMatrix.API_LEVELS.filter { it >= setting.existsFromApi }) {
                val byPlay = RestrictedSettingsMatrix.availability(InstallSource.RESTORED_BY_PLAY, setting, apiLevel)
                assertEquals("$setting $apiLevel", SettingAvailability.REGRANT_REQUIRED, byPlay)
                val byTransfer =
                    RestrictedSettingsMatrix.availability(
                        InstallSource.RESTORED_BY_TRANSFER,
                        setting,
                        apiLevel,
                    )
                assertTrue(
                    "$setting $apiLevel gave $byTransfer",
                    byTransfer == SettingAvailability.REGRANT_REQUIRED ||
                        byTransfer == SettingAvailability.RESTRICTED_AND_REGRANT_REQUIRED,
                )
            }
        }
    }

    @Test
    fun `locked cells recover through the platform unlock and never through app behaviour`() {
        val forbiddenInSteps =
            listOf("appops", "hidden api", "reflection", "play protect", "targetsdk", "root", "debuggable")
        for (cell in RestrictedSettingsMatrix.cells()) {
            val path = cell.recoveryPath
            if (cell.availability.isLocked) {
                assertEquals("$cell", RecoveryPath.ALLOW_RESTRICTED_SETTINGS, path)
            }
            if (cell.availability == SettingAvailability.NOT_APPLICABLE) {
                assertEquals("$cell", RecoveryPath.NONE, path)
            }
            for (step in path.steps) {
                for (term in forbiddenInSteps) {
                    assertFalse("recovery step weakens policy ($term): $step", step.lowercase().contains(term))
                }
            }
        }
        assertTrue(RecoveryPath.ALLOW_RESTRICTED_SETTINGS.steps.any { it.contains("Allow restricted settings") })
        assertTrue(RecoveryPath.ALLOW_RESTRICTED_SETTINGS.steps.any { it.contains("adb install -r") })
    }

    @Test
    fun `not-granted settings bind to exactly one taxonomy condition`() {
        for (cell in RestrictedSettingsMatrix.cells()) {
            val condition = cell.conditionWhenNotGranted
            val reason = cell.reasonWhenNotGranted
            when {
                cell.availability == SettingAvailability.NOT_APPLICABLE -> {
                    assertNull("$cell", condition)
                    assertNull("$cell", reason)
                }

                cell.availability.isLocked && cell.setting.usedByPenniLogic -> {
                    assertEquals("$cell", PlatformCapabilityReason.RESTRICTED_SETTING_LOCKED, reason)
                    assertEquals("$cell", CaptureHealthCondition.CAPTURE_BLOCKED_BY_SETTING, condition)
                }

                cell.setting == SensitiveSetting.NOTIFICATION_LISTENER -> {
                    assertEquals("$cell", PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED, reason)
                }

                cell.setting == SensitiveSetting.SMS_PERMISSION -> {
                    assertEquals("$cell", PlatformCapabilityReason.CAPTURE_PERMISSION_NOT_GRANTED, reason)
                }

                else -> {
                    assertNull("$cell", reason)
                    assertEquals("$cell", CaptureHealthCondition.DEVICE_PERMISSION_NOT_GRANTED, condition)
                }
            }
            condition?.let { assertEquals("$cell", ClientState.PERMISSION_DENIED, it.bindsTo) }
        }
    }

    @Test
    fun `capture settings of a sideloaded api 36 build are locked and named`() {
        val locked = RestrictedSettingsMatrix.lockedSettings(InstallSource.LEGACY_SIDELOAD, 36)
        assertTrue(SensitiveSetting.NOTIFICATION_LISTENER in locked)
        assertTrue(SensitiveSetting.SMS_PERMISSION in locked)
        assertEquals(
            PlatformCapabilityReason.RESTRICTED_SETTING_LOCKED,
            RestrictedSettingsMatrix
                .cell(
                    InstallSource.LEGACY_SIDELOAD,
                    SensitiveSetting.NOTIFICATION_LISTENER,
                    36,
                ).reasonWhenNotGranted,
        )
        assertEquals(
            PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED,
            RestrictedSettingsMatrix
                .cell(
                    InstallSource.ADB,
                    SensitiveSetting.NOTIFICATION_LISTENER,
                    36,
                ).reasonWhenNotGranted,
        )
    }

    @Test
    fun `identifiers are unique and snake_case`() {
        val identifier = Regex("[a-z][a-z0-9_]*")
        val ids =
            InstallSource.entries.map { it.id } + SensitiveSetting.entries.map { it.id } +
                SettingAvailability.entries.map { it.id } + RecoveryPath.entries.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
        ids.forEach { assertTrue(it, identifier.matches(it)) }
    }
}
