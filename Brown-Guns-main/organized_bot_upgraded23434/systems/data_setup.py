# === Database auto-setup + events ===
# Source lines 10037-11161 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# PART EXTRA — DATABASE SERVER AUTO SETUP SYSTEM
# -----------------------------------------------------------------------
# Fully additive. Nothing above this section is changed by this block.
#
# Run "!startdata" once inside a brand new Discord server and the bot
# builds every category/channel it needs, wires itself up to save all
# the created channel IDs into SQLite (no .env editing required), posts
# the Voice Panel + Control Panel automatically, and self-heals any of
# those channels if they are ever deleted.
#
# A handful of the busiest existing log call-sites (message edits/
# deletes, member join/leave/update, bans, voice state, role/channel
# changes, staff actions, rank review, complaints) now resolve their
# destination through `auto_log_channel_id(key, fallback)`, which
# transparently prefers the new auto-created channel for that key and
# falls back to the exact same static channel ID that was already being
# used before — so behaviour for anyone who hasn't run !startdata is
# completely unchanged.
# =========================================================

DATA_SETUP_SPEC: dict[str, list[tuple[str, str]]] = {
    "📁 DATABASE": [
        ("member_logs", "member-logs"),
        ("register_logs", "register-logs"),
        ("rank_review_logs", "rank-review-logs"),
        ("complaint_logs", "complaint-logs"),
        ("punishment_logs", "punishment-logs"),
        ("family_logs_db", "family-logs"),
        ("attendance_logs", "attendance-logs"),
        ("state_control_logs", "state-control-logs"),
        ("rp_ticket_logs", "rp-ticket-logs"),
        ("system_logs", "system-logs"),
        ("bot_errors", "bot-errors"),
    ],
    "📁 MODERATION": [
        ("kick_logs", "kick-logs"),
        ("ban_logs", "ban-logs"),
        ("timeout_logs", "timeout-logs"),
        ("warn_logs", "warn-logs"),
        ("unwarn_logs", "unwarn-logs"),
        ("nickname_logs", "nickname-logs"),
        ("role_update_logs", "role-update-logs"),
        ("message_delete_logs", "message-delete-logs"),
        ("message_edit_logs", "message-edit-logs"),
        ("channel_logs", "channel-logs"),
    ],
    "📁 VOICE": [
        ("voice_logs", "voice-logs"),
        ("voice_panel", "voice-panel"),
        ("voice_join", "voice-join"),
        ("voice_leave", "voice-leave"),
        ("voice_move", "voice-move"),
        ("voice_mute", "voice-mute"),
        ("voice_deaf", "voice-deaf"),
        ("voice_stream", "voice-stream"),
        ("voice_camera", "voice-camera"),
        ("voice_disconnect", "voice-disconnect"),
    ],
    "📁 FAMILY": [
        ("hc_logs", "hc-logs"),
        ("event_logs", "event-logs"),
        ("rp_reminder_db", "rp-reminder"),
        ("family_announcements", "family-announcements"),
    ],
    "📁 CONTROL": [
        ("control_commands", "control-commands"),
        ("system_control", "system-control"),
        ("owner_control", "owner-control"),
    ],
}

_data_guild_id_cache: int | None = None


async def get_data_guild_id() -> int | None:
    """The guild ID of the server !startdata was last run in."""
    global _data_guild_id_cache
    if _data_guild_id_cache is not None:
        return _data_guild_id_cache
    value = await get_setting("data_guild_id")
    if value and value.isdigit():
        _data_guild_id_cache = int(value)
    return _data_guild_id_cache


async def set_data_guild_id(guild_id: int) -> None:
    global _data_guild_id_cache
    _data_guild_id_cache = guild_id
    await set_setting("data_guild_id", str(guild_id))


async def get_auto_channel_id(guild_id: int, key: str) -> int | None:
    row = await db.fetchone(
        "SELECT channel_id FROM auto_data_channels WHERE guild_id = ? AND key = ?",
        (guild_id, key),
    )
    return int(row["channel_id"]) if row else None


async def set_auto_channel_id(guild_id: int, key: str, channel_id: int) -> None:
    await db.execute(
        """
        INSERT INTO auto_data_channels(guild_id, key, channel_id, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, key) DO UPDATE SET channel_id = excluded.channel_id
        """,
        (guild_id, key, channel_id, now_local().isoformat()),
    )


