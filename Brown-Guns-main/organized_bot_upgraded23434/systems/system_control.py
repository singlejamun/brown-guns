# === System on/off + RP tickets ===
# Source lines 7104-7685 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# SYSTEM ON/OFF + RP TICKET REMINDER SYSTEM
# =========================================================


async def initialize_system_statuses() -> None:
    timestamp = now_local().isoformat()
    for system_name in SYSTEM_NAMES:
        await db.execute(
            """
            INSERT OR IGNORE INTO system_status(system_name, enabled, updated_at)
            VALUES (?, 1, ?)
            """,
            (system_name, timestamp),
        )


async def is_system_enabled(system_name: str) -> bool:
    row = await db.fetchone(
        "SELECT enabled FROM system_status WHERE system_name = ?",
        (system_name,),
    )
    return True if row is None else bool(row["enabled"])


async def send_system_disabled_interaction(
    interaction: discord.Interaction,
    system_name: str,
) -> None:
    text = f"⛔ **{SYSTEM_NAMES.get(system_name, system_name)}** is currently disabled."
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


def normalize_system_name(value: str) -> str | None:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "informal": "informal_registration",
        "registration": "informal_registration",
        "event": "event_panel",
        "events": "event_panel",
        "rp": "rp_ticket_reminder",
        "rp_ticket": "rp_ticket_reminder",
        "ticket": "rp_ticket_reminder",
        "hack": "hack_detection",
        "hack_detection": "hack_detection",
        "security_detect": "hack_detection",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in SYSTEM_NAMES else None


async def set_system_status(system_name: str, enabled: bool, updated_by: int) -> None:
    await db.execute(
        """
        INSERT INTO system_status(system_name, enabled, updated_by, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(system_name) DO UPDATE SET
            enabled = excluded.enabled,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at
        """,
        (system_name, int(enabled), updated_by, now_local().isoformat()),
    )


async def system_command_check(ctx: commands.Context) -> bool:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return False

    data_guild_id = await get_data_guild_id()
    is_static_guild = CONTROL_GUILD_ID > 0 and ctx.guild.id == CONTROL_GUILD_ID
    is_auto_guild = data_guild_id is not None and ctx.guild.id == data_guild_id

    if not is_static_guild and not is_auto_guild:
        await ctx.reply("❌ Use this command in the private control server.", mention_author=False)
        return False

    if is_static_guild:
        if CONTROL_COMMAND_CHANNEL_ID > 0 and ctx.channel.id != CONTROL_COMMAND_CHANNEL_ID:
            await ctx.reply(f"❌ Use this command in <#{CONTROL_COMMAND_CHANNEL_ID}>.", mention_author=False)
            return False
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
            return False

    if not is_control_admin(ctx.author):
        await ctx.reply("❌ You do not have control access.", mention_author=False)
        return False
    return True


@bot.command(name="systemoff")
async def system_off_command(ctx: commands.Context, *, system: str = "") -> None:
    if not await system_command_check(ctx):
        return
    key = normalize_system_name(system)
    if key is None:
        options = "\n".join(f"• `{name}` — {label}" for name, label in SYSTEM_NAMES.items())
        await ctx.reply(f"❌ Choose a valid system:\n{options}", mention_author=False)
        return
    await set_system_status(key, False, ctx.author.id)
    await ctx.reply(f"🔴 **{SYSTEM_NAMES[key]}** is now **OFF**.", mention_author=False)


@bot.command(name="systemon")
async def system_on_command(ctx: commands.Context, *, system: str = "") -> None:
    if not await system_command_check(ctx):
        return
    key = normalize_system_name(system)
    if key is None:
        options = "\n".join(f"• `{name}` — {label}" for name, label in SYSTEM_NAMES.items())
        await ctx.reply(f"❌ Choose a valid system:\n{options}", mention_author=False)
        return
    await set_system_status(key, True, ctx.author.id)
    await ctx.reply(f"🟢 **{SYSTEM_NAMES[key]}** is now **ON**.", mention_author=False)


@bot.command(name="systemstatus", aliases=["systems"])
async def system_status_command(ctx: commands.Context) -> None:
    if not await system_command_check(ctx):
        return
    rows = await db.fetchall(
        "SELECT system_name, enabled, updated_by, updated_at FROM system_status ORDER BY system_name"
    )
    state = {str(row["system_name"]): bool(row["enabled"]) for row in rows}
    embed = discord.Embed(title="⚙️ Bot System Status", colour=Theme.PRIMARY)
    for key, label in SYSTEM_NAMES.items():
        embed.add_field(
            name=label,
            value="🟢 Enabled" if state.get(key, True) else "🔴 Disabled",
            inline=False,
        )
    await ctx.reply(embed=embed, mention_author=False)


