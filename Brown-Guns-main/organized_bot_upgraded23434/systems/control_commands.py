# === Control server commands ===
# Source lines 11162-12006 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# PRIVATE CONTROL / DATABASE SERVER COMMANDS
# =========================================================


async def load_control_access() -> None:
    """Load all saved control-access members into memory."""
    CONTROL_ACCESS_USER_IDS.clear()

    rows = await db.fetchall(
        """
        SELECT user_id
        FROM control_access
        WHERE guild_id = ?
        """,
        (CONTROL_GUILD_ID,),
    )

    CONTROL_ACCESS_USER_IDS.update(int(row["user_id"]) for row in rows)
    log.info("Loaded %s control-access member(s).", len(CONTROL_ACCESS_USER_IDS))


def is_bot_owner(member: discord.Member) -> bool:
    return BOT_OWNER_ID > 0 and member.id == BOT_OWNER_ID


@bot.command(name="controlaccess", aliases=["controllaccess"])
async def control_access_command(
    ctx: commands.Context,
    member: discord.Member,
) -> None:
    """Give a control-server member access to every private control command."""
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if ctx.guild.id != CONTROL_GUILD_ID:
        await ctx.reply(
            "❌ Use this command in the private database/control server.",
            mention_author=False,
        )
        return

    if not is_bot_owner(ctx.author):
        await ctx.reply(
            "❌ Only the bot owner can give control access.",
            mention_author=False,
        )
        return

    if member.bot:
        await ctx.reply(
            "❌ Control access cannot be given to a bot account.",
            mention_author=False,
        )
        return

    await db.execute(
        """
        INSERT INTO control_access(guild_id, user_id, granted_by, granted_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            granted_by = excluded.granted_by,
            granted_at = excluded.granted_at
        """,
        (
            CONTROL_GUILD_ID,
            member.id,
            ctx.author.id,
            now_local().isoformat(),
        ),
    )

    CONTROL_ACCESS_USER_IDS.add(member.id)

    await ctx.reply(
        f"✅ {member.mention} now has access to all control commands, "
        "member information, event-panel deployment, and voice controls.",
        mention_author=False,
    )


@bot.command(name="removecontrolaccess", aliases=["revokecontrolaccess"])
async def remove_control_access_command(
    ctx: commands.Context,
    member: discord.Member,
) -> None:
    """Remove a member's saved private control-server access."""
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if ctx.guild.id != CONTROL_GUILD_ID:
        return

    if not is_bot_owner(ctx.author):
        await ctx.reply(
            "❌ Only the bot owner can remove control access.",
            mention_author=False,
        )
        return

    await db.execute(
        """
        DELETE FROM control_access
        WHERE guild_id = ? AND user_id = ?
        """,
        (CONTROL_GUILD_ID, member.id),
    )

    CONTROL_ACCESS_USER_IDS.discard(member.id)

    await ctx.reply(
        f"✅ Control access removed from {member.mention}.",
        mention_author=False,
    )


@bot.command(name="listcontrolaccess")
async def list_control_access_command(ctx: commands.Context) -> None:
    """List members who have persistent control access."""
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if ctx.guild.id != CONTROL_GUILD_ID:
        return

    if not is_bot_owner(ctx.author):
        await ctx.reply(
            "❌ Only the bot owner can view the control-access list.",
            mention_author=False,
        )
        return

    if not CONTROL_ACCESS_USER_IDS:
        await ctx.reply(
            "ℹ️ No members currently have saved control access.",
            mention_author=False,
        )
        return

    mentions = "\n".join(
        f"• <@{user_id}> — `{user_id}`"
        for user_id in sorted(CONTROL_ACCESS_USER_IDS)
    )

    embed = discord.Embed(
        title="🛡️ Control Access Members",
        description=mentions[:4000],
        colour=Theme.PRIMARY,
        timestamp=now_local(),
    )

    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="ui")