async def get_auto_category_id(guild_id: int, key: str) -> int | None:
    row = await db.fetchone(
        "SELECT category_id FROM auto_data_categories WHERE guild_id = ? AND key = ?",
        (guild_id, key),
    )
    return int(row["category_id"]) if row else None


async def set_auto_category_id(guild_id: int, key: str, category_id: int) -> None:
    await db.execute(
        """
        INSERT INTO auto_data_categories(guild_id, key, category_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, key) DO UPDATE SET category_id = excluded.category_id
        """,
        (guild_id, key, category_id),
    )


async def auto_log_channel_id(key: str, fallback: int = 0) -> int:
    """Resolve a log channel: prefer the auto-created database-server
    channel for `key`; fall back to whatever static ID was already
    being used before this system existed."""
    guild_id = await get_data_guild_id()
    if guild_id is not None:
        channel_id = await get_auto_channel_id(guild_id, key)
        if channel_id:
            return channel_id
    return fallback


async def _auto_resolve_category(
    guild: discord.Guild,
    category_name: str,
) -> discord.CategoryChannel:
    stored_id = await get_auto_category_id(guild.id, category_name)
    if stored_id:
        existing = guild.get_channel(stored_id)
        if isinstance(existing, discord.CategoryChannel):
            return existing

    for category in guild.categories:
        if category.name == category_name:
            await set_auto_category_id(guild.id, category_name, category.id)
            return category

    category = await guild.create_category(category_name, reason="Database auto setup")
    await set_auto_category_id(guild.id, category_name, category.id)
    return category


async def _auto_resolve_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    key: str,
    name: str,
) -> tuple[discord.TextChannel, bool]:
    """Returns (channel, created_now)."""
    stored_id = await get_auto_channel_id(guild.id, key)
    if stored_id:
        existing = guild.get_channel(stored_id)
        if isinstance(existing, discord.TextChannel):
            return existing, False

    for channel in category.text_channels:
        if channel.name == name:
            await set_auto_channel_id(guild.id, key, channel.id)
            return channel, False

    channel = await guild.create_text_channel(
        name,
        category=category,
        reason="Database auto setup",
    )
    await set_auto_channel_id(guild.id, key, channel.id)
    return channel, True


async def run_database_auto_setup(
    guild: discord.Guild,
    *,
    only_missing: bool = False,
) -> dict[str, list[str]]:
    """Creates every category/channel from DATA_SETUP_SPEC that does not
    already exist. Safe to call repeatedly — never creates duplicates."""
    created: list[str] = []
    existing: list[str] = []

    for category_name, channels in DATA_SETUP_SPEC.items():
        category = await _auto_resolve_category(guild, category_name)

        for key, name in channels:
            channel, was_created = await _auto_resolve_channel(guild, category, key, name)

            if was_created:
                created.append(channel.name)
                if key == "voice_panel":
                    await post_auto_voice_panel(channel)
                if key == "control_commands":
                    await post_auto_control_panel(channel)
            else:
                existing.append(channel.name)

    await set_data_guild_id(guild.id)
    return {"created": created, "existing": existing}


async def handle_auto_channel_deleted(channel: discord.abc.GuildChannel) -> None:
    """Self-healing: if a tracked auto-setup channel gets deleted,
    silently recreate it and update SQLite."""
    guild_id = await get_data_guild_id()
    if guild_id is None or channel.guild.id != guild_id:
        return

    row = await db.fetchone(
        "SELECT key FROM auto_data_channels WHERE guild_id = ? AND channel_id = ?",
        (guild_id, channel.id),
    )
    if row is None:
        return

    key = str(row["key"])
    name = None
    category_name = None
    for cat_name, channels in DATA_SETUP_SPEC.items():
        for chan_key, chan_name in channels:
            if chan_key == key:
                name = chan_name
                category_name = cat_name
                break
        if name is not None:
            break

    if name is None or category_name is None:
        return

    guild = channel.guild
    try:
        category = await _auto_resolve_category(guild, category_name)
        new_channel, was_created = await _auto_resolve_channel(guild, category, key, name)
    except discord.HTTPException:
        log.exception("Self-healing failed to recreate #%s", name)
        return

    if not was_created:
        return

    log.info("Self-healing: recreated deleted database channel #%s", name)

    if key == "voice_panel":
        await post_auto_voice_panel(new_channel)
    if key == "control_commands":
        await post_auto_control_panel(new_channel)

    notify_key = "system_logs" if key != "system_logs" else "bot_errors"
    notify_id = await auto_log_channel_id(notify_key)
    if notify_id:
        await send_control_log(
            bot,
            notify_id,
            control_log_embed(
                "♻️ Auto-Healed Channel",
                description=f"`{name}` was deleted and has been automatically recreated.",
                colour=Theme.WARNING,
            ),
        )


class DataControlPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Check Data",
        emoji="📊",
        style=discord.ButtonStyle.primary,
        custom_id="auto_data_check",
    )
    async def check_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_control_admin(interaction.user):
            await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
            return

        if interaction.guild is None:
            return

        embed = await build_data_status_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Recreate Missing",
        emoji="♻️",
        style=discord.ButtonStyle.success,
        custom_id="auto_data_recreate",
    )
    async def recreate_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_control_admin(interaction.user):
            await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
            return

        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await run_database_auto_setup(interaction.guild, only_missing=True)

        if result["created"]:
            await interaction.followup.send(
                "✅ Recreated: " + ", ".join(f"`{n}`" for n in result["created"][:25]),
                ephemeral=True,
            )
        else:
            await interaction.followup.send("✅ Nothing was missing.", ephemeral=True)


def data_control_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛠️ Database Control Panel",
        description=(
            "Use the buttons below, or run `!checkdata`, `!recreatedata`, "
            "or `!startdata` directly in this server."
        ),
        colour=Theme.PRIMARY,
    )
    embed.set_footer(text="Auto Database Setup System")
    return embed


async def build_data_status_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Database Server Status",
        colour=Theme.PRIMARY,
        timestamp=now_local(),
    )
    all_ok = True

    for category_name, channels in DATA_SETUP_SPEC.items():
        lines = []
        for key, name in channels:
            channel_id = await get_auto_channel_id(guild.id, key)
            found = channel_id is not None and guild.get_channel(channel_id) is not None
            all_ok = all_ok and found
            lines.append(f"{'✅' if found else '❌'} {name}")
        embed.add_field(name=category_name, value="\n".join(lines), inline=True)

    embed.set_footer(
        text="Everything Working" if all_ok else "Run !recreatedata to fix missing channels"
    )
    return embed


async def post_auto_control_panel(channel: discord.TextChannel) -> None:
    try:
        async for message in channel.history(limit=50):
            if (
                bot.user is not None
                and message.author.id == bot.user.id
                and message.embeds
                and message.embeds[0].title == "🛠️ Database Control Panel"
            ):
                return

        await channel.send(embed=data_control_panel_embed(), view=DataControlPanelView())
    except discord.HTTPException:
        log.exception("Could not post the auto control panel.")


async def post_auto_voice_panel(channel: discord.TextChannel) -> None:
    try:
        async for message in channel.history(limit=50):
            if (
                bot.user is not None
                and message.author.id == bot.user.id
                and message.embeds
                and message.embeds[0].title == "🎙️ Voice Control Panel"
            ):
                return

        await channel.send(embed=voice_control_panel_embed(), view=VoiceControlPanelView())
    except discord.HTTPException:
        log.exception("Could not post the auto voice panel.")


