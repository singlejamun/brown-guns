# === Error handler ===
# Source lines 12612-12697 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# COMMAND ERROR HANDLER
# =========================================================

@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    if isinstance(
        error,
        commands.CommandNotFound,
    ):
        return

    # Commands with their own error handler (e.g. !lol) already told the user
    # why the check failed — don't also say "something went wrong".
    if (
        isinstance(error, commands.CheckFailure)
        and ctx.command is not None
        and ctx.command.has_error_handler()
    ):
        return

    if isinstance(
        error,
        commands.MemberNotFound,
    ):
        await ctx.reply(
            "❌ Member not found. Mention the member correctly.",
            mention_author=False,
        )
        return

    if isinstance(
        error,
        commands.MissingRequiredArgument,
    ):
        command_name = (
            ctx.command.qualified_name
            if ctx.command
            else "command"
        )

        await ctx.reply(
            f"❌ Missing information for `!{command_name}`.\n"
            "Use `!help` to see the correct format.",
            mention_author=False,
        )
        return

    if isinstance(
        error,
        commands.BadArgument,
    ):
        await ctx.reply(
            "❌ Invalid command information. "
            "Check the member mention or number.",
            mention_author=False,
        )
        return

    if isinstance(
        error,
        commands.CommandOnCooldown,
    ):
        await ctx.reply(
            f"⏳ Try again in **{error.retry_after:.1f} seconds**.",
            mention_author=False,
        )
        return

    original_error = getattr(
        error,
        "original",
        error,
    )

    log.error(
        "Unhandled command error in %s: %r",
        ctx.command,
        original_error,
        exc_info=(
            type(original_error),
            original_error,
            original_error.__traceback__,
        ),
    )

    await ctx.reply(
        "❌ Something went wrong while running that command.",
        mention_author=False,
    )


