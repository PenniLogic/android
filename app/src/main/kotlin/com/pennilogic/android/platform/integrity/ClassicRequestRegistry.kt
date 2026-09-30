package com.pennilogic.android.platform.integrity

/**
 * A classic Play Integrity request reserved for one named highest-value action. Classic requests
 * cost seconds of latency, count against the default quota of 10,000 requests per day across all
 * installs, and leave replay and exfiltration protection to the app, so every reservation must name
 * the risk it mitigates and the daily budget it may spend. Standard requests protect every other
 * server action.
 */
data class ClassicRequestReservation(
    /** Stable action identifier, snake_case. */
    val actionId: String,
    /** The threat that justifies the latency and quota (for example "irreversible payout of pooled funds"). */
    val riskStatement: String,
    /** Requests per day this action may spend; the sum over all reservations must stay under the quota. */
    val dailyQuotaBudget: Int,
    /** Ticket or decision record that approved the reservation. */
    val approvedIn: String,
) {
    init {
        require(ACTION_ID.matches(actionId)) { "actionId must be snake_case" }
        require(riskStatement.isNotBlank()) { "riskStatement is required" }
        require(dailyQuotaBudget > 0) { "dailyQuotaBudget must be positive" }
        require(approvedIn.isNotBlank()) { "approvedIn is required" }
    }

    private companion object {
        val ACTION_ID = Regex("[a-z][a-z0-9_]*")
    }
}

/**
 * The only place a classic request may be justified. This baseline reserves none: no PenniLogic
 * action has yet been named as requiring the classic flow. A feature ticket that needs one adds a
 * reservation here with its risk and budget, and the security review of that ticket approves it;
 * `ClassicRequestRegistryTest` enforces the shape and the quota ceiling.
 */
object ClassicRequestRegistry {
    /** Play's default quota across all installs; a higher quota is a request to Google, not a code change. */
    const val DEFAULT_DAILY_QUOTA: Int = 10_000

    /** Keep well below the ceiling so standard-request fallbacks and retries never starve. */
    const val RESERVABLE_SHARE_PERCENT: Int = 50

    val reservations: List<ClassicRequestReservation> = emptyList()

    fun isReserved(actionId: String): Boolean = reservations.any { it.actionId == actionId }

    fun totalDailyBudget(): Int = reservations.sumOf { it.dailyQuotaBudget }
}