async def control_member_ui_command(
    ctx: commands.Context,
    user: str,
) -> None:
    """Show all available information about a main-server member."""
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    data_guild_id = await get_data_guild_id()
    is_static_guild = CONTROL_GUILD_ID > 0 and ctx.guild.id == CONTROL_GUILD_ID
    is_auto_guild = data_guild_id is not None and ctx.guild.id == data_guild_id

    if not is_static_guild and not is_auto_guild:
        return

    if is_static_guild:
        if (
            CONTROL_MEMBER_INFO_CHANNEL_ID > 0
            and ctx.channel.id != CONTROL_MEMBER_INFO_CHANNEL_ID
        ):
            await ctx.reply(
                f"❌ Use this command in <#{CONTROL_MEMBER_INFO_CHANNEL_ID}>.",
                mention_author=False,
            )
            return
    else:
        allowed_ids = {
            cid
            for cid in (
                await get_auto_channel_id(ctx.guild.id, "control_commands"),
                await get_auto_channel_id(ctx.guild.id, "owner_control"),
            )
            if cid is not None
        }
        if allowed_ids and ctx.channel.id not in allowed_ids:
            mentions = " or ".join(f"<#{cid}>" for cid in allowed_ids)
            await ctx.reply(f"❌ Use this command in {mentions}.", mention_author=False)
            return

    if not is_control_admin(ctx.author):
        await ctx.reply(
            "❌ You are not allowed to inspect main-server members.",
            mention_author=False,
        )
        return

    user_id = parse_user_id(user)

    if user_id is None:
        await ctx.reply(
            "❌ Use `!ui USER_ID` or `!ui @member`.",
            mention_author=False,
        )
        return

    member = await get_main_member(bot, user_id)

    if member is None:
        await ctx.reply(
            "❌ That member was not found in the main server.",
            mention_author=False,
        )
        return

    active_warnings = await active_warning_rows(
        member.guild.id,
        member.id,
    )

    tracked_invite = await db.fetchone(
        """
        SELECT inviter_id, invite_code
        FROM member_invites
        WHERE guild_id = ? AND member_id = ?
        """,
        (
            member.guild.id,
            member.id,
        ),
    )

    latest_request = await db.fetchone(
        """
        SELECT ingame_name, ingame_id, invited_by, requested_rank, status
        FROM role_requests
        WHERE guild_id = ? AND requester_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (
            member.guild.id,
            member.id,
        ),
    )

    family_rank = next(
        (
            f"{rank} | {RANK_NAMES.get(rank, 'Unknown Rank')}"
            for rank, role_id in RANK_ROLE_IDS.items()
            if role_id > 0
            and any(role.id == role_id for role in member.roles)
        ),
        "No configured family rank",
    )

    role_mentions = [
        role.mention
        for role in reversed(member.roles)
        if role != member.guild.default_role
    ]
    roles_text = " ".join(role_mentions) if role_mentions else "*No roles*"

    if len(roles_text) > 1000:
        roles_text = roles_text[:997] + "..."

    created_timestamp = int(member.created_at.timestamp())
    joined_timestamp = (
        int(member.joined_at.timestamp())
        if member.joined_at
        else None
    )
    timeout_text = (
        f"Until <t:{int(member.timed_out_until.timestamp())}:F>"
        if member.timed_out_until
        and member.timed_out_until > discord.utils.utcnow()
        else "No"
    )

    embed = discord.Embed(
        title="👤 Main Server Member Information",
        colour=Theme.PRIMARY,
        timestamp=now_local(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(
        name="Discord Account",
        value=(
            f"**Username:** `{member}`\n"
            f"**Display Name:** {member.display_name}\n"
            f"**Nickname:** {member.nick or 'None'}\n"
            f"**User ID:** `{member.id}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Account Created",
        value=f"<t:{created_timestamp}:F>\n<t:{created_timestamp}:R>",
        inline=True,
    )
    embed.add_field(
        name="Joined Main Server",
        value=(
            f"<t:{joined_timestamp}:F>\n<t:{joined_timestamp}:R>"
            if joined_timestamp
            else "Unknown"
        ),
        inline=True,
    )
    embed.add_field(name="Family Rank", value=family_rank, inline=True)
    embed.add_field(
        name="Active Warnings",
        value=f"**{len(active_warnings)}/3**",
        inline=True,
    )
    embed.add_field(name="Timed Out", value=timeout_text, inline=True)
    embed.add_field(name="Bot Account", value="Yes" if member.bot else "No", inline=True)

    if latest_request is not None:
        embed.add_field(
            name="Latest Rank Request",
            value=(
                f"**Name:** {latest_request['ingame_name'] or 'Unknown'}\n"
                f"**Game ID:** {latest_request['ingame_id'] or 'Unknown'}\n"
                f"**Invited By:** {latest_request['invited_by'] or 'Not Provided'}\n"
                f"**Requested Rank:** {latest_request['requested_rank']}\n"
                f"**Status:** {str(latest_request['status']).title()}"
            ),
            inline=False,
        )

    if tracked_invite is not None:
        inviter_id = tracked_invite["inviter_id"]
        inviter_text = (
            f"<@{int(inviter_id)}>"
            if inviter_id
            else "Unknown"
        )
        invite_code_text = (
            f"`{tracked_invite['invite_code']}`"
            if tracked_invite["invite_code"]
            else "Unknown"
        )

        embed.add_field(
            name="Server Invite Information",
            value=(
                f"**Invited By:** {inviter_text}\n"
                f"**Invite Code:** {invite_code_text}"
            ),
            inline=False,
        )

    embed.add_field(
        name=f"Roles ({len(role_mentions)})",
        value=roles_text,
        inline=False,
    )

    await ctx.reply(
        embed=embed,
        mention_author=False,
    )



async def validate_control_command(
    ctx: commands.Context,
) -> discord.Member | None:
    """Validate that a command is used by an approved controller."""
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return None

    data_guild_id = await get_data_guild_id()
    is_static_guild = CONTROL_GUILD_ID > 0 and ctx.guild.id == CONTROL_GUILD_ID
    is_auto_guild = data_guild_id is not None and ctx.guild.id == data_guild_id

    if not is_static_guild and not is_auto_guild:
        return None

    if is_static_guild:
        if (
            CONTROL_COMMAND_CHANNEL_ID > 0
            and ctx.channel.id != CONTROL_COMMAND_CHANNEL_ID
        ):
            await ctx.reply(
                f"❌ Use this command in <#{CONTROL_COMMAND_CHANNEL_ID}>.",
                mention_author=False,
            )
            return None
    else:
        allowed_ids = {
            cid
            for cid in (
                await get_auto_channel_id(ctx.guild.id, "system_control"),
                await get_auto_channel_id(ctx.guild.id, "control_commands"),
            )
            if cid is not None
        }
        if allowed_ids and ctx.channel.id not in allowed_ids:
            mentions = " or ".join(f"<#{cid}>" for cid in allowed_ids)
            await ctx.reply(f"❌ Use this command in {mentions}.", mention_author=False)
            return None

    if not is_control_admin(ctx.author):
        await ctx.reply(
            "❌ You are not allowed to use this control command.",
            mention_author=False,
        )
        return None

    return ctx.author


async def get_main_voice_channel(
    channel_id: int,
) -> discord.VoiceChannel | None:
    channel = bot.get_channel(channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return None

    if not isinstance(channel, discord.VoiceChannel):
        return None

    if channel.guild.id != GUILD_ID:
        return None

    return channel


@bot.command(name="unban")
async def control_unban_command(
    ctx: commands.Context,
    user_id: int,
) -> None:
    controller = await validate_control_command(ctx)

    if controller is None:
        return

    main_guild = bot.get_guild(GUILD_ID)

    if main_guild is None:
        await ctx.reply(
            "❌ The main server is unavailable.",
            mention_author=False,
        )
        return

    try:
        user = await bot.fetch_user(user_id)
        await main_guild.unban(
            user,
            reason=(
                f"Remote unban by {controller} "
                f"({controller.id})"
            ),
        )
    except discord.NotFound:
        await ctx.reply(
            "❌ That account is not banned or the user ID is invalid.",
            mention_author=False,
        )
        return
    except discord.Forbidden:
        await ctx.reply(
            "❌ The bot does not have permission to unban that account.",
            mention_author=False,
        )
        return
    except discord.HTTPException:
        await ctx.reply(
            "❌ Discord rejected the unban request.",
            mention_author=False,
        )
        return

    embed = control_log_embed(
        "🔓 Remote Unban Executed",
        colour=Theme.SUCCESS,
    )
    embed.add_field(
        name="User",
        value=f"`{user}`\n`{user.id}`",
        inline=True,
    )
    embed.add_field(
        name="Unbanned By",
        value=f"{controller.mention}\n`{controller.id}`",
        inline=True,
    )

    await send_control_log(
        bot,
        await auto_log_channel_id("ban_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )

    await ctx.reply(
        f"✅ User `{user_id}` was unbanned from **{main_guild.name}**.",
        mention_author=False,
    )


async def set_voice_mute_for_all(
    ctx: commands.Context,
    voice_channel_id: int,
    *,
    mute: bool,
) -> None:
    controller = await validate_control_command(ctx)

    if controller is None:
        return

    channel = await get_main_voice_channel(voice_channel_id)

    if channel is None:
        await ctx.reply(
            "❌ That voice channel was not found in the main server.",
            mention_author=False,
        )
        return

    changed = 0
    failed = 0

    for member in list(channel.members):
        try:
            await member.edit(
                mute=mute,
                reason=(
                    f"Remote {'mute' if mute else 'unmute'}-all "
                    f"by {controller} ({controller.id})"
                ),
            )
            changed += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

    action = "Muted" if mute else "Unmuted"
    emoji = "🔇" if mute else "🔊"

    embed = control_log_embed(
        f"{emoji} Voice Channel {action}",
        colour=(
            Theme.WARNING
            if mute
            else Theme.SUCCESS
        ),
    )
    embed.add_field(
        name="Voice Channel",
        value=f"{channel.mention}\n`{channel.id}`",
        inline=False,
    )
    embed.add_field(name=action, value=str(changed), inline=True)
    embed.add_field(name="Failed", value=str(failed), inline=True)
    embed.add_field(
        name="Executed By",
        value=f"{controller.mention}\n`{controller.id}`",
        inline=True,
    )

    await send_control_log(
        bot,
        await auto_log_channel_id("voice_mute", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )

    await ctx.reply(
        f"{emoji} {action} **{changed}** member(s) in **{channel.name}**."
        + (f" Failed: **{failed}**." if failed else ""),
        mention_author=False,
    )


@bot.command(name="muteall")
async def control_muteall_command(
    ctx: commands.Context,
    voice_channel_id: int,
) -> None:
    await set_voice_mute_for_all(
        ctx,
        voice_channel_id,
        mute=True,
    )


@bot.command(name="unmuteall")
async def control_unmuteall_command(
    ctx: commands.Context,
    voice_channel_id: int,
) -> None:
    await set_voice_mute_for_all(
        ctx,
        voice_channel_id,
        mute=False,
    )



@bot.command(name="ban")
async def control_ban_command(
    ctx: commands.Context,
    user_id: int,
    *,
    reason: str = "Banned remotely from the private control server",
) -> None:
    """Ban a Discord account from the main server by user ID."""
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if ctx.guild.id != CONTROL_GUILD_ID:
        return

    if (
        CONTROL_COMMAND_CHANNEL_ID > 0
        and ctx.channel.id != CONTROL_COMMAND_CHANNEL_ID
    ):
        await ctx.reply(
            f"❌ Use this command in <#{CONTROL_COMMAND_CHANNEL_ID}>.",
            mention_author=False,
        )
        return

    if not is_control_admin(ctx.author):
        await ctx.reply(
            "❌ You are not allowed to ban main-server members.",
            mention_author=False,
        )
        return

    main_guild = bot.get_guild(GUILD_ID)

    if main_guild is None:
        await ctx.reply(
            "❌ The main server is unavailable.",
            mention_author=False,
        )
        return

    try:
        await main_guild.ban(
            discord.Object(id=user_id),
            reason=(
                f"{reason} | Remote moderator: "
                f"{ctx.author} ({ctx.author.id})"
            ),
            delete_message_seconds=0,
        )
    except discord.Forbidden:
        await ctx.reply(
            "❌ The bot does not have permission to ban that account.",
            mention_author=False,
        )
        return
    except discord.NotFound:
        await ctx.reply(
            "❌ Discord could not find that user ID.",
            mention_author=False,
        )
        return
    except discord.HTTPException:
        await ctx.reply(
            "❌ Discord rejected the ban request.",
            mention_author=False,
        )
        return

    embed = control_log_embed(
        "🔨 Remote Ban Executed",
        colour=Theme.DANGER,
    )
    embed.add_field(name="User ID", value=f"`{user_id}`", inline=True)
    embed.add_field(
        name="Banned By",
        value=f"{ctx.author.mention}\n`{ctx.author.id}`",
        inline=True,
    )
    embed.add_field(name="Reason", value=reason[:1024], inline=False)

    await send_control_log(
        bot,
        await auto_log_channel_id("ban_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )

    await ctx.reply(
        f"✅ User `{user_id}` was banned from **{main_guild.name}**.",
        mention_author=False,
    )


@bot.command(name="serverstatus")
async def control_server_status_command(
    ctx: commands.Context,
) -> None:
    controller = await validate_control_command(ctx)

    if controller is None:
        return

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        await ctx.reply("❌ The main server is unavailable.", mention_author=False)
        return

    members = list(guild.members)
    bots = sum(1 for member in members if member.bot)
    humans = len(members) - bots
    voice_users = sum(len(channel.members) for channel in guild.voice_channels)
    timed_out = sum(
        1
        for member in members
        if member.timed_out_until
        and member.timed_out_until > discord.utils.utcnow()
    )

    try:
        banned = [entry async for entry in guild.bans(limit=None)]
        ban_count = len(banned)
    except (discord.Forbidden, discord.HTTPException):
        ban_count = None

    embed = discord.Embed(
        title="📊 Main Server Status",
        colour=Theme.PRIMARY,
        timestamp=now_local(),
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(name="Server", value=f"**{guild.name}**\n`{guild.id}`", inline=False)
    embed.add_field(name="Total Members", value=str(guild.member_count or len(members)), inline=True)
    embed.add_field(name="Humans", value=str(humans), inline=True)
    embed.add_field(name="Bots", value=str(bots), inline=True)
    embed.add_field(name="Voice Users", value=str(voice_users), inline=True)
    embed.add_field(name="Timed Out", value=str(timed_out), inline=True)
    embed.add_field(
        name="Banned Accounts",
        value=str(ban_count) if ban_count is not None else "No permission",
        inline=True,
    )
    embed.add_field(name="Text Channels", value=str(len(guild.text_channels)), inline=True)
    embed.add_field(name="Voice Channels", value=str(len(guild.voice_channels)), inline=True)
    embed.add_field(name="Roles", value=str(max(len(guild.roles) - 1, 0)), inline=True)
    embed.add_field(name="Bot Latency", value=f"{round(bot.latency * 1000)} ms", inline=True)
    embed.add_field(name="Connection", value="🟢 Online", inline=True)

    await ctx.reply(embed=embed, mention_author=False)

    await log_staff_action(
        action="Server Status Viewed",
        controller=controller,
        target=f"{guild.name} ({guild.id})",
        result="Status information displayed.",
    )


@bot.command(name="announce")
async def control_announce_command(
    ctx: commands.Context,
    channel_id: int,
    *,
    message: str,
) -> None:
    controller = await validate_control_command(ctx)

    if controller is None:
        return

    channel = await get_text_channel(bot, channel_id)

    if channel is None or channel.guild.id != GUILD_ID:
        await ctx.reply(
            "❌ That text channel was not found in the main server.",
            mention_author=False,
        )
        return

    if len(message) > 4000:
        await ctx.reply(
            "❌ Announcement must be 4,000 characters or fewer.",
            mention_author=False,
        )
        return

    embed = discord.Embed(
        title="📢 Family Announcement",
        description=message,
        colour=Theme.WARNING,
        timestamp=now_local(),
    )
    embed.set_footer(text=f"Posted by {controller.display_name}")

    if channel.guild.icon:
        embed.set_author(
            name=channel.guild.name,
            icon_url=channel.guild.icon.url,
        )

    try:
        posted = await channel.send(embed=embed)
    except discord.Forbidden:
        await ctx.reply(
            "❌ I cannot send messages in that main-server channel.",
            mention_author=False,
        )
        return
    except discord.HTTPException:
        await ctx.reply(
            "❌ Discord rejected the announcement.",
            mention_author=False,
        )
        return

    await ctx.reply(
        f"✅ Announcement posted in {channel.mention}.",
        mention_author=False,
    )

    await log_staff_action(
        action="Remote Announcement",
        controller=controller,
        target=f"{channel.name} ({channel.id})",
        result=f"Posted successfully. Message ID: {posted.id}",
        reason=message,
    )