# How long to wait after a collect-prompt goes out, with nobody clicking
# "I Collected RP Ticket", before the bot pings the Adviser role again.
RP_TICKET_ESCALATION_DELAY = timedelta(minutes=10)

# How long after the reminder slot (i.e. once the next hourly slot begins)
# before an un-escalated-but-still-uncollected ticket is handed off to the
# "missed" prompt (I Collect / Log It) in the RP logs channel.
RP_TICKET_MISSED_DELAY = timedelta(minutes=60)


async def rp_ticket_log_channel_id() -> int:
    """Channel used for the persistent RP Ticket log panel + missed-ticket
    prompts. Prefers the auto-created '#rp-ticket-logs' channel (from
    !startdata); falls back to the configured family log channel."""
    return await auto_log_channel_id("rp_ticket_logs", FAMILY_LOG_CHANNEL_ID)


# RP Factory timing (server time).
# Factory #1: starts 4:00 PM, ends 4:20 PM. Factory #2: starts 10:00 PM, ends 10:20 PM.
# The WIN/LOSE result panel is posted at 4:25 PM and 10:25 PM, followed by
# 6 hourly ticket reminders at :30.
RP_FACTORY_STARTS = ((16, 0), (22, 0))
RP_FACTORY_RESULTS = ((16, 25), (22, 25))
RP_REMINDER_COUNT = 6


def _at(day, hour: int, minute: int) -> datetime:
    return datetime.combine(day, datetime.min.time(), TIMEZONE).replace(hour=hour, minute=minute)


def _slot_schedule(day, slot: int) -> tuple[datetime, list[datetime]]:
    """Result time + 6 hourly reminders (first at result hour :30) for a slot."""
    result_hour, result_minute = RP_FACTORY_RESULTS[slot]
    status_at = _at(day, result_hour, result_minute)
    first_reminder = _at(day, result_hour, 30)
    reminders = [first_reminder + timedelta(hours=i) for i in range(RP_REMINDER_COUNT)]
    return status_at, reminders


def rp_factory_schedule(started_at: datetime) -> tuple[datetime, list[datetime]]:
    """Return the fixed result time and ticket reminder times for an RP Factory click."""
    local = started_at.astimezone(TIMEZONE)
    day = local.date()

    # Before the second factory starts -> first slot, otherwise second slot.
    slot = 0 if local < _at(day, *RP_FACTORY_STARTS[1]) else 1
    status_at, reminders = _slot_schedule(day, slot)

    # If the button is pressed after that slot's result time, use the next valid slot.
    if status_at <= local:
        if slot == 0:
            status_at, reminders = _slot_schedule(day, 1)
        else:
            status_at, reminders = _slot_schedule(day + timedelta(days=1), 0)

    return status_at, reminders


def fixed_rp_slots(day) -> list[tuple[datetime, datetime]]:
    """Return (factory start, result time) for the two automatic daily RP sessions."""
    return [
        (_at(day, *RP_FACTORY_STARTS[i]), _at(day, *RP_FACTORY_RESULTS[i]))
        for i in range(2)
    ]


async def ensure_automatic_rp_sessions() -> None:
    """Create today's two RP result sessions without requiring the Event Panel button."""
    result_channel_id = MAIN_EVENT_PANEL_CHANNEL_ID or EVENT_PING_CHANNEL_ID
    if result_channel_id <= 0:
        return

    today = now_local().date()
    for started_at, result_due_at in fixed_rp_slots(today):
        existing = await db.fetchone(
            "SELECT id FROM rp_ticket_sessions WHERE result_due_at = ? LIMIT 1",
            (result_due_at.isoformat(),),
        )
        if existing is not None:
            continue
        await db.execute(
            """
            INSERT INTO rp_ticket_sessions(
                guild_id, event_channel_id, started_by, started_at, result_due_at, status
            ) VALUES (?, ?, 0, ?, ?, 'pending_status')
            """,
            (GUILD_ID, result_channel_id, started_at.isoformat(), result_due_at.isoformat()),
        )


async def create_rp_ticket_session(interaction: discord.Interaction) -> int:
    started_at = now_local()
    due, _ = rp_factory_schedule(started_at)
    return await db.execute(
        """
        INSERT INTO rp_ticket_sessions(
            guild_id, event_channel_id, started_by, started_at, result_due_at, status
        ) VALUES (?, ?, ?, ?, ?, 'pending_status')
        """,
        (
            interaction.guild.id,
            EVENT_PING_CHANNEL_ID,
            interaction.user.id,
            started_at.isoformat(),
            due.isoformat(),
        ),
    )


