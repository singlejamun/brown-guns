# === Setup hook integration ===
# Source lines 9966-10036 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# PART 6 — FINAL INTEGRATION, STARTUP, COMMANDS, AND BOT RUN
# Paste this directly below Part 5.
# =========================================================


async def final_setup_hook(
    self: FamilyBot,
) -> None:
    """
    Restores every persistent button view and starts
    all background schedulers before the bot becomes ready.
    """

    await detect_bot_owner()

    # A failing restore step must not stop the bot from starting — especially
    # while it is still being configured with !config.
    for label, step in (
        ("registration views", lambda: restore_registration_views(self)),
        ("role request views", lambda: restore_role_request_views(self)),
        ("punishment views", lambda: restore_punishment_views(self)),
        ("punishment appeal views", lambda: restore_punishment_appeal_views(self)),
        ("system statuses", lambda: initialize_system_statuses()),
        ("event views", lambda: restore_event_views(self)),
        ("rp ticket views", lambda: restore_rp_ticket_views(self)),
        ("complaint views", lambda: restore_complaint_views(self)),
        ("jailer role", lambda: load_jailer_role()),
        ("pov views", lambda: restore_pov_views(self)),
        ("control access", lambda: load_control_access()),
    ):
        try:
            await step()
        except Exception:
            log.exception("Startup step '%s' failed; continuing.", label)

    self.add_view(RankRequestPanelView(self))
    self.add_view(ComplaintPanelView(self))
    self.add_view(VoiceControlPanelView())
    self.add_view(DataControlPanelView())

    # A failure while (re)posting one panel must never abort startup —
    # otherwise the schedulers below would never start.
    for label, step in (
        ("rank request panel", lambda: ensure_rank_request_panel(self)),
        ("complaint panel", lambda: ensure_complaint_panel(self)),
        ("voice control panel", lambda: ensure_voice_control_panel()),
    ):
        try:
            await step()
        except Exception:
            log.exception("Could not restore the %s on startup.", label)

    data_guild_id = await get_data_guild_id()
    if data_guild_id is not None:
        data_guild = self.get_guild(data_guild_id)
        if data_guild is not None:
            try:
                await run_database_auto_setup(data_guild, only_missing=True)
            except discord.HTTPException:
                log.exception("Could not verify the database server on startup.")

    await run_database_health_check()
    if not database_backup_scheduler.is_running():
        database_backup_scheduler.start()

    if not registration_scheduler.is_running():
        registration_scheduler.start()

    if not warning_expiry_scheduler.is_running():
        warning_expiry_scheduler.start()

    if not pov_expiry_scheduler.is_running():
        pov_expiry_scheduler.start()

    if not rp_ticket_scheduler.is_running():
        rp_ticket_scheduler.start()

    if not rotating_bot_status.is_running():
        rotating_bot_status.start()

    if not state_register_expiry_scheduler.is_running():
        state_register_expiry_scheduler.start()

    await ensure_automatic_rp_sessions()

    log.info("All persistent views and schedulers loaded.")


# Replace the temporary Part 1 setup_hook with the final one.
FamilyBot.setup_hook = final_setup_hook


