# === Config validation ===
# Source lines 12945-13065 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# CONFIGURATION VALIDATION
# =========================================================

def final_validate_configuration() -> None:
    # Everything except the token is now set from Discord with `!config`,
    # so missing values are reported instead of stopping the bot.
    missing = cfg_missing_required()

    if missing:
        log.warning(
            "Not configured yet (%d): %s. Finish setup in Discord with `!config` "
            "(see `!config help`).",
            len(missing),
            ", ".join(missing),
        )

    if not STAFF_ROLE_IDS:
        log.warning(
            "STAFF_ROLE_IDS is empty. "
            "Only administrators/manage-server members can review requests."
        )

    if not HIGH_COMMAND_ROLE_IDS:
        log.warning(
            "HIGH_COMMAND_ROLE_IDS is empty. "
            "Punishment access will fall back to STAFF_ROLE_IDS."
        )