class RPStatusView(discord.ui.View):
    def __init__(self, session_id: int) -> None:
        super().__init__(timeout=None)
        self.session_id = session_id
        win = discord.ui.Button(
            label="Win", emoji="🏆", style=discord.ButtonStyle.success,
            custom_id=f"rpstatus:win:{session_id}",
        )
        lose = discord.ui.Button(
            label="Lose", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"rpstatus:lose:{session_id}",
        )
        win.callback = self.win_callback
        lose.callback = self.lose_callback
        self.add_item(win)
        self.add_item(lose)

    async def _validate(self, interaction: discord.Interaction) -> sqlite3.Row | None:
        if not await is_system_enabled("rp_ticket_reminder"):
            await send_system_disabled_interaction(interaction, "rp_ticket_reminder")
            return None
        if not isinstance(interaction.user, discord.Member) or not can_manage_events(interaction.user):
            await interaction.response.send_message(
                "❌ Only Rank 8, Rank 9, and Rank 10 members can select the RP result.",
                ephemeral=True,
            )
            return None
        session = await db.fetchone("SELECT * FROM rp_ticket_sessions WHERE id = ?", (self.session_id,))
        if session is None or session["status"] != "awaiting_result":
            await interaction.response.send_message("❌ This RP status is already completed.", ephemeral=True)
            return None
        return session

    async def win_callback(self, interaction: discord.Interaction) -> None:
        session = await self._validate(interaction)
        if session is None:
            return
        _, reminders = rp_factory_schedule(datetime.fromisoformat(str(session["started_at"])))
        now = now_local()
        future_reminders = [moment for moment in reminders if moment > now]
        if not future_reminders:
            await interaction.response.send_message(
                "❌ The RP Ticket collection window for this event has already ended.",
                ephemeral=True,
            )
            return
        next_reminder = future_reminders[0]
        await db.execute(
            """
            UPDATE rp_ticket_sessions
            SET status = 'active', decided_by = ?, decided_at = ?, next_reminder_at = ?
            WHERE id = ? AND status = 'awaiting_result'
            """,
            (interaction.user.id, now.isoformat(), next_reminder.isoformat(), self.session_id),
        )
        try:
            await ensure_rp_log_panel(self.session_id)
        except discord.HTTPException:
            log.exception("Could not post RP ticket log panel for session %s", self.session_id)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Result Recorded — WIN",
                description=(
                    "RP Ticket reminders are now active.\n"
                    f"Next reminder: **{next_reminder.strftime('%I:%M %p')}**."
                ),
                colour=Theme.SUCCESS,
                timestamp=now,
            ),
            view=None,
        )
        await asyncio.sleep(2)
        try:
            if interaction.message is not None:
                await interaction.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def lose_callback(self, interaction: discord.Interaction) -> None:
        session = await self._validate(interaction)
        if session is None:
            return
        await db.execute(
            """
            UPDATE rp_ticket_sessions
            SET status = 'lost', decided_by = ?, decided_at = ?, next_reminder_at = NULL
            WHERE id = ? AND status = 'awaiting_result'
            """,
            (interaction.user.id, now_local().isoformat(), self.session_id),
        )
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Result Recorded — LOSS",
                description="No RP Ticket reminders will be sent for this session.",
                colour=Theme.DANGER,
                timestamp=now_local(),
            ),
            view=None,
        )
        await asyncio.sleep(2)
        try:
            if interaction.message is not None:
                await interaction.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


