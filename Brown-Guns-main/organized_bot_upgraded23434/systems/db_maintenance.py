# === Database maintenance: automatic backups + health check ===
# Loaded into shared namespace by bot.py — do not run this file alone.
#
# Environment variables (all optional):
#   DB_BACKUP_HOURS   how often to back up            (default 6)
#   DB_BACKUP_KEEP    how many backups to keep        (default 14)
#   DB_BACKUP_DIR     where backups go                (default <db folder>/backups)
#   DB_REQUIRE_PERSISTENT=1   refuse to start when the DB is on temporary disk

DB_BACKUP_INTERVAL_HOURS = max(1, _env_int("DB_BACKUP_HOURS", 6))
DB_BACKUP_KEEP = max(1, _env_int("DB_BACKUP_KEEP", 14))


async def run_database_health_check() -> dict | None:
    """Logs the integrity result. Never raises — startup must not depend on it."""
    try:
        report = await asyncio.to_thread(db.health_report)
    except Exception:
        log.exception("Database health check could not run.")
        return None

    if report["ok"]:
        log.info(
            "Database healthy: v%s, %s journal, %.1f KB at %s",
            report["schema_version"],
            report["journal_mode"],
            report["size_bytes"] / 1024,
            db.path,
        )
    else:
        log.critical(
            "DATABASE PROBLEM — quick_check=%s, foreign_key_problems=%s. "
            "Restore the newest file from %s.",
            report["quick_check"],
            report["foreign_key_problems"],
            db.backup_dir,
        )
    return report


@tasks.loop(hours=DB_BACKUP_INTERVAL_HOURS)
async def database_backup_scheduler() -> None:
    # Runs once immediately at startup, then every DB_BACKUP_HOURS.
    try:
        path = await asyncio.to_thread(db.backup_now, DB_BACKUP_KEEP)
        log.info("Database backup saved: %s", path)
    except Exception:
        log.exception("Database backup FAILED.")


@bot.command(name="dbbackup")
async def dbbackup_command(ctx: commands.Context) -> None:
    """Owner only: take a fresh backup and DM the file to the owner, so a copy
    exists OUTSIDE Railway too."""
    if BOT_OWNER_ID <= 0 or ctx.author.id != BOT_OWNER_ID:
        await ctx.reply("❌ Only the bot owner can use `!dbbackup`.", mention_author=False)
        return

    try:
        path = await asyncio.to_thread(db.backup_now, DB_BACKUP_KEEP)
    except Exception:
        log.exception("Manual database backup FAILED.")
        await ctx.reply("❌ Backup failed — check the Railway logs.", mention_author=False)
        return

    report = await run_database_health_check()
    healthy = "✅ healthy" if report and report["ok"] else "⚠️ problem detected — see logs"
    size_kb = path.stat().st_size / 1024

    try:
        await ctx.author.send(
            f"Database backup `{path.name}` ({size_kb:.1f} KB) — {healthy}",
            file=discord.File(path),
        )
    except (discord.Forbidden, discord.HTTPException):
        await ctx.reply(
            f"✅ Backup saved on the server (`{path.name}`, {size_kb:.1f} KB, {healthy}), "
            "but I couldn't DM it to you — open your DMs and run the command again.",
            mention_author=False,
        )
        return

    if ctx.guild is not None:
        await ctx.reply("✅ Backup done — sent to your DMs.", mention_author=False)