@bot.command(name="startdata")
async def start_data_command(ctx: commands.Context) -> None:
    """Fully auto-builds the Database Server in the server this is run in."""
    if not isinstance(ctx.author, discord.Member) or not is_bot_owner(ctx.author):
        await ctx.reply("❌ Only the bot owner can run this command.", mention_author=False)
        return

    if ctx.guild is None:
        return

    status = await ctx.reply("⚙️ Setting up the database server...", mention_author=False)

    try:
        result = await run_database_auto_setup(ctx.guild)
    except discord.Forbidden:
        await status.edit(content="❌ I need the **Manage Channels** permission in this server.")
        return
    except discord.HTTPException:
        log.exception("!startdata failed")
        await status.edit(content="❌ Discord rejected part of the setup. Try `!recreatedata`.")
        return

    embed = discord.Embed(
        title="✅ Database Server Ready",
        colour=Theme.SUCCESS,
        timestamp=now_local(),
    )
    embed.add_field(
        name=f"Created ({len(result['created'])})",
        value=("\n".join(f"#{name}" for name in result["created"][:25]) or "*Nothing new*")[:1024],
        inline=False,
    )
    embed.add_field(
        name="Already Existed",
        value=str(len(result["existing"])),
        inline=False,
    )
    embed.set_footer(text="Every connected system now logs into this server automatically.")

    await status.edit(content=None, embed=embed)


@bot.command(name="checkdata")
async def check_data_command(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member) or not is_control_admin(ctx.author):
        await ctx.reply("❌ You are not allowed to run this command.", mention_author=False)
        return

    if ctx.guild is None:
        return

    embed = await build_data_status_embed(ctx.guild)
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="recreatedata")
async def recreate_data_command(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member) or not is_control_admin(ctx.author):
        await ctx.reply("❌ You are not allowed to run this command.", mention_author=False)
        return

    if ctx.guild is None:
        return

    status = await ctx.reply("♻️ Checking for missing categories/channels...", mention_author=False)

    try:
        result = await run_database_auto_setup(ctx.guild, only_missing=True)
    except discord.HTTPException:
        log.exception("!recreatedata failed")
        await status.edit(content="❌ Discord rejected part of the recreation. Try again shortly.")
        return

    if result["created"]:
        await status.edit(
            content=(
                f"✅ Recreated {len(result['created'])} missing channel(s): "
                + ", ".join(f"`{n}`" for n in result["created"][:25])
            )
        )
    else:
        await status.edit(content="✅ Nothing was missing. Everything is already set up.")



@bot.event
async def on_ready() -> None:
    log.info("=" * 55)
    log.info("Grand RP Family Management Bot is online")
    log.info(
        "Logged in as %s (%s)",
        bot.user,
        bot.user.id if bot.user else "unknown",
    )
    log.info("Informal registration: 10 main + 5 waiting")
    log.info("Role request system: active")
    log.info("Punishment system: active")
    await refresh_main_invite_cache()
    log.info("Invite tracking: active")
    log.info("=" * 55)