class RPTicketCollectView(discord.ui.View):
    def __init__(self, session_id: int, reminder_at: datetime) -> None:
        super().__init__(timeout=None)
        self.session_id = session_id
        self.reminder_at = reminder_at.astimezone(TIMEZONE)
        epoch = int(self.reminder_at.timestamp())
        collect = discord.ui.Button(
            label="I Collected RP Ticket",
            emoji="🎫",
            style=discord.ButtonStyle.success,
            custom_id=f"rpticket:collect:{session_id}:{epoch}",
        )
        collect.callback = self.collect_callback
        self.add_item(collect)

    async def collect_callback(self, interaction: discord.Interaction) -> None:
        if not await is_system_enabled("rp_ticket_reminder"):
            await send_system_disabled_interaction(interaction, "rp_ticket_reminder")
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Member information was not found.", ephemeral=True)
            return
        adviser_role = interaction.guild.get_role(ADVISER_ROLE_ID) if interaction.guild else None
        if adviser_role is None:
            await interaction.response.send_message("❌ Adviser role is not configured correctly.", ephemeral=True)
            return
        if adviser_role not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ Only members with the **Adviser** role can use this button.",
                ephemeral=True,
            )
            return
        session = await db.fetchone("SELECT status FROM rp_ticket_sessions WHERE id = ?", (self.session_id,))
        if session is None or session["status"] != "active":
            await interaction.response.send_message("❌ This RP Ticket session is no longer active.", ephemeral=True)
            return

        reminder_key = self.reminder_at.isoformat()

        reminder_row = await db.fetchone(
            "SELECT escalated_at, escalation_message_id FROM rp_ticket_reminders WHERE session_id = ? AND reminder_at = ?",
            (self.session_id, reminder_key),
        )
        missed_row = await db.fetchone(
            "SELECT prompt_message_id, resolution FROM rp_ticket_missed_log WHERE session_id = ? AND reminder_at = ?",
            (self.session_id, reminder_key),
        )
        if missed_row is not None and missed_row["resolution"] is not None:
            await interaction.response.send_message("✅ This hour has already been logged.", ephemeral=True)
            return

        was_late = bool((reminder_row and reminder_row["escalated_at"]) or missed_row is not None)

        try:
            await db.execute(
                """
                INSERT INTO rp_ticket_hourly_collections(session_id, reminder_at, user_id, collected_at, late)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.session_id, reminder_key, interaction.user.id, now_local().isoformat(), int(was_late)),
            )
        except sqlite3.IntegrityError:
            await interaction.response.send_message(
                "✅ Your collection for this reminder is already recorded.",
                ephemeral=True,
            )
            return

        # A 10-minute escalation ping was sent for this slot — it's now
        # collected, so remove the ping.
        if reminder_row and reminder_row["escalation_message_id"]:
            ping_channel = await get_text_channel(bot, RP_TICKET_REMINDER_CHANNEL_ID)
            if ping_channel is not None:
                try:
                    ping_message = await ping_channel.fetch_message(int(reminder_row["escalation_message_id"]))
                    await ping_message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            await db.execute(
                "UPDATE rp_ticket_reminders SET escalation_message_id = NULL WHERE session_id = ? AND reminder_at = ?",
                (self.session_id, reminder_key),
            )

        # A "this hour has been missed" prompt was already posted in the
        # RP logs channel — resolve it and disable its buttons.
        if missed_row is not None:
            await db.execute(
                """
                UPDATE rp_ticket_missed_log
                SET resolution = 'collected_late', logged_by = ?, logged_at = ?
                WHERE session_id = ? AND reminder_at = ?
                """,
                (interaction.user.id, now_local().isoformat(), self.session_id, reminder_key),
            )
            if missed_row["prompt_message_id"]:
                log_channel = await get_text_channel(bot, await rp_ticket_log_channel_id())
                if log_channel is not None:
                    try:
                        prompt_message = await log_channel.fetch_message(int(missed_row["prompt_message_id"]))
                        await prompt_message.edit(
                            content=f"✅ {interaction.user.mention} collected this hour's RP Ticket (late).",
                            view=None,
                        )
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass

        channel = await get_text_channel(bot, FAMILY_LOG_CHANNEL_ID)
        if channel is not None:
            collected_time = now_local()
            embed = discord.Embed(
                title="🎫 RP Ticket Collected",
                description=(
                    f"{interaction.user.mention} collected the RP Ticket.\n"
                    f"**Time:** {collected_time.strftime('%I:%M %p')}"
                ),
                colour=Theme.SUCCESS if not was_late else Theme.WARNING,
                timestamp=collected_time,
            )
            embed.add_field(name="Reminder Slot", value=self.reminder_at.strftime("%d %B %Y, %I:%M %p"), inline=False)
            if was_late:
                embed.add_field(name="Status", value="⚠️ Collected late", inline=False)
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

        await update_rp_log_panel(self.session_id)

        await interaction.response.send_message(
            "✅ Your RP Ticket collection has been recorded in the family log.",
            ephemeral=True,
        )


class RPMissedTicketView(discord.ui.View):
    """Posted in the RP logs channel when a ticket collection slot has
    gone unclicked for a full hour after its 10-minute escalation ping.
    Gives advisers two options: log a late collection, or log it as
    missed outright."""

    def __init__(self, session_id: int, reminder_at: datetime) -> None:
        super().__init__(timeout=None)
        self.session_id = session_id
        self.reminder_at = reminder_at.astimezone(TIMEZONE)
        epoch = int(self.reminder_at.timestamp())

        collect = discord.ui.Button(
            label="I Collect",
            emoji="🎫",
            style=discord.ButtonStyle.success,
            custom_id=f"rpticket:missedcollect:{session_id}:{epoch}",
        )
        log_it = discord.ui.Button(
            label="Log It",
            emoji="📝",
            style=discord.ButtonStyle.danger,
            custom_id=f"rpticket:missedlog:{session_id}:{epoch}",
        )
        collect.callback = self.collect_callback
        log_it.callback = self.log_callback
        self.add_item(collect)
        self.add_item(log_it)

    async def _adviser(self, interaction: discord.Interaction) -> discord.Member | None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Member information was not found.", ephemeral=True)
            return None
        adviser_role = interaction.guild.get_role(ADVISER_ROLE_ID) if interaction.guild else None
        if adviser_role is None:
            await interaction.response.send_message("❌ Adviser role is not configured correctly.", ephemeral=True)
            return None
        if adviser_role not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ Only members with the **Adviser** role can use this.",
                ephemeral=True,
            )
            return None
        return interaction.user

    async def _resolution(self) -> sqlite3.Row | None:
        return await db.fetchone(
            "SELECT resolution FROM rp_ticket_missed_log WHERE session_id = ? AND reminder_at = ?",
            (self.session_id, self.reminder_at.isoformat()),
        )

    async def collect_callback(self, interaction: discord.Interaction) -> None:
        member = await self._adviser(interaction)
        if member is None:
            return

        row = await self._resolution()
        if row is not None and row["resolution"] is not None:
            await interaction.response.send_message("✅ This hour has already been logged.", ephemeral=True)
            return

        try:
            await db.execute(
                """
                INSERT INTO rp_ticket_hourly_collections(session_id, reminder_at, user_id, collected_at, late)
                VALUES (?, ?, ?, ?, 1)
                """,
                (self.session_id, self.reminder_at.isoformat(), member.id, now_local().isoformat()),
            )
        except sqlite3.IntegrityError:
            await interaction.response.send_message("✅ This hour has already been logged.", ephemeral=True)
            return

        await db.execute(
            """
            UPDATE rp_ticket_missed_log
            SET resolution = 'collected_late', logged_by = ?, logged_at = ?
            WHERE session_id = ? AND reminder_at = ?
            """,
            (member.id, now_local().isoformat(), self.session_id, self.reminder_at.isoformat()),
        )

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ {member.mention} collected this hour's RP Ticket (late).",
            view=self,
        )
        await update_rp_log_panel(self.session_id)

    async def log_callback(self, interaction: discord.Interaction) -> None:
        member = await self._adviser(interaction)
        if member is None:
            return

        row = await self._resolution()
        if row is not None and row["resolution"] is not None:
            await interaction.response.send_message("✅ This hour has already been logged.", ephemeral=True)
            return

        await db.execute(
            """
            UPDATE rp_ticket_missed_log
            SET resolution = 'missed', logged_by = ?, logged_at = ?
            WHERE session_id = ? AND reminder_at = ?
            """,
            (member.id, now_local().isoformat(), self.session_id, self.reminder_at.isoformat()),
        )

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"❌ This hour's RP Ticket was logged as **missed** by {member.mention}.",
            view=self,
        )
        await update_rp_log_panel(self.session_id)


async def send_rp_ticket_escalation(session: sqlite3.Row, reminder_at: datetime) -> None:
    """10-minute escalation ping, posted in the same reminder channel."""
    channel = await get_text_channel(bot, RP_TICKET_REMINDER_CHANNEL_ID)
    if channel is None:
        return
    message = await channel.send(
        content=f"<@&{ADVISER_ROLE_ID}> please collect this hour rp ticket",
        allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
    )
    await db.execute(
        """
        UPDATE rp_ticket_reminders
        SET escalation_message_id = ?, escalated_at = ?
        WHERE session_id = ? AND reminder_at = ?
        """,
        (message.id, now_local().isoformat(), int(session["id"]), reminder_at.isoformat()),
    )


async def send_rp_ticket_missed_prompt(session: sqlite3.Row, reminder_at: datetime) -> None:
    """Posted in the RP logs channel, below the log panel, once a slot has
    gone a full hour past its reminder with no collection."""
    channel = await get_text_channel(bot, await rp_ticket_log_channel_id())
    if channel is None:
        return
    session_id = int(session["id"])
    view = RPMissedTicketView(session_id, reminder_at)
    message = await channel.send(
        content=f"<@&{ADVISER_ROLE_ID}> this hour rp ticket has been missed",
        view=view,
        allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
    )
    await db.execute(
        """
        INSERT INTO rp_ticket_missed_log(session_id, reminder_at, prompt_message_id)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, reminder_at) DO UPDATE SET prompt_message_id = excluded.prompt_message_id
        """,
        (session_id, reminder_at.isoformat(), message.id),
    )
    bot.add_view(view, message_id=message.id)
    await update_rp_log_panel(session_id)


async def build_rp_log_panel_embed(session_id: int) -> discord.Embed:
    """Renders the persistent, live-updated RP Ticket log panel — one row
    per hourly collection slot, styled the same way as the informal
    registration panel."""
    session = await db.fetchone("SELECT * FROM rp_ticket_sessions WHERE id = ?", (session_id,))
    started_at = datetime.fromisoformat(str(session["started_at"]))
    _, schedule = rp_factory_schedule(started_at)

    collections = await db.fetchall(
        "SELECT reminder_at, user_id, collected_at, late FROM rp_ticket_hourly_collections WHERE session_id = ?",
        (session_id,),
    )
    collected_by_slot = {str(row["reminder_at"]): row for row in collections}

    missed = await db.fetchall(
        "SELECT reminder_at, resolution, logged_by, logged_at FROM rp_ticket_missed_log WHERE session_id = ?",
        (session_id,),
    )
    missed_by_slot = {str(row["reminder_at"]): row for row in missed}

    now = now_local()
    lines: list[str] = []

    for index, reminder_at in enumerate(schedule, start=1):
        key = reminder_at.isoformat()
        time_label = reminder_at.strftime("%I:%M %p")
        collected_row = collected_by_slot.get(key)
        missed_row = missed_by_slot.get(key)

        if collected_row is not None:
            when = datetime.fromisoformat(str(collected_row["collected_at"])).strftime("%I:%M %p")
            if collected_row["late"]:
                lines.append(
                    f"`{index:02}` {time_label} {Theme.ICON_ARROW} ⚠️ Collected late by "
                    f"<@{int(collected_row['user_id'])}> at {when}"
                )
            else:
                lines.append(
                    f"`{index:02}` {time_label} {Theme.ICON_ARROW} ✅ Collected by "
                    f"<@{int(collected_row['user_id'])}> at {when}"
                )
        elif missed_row is not None and missed_row["resolution"] == "missed":
            when = (
                datetime.fromisoformat(str(missed_row["logged_at"])).strftime("%I:%M %p")
                if missed_row["logged_at"]
                else "—"
            )
            logger = f"<@{int(missed_row['logged_by'])}>" if missed_row["logged_by"] else "unresolved"
            lines.append(f"`{index:02}` {time_label} {Theme.ICON_ARROW} ❌ Missed — logged by {logger} at {when}")
        elif missed_row is not None:
            lines.append(f"`{index:02}` {time_label} {Theme.ICON_ARROW} 🔔 Missed ping sent — awaiting response")
        elif now >= reminder_at:
            lines.append(f"`{index:02}` {time_label} {Theme.ICON_ARROW} ⏳ Awaiting collection")
        else:
            lines.append(f"`{index:02}` {time_label} {Theme.ICON_ARROW} ⏳ Pending")

    embed = discord.Embed(
        title=f"{Theme.ICON_EVENT}  RP TICKET COLLECTION LOG",
        description=f"{Theme.DIVIDER}\n" + "\n".join(lines),
        colour=Theme.ACCENT,
        timestamp=started_at,
    )
    embed.set_footer(text="Brown Guns Family • Updated live as each hour is collected or logged")
    return embed


async def ensure_rp_log_panel(session_id: int) -> None:
    """Creates the RP Ticket log panel message the first time a session
    goes 'active' (i.e. when !rpwin / the Win button is clicked)."""
    existing = await db.fetchone(
        "SELECT message_id FROM rp_ticket_log_panels WHERE session_id = ?",
        (session_id,),
    )
    if existing is not None and existing["message_id"]:
        return

    channel = await get_text_channel(bot, await rp_ticket_log_channel_id())
    if channel is None:
        log.error("RP ticket log channel was not found for session %s", session_id)
        return

    message = await channel.send(embed=await build_rp_log_panel_embed(session_id))
    await db.execute(
        """
        INSERT INTO rp_ticket_log_panels(session_id, channel_id, message_id, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET channel_id = excluded.channel_id, message_id = excluded.message_id
        """,
        (session_id, channel.id, message.id, now_local().isoformat()),
    )


async def update_rp_log_panel(session_id: int) -> None:
    panel = await db.fetchone(
        "SELECT channel_id, message_id FROM rp_ticket_log_panels WHERE session_id = ?",
        (session_id,),
    )
    if panel is None or not panel["message_id"]:
        return
    channel = await get_text_channel(bot, int(panel["channel_id"]))
    if channel is None:
        return
    try:
        message = await channel.fetch_message(int(panel["message_id"]))
        await message.edit(embed=await build_rp_log_panel_embed(session_id))
    except discord.NotFound:
        log.warning("RP ticket log panel message %s was deleted.", panel["message_id"])
    except discord.Forbidden:
        log.error("Bot cannot edit RP ticket log panel message %s.", panel["message_id"])
    except discord.HTTPException:
        log.exception("Discord error updating RP ticket log panel %s.", panel["message_id"])


async def post_rp_status(session: sqlite3.Row) -> None:
    channel = await get_text_channel(bot, int(session["event_channel_id"]))
    if channel is None:
        return
    session_id = int(session["id"])
    view = RPStatusView(session_id)
    message = await channel.send(
        embed=discord.Embed(
            title="🏭 RP Factory Result",
            description="Did we win today's RP Factory?",
            colour=Theme.WARNING,
            timestamp=now_local(),
        ),
        view=view,
    )
    await db.execute(
        """
        UPDATE rp_ticket_sessions
        SET status = 'awaiting_result', status_message_id = ?
        WHERE id = ? AND status = 'pending_status'
        """,
        (message.id, session_id),
    )
    bot.add_view(view, message_id=message.id)


async def post_rp_ticket_reminder(session: sqlite3.Row, reminder_at: datetime) -> None:
    channel = await get_text_channel(bot, RP_TICKET_REMINDER_CHANNEL_ID)
    if channel is None:
        return
    session_id = int(session["id"])
    message = await channel.send(
        content=f"<@&{ADVISER_ROLE_ID}>",
        embed=discord.Embed(
            title="🎫 RP Ticket Collect Reminder",
            description="Advisers, please collect your RP Ticket now.",
            colour=Theme.WARNING,
            timestamp=now_local(),
        ),
        allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO rp_ticket_reminders(
            session_id, reminder_at, reminder_message_id, claim_prompt_at
        ) VALUES (?, ?, ?, ?)
        """,
        (session_id, reminder_at.isoformat(), message.id, (reminder_at + timedelta(minutes=1)).isoformat()),
    )


