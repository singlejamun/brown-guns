# === Voice control panel ===
# Loaded into shared namespace by bot.py — do not run this file alone.
#
# FIXES in this version (see CHANGELOG at the bottom of README):
#   * The old `get_main_voice_channel` here was silently overwritten by a
#     second function with the same name in control_commands.py (loaded later,
#     same namespace). That version rejected every channel outside GUILD_ID and
#     every Stage channel, so the panel buttons answered "not configured
#     correctly". This file now uses its own unique helper names.
#   * VOICE_MAIN_GUILD_ID / CONTROL_PANEL_CHANNEL_ID / EVENT_VC_ID / TALK_2_VC_ID
#     are parsed with `_env_int` (an empty Railway variable no longer breaks the
#     GUILD_ID fallback) and every failure now says exactly which setting is wrong.
#   * The bot's own Move Members / Connect permission is checked up front, so
#     "Failed: N" is no longer a mystery.
#   * Panel creation can no longer crash startup (Forbidden / missing channel).
#   * Custom Drag dropdowns now show what you selected.

# =========================================================
# VOICE CONTROL PANEL — DATABASE/CONTROL SERVER
# =========================================================

VOICE_MAIN_GUILD_ID = _env_int("VOICE_MAIN_GUILD_ID", 0) or GUILD_ID
CONTROL_PANEL_CHANNEL_ID = _env_int("CONTROL_PANEL_CHANNEL_ID", 0)
EVENT_VC_ID = _env_int("EVENT_VC_ID", 0)
TALK_2_VC_ID = _env_int("TALK_2_VC_ID", 0)
CONTROL_PANEL_ROLE_IDS = {
    int(value.strip())
    for value in os.getenv("CONTROL_PANEL_ROLE_IDS", "").split(",")
    if value.strip().isdigit()
}
VOICE_MOVE_DELAY = 0.20
VOICE_PANEL_TITLE = "🎙️ Voice Control Panel"


def can_use_voice_panel(member: discord.Member) -> bool:
    if BOT_OWNER_ID and member.id == BOT_OWNER_ID:
        return True

    # Same people who can use the other control-server tools.
    if is_control_admin(member):
        return True

    if member.guild_permissions.move_members:
        return True

    return any(role.id in CONTROL_PANEL_ROLE_IDS for role in member.roles)


async def voice_panel_access(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "❌ Your member information could not be found.",
            ephemeral=True,
        )
        return False

    if not can_use_voice_panel(interaction.user):
        await interaction.response.send_message(
            "❌ You are not allowed to use the Voice Control Panel.",
            ephemeral=True,
        )
        return False

    return True