@bot.event
async def on_message_delete(message: discord.Message) -> None:
    if message.guild is None or message.guild.id != GUILD_ID:
        return

    if message.author.bot:
        return

    embed = control_log_embed(
        "🗑️ Message Deleted",
        colour=Theme.DANGER,
    )
    embed.add_field(
        name="Member",
        value=f"{message.author.mention}\n`{message.author.id}`",
        inline=True,
    )
    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
    embed.add_field(
        name="Content",
        value=(message.content or "*No text content*")[:1024],
        inline=False,
    )

    if message.attachments:
        embed.add_field(
            name="Attachments",
            value="\n".join(
                attachment.url
                for attachment in message.attachments[:5]
            )[:1024],
            inline=False,
        )

    await send_control_log(
        bot,
        await auto_log_channel_id("message_delete_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )


@bot.event
async def on_message_edit(
    before: discord.Message,
    after: discord.Message,
) -> None:
    if (
        before.guild is None
        or before.guild.id != GUILD_ID
        or before.author.bot
        or before.content == after.content
    ):
        return

    embed = control_log_embed(
        "✏️ Message Edited",
        colour=Theme.WARNING,
    )
    embed.add_field(
        name="Member",
        value=f"{before.author.mention}\n`{before.author.id}`",
        inline=True,
    )
    embed.add_field(name="Channel", value=before.channel.mention, inline=True)
    embed.add_field(name="Before", value=(before.content or "*Empty*")[:1024], inline=False)
    embed.add_field(name="After", value=(after.content or "*Empty*")[:1024], inline=False)
    embed.add_field(name="Message", value=f"[Open message]({after.jump_url})", inline=False)

    await send_control_log(
        bot,
        await auto_log_channel_id("message_edit_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )


@bot.event
async def on_member_join(member: discord.Member) -> None:
    if member.guild.id != GUILD_ID:
        return

    inviter, invite_code = await detect_member_inviter(member)
    await save_member_inviter(member, inviter, invite_code)
    await send_welcome_message(member)

    account_age_days = max(
        (discord.utils.utcnow() - member.created_at).days,
        0,
    )
    suspicious = account_age_days <= ALT_ACCOUNT_MAX_DAYS

    embed = control_log_embed(
        "⚠️ Possible Alt Account Joined"
        if suspicious
        else "📥 Member Joined",
        colour=Theme.DANGER
        if suspicious
        else Theme.SUCCESS,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(
        name="Member",
        value=f"{member.mention}\n`{member}`\n`{member.id}`",
        inline=False,
    )
    embed.add_field(
        name="Account Created",
        value=(
            f"<t:{int(member.created_at.timestamp())}:F>\n"
            f"Age: **{account_age_days} day(s)**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Member Count",
        value=str(member.guild.member_count),
        inline=True,
    )
    embed.add_field(
        name="Invited By",
        value=(
            f"{inviter.mention}\n`{inviter.id}`"
            if inviter is not None
            else "*Could not detect*"
        ),
        inline=True,
    )
    embed.add_field(
        name="Invite Code",
        value=f"`{invite_code}`" if invite_code else "*Unknown*",
        inline=True,
    )

    if suspicious:
        embed.add_field(
            name="Anti-Alt Warning",
            value=(
                f"Account age is at or below the configured "
                f"**{ALT_ACCOUNT_MAX_DAYS}-day** threshold.\n"
                "This is a warning only, not proof of ban evasion."
            ),
            inline=False,
        )

    await send_control_log(
        bot,
        await auto_log_channel_id("member_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )


@bot.event
async def on_invite_create(invite: discord.Invite) -> None:
    if invite.guild is not None and invite.guild.id == GUILD_ID:
        MAIN_INVITE_USES[invite.code] = int(invite.uses or 0)


@bot.event
async def on_invite_delete(invite: discord.Invite) -> None:
    if invite.guild is not None and invite.guild.id == GUILD_ID:
        MAIN_INVITE_USES.pop(invite.code, None)


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    if member.guild.id != GUILD_ID:
        return

    actor, reason = await find_audit_actor(
        member.guild,
        discord.AuditLogAction.kick,
        member.id,
    )

    embed = control_log_embed(
        "👢 Member Kicked" if actor else "📤 Member Left",
        colour=Theme.DANGER,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(
        name="Member",
        value=f"`{member}`\n`{member.id}`",
        inline=False,
    )

    if actor:
        embed.add_field(
            name="Moderator",
            value=f"{actor.mention}\n`{actor.id}`",
            inline=True,
        )

    if reason:
        embed.add_field(name="Reason", value=reason[:1024], inline=False)

    await send_control_log(
        bot,
        await auto_log_channel_id(
            "kick_logs" if actor else "member_logs",
            CONTROL_SERVER_LOG_CHANNEL_ID,
        ),
        embed,
    )


@bot.event
async def on_member_update(
    before: discord.Member,
    after: discord.Member,
) -> None:
    if before.guild.id != GUILD_ID:
        return

    before_roles = {role.id: role for role in before.roles}
    after_roles = {role.id: role for role in after.roles}

    added = [
        role
        for role_id, role in after_roles.items()
        if role_id not in before_roles
        and role != after.guild.default_role
    ]
    removed = [
        role
        for role_id, role in before_roles.items()
        if role_id not in after_roles
        and role != after.guild.default_role
    ]

    if added or removed:
        embed = control_log_embed("🎭 Member Roles Updated")
        embed.add_field(
            name="Member",
            value=f"{after.mention}\n`{after.id}`",
            inline=False,
        )

        if added:
            embed.add_field(
                name="Roles Added",
                value=" ".join(role.mention for role in added)[:1024],
                inline=False,
            )

        if removed:
            embed.add_field(
                name="Roles Removed",
                value=" ".join(role.mention for role in removed)[:1024],
                inline=False,
            )

        actor, reason = await find_audit_actor(
            after.guild,
            discord.AuditLogAction.member_role_update,
            after.id,
        )

        if actor:
            embed.add_field(
                name="Changed By",
                value=f"{actor.mention}\n`{actor.id}`",
                inline=True,
            )

        if reason:
            embed.add_field(name="Reason", value=reason[:1024], inline=False)

        await send_control_log(
            bot,
            await auto_log_channel_id("role_update_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
            embed,
        )

    if before.nick != after.nick:
        embed = control_log_embed("📝 Nickname Updated")
        embed.add_field(
            name="Member",
            value=f"{after.mention}\n`{after.id}`",
            inline=False,
        )
        embed.add_field(name="Before", value=before.nick or "*No nickname*", inline=True)
        embed.add_field(name="After", value=after.nick or "*No nickname*", inline=True)

        await send_control_log(
            bot,
            await auto_log_channel_id("nickname_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
            embed,
        )

    if before.timed_out_until != after.timed_out_until:
        timed_out = bool(
            after.timed_out_until
            and after.timed_out_until > discord.utils.utcnow()
        )

        embed = control_log_embed(
            "🔇 Member Timed Out" if timed_out else "🔊 Timeout Removed",
            colour=Theme.DANGER if timed_out else Theme.SUCCESS,
        )
        embed.add_field(
            name="Member",
            value=f"{after.mention}\n`{after.id}`",
            inline=False,
        )

        if timed_out:
            embed.add_field(
                name="Until",
                value=f"<t:{int(after.timed_out_until.timestamp())}:F>",
                inline=True,
            )

        actor, reason = await find_audit_actor(
            after.guild,
            discord.AuditLogAction.member_update,
            after.id,
        )

        if actor:
            embed.add_field(
                name="Moderator",
                value=f"{actor.mention}\n`{actor.id}`",
                inline=True,
            )

        if reason:
            embed.add_field(name="Reason", value=reason[:1024], inline=False)

        await send_control_log(
            bot,
            await auto_log_channel_id("timeout_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
            embed,
        )


@bot.event
async def on_member_ban(
    guild: discord.Guild,
    user: discord.User,
) -> None:
    if guild.id != GUILD_ID:
        return

    actor, reason = await find_audit_actor(
        guild,
        discord.AuditLogAction.ban,
        user.id,
    )

    embed = control_log_embed(
        "🔨 Member Banned",
        colour=Theme.DANGER,
    )
    embed.add_field(name="User", value=f"`{user}`\n`{user.id}`", inline=False)

    if actor:
        embed.add_field(
            name="Moderator",
            value=f"{actor.mention}\n`{actor.id}`",
            inline=True,
        )

    if reason:
        embed.add_field(name="Reason", value=reason[:1024], inline=False)

    await send_control_log(
        bot,
        await auto_log_channel_id("ban_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )


@bot.event
async def on_member_unban(
    guild: discord.Guild,
    user: discord.User,
) -> None:
    if guild.id != GUILD_ID:
        return

    actor, reason = await find_audit_actor(
        guild,
        discord.AuditLogAction.unban,
        user.id,
    )

    embed = control_log_embed(
        "🔓 Member Unbanned",
        colour=Theme.SUCCESS,
    )
    embed.add_field(name="User", value=f"`{user}`\n`{user.id}`", inline=False)

    if actor:
        embed.add_field(
            name="Moderator",
            value=f"{actor.mention}\n`{actor.id}`",
            inline=True,
        )

    if reason:
        embed.add_field(name="Reason", value=reason[:1024], inline=False)

    await send_control_log(
        bot,
        await auto_log_channel_id("ban_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    if member.guild.id != GUILD_ID or before.channel == after.channel:
        return

    if before.channel is None and after.channel is not None:
        title = "🎤 Voice Channel Joined"
        description = f"{member.mention} joined {after.channel.mention}."
    elif before.channel is not None and after.channel is None:
        title = "📴 Voice Channel Left / Disconnected"
        description = f"{member.mention} left {before.channel.mention}."
    else:
        title = "🔄 Voice Channel Switched"
        description = (
            f"{member.mention} moved from {before.channel.mention} "
            f"to {after.channel.mention}."
        )

    embed = control_log_embed(
        title,
        description=description,
    )
    embed.add_field(name="Member ID", value=f"`{member.id}`", inline=True)

    await send_control_log(
        bot,
        await auto_log_channel_id("voice_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )


@bot.event
async def on_guild_update(
    before: discord.Guild,
    after: discord.Guild,
) -> None:
    if after.id != GUILD_ID:
        return

    changes = []

    if before.name != after.name:
        changes.append(f"**Name:** `{before.name}` → `{after.name}`")

    if before.icon != after.icon:
        changes.append("**Server icon changed.**")

    if before.verification_level != after.verification_level:
        changes.append(
            f"**Verification:** `{before.verification_level}` → "
            f"`{after.verification_level}`"
        )

    if not changes:
        return

    await send_control_log(
        bot,
        await auto_log_channel_id("system_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        control_log_embed(
            "⚙️ Server Updated",
            description="\n".join(changes),
            colour=Theme.WARNING,
        ),
    )


async def log_role_change(
    title: str,
    role: discord.Role,
    *,
    before: discord.Role | None = None,
) -> None:
    if role.guild.id != GUILD_ID:
        return

    embed = control_log_embed(
        title,
        colour=Theme.WARNING,
    )
    embed.add_field(
        name="Role",
        value=f"{role.mention}\n`{role.id}`",
        inline=False,
    )

    if before is not None:
        changes = []

        if before.name != role.name:
            changes.append(f"Name: `{before.name}` → `{role.name}`")

        if before.colour != role.colour:
            changes.append(f"Colour: `{before.colour}` → `{role.colour}`")

        if before.permissions != role.permissions:
            changes.append("Permissions changed.")

        if changes:
            embed.add_field(
                name="Changes",
                value="\n".join(changes)[:1024],
                inline=False,
            )

    await send_control_log(
        bot,
        await auto_log_channel_id("role_update_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )


@bot.event
async def on_guild_role_create(role: discord.Role) -> None:
    await log_role_change("➕ Role Created", role)


@bot.event
async def on_guild_role_delete(role: discord.Role) -> None:
    await log_role_change("➖ Role Deleted", role)


@bot.event
async def on_guild_role_update(
    before: discord.Role,
    after: discord.Role,
) -> None:
    await log_role_change("✏️ Role Updated", after, before=before)


async def log_channel_change(
    title: str,
    channel: discord.abc.GuildChannel,
    *,
    before: discord.abc.GuildChannel | None = None,
) -> None:
    if channel.guild.id != GUILD_ID:
        return

    embed = control_log_embed(
        title,
        colour=Theme.WARNING,
    )
    embed.add_field(
        name="Channel",
        value=f"{channel.mention}\n`{channel.id}`",
        inline=False,
    )

    if before is not None and before.name != channel.name:
        embed.add_field(
            name="Name Changed",
            value=f"`{before.name}` → `{channel.name}`",
            inline=False,
        )

    await send_control_log(
        bot,
        await auto_log_channel_id("channel_logs", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )


@bot.event
async def on_guild_channel_create(
    channel: discord.abc.GuildChannel,
) -> None:
    await log_channel_change("➕ Channel Created", channel)


@bot.event
async def on_guild_channel_delete(
    channel: discord.abc.GuildChannel,
) -> None:
    await handle_auto_channel_deleted(channel)
    await log_channel_change("➖ Channel Deleted", channel)


@bot.event
async def on_guild_channel_update(
    before: discord.abc.GuildChannel,
    after: discord.abc.GuildChannel,
) -> None:
    await log_channel_change("✏️ Channel Updated", after, before=before)


@bot.event
async def on_message(
    message: discord.Message,
) -> None:
    if message.author.bot:
        return

    # Anti-hack detection (MrBeast-style / compromised account posts)
    # Must run before other handlers so we can delete the message quickly.
    if message.guild is not None:
        try:
            if await handle_hacked_account_message(message):
                return
        except Exception:
            log.exception("Error in handle_hacked_account_message")

    if message.guild is None:
        handled = await process_rank_proof_dm(
            bot,
            message,
        )
        if handled:
            return

        # Suspect answering a POV request with a video link.
        try:
            handled = await process_pov_dm(bot, message)
        except Exception:
            log.exception("Error in process_pov_dm")
            handled = False
        if handled:
            return
    else:
        handled = await process_rp_list_reply(message)
        if handled:
            return

    await bot.process_commands(message)