async def post_rp_collect_prompt(session: sqlite3.Row, reminder: sqlite3.Row) -> None:
    channel = await get_text_channel(bot, RP_TICKET_REMINDER_CHANNEL_ID)
    if channel is None:
        return
    session_id = int(session["id"])
    reminder_at = datetime.fromisoformat(str(reminder["reminder_at"]))
    view = RPTicketCollectView(session_id, reminder_at)
    message = await channel.send(
        content=f"<@&{ADVISER_ROLE_ID}> who collected the RP Ticket?",
        view=view,
        allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
    )
    await db.execute(
        """
        UPDATE rp_ticket_reminders
        SET claim_message_id = ?
        WHERE session_id = ? AND reminder_at = ?
        """,
        (message.id, session_id, reminder_at.isoformat()),
    )
    bot.add_view(view, message_id=message.id)


@tasks.loop(seconds=15)
async def rp_ticket_scheduler() -> None:
    if not await is_system_enabled("rp_ticket_reminder"):
        return
    now = now_local()
    await ensure_automatic_rp_sessions()

    pending = await db.fetchall("SELECT * FROM rp_ticket_sessions WHERE status = 'pending_status'")
    for session in pending:
        if datetime.fromisoformat(str(session["result_due_at"])) <= now:
            try:
                await post_rp_status(session)
            except discord.HTTPException:
                log.exception("Could not post RP status for session %s", session["id"])

    active = await db.fetchall("SELECT * FROM rp_ticket_sessions WHERE status = 'active'")
    for session in active:
        _, scheduled = rp_factory_schedule(datetime.fromisoformat(str(session["started_at"])))
        for reminder_at in scheduled:
            if reminder_at > now:
                continue
            exists = await db.fetchone(
                "SELECT 1 FROM rp_ticket_reminders WHERE session_id = ? AND reminder_at = ?",
                (int(session["id"]), reminder_at.isoformat()),
            )
            if exists is None:
                try:
                    await post_rp_ticket_reminder(session, reminder_at)
                except discord.HTTPException:
                    log.exception("Could not post RP reminder for session %s", session["id"])

        reminders = await db.fetchall(
            """
            SELECT * FROM rp_ticket_reminders
            WHERE session_id = ? AND claim_message_id IS NULL AND claim_prompt_at <= ?
            ORDER BY reminder_at
            """,
            (int(session["id"]), now.isoformat()),
        )
        for reminder in reminders:
            try:
                await post_rp_collect_prompt(session, reminder)
            except discord.HTTPException:
                log.exception("Could not post RP collection prompt for session %s", session["id"])

        # Escalation pings (10 min) + missed-ticket handoff (60 min) for
        # slots whose collect prompt has gone out but nobody has clicked
        # "I Collected RP Ticket" yet.
        outstanding = await db.fetchall(
            """
            SELECT * FROM rp_ticket_reminders
            WHERE session_id = ? AND claim_message_id IS NOT NULL
            ORDER BY reminder_at
            """,
            (int(session["id"]),),
        )
        for reminder in outstanding:
            reminder_at = datetime.fromisoformat(str(reminder["reminder_at"]))

            collected = await db.fetchone(
                "SELECT 1 FROM rp_ticket_hourly_collections WHERE session_id = ? AND reminder_at = ?",
                (int(session["id"]), reminder["reminder_at"]),
            )
            if collected is not None:
                continue

            if reminder["escalated_at"] is None and now >= reminder_at + RP_TICKET_ESCALATION_DELAY:
                try:
                    await send_rp_ticket_escalation(session, reminder_at)
                except discord.HTTPException:
                    log.exception("Could not escalate RP ticket reminder for session %s", session["id"])

            if reminder["escalated_at"] is not None and now >= reminder_at + RP_TICKET_MISSED_DELAY:
                missed_exists = await db.fetchone(
                    "SELECT 1 FROM rp_ticket_missed_log WHERE session_id = ? AND reminder_at = ?",
                    (int(session["id"]), reminder["reminder_at"]),
                )
                if missed_exists is None:
                    try:
                        await send_rp_ticket_missed_prompt(session, reminder_at)
                    except discord.HTTPException:
                        log.exception("Could not post RP ticket missed prompt for session %s", session["id"])

        # Keep the session 'active' long enough for the last slot's full
        # escalation (10 min) + missed-flow (60 min) pipeline to run before
        # the session is marked completed.
        if scheduled and now > scheduled[-1] + timedelta(minutes=75):
            await db.execute(
                "UPDATE rp_ticket_sessions SET status = 'completed', next_reminder_at = NULL WHERE id = ?",
                (int(session["id"]),),
            )


