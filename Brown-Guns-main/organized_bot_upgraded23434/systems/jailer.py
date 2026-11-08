# === Jailer role ===
# Loaded into shared namespace by bot.py — do not run this file alone.
#
#   !jailer @role   -> set the Jailer role   (high command / admin / bot owner)
#   !jailer         -> show the current Jailer role
#   !jailer off     -> remove it (falls back to high command until a new role is set)
#
# Only members with this role can: give punishments (!punish, !remove, Remove Strike),
# review punishment appeals, review complaints and review POV requests.
# The role is saved in the database, so it survives restarts.

JAILER_SETTING_KEY = "jailer_role_id"


async def load_jailer_role() -> None:
    """Called once at startup. The saved role overrides the JAILER_ROLE_ID env value."""
    global JAILER_ROLE_ID
    saved = await get_setting(JAILER_SETTING_KEY)
    if saved and saved.isdigit():
        JAILER_ROLE_ID = int(saved)
    log.info("Jailer role: %s", JAILER_ROLE_ID or "not set (high command is used)")


@bot.command(name="jailer")
async def jailer_command(ctx: commands.Context, *, value: str = "") -> None:
    global JAILER_ROLE_ID

    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    quiet = discord.AllowedMentions.none()
    allowed_to_set = ctx.author.id == BOT_OWNER_ID or is_high_command(ctx.author)
    allowed_to_view = allowed_to_set or is_jailer(ctx.author)

    if not allowed_to_view:
        await ctx.reply("❌ Only high command can manage the Jailer role.", mention_author=False)
        return

    value = value.strip()

    # ---- show
    if not value:
        role = ctx.guild.get_role(JAILER_ROLE_ID) if JAILER_ROLE_ID else None
        if role is None:
            text = (
                "ℹ️ No Jailer role is set yet — until then **high command** can punish and review.\n"
                "Set it with `!jailer @role`."
            )
        else:
            text = (
                f"🛡️ Jailer role: {role.mention}\n"
                "Only this role can punish, remove strikes and review complaints, "
                "appeals and POV requests."
            )
        await ctx.reply(text, mention_author=False, allowed_mentions=quiet)
        return

    if not allowed_to_set:
        await ctx.reply("❌ Only high command can change the Jailer role.", mention_author=False)
        return

    # ---- clear
    if value.lower() in {"off", "none", "remove", "clear", "reset"}:
        JAILER_ROLE_ID = 0
        await delete_setting(JAILER_SETTING_KEY)
        await ctx.reply(
            "✅ Jailer role removed. High command can punish and review again until you set a new one.",
            mention_author=False,
        )
        return

    # ---- set
    try:
        role = await commands.RoleConverter().convert(ctx, value)
    except commands.BadArgument:
        await ctx.reply(
            "❌ I can't find that role. Use `!jailer @role`, the role name, or the role ID.",
            mention_author=False,
        )
        return

    if role.is_default():
        await ctx.reply("❌ `@everyone` can't be the Jailer role.", mention_author=False)
        return

    JAILER_ROLE_ID = role.id
    await set_setting(JAILER_SETTING_KEY, str(role.id))
    await ctx.reply(
        f"✅ Jailer role set to {role.mention}.\n"
        "Only members with this role can now punish, remove strikes and review "
        "complaints, appeals and POV requests.",
        mention_author=False,
        allowed_mentions=quiet,
    )
