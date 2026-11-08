# === LOL VC dragger ===
# Source lines 12520-12611 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# LOL — CONTINUOUS VC DRAGGER
# =========================================================

# ---------------------------------------------------------
# ACCESS CONTROL — only the bot owner (BOT_OWNER_ID) can give access with
# !lolaccess @member. Only the owner and members with access can use !lol.
# ---------------------------------------------------------

def initialize_lol_access_schema() -> None:
    with db.connect() as database:
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS lol_access (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                granted_by INTEGER NOT NULL,
                granted_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, user_id)
            )
            """
        )
        database.commit()


initialize_lol_access_schema()


def is_lol_owner(user_id: int) -> bool:
    return BOT_OWNER_ID > 0 and user_id == BOT_OWNER_ID


async def has_lol_access(guild_id: int, user_id: int) -> bool:
    if is_lol_owner(user_id):
        return True
    row = await db.fetchone(
        "SELECT 1 FROM lol_access WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    return row is not None


async def lol_access_check(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        return False
    if await has_lol_access(ctx.guild.id, ctx.author.id):
        return True
    raise commands.CheckFailure("lol access required")


async def _drag_loop(member: discord.Member) -> None:
    """Continuously move a member through every VC in the server."""
    guild = member.guild
    while True:
        if guild.id not in drag_tasks or member.id not in drag_tasks[guild.id]:
            return

        voice_channels = [
            ch for ch in guild.channels if isinstance(ch, discord.VoiceChannel)
        ]

        if not voice_channels:
            await asyncio.sleep(0)
            continue

        try:
            member = await guild.fetch_member(member.id)
        except discord.NotFound:
            drag_tasks.get(guild.id, {}).pop(member.id, None)
            return

        for vc in voice_channels:
            if guild.id not in drag_tasks or member.id not in drag_tasks[guild.id]:
                return
            try:
                await member.move_to(vc)
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.1)


@bot.command(name="lol")
@commands.check(lol_access_check)
async def lol_command(
    ctx: commands.Context,
    member: discord.Member,
) -> None:
    """Toggle continuous VC dragging for a member. Run again to stop."""
    if ctx.guild is None:
        return

    guild_id = ctx.guild.id
    user_id = member.id

    if guild_id not in drag_tasks:
        drag_tasks[guild_id] = {}

    if user_id in drag_tasks[guild_id]:
        task = drag_tasks[guild_id].pop(user_id)
        task.cancel()
        await ctx.reply(
            f"✅ Stopped dragging {member.mention}.",
            mention_author=False,
        )
    else:
        task = asyncio.create_task(_drag_loop(member))
        drag_tasks[guild_id][user_id] = task
        await ctx.reply(
            f"🔄 Now dragging {member.mention} through every VC! "
            f"Run `!lol {member.mention}` again to stop.",
            mention_author=False,
        )


@lol_command.error
async def lol_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    if isinstance(error, commands.CheckFailure):
        await ctx.reply(
            "❌ You don't have access to `!lol`. "
            "Only the bot owner can give access with `!lolaccess @member`.",
            mention_author=False,
        )
    elif isinstance(error, commands.MemberNotFound):
        await ctx.reply(
            "❌ Member not found. Mention them with @.",
            mention_author=False,
        )
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(
            "❌ Usage: `!lol @member`",
            mention_author=False,
        )
    else:
        raise error


@bot.command(name="lolaccess")
async def lolaccess_command(
    ctx: commands.Context,
    member: discord.Member,
) -> None:
    """Bot owner only: give or remove !lol access. Run again to remove."""
    if ctx.guild is None:
        return

    if not is_lol_owner(ctx.author.id):
        await ctx.reply(
            "❌ Only the bot owner can use `!lolaccess`.",
            mention_author=False,
        )
        return

    if member.bot:
        await ctx.reply("❌ You can't give access to a bot.", mention_author=False)
        return

    if is_lol_owner(member.id):
        await ctx.reply(
            "ℹ️ The bot owner always has access to `!lol`.",
            mention_author=False,
        )
        return

    existing = await db.fetchone(
        "SELECT 1 FROM lol_access WHERE guild_id = ? AND user_id = ?",
        (ctx.guild.id, member.id),
    )

    if existing is not None:
        await db.execute(
            "DELETE FROM lol_access WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, member.id),
        )
        await ctx.reply(
            f"🚫 Removed `!lol` access from {member.mention}.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    else:
        await db.execute(
            "INSERT OR REPLACE INTO lol_access(guild_id, user_id, granted_by, granted_at) "
            "VALUES (?, ?, ?, ?)",
            (ctx.guild.id, member.id, ctx.author.id, now_local().isoformat()),
        )
        await ctx.reply(
            f"✅ {member.mention} can now use `!lol @member`.\n"
            f"Run `!lolaccess {member.mention}` again to remove their access.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