@rp_ticket_scheduler.before_loop
async def before_rp_ticket_scheduler() -> None:
    await bot.wait_until_ready()


async def restore_rp_ticket_views(client: commands.Bot) -> None:
    status_rows = await db.fetchall(
        "SELECT id, status, status_message_id FROM rp_ticket_sessions WHERE status = 'awaiting_result'"
    )
    for row in status_rows:
        client.add_view(
            RPStatusView(int(row["id"])),
            message_id=(int(row["status_message_id"]) if row["status_message_id"] else None),
        )

    claim_rows = await db.fetchall(
        """
        SELECT session_id, reminder_at, claim_message_id
        FROM rp_ticket_reminders
        WHERE claim_message_id IS NOT NULL
        """
    )
    for row in claim_rows:
        client.add_view(
            RPTicketCollectView(
                int(row["session_id"]),
                datetime.fromisoformat(str(row["reminder_at"])),
            ),
            message_id=int(row["claim_message_id"]),
        )

    missed_rows = await db.fetchall(
        """
        SELECT session_id, reminder_at, prompt_message_id
        FROM rp_ticket_missed_log
        WHERE resolution IS NULL AND prompt_message_id IS NOT NULL
        """
    )
    for row in missed_rows:
        client.add_view(
            RPMissedTicketView(
                int(row["session_id"]),
                datetime.fromisoformat(str(row["reminder_at"])),
            ),
            message_id=int(row["prompt_message_id"]),
        )