async def get_panel_voice_channel(
    channel_id: int,
) -> discord.VoiceChannel | discord.StageChannel | None:
    """Look up a voice/stage channel in the MAIN server (not the control server)."""
    if not channel_id:
        return None

    channel = bot.get_channel(channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        return None

    if VOICE_MAIN_GUILD_ID and channel.guild.id != VOICE_MAIN_GUILD_ID:
        return None

    return channel


async def resolve_panel_channel(
    channel_id: int,
    env_name: str,
    label: str,
) -> tuple[discord.VoiceChannel | discord.StageChannel | None, str | None]:
    """Returns (channel, None) or (None, human-readable error)."""
    if not channel_id:
        return None, f"❌ **{label}** is not set. Set it with `!config set {env_name} #channel`."

    channel = await get_panel_voice_channel(channel_id)
    if channel is None:
        return None, (
            f"❌ **{label}** (`{channel_id}`) was not found in the main server. "
            f"Check `!config` (`{env_name}`, `VOICE_MAIN_GUILD_ID`/`GUILD_ID`), and that the bot can see that channel."
        )
    return channel, None


def bot_move_problem(
    destination: discord.VoiceChannel | discord.StageChannel,
) -> str | None:
    """Checks the BOT's permissions on the destination channel."""
    me = destination.guild.me
    if me is None:
        return None  # can't tell — let Discord decide
    perms = destination.permissions_for(me)
    missing = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.connect:
        missing.append("Connect")
    if not perms.move_members:
        missing.append("Move Members")
    if missing:
        return (
            f"❌ I'm missing **{', '.join(missing)}** in {destination.mention}. "
            "Give the bot those permissions there (or server-wide) and try again."
        )
    return None


async def move_members(
    members: list[discord.Member],
    destination: discord.VoiceChannel | discord.StageChannel,
) -> tuple[int, int, int]:
    moved = 0
    failed = 0
    skipped = 0
    seen_ids: set[int] = set()

    for member in members:
        if member.id in seen_ids:
            continue
        seen_ids.add(member.id)

        if member.voice is None or member.voice.channel is None:
            skipped += 1
            continue

        if member.voice.channel.id == destination.id:
            skipped += 1
            continue

        try:
            await member.move_to(
                destination,
                reason="Voice Control Panel action",
            )
            moved += 1
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed += 1
            log.warning(
                "Voice panel could not move %s (%s) to %s: %s",
                member, member.id, destination.id, exc,
            )

        await asyncio.sleep(VOICE_MOVE_DELAY)

    return moved, failed, skipped


async def send_voice_result(
    interaction: discord.Interaction,
    *,
    action: str,
    destination: discord.abc.GuildChannel,
    moved: int,
    failed: int,
    skipped: int,
) -> None:
    embed = discord.Embed(
        title="✅ Voice action completed",
        colour=Theme.SUCCESS if failed == 0 else Theme.WARNING,
    )
    embed.add_field(name="Action", value=action, inline=False)
    embed.add_field(name="Destination", value=destination.mention, inline=False)
    embed.add_field(name="Moved", value=str(moved), inline=True)
    embed.add_field(name="Failed", value=str(failed), inline=True)
    embed.add_field(name="Skipped", value=str(skipped), inline=True)
    if failed:
        embed.set_footer(
            text=f"Used by {interaction.user} • Failed = Discord refused the move (see bot logs)"
        )
    else:
        embed.set_footer(text=f"Used by {interaction.user}")

    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def _reply_error(interaction: discord.Interaction, text: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


def _voice_select_options(
    guild: discord.Guild | None,
    emoji: str,
    *,
    priority_ids: set[int],
    selected_id: int | None = None,
) -> list[discord.SelectOption]:
    """Build up to 25 dropdown options (Discord's hard limit)."""
    channels: list[discord.VoiceChannel | discord.StageChannel] = []
    if guild is not None:
        channels = [*guild.voice_channels, *guild.stage_channels]

    # If the server has more than 25 voice channels, keep the important ones
    # (Event / Talk 2 / channels that currently have people) instead of
    # silently dropping them.
    channels.sort(
        key=lambda c: (
            0 if (c.id in priority_ids or c.members) else 1,
            c.position,
        )
    )
    channels = channels[:25]
    channels.sort(key=lambda c: c.position)

    options = [
        discord.SelectOption(
            label=channel.name[:100],
            value=str(channel.id),
            description=f"{len(channel.members)} member(s)",
            emoji=emoji,
            default=(selected_id == channel.id),
        )
        for channel in channels
    ]

    if not options:
        options = [
            discord.SelectOption(
                label="No voice channels found",
                value="0",
                description="Check VOICE_MAIN_GUILD_ID / GUILD_ID",
            )
        ]
    return options


class SourceVoiceSelect(discord.ui.Select):
    def __init__(self, parent_view: "CustomDragView") -> None:
        self.parent_view = parent_view
        guild = bot.get_guild(VOICE_MAIN_GUILD_ID)

        super().__init__(
            placeholder="1. Select source voice channel",
            min_values=1,
            max_values=1,
            options=_voice_select_options(
                guild, "🔊", priority_ids={EVENT_VC_ID, TALK_2_VC_ID}
            ),
            custom_id="voice_panel:custom_source",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await voice_panel_access(interaction):
            return

        selected_id = int(self.values[0])
        if selected_id == 0:
            await interaction.response.send_message(
                "❌ No valid source channel is available.",
                ephemeral=True,
            )
            return

        self.parent_view.source_channel_id = selected_id
        for option in self.options:
            option.default = option.value == str(selected_id)
        await interaction.response.edit_message(view=self.parent_view)


class DestinationVoiceSelect(discord.ui.Select):
    def __init__(self, parent_view: "CustomDragView") -> None:
        self.parent_view = parent_view
        guild = bot.get_guild(VOICE_MAIN_GUILD_ID)

        super().__init__(
            placeholder="2. Select destination voice channel",
            min_values=1,
            max_values=1,
            options=_voice_select_options(
                guild, "📥", priority_ids={EVENT_VC_ID, TALK_2_VC_ID}
            ),
            custom_id="voice_panel:custom_destination",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await voice_panel_access(interaction):
            return

        selected_id = int(self.values[0])
        if selected_id == 0:
            await interaction.response.send_message(
                "❌ No valid destination channel is available.",
                ephemeral=True,
            )
            return

        self.parent_view.destination_channel_id = selected_id
        for option in self.options:
            option.default = option.value == str(selected_id)
        await interaction.response.edit_message(view=self.parent_view)


class CustomDragView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=180)
        self.source_channel_id: int | None = None
        self.destination_channel_id: int | None = None
        self.add_item(SourceVoiceSelect(self))
        self.add_item(DestinationVoiceSelect(self))

    @discord.ui.button(
        label="Move Members",
        emoji="🚚",
        style=discord.ButtonStyle.success,
        custom_id="voice_panel:custom_execute",
        row=2,
    )
    async def move_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await voice_panel_access(interaction):
            return

        if self.source_channel_id is None or self.destination_channel_id is None:
            await interaction.response.send_message(
                "❌ Select both the source VC and destination VC first.",
                ephemeral=True,
            )
            return

        if self.source_channel_id == self.destination_channel_id:
            await interaction.response.send_message(
                "❌ Source and destination cannot be the same channel.",
                ephemeral=True,
            )
            return

        source = await get_panel_voice_channel(self.source_channel_id)
        destination = await get_panel_voice_channel(self.destination_channel_id)

        if source is None or destination is None:
            await interaction.response.send_message(
                "❌ One of the selected voice channels no longer exists.",
                ephemeral=True,
            )
            return

        problem = bot_move_problem(destination)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        moved, failed, skipped = await move_members(list(source.members), destination)

        await send_voice_result(
            interaction,
            action=f"{source.name} → {destination.name}",
            destination=destination,
            moved=moved,
            failed=failed,
            skipped=skipped,
        )
        self.stop()


class VoiceControlPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Drag to Event VC",
        emoji="🎮",
        style=discord.ButtonStyle.success,
        custom_id="voice_panel:all_to_event",
        row=0,
    )
    async def all_to_event(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await voice_panel_access(interaction):
            return

        destination, error = await resolve_panel_channel(
            EVENT_VC_ID, "EVENT_VC_ID", "Event VC"
        )
        if destination is None:
            await interaction.response.send_message(error, ephemeral=True)
            return

        guild = destination.guild

        problem = bot_move_problem(destination)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        members: list[discord.Member] = []
        for channel in [*guild.voice_channels, *guild.stage_channels]:
            if channel.id != destination.id:
                members.extend(channel.members)

        moved, failed, skipped = await move_members(members, destination)

        await send_voice_result(
            interaction,
            action="All voice channels → Event VC",
            destination=destination,
            moved=moved,
            failed=failed,
            skipped=skipped,
        )

    @discord.ui.button(
        label="Event to Talk 2",
        emoji="💬",
        style=discord.ButtonStyle.primary,
        custom_id="voice_panel:event_to_talk2",
        row=0,
    )
    async def event_to_talk_2(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await voice_panel_access(interaction):
            return

        source, error = await resolve_panel_channel(
            EVENT_VC_ID, "EVENT_VC_ID", "Event VC"
        )
        if source is None:
            await interaction.response.send_message(error, ephemeral=True)
            return

        destination, error = await resolve_panel_channel(
            TALK_2_VC_ID, "TALK_2_VC_ID", "Talk 2 VC"
        )
        if destination is None:
            await interaction.response.send_message(error, ephemeral=True)
            return

        if source.id == destination.id:
            await interaction.response.send_message(
                "❌ Event VC and Talk 2 VC cannot use the same ID.",
                ephemeral=True,
            )
            return

        problem = bot_move_problem(destination)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        moved, failed, skipped = await move_members(list(source.members), destination)

        await send_voice_result(
            interaction,
            action=f"{source.name} → {destination.name}",
            destination=destination,
            moved=moved,
            failed=failed,
            skipped=skipped,
        )

    @discord.ui.button(
        label="Custom Drag",
        emoji="🔁",
        style=discord.ButtonStyle.secondary,
        custom_id="voice_panel:custom_open",
        row=0,
    )
    async def custom_drag(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await voice_panel_access(interaction):
            return

        guild = bot.get_guild(VOICE_MAIN_GUILD_ID)
        if guild is None:
            await interaction.response.send_message(
                "❌ The main server was not found. Set `VOICE_MAIN_GUILD_ID` "
                "(or `GUILD_ID`) to the server whose voice channels you want to move.",
                ephemeral=True,
            )
            return

        if not guild.voice_channels and not guild.stage_channels:
            await interaction.response.send_message(
                "❌ The main server has no voice channels the bot can see.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title="🔁 Custom Voice Drag",
                description=(
                    "Select the **source VC**, select the **destination VC**, "
                    "and then click **Move Members**."
                ),
                colour=Theme.PRIMARY,
            ),
            view=CustomDragView(),
            ephemeral=True,
        )


def voice_control_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title=VOICE_PANEL_TITLE,
        description=(
            "**Drag to Event VC**\n"
            "Moves members from every voice channel into the Event VC.\n\n"
            "**Event to Talk 2**\n"
            "Moves everyone from Event VC into Talk 2.\n\n"
            "**Custom Drag**\n"
            "Select a source VC and destination VC."
        ),
        colour=Theme.PRIMARY,
    )
    embed.set_footer(text="Restricted staff controls")
    return embed


async def resolve_voice_panel_text_channel() -> discord.TextChannel | None:
    """CONTROL_PANEL_CHANNEL_ID if set, otherwise the #voice-panel channel
    that !startdata created in the database server."""
    channel_id = CONTROL_PANEL_CHANNEL_ID

    if not channel_id:
        try:
            data_guild_id = await get_data_guild_id()
            if data_guild_id is not None:
                channel_id = await get_auto_channel_id(data_guild_id, "voice_panel") or 0
        except Exception:
            log.exception("Could not look up the auto-created voice-panel channel.")

    if not channel_id:
        return None

    return await get_text_channel(bot, channel_id)


async def ensure_voice_control_panel(
    channel: discord.TextChannel | None = None,
) -> str:
    """Create or refresh the panel. Returns a short status string.
    Never raises — a broken panel must not stop the bot from starting."""
    try:
        if channel is None:
            channel = await resolve_voice_panel_text_channel()

        if channel is None:
            log.warning(
                "Voice panel channel not found. Set CONTROL_PANEL_CHANNEL_ID, "
                "run !startdata, or run !voicepanel inside the channel you want."
            )
            return "no_channel"

        async for message in channel.history(limit=50):
            if (
                bot.user is not None
                and message.author.id == bot.user.id
                and message.embeds
                and message.embeds[0].title == VOICE_PANEL_TITLE
            ):
                try:
                    await message.edit(
                        embed=voice_control_panel_embed(),
                        view=VoiceControlPanelView(),
                    )
                    log.info("Existing Voice Control Panel restored in #%s.", channel.name)
                    return "restored"
                except discord.HTTPException:
                    log.warning("Could not edit the old voice panel message; posting a new one.")
                    break

        await channel.send(
            embed=voice_control_panel_embed(),
            view=VoiceControlPanelView(),
        )
        log.info("Voice Control Panel created in #%s.", channel.name)
        return "created"

    except discord.Forbidden:
        log.error(
            "Voice panel: bot lacks View Channel / Read History / Send Messages / "
            "Embed Links in the panel channel."
        )
        return "forbidden"
    except discord.HTTPException:
        log.exception("Voice panel: Discord rejected the panel update.")
        return "http_error"
    except Exception:
        log.exception("Voice panel: unexpected error.")
        return "error"


@bot.command(name="voicepanel")
async def voice_panel_command(ctx: commands.Context, where: str = "") -> None:
    """!voicepanel        → refresh/create the panel in the configured channel
    !voicepanel here   → create/refresh it in the channel you type this in"""
    if not isinstance(ctx.author, discord.Member):
        return

    if not can_use_voice_panel(ctx.author):
        await ctx.reply(
            "❌ You are not allowed to create this panel.",
            mention_author=False,
        )
        return

    target: discord.TextChannel | None = None
    if where.lower() == "here" and isinstance(ctx.channel, discord.TextChannel):
        target = ctx.channel
    elif await resolve_voice_panel_text_channel() is None and isinstance(
        ctx.channel, discord.TextChannel
    ):
        # Nothing configured yet → use the current channel instead of failing.
        target = ctx.channel

    status = await ensure_voice_control_panel(target)

    messages = {
        "created": "✅ Voice Control Panel created.",
        "restored": "✅ Voice Control Panel already existed — buttons re-attached.",
        "forbidden": "❌ I don't have permission to read/send/embed in that channel.",
        "no_channel": "❌ No panel channel found. Run `!voicepanel here` in the channel you want.",
    }
    await ctx.reply(
        messages.get(status, "❌ Something went wrong — check the bot logs."),
        mention_author=False,
    )


@bot.command(name="testsystem")
async def test_system_command(ctx: commands.Context) -> None:
    """Check the RP Ticket configuration, database, channels, role and scheduler."""
    if not await system_command_check(ctx):
        return

    checks: list[tuple[str, bool, str]] = []
    try:
        await db.fetchone("SELECT 1 AS ok")
        checks.append(("SQLite Database", True, "Connected"))
    except Exception as exc:
        checks.append(("SQLite Database", False, type(exc).__name__))

    event_channel = await get_text_channel(bot, MAIN_EVENT_PANEL_CHANNEL_ID or EVENT_PING_CHANNEL_ID)
    reminder_channel = await get_text_channel(bot, RP_TICKET_REMINDER_CHANNEL_ID)
    family_log = await get_text_channel(bot, FAMILY_LOG_CHANNEL_ID)
    guild = bot.get_guild(GUILD_ID)
    adviser_role = guild.get_role(ADVISER_ROLE_ID) if guild else None

    checks.extend([
        ("RP Ticket System", await is_system_enabled("rp_ticket_reminder"), "Enabled"),
        ("RP Scheduler", rp_ticket_scheduler.is_running(), "Running"),
        ("Event Panel Channel", event_channel is not None, f"<#{MAIN_EVENT_PANEL_CHANNEL_ID or EVENT_PING_CHANNEL_ID}>") ,
        ("Reminder Channel", reminder_channel is not None, f"<#{RP_TICKET_REMINDER_CHANNEL_ID}>") ,
        ("Family Log Channel", family_log is not None, f"<#{FAMILY_LOG_CHANNEL_ID}>") ,
        ("Adviser Role", adviser_role is not None, f"<@&{ADVISER_ROLE_ID}>") ,
    ])

    all_ok = all(ok for _, ok, _ in checks)
    embed = discord.Embed(
        title="🧪 Brown Guns System Test",
        colour=Theme.SUCCESS if all_ok else Theme.WARNING,
        timestamp=now_local(),
    )
    for name, ok, detail in checks:
        embed.add_field(name=name, value=f"{'✅' if ok else '❌'} {detail}", inline=False)
    embed.set_footer(text="ALL SYSTEMS OPERATIONAL" if all_ok else "Some configuration needs attention")
    await ctx.reply(embed=embed, mention_author=False)



BOT_ACTIVITIES = [
    (discord.ActivityType.playing, "Grand RP"),
    (discord.ActivityType.playing, "Python Krieger Fixing Me"),
    (discord.ActivityType.watching, "Brown Guns Family"),
    (discord.ActivityType.watching, "Protecting Brown Guns"),
    (discord.ActivityType.watching, "Managing Family Events"),
    (discord.ActivityType.watching, "Tracking RP Tickets"),
    (discord.ActivityType.watching, "Running Family Operations"),
]


@tasks.loop(seconds=45)
async def rotating_bot_status() -> None:
    if bot.is_closed():
        return

    index = rotating_bot_status.current_loop % len(BOT_ACTIVITIES)
    activity_type, name = BOT_ACTIVITIES[index]
    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(type=activity_type, name=name),
        )
    except (discord.ConnectionClosed, ConnectionResetError, OSError):
        log.debug("Skipped presence update — gateway not currently connected.")


@rotating_bot_status.before_loop
async def before_rotating_bot_status() -> None:
    await bot.wait_until_ready()


