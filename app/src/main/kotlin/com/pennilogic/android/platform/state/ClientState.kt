package com.pennilogic.android.platform.state

/**
 * Pin of the shared client state and copy taxonomy published by PenniLogic/docs (`T-UX-01`,
 * `product/client-state-taxonomy.json`). Android asserts these identifiers, never display strings,
 * and its state enumeration is exactly the published set (`taxonomy_first`). A new state or cause is
 * added to the taxonomy first and only then here.
 */
object ClientStateTaxonomy {
    const val VERSION: String = "1.0.0"
    const val SIGNAL_PREFIX: String = "client_state."
    const val CLIENT: String = "android"
}

/** The eight published states, by stable identifier. */
enum class ClientState(
    val id: String,
) {
    EMPTY("empty"),
    LOADING("loading"),
    ERROR("error"),
    OFFLINE("offline"),
    STALE("stale"),
    PERMISSION_DENIED("permission_denied"),
    QUOTA_EXCEEDED("quota_exceeded"),
    DEGRADED("degraded"),
    ;

    /** The signal recorded when the state is entered and when its recovery action is taken. */
    val signal: String
        get() = ClientStateTaxonomy.SIGNAL_PREFIX + id

    companion object {
        fun fromId(id: String): ClientState? = entries.firstOrNull { it.id == id }
    }
}

/** Cause classes of `permission_denied`; the platform layer only ever produces [DEVICE]. */
enum class PermissionDeniedCause(
    val id: String,
) {
    DEVICE("device"),
    PLAN("plan"),
    SHARING("sharing"),
    ROLE("role"),
}

/** Where a state is rendered. */
enum class StateScope(
    val id: String,
) {
    SURFACE("surface"),
    REGION("region"),
    ACTION("action"),
}

/**
 * The taxonomy's contract-level conditions owned by `T-AND-07`. Each binds to exactly one state and,
 * for `permission_denied`, to exactly one cause; the binding is asserted by
 * `ClientStateTaxonomyTest` against `docs/platform/capture-health-identifiers.json`.
 */
enum class CaptureHealthCondition(
    val id: String,
    val bindsTo: ClientState,
    val cause: PermissionDeniedCause?,
) {
    /** Automatic capture is paused for a platform reason while the rest of the app works. */
    CAPTURE_PAUSED_BY_PLATFORM("capture_paused_by_platform", ClientState.DEGRADED, null),

    /** Automatic capture cannot run because a permission or restricted setting is not granted. */
    CAPTURE_BLOCKED_BY_SETTING(
        "capture_blocked_by_setting",
        ClientState.PERMISSION_DENIED,
        PermissionDeniedCause.DEVICE,
    ),

    /** Any other platform permission or restricted setting reported as not granted. */
    DEVICE_PERMISSION_NOT_GRANTED(
        "device_permission_not_granted",
        ClientState.PERMISSION_DENIED,
        PermissionDeniedCause.DEVICE,
    ),
    ;

    companion object {
        fun fromId(id: String): CaptureHealthCondition? = entries.firstOrNull { it.id == id }
    }
}

/**
 * Platform-capability reasons android#57 publishes underneath the taxonomy conditions. They are the
 * identifiers `T-UX-01` and `T-QA-12` consume for "tracking paused" and "capture blocked"; each rolls
 * up to exactly one condition and therefore to exactly one state, so no parallel vocabulary exists.
 * A reason never carries transaction content, message content or a device identifier.
 */
enum class PlatformCapabilityReason(
    val id: String,
    val condition: CaptureHealthCondition,
) {
    /** The user force-stopped the app; receivers and jobs stay off until the user opens it again. */
    FORCE_STOPPED("force_stopped", CaptureHealthCondition.CAPTURE_PAUSED_BY_PLATFORM),

    /** The private space holding the app was locked, which stops every app inside it. */
    PRIVATE_SPACE_PAUSED("private_space_paused", CaptureHealthCondition.CAPTURE_PAUSED_BY_PLATFORM),

    /** The app is in the `restricted` standby bucket, so background work is severely limited. */
    STANDBY_BUCKET_RESTRICTED("standby_bucket_restricted", CaptureHealthCondition.CAPTURE_PAUSED_BY_PLATFORM),

    /** The user applied a background restriction to the app in system settings. */
    BACKGROUND_RESTRICTED("background_restricted", CaptureHealthCondition.CAPTURE_PAUSED_BY_PLATFORM),

    /** Notification listener access has not been granted for the capture service. */
    LISTENER_ACCESS_NOT_GRANTED("listener_access_not_granted", CaptureHealthCondition.CAPTURE_BLOCKED_BY_SETTING),

    /** A restricted setting is locked for this install (sideloaded or restored build). */
    RESTRICTED_SETTING_LOCKED("restricted_setting_locked", CaptureHealthCondition.CAPTURE_BLOCKED_BY_SETTING),

    /** A runtime permission capture needs (for example SMS) has not been granted. */
    CAPTURE_PERMISSION_NOT_GRANTED("capture_permission_not_granted", CaptureHealthCondition.CAPTURE_BLOCKED_BY_SETTING),
    ;

    /** The state a surface renders for this reason, resolved through the single binding. */
    val state: ClientState
        get() = condition.bindsTo

    companion object {
        fun fromId(id: String): PlatformCapabilityReason? = entries.firstOrNull { it.id == id }
    }
}

/**
 * The taxonomy signal `client_state.<identifier>`, recorded once on entering a state and once more
 * when its recovery action is taken. It carries exactly the published attributes: no amount, no
 * identifier of a denied resource, no other person, no message content and no device identifier.
 */
data class ClientStateSignal(
    val state: ClientState,
    val surfaceId: String,
    val scope: StateScope,
    val cause: PermissionDeniedCause? = null,
    /** For the `device` cause only: the permission as the platform names it. */
    val permission: String? = null,
    val recoveryActionTaken: Boolean = false,
) {
    init {
        require(
            cause == null || state == ClientState.PERMISSION_DENIED,
        ) { "cause is only defined for permission_denied" }
        require(permission == null || cause == PermissionDeniedCause.DEVICE) {
            "permission is only defined for the device cause"
        }
        require(SURFACE_ID.matches(surfaceId)) { "surface_id must be a snake_case identifier" }
    }

    val name: String
        get() = state.signal

    /** Attribute map in the taxonomy's names; absent attributes are omitted rather than blank. */
    fun attributes(): Map<String, String> =
        buildMap {
            put("client", ClientStateTaxonomy.CLIENT)
            put("surface_id", surfaceId)
            put("scope", scope.id)
            cause?.let { put("cause", it.id) }
            permission?.let { put("permission", it) }
            put("recovery_action_taken", recoveryActionTaken.toString())
            put("taxonomy_version", ClientStateTaxonomy.VERSION)
        }

    private companion object {
        val SURFACE_ID = Regex("[a-z][a-z0-9_]*")
    }
}
