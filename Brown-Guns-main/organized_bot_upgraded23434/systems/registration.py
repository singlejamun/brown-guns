# === Informal registration ===
# Source lines 1407-2534 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# PART 2 — INFORMAL REGISTRATION SYSTEM
# Paste this directly below Part 1.
# =========================================================


# One lock per registration session (slot_key). Serializes the
# "count current registrations -> decide main/waiting -> insert" block
# in register_callback so two near-simultaneous clicks can never both
# read the same "9/10" count and both slip into the main list.
registration_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def enforce_registration_capacity(
    client: commands.Bot,
    key: str,
) -> None:
    """Safety net: if registered members ever exceed MAX_REGISTRATIONS
    (e.g. from a bug, manual DB edit, or bot downtime), demote the
    newest overflow entrant(s) back to the waiting list and DM them.
    Safe to call any time; it's a no-op when capacity is not exceeded.
    """
    registered = await registration_rows(key, "registered")

    overflow = len(registered) - MAX_REGISTRATIONS

    if overflow <= 0:
        return

    # registered rows are ordered by position/joined_at, so the tail
    # is the newest entrant(s) that pushed the list over capacity.
    overflow_rows = registered[MAX_REGISTRATIONS:]

    guild = client.get_guild(GUILD_ID)
    roster_role = guild.get_role(INFORMAL_ROSTER_ROLE_ID) if guild else None

    for row in overflow_rows:
        user_id = int(row["user_id"])

        waiting = await registration_rows(key, "waiting")

        if len(waiting) >= MAX_WAITING_LIST:
            # No room even on the waiting list. Remove them entirely
            # and let them know, rather than silently over-filling.
            await db.execute(
                """
                DELETE FROM registration_members
                WHERE slot_key = ? AND user_id = ?
                """,
                (key, user_id),
            )
            notice = (
                "❌ A registration error put you in a slot that didn't "
                "exist (main list and waiting list were both full). "
                "You've been removed from this session — sorry about that."
            )
        else:
            # Put them at the FRONT of the waiting list: they were
            # already "in" before the correction, so they get first
            # right of promotion when a real slot opens.
            await db.execute(
                """
                UPDATE registration_members
                SET status = 'waiting',
                    position = 0
                WHERE slot_key = ? AND user_id = ?
                """,
                (key, user_id),
            )
            notice = (
                "⏳ A registration error briefly counted you as slot "
                f"#{MAX_REGISTRATIONS + 1} on the main list, which isn't "
                "a valid slot. You've been moved to the **front of the "
                "waiting list** instead and will be promoted automatically "
                "as soon as a main slot opens."
            )

        if roster_role is not None and guild is not None:
            member = guild.get_member(user_id)
            if member is not None and roster_role in member.roles:
                try:
                    await member.remove_roles(
                        roster_role,
                        reason=f"Capacity correction for session {key}",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

        if guild is not None:
            member = guild.get_member(user_id)
            if member is not None:
                try:
                    await member.send(notice)
                except discord.HTTPException:
                    pass

    await normalize_positions(key, "registered")
    await normalize_positions(key, "waiting")


async def registration_rows(
    key: str,
    status: str | None = None,
) -> list[sqlite3.Row]:
    if status is None:
        return await db.fetchall(
            """
            SELECT user_id, status, position, joined_at
            FROM registration_members
            WHERE slot_key = ?
            ORDER BY
                CASE status
                    WHEN 'registered' THEN 0
                    ELSE 1
                END,
                position,
                joined_at
            """,
            (key,),
        )

    return await db.fetchall(
        """
        SELECT user_id, status, position, joined_at
        FROM registration_members
        WHERE slot_key = ? AND status = ?
        ORDER BY position, joined_at
        """,
        (key, status),
    )


async def next_position(
    key: str,
    status: str,
) -> int:
    row = await db.fetchone(
        """
        SELECT COALESCE(MAX(position), 0) + 1 AS next_position
        FROM registration_members
        WHERE slot_key = ? AND status = ?
        """,
        (key, status),
    )

    return int(row["next_position"])


async def normalize_positions(
    key: str,
    status: str,
) -> None:
    rows = await registration_rows(key, status)

    for index, row in enumerate(rows, start=1):
        await db.execute(
            """
            UPDATE registration_members
            SET position = ?
            WHERE slot_key = ? AND user_id = ?
            """,
            (
                index,
                key,
                int(row["user_id"]),
            ),
        )


async def promote_next_waiting_member(
    client: commands.Bot,
    key: str,
) -> int | None:
    waiting_member = await db.fetchone(
        """
        SELECT user_id
        FROM registration_members
        WHERE slot_key = ?
          AND status = 'waiting'
        ORDER BY position, joined_at
        LIMIT 1
        """,
        (key,),
    )

    if waiting_member is None:
        return None

    user_id = int(waiting_member["user_id"])
    registered_position = await next_position(
        key,
        "registered",
    )

    await db.execute(
        """
        UPDATE registration_members
        SET status = 'registered',
            position = ?
        WHERE slot_key = ?
          AND user_id = ?
        """,
        (
            registered_position,
            key,
            user_id,
        ),
    )

    await normalize_positions(
        key,
        "waiting",
    )

    guild = client.get_guild(GUILD_ID)

    if guild is None:
        return user_id

    member = guild.get_member(user_id)

    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return user_id

    roster_role = guild.get_role(
        INFORMAL_ROSTER_ROLE_ID
    )

    if roster_role is not None:
        if roster_role not in member.roles:
            try:
                await member.add_roles(
                    roster_role,
                    reason=(
                        f"Promoted from informal waiting list {key}"
                    ),
                )
            except discord.Forbidden:
                log.error(
                    "Bot cannot add roster role to promoted member %s",
                    user_id,
                )
            except discord.HTTPException:
                log.exception(
                    "Discord error while promoting waiting member %s",
                    user_id,
                )

    try:
        await member.send(
            "🎉 **A slot opened in the informal registration!**\n\n"
            f"You were moved from the waiting list into the "
            f"**{slot_time(key)} informal**.\n"
            "You have received the Informal Roster role."
        )
    except discord.HTTPException:
        pass

    return user_id


async def registration_embed(
    key: str,
) -> discord.Embed:
    session = await db.fetchone(
        """
        SELECT closed, roles_removed
        FROM registration_sessions
        WHERE slot_key = ?
        """,
        (key,),
    )

    registered = await registration_rows(
        key,
        "registered",
    )
    waiting = await registration_rows(
        key,
        "waiting",
    )

    closed = bool(session["closed"]) if session else False

    if closed:
        status_text = f"{Theme.ICON_OFFLINE} Closed"
        colour = Theme.DANGER
    elif len(registered) >= MAX_REGISTRATIONS:
        if len(waiting) >= MAX_WAITING_LIST:
            status_text = f"{Theme.ICON_OFFLINE} Main & Waiting List Full"
            colour = Theme.DANGER
        else:
            status_text = f"{Theme.ICON_PENDING} Main Full — Waiting Open"
            colour = Theme.WARNING
    else:
        status_text = f"{Theme.ICON_ONLINE} Open"
        colour = Theme.SUCCESS

    embed = discord.Embed(
        title=f"{Theme.ICON_EVENT}  INFORMAL REGISTRATION — {slot_time(key)}",
        description=(
            f"{Theme.DIVIDER}\n"
            "Only members with the **Turfer** role can register.\n"
        ),
        colour=colour,
        timestamp=parse_slot(key),
    )

    embed.add_field(
        name=f"{Theme.ICON_ARROW}  Status",
        value=hud_field_block([
            ("Status", status_text),
            ("Closes", f"**{slot_time(key, REGISTRATION_CLOSE_MINUTE)}**"),
            ("Role Removed", f"**{slot_time(key, ROLE_REMOVE_MINUTE)}**"),
        ]),
        inline=False,
    )

    embed.add_field(
        name=f"{Theme.ICON_ARROW}  Main Slots",
        value=hud_progress_bar(len(registered), MAX_REGISTRATIONS),
        inline=True,
    )

    embed.add_field(
        name=f"{Theme.ICON_ARROW}  Waiting List",
        value=hud_progress_bar(len(waiting), MAX_WAITING_LIST),
        inline=True,
    )

    if registered:
        registered_text = "\n".join(
            f"`{index:02}` {Theme.ICON_ARROW} <@{int(row['user_id'])}>"
            for index, row in enumerate(
                registered,
                start=1,
            )
        )
    else:
        registered_text = "*No registered players yet.*"

    if waiting:
        waiting_text = "\n".join(
            f"`W{index:02}` {Theme.ICON_ARROW} <@{int(row['user_id'])}>"
            for index, row in enumerate(
                waiting,
                start=1,
            )
        )
    else:
        waiting_text = "*Waiting list is empty.*"

    embed.add_field(
        name=f"{Theme.ICON_ONLINE}  Registered Players",
        value=registered_text,
        inline=False,
    )

    embed.add_field(
        name=f"{Theme.ICON_PENDING}  Waiting List",
        value=waiting_text,
        inline=False,
    )

    embed.set_footer(
        text=(
            "Brown Guns Family • A waiting member is promoted automatically "
            "if a slot opens"
        )
    )

    return embed


class RegistrationView(discord.ui.View):
    def __init__(
        self,
        client: commands.Bot,
        key: str,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            timeout=None,
        )

        self.client = client
        self.key = key

        register_button = discord.ui.Button(
            label="Participate",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"informal:register:{key}",
            disabled=disabled,
        )

        cancel_button = discord.ui.Button(
            label="Cancel Registration",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"informal:cancel:{key}",
            disabled=disabled,
        )

        register_button.callback = self.register_callback
        cancel_button.callback = self.cancel_callback

        self.add_item(register_button)
        self.add_item(cancel_button)

    async def validate(
        self,
        interaction: discord.Interaction,
    ) -> tuple[discord.Member, discord.Role] | None:
        if not await is_system_enabled("informal_registration"):
            await send_system_disabled_interaction(interaction, "informal_registration")
            return None

        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ This button can only be used in the server.",
                ephemeral=True,
            )
            return None

        if interaction.guild.id != GUILD_ID:
            await interaction.response.send_message(
                "❌ Invalid registration panel.",
                ephemeral=True,
            )
            return None

        if not isinstance(
            interaction.user,
            discord.Member,
        ):
            await interaction.response.send_message(
                "❌ Member not found.",
                ephemeral=True,
            )
            return None

        roster_role = interaction.guild.get_role(
            INFORMAL_ROSTER_ROLE_ID
        )
        turfer_role = interaction.guild.get_role(
            TURFER_ROLE_ID
        )

        if roster_role is None:
            await interaction.response.send_message(
                "❌ Informal Roster role is not configured correctly.",
                ephemeral=True,
            )
            return None

        if turfer_role is None:
            await interaction.response.send_message(
                "❌ Turfer role is not configured correctly.",
                ephemeral=True,
            )
            return None

        if turfer_role not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ Only members with the **Turfer** role can register.",
                ephemeral=True,
            )
            return None

        current = now_local()

        if slot_key(current) != self.key:
            await interaction.response.send_message(
                "❌ This registration belongs to an old session.",
                ephemeral=True,
            )
            return None

        if current.minute >= REGISTRATION_CLOSE_MINUTE:
            await interaction.response.send_message(
                "❌ Registration is already closed.",
                ephemeral=True,
            )
            return None

        session = await db.fetchone(
            """
            SELECT closed
            FROM registration_sessions
            WHERE slot_key = ?
            """,
            (self.key,),
        )

        if session is None:
            await interaction.response.send_message(
                "❌ Registration session was not found.",
                ephemeral=True,
            )
            return None

        if session["closed"]:
            await interaction.response.send_message(
                "❌ Registration is already closed.",
                ephemeral=True,
            )
            return None

        return interaction.user, roster_role

    async def register_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        validated = await self.validate(interaction)

        if validated is None:
            return

        member, roster_role = validated

        # Acknowledge immediately. Under load (many people clicking when a
        # session opens, role edits being rate-limited, the per-session lock)
        # this callback can take longer than Discord's 3-second window, which
        # caused "404 Unknown interaction" errors.
        await interaction.response.defer(ephemeral=True)

        # Everything that reads "how many slots are taken" and then
        # decides main-vs-waiting-vs-full must be serialized per session,
        # otherwise two clicks arriving at nearly the same time can both
        # read e.g. 9/10 and both register into the main list -> 11/10.
        async with registration_locks[self.key]:
            existing = await db.fetchone(
                """
                SELECT status, position
                FROM registration_members
                WHERE slot_key = ?
                  AND user_id = ?
                """,
                (
                    self.key,
                    member.id,
                ),
            )

            if existing is not None:
                if existing["status"] == "registered":
                    message = (
                        "✅ You are already on the main registration list."
                    )
                else:
                    message = (
                        f"⏳ You are already on the waiting list at "
                        f"position **#{existing['position']}**."
                    )

                await interaction.followup.send(
                    message,
                    ephemeral=True,
                )
                return

            registered = await registration_rows(
                self.key,
                "registered",
            )

            if len(registered) < MAX_REGISTRATIONS:
                status = "registered"
                position = await next_position(
                    self.key,
                    "registered",
                )

                try:
                    if roster_role not in member.roles:
                        await member.add_roles(
                            roster_role,
                            reason=(
                                f"Registered for informal session {self.key}"
                            ),
                        )
                except discord.Forbidden:
                    await interaction.followup.send(
                        "❌ I cannot give the Informal Roster role. "
                        "Move my bot role above it.",
                        ephemeral=True,
                    )
                    return
                except discord.HTTPException:
                    await interaction.followup.send(
                        "❌ Discord could not give the role. Try again.",
                        ephemeral=True,
                    )
                    return
                    return

                response_text = (
                    "✅ You secured a main slot in the informal registration."
                )

            else:
                waiting = await registration_rows(
                    self.key,
                    "waiting",
                )

                if len(waiting) >= MAX_WAITING_LIST:
                    await interaction.followup.send(
                        "❌ All **10 main slots** and all "
                        "**5 waiting-list slots** are full.",
                        ephemeral=True,
                    )
                    return

                status = "waiting"
                position = await next_position(
                    self.key,
                    "waiting",
                )

                response_text = (
                    "⏳ The main list is full.\n"
                    f"You were added to the waiting list at "
                    f"position **#{position}/{MAX_WAITING_LIST}**."
                )

            try:
                await db.execute(
                    """
                    INSERT INTO registration_members(
                        slot_key,
                        user_id,
                        status,
                        position,
                        joined_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self.key,
                        member.id,
                        status,
                        position,
                        now_local().isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                await interaction.followup.send(
                    "❌ You are already registered or waiting.",
                    ephemeral=True,
                )
                return

            # Safety net: even though the lock above prevents the race,
            # this catches any leftover bad state (old data, manual DB
            # edits, etc.) so slot #11 can never persist on the panel.
            await enforce_registration_capacity(self.client, self.key)

        await interaction.followup.send(
            response_text,
            ephemeral=True,
        )

        await update_registration_message(
            self.client,
            self.key,
        )

    async def cancel_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        validated = await self.validate(interaction)

        if validated is None:
            return

        member, roster_role = validated

        # Acknowledge immediately. Under load (many people clicking when a
        # session opens, role edits being rate-limited, the per-session lock)
        # this callback can take longer than Discord's 3-second window, which
        # caused "404 Unknown interaction" errors.
        await interaction.response.defer(ephemeral=True)

        existing = await db.fetchone(
            """
            SELECT status
            FROM registration_members
            WHERE slot_key = ?
              AND user_id = ?
            """,
            (
                self.key,
                member.id,
            ),
        )

        if existing is None:
            await interaction.followup.send(
                "❌ You are not registered or waiting.",
                ephemeral=True,
            )
            return

        previous_status = str(existing["status"])

        if previous_status == "registered":
            if roster_role in member.roles:
                try:
                    await member.remove_roles(
                        roster_role,
                        reason=(
                            f"Cancelled informal session {self.key}"
                        ),
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        "❌ I cannot remove the Informal Roster role.",
                        ephemeral=True,
                    )
                    return
                except discord.HTTPException:
                    await interaction.followup.send(
                        "❌ Discord could not remove the role.",
                        ephemeral=True,
                    )
                    return

        await db.execute(
            """
            DELETE FROM registration_members
            WHERE slot_key = ?
              AND user_id = ?
            """,
            (
                self.key,
                member.id,
            ),
        )

        await normalize_positions(
            self.key,
            previous_status,
        )

        promoted_user_id = None

        if previous_status == "registered":
            promoted_user_id = await promote_next_waiting_member(
                self.client,
                self.key,
            )

        response_text = (
            "❌ Your informal registration has been cancelled."
        )

        if promoted_user_id is not None:
            response_text += (
                "\nThe first waiting-list member was promoted automatically."
            )

        await interaction.followup.send(
            response_text,
            ephemeral=True,
        )

        await update_registration_message(
            self.client,
            self.key,
        )


async def update_registration_message(
    client: commands.Bot,
    key: str,
) -> None:
    session = await db.fetchone(
        """
        SELECT channel_id, message_id, closed
        FROM registration_sessions
        WHERE slot_key = ?
        """,
        (key,),
    )

    if session is None:
        return

    if not session["message_id"]:
        return

    channel = await get_text_channel(
        client,
        int(session["channel_id"]),
    )

    if channel is None:
        return

    try:
        message = await channel.fetch_message(
            int(session["message_id"])
        )

        await message.edit(
            embed=await registration_embed(key),
            view=RegistrationView(
                client,
                key,
                disabled=bool(session["closed"]),
            ),
        )
    except discord.NotFound:
        log.warning(
            "Registration message %s was deleted.",
            session["message_id"],
        )
    except discord.Forbidden:
        log.error(
            "Bot cannot edit registration message %s.",
            session["message_id"],
        )
    except discord.HTTPException:
        log.exception(
            "Discord error updating registration message %s.",
            session["message_id"],
        )


async def create_registration(
    client: commands.Bot,
    key: str,
) -> None:
    existing = await db.fetchone(
        """
        SELECT message_id
        FROM registration_sessions
        WHERE slot_key = ?
        """,
        (key,),
    )

    if existing is not None and existing["message_id"]:
        return

    await db.execute(
        """
        INSERT OR IGNORE INTO registration_sessions(
            slot_key,
            channel_id,
            message_id,
            closed,
            roles_removed,
            created_at
        )
        VALUES (?, ?, NULL, 0, 0, ?)
        """,
        (
            key,
            REGISTRATION_CHANNEL_ID,
            now_local().isoformat(),
        ),
    )

    channel = await get_text_channel(
        client,
        REGISTRATION_CHANNEL_ID,
    )

    if channel is None:
        log.error(
            "Registration channel %s was not found.",
            REGISTRATION_CHANNEL_ID,
        )
        return

    view = RegistrationView(
        client,
        key,
    )

    try:
        message = await channel.send(
            content=f"<@&{TURFER_ROLE_ID}>",
            embed=await registration_embed(key),
            view=view,
            allowed_mentions=discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
            ),
        )
    except discord.Forbidden:
        log.error(
            "Bot cannot send messages in registration channel."
        )
        return
    except discord.HTTPException:
        log.exception(
            "Discord error creating registration message."
        )
        return

    await db.execute(
        """
        UPDATE registration_sessions
        SET message_id = ?
        WHERE slot_key = ?
        """,
        (
            message.id,
            key,
        ),
    )

    client.add_view(
        view,
        message_id=message.id,
    )

    log.info(
        "Created informal registration session %s",
        key,
    )


async def close_registration(
    client: commands.Bot,
    key: str,
) -> None:
    await db.execute(
        """
        UPDATE registration_sessions
        SET closed = 1
        WHERE slot_key = ?
        """,
        (key,),
    )

    await update_registration_message(
        client,
        key,
    )

    log.info(
        "Closed informal registration session %s",
        key,
    )


async def remove_roster_roles(
    client: commands.Bot,
    key: str,
) -> None:
    session = await db.fetchone(
        """
        SELECT roles_removed
        FROM registration_sessions
        WHERE slot_key = ?
        """,
        (key,),
    )

    if session is None:
        return

    if session["roles_removed"]:
        return

    guild = client.get_guild(GUILD_ID)

    if guild is None:
        log.error(
            "Guild %s was not found while removing roster roles.",
            GUILD_ID,
        )
        return

    roster_role = guild.get_role(
        INFORMAL_ROSTER_ROLE_ID
    )

    if roster_role is None:
        log.error(
            "Informal Roster role %s was not found.",
            INFORMAL_ROSTER_ROLE_ID,
        )
        return

    registered = await registration_rows(
        key,
        "registered",
    )

    for row in registered:
        user_id = int(row["user_id"])
        member = guild.get_member(user_id)

        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                continue

        if roster_role not in member.roles:
            continue

        try:
            await member.remove_roles(
                roster_role,
                reason=(
                    f"Informal session {key} ended"
                ),
            )
        except discord.Forbidden:
            log.error(
                "Bot cannot remove roster role from member %s.",
                user_id,
            )
        except discord.HTTPException:
            log.exception(
                "Discord error removing roster role from member %s.",
                user_id,
            )

    await db.execute(
        """
        UPDATE registration_sessions
        SET roles_removed = 1
        WHERE slot_key = ?
        """,
        (key,),
    )

    log.info(
        "Removed roster roles for informal session %s",
        key,
    )


# Hours (24h, local time) when the informal registration is skipped
# entirely — no session is created, closed, or cleaned up for that hour.
INFORMAL_REGISTRATION_SKIP_HOURS = {16, 22}  # 4 PM and 10 PM (RP Factory hours)


@tasks.loop(seconds=15)
async def registration_scheduler() -> None:
    if not await is_system_enabled("informal_registration"):
        return

    current = now_local().replace(
        second=0,
        microsecond=0,
    )

    if current.hour in INFORMAL_REGISTRATION_SKIP_HOURS:
        return

    key = slot_key(current)

    session = await db.fetchone(
        """
        SELECT closed, roles_removed, message_id
        FROM registration_sessions
        WHERE slot_key = ?
        """,
        (key,),
    )

    if current.minute < REGISTRATION_CLOSE_MINUTE:
        if session is None or not session["message_id"]:
            await create_registration(
                bot,
                key,
            )

    elif current.minute < ROLE_REMOVE_MINUTE:
        if session is not None and not session["closed"]:
            await close_registration(
                bot,
                key,
            )

    else:
        if session is not None and not session["roles_removed"]:
            await remove_roster_roles(
                bot,
                key,
            )


@registration_scheduler.before_loop
async def before_registration_scheduler() -> None:
    await bot.wait_until_ready()


async def restore_registration_views(
    client: commands.Bot,
) -> None:
    sessions = await db.fetchall(
        """
        SELECT slot_key, message_id, closed
        FROM registration_sessions
        WHERE message_id IS NOT NULL
        """
    )

    for session in sessions:
        client.add_view(
            RegistrationView(
                client,
                str(session["slot_key"]),
                disabled=bool(session["closed"]),
            ),
            message_id=int(session["message_id"]),
        )

    log.info(
        "Restored %s registration view(s).",
        len(sessions),
    )


# Part 3 must be pasted below this line.