@bot.command(name="testrpreminder")
@commands.guild_only()
async def test_rp_reminder_command(ctx: commands.Context) -> None:
    """Staff/owner test: post a sample RP Ticket Collect Reminder in the
    configured RP reminder channel so you can verify the system works."""
    if not isinstance(ctx.author, discord.Member):
        return
    if not (is_staff(ctx.author) or is_high_command(ctx.author) or (
        BOT_OWNER_ID > 0 and ctx.author.id == BOT_OWNER_ID
    )):
        await ctx.reply("❌ Only staff / high command can use this test command.", mention_author=False)
        return

    channel = await get_text_channel(bot, RP_TICKET_REMINDER_CHANNEL_ID)
    if channel is None:
        await ctx.reply(
            "❌ RP_TICKET_REMINDER_CHANNEL_ID is not configured or the channel was not found.",
            mention_author=False,
        )
        return

    enabled = await is_system_enabled("rp_ticket_reminder")
    status_note = "enabled" if enabled else "⚠️ currently **disabled** (system is off)"

    embed = discord.Embed(
        title="🎫 RP Ticket Collect Reminder (TEST)",
        description=(
            "Advisers, please collect your RP Ticket now.\n\n"
            f"*This is a test message triggered by {ctx.author.mention} via `!testrpreminder`.*\n"
            f"System status: {status_note}"
        ),
        colour=Theme.WARNING,
        timestamp=now_local(),
    )
    embed.set_footer(text="Test — no session was created and no scheduler entry was written.")

    try:
        await channel.send(
            content=f"<@&{ADVISER_ROLE_ID}>" if ADVISER_ROLE_ID > 0 else None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
        )
        await ctx.reply(
            f"✅ Test RP reminder posted in {channel.mention}.",
            mention_author=False,
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        await ctx.reply(
            f"❌ Failed to post test reminder: `{exc}`",
            mention_author=False,
        )

