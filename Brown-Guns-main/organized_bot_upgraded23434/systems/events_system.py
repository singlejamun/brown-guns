# === Event system ===
# Source lines 7686-8753 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# EVENT SYSTEM
# =========================================================

STATE_TEAM_SIZE = 10
STATE_SELECT_MAX_OPTIONS = 25  # Discord select-menu hard limit
STATE_REGISTER_TIMEOUT_MINUTES = 40  # auto-close registration after this long


def is_state_leader(member: discord.Member) -> bool:
    if STATE_LEADER_ROLE_ID <= 0:
        return False
    return any(role.id == STATE_LEADER_ROLE_ID for role in member.roles)


def can_manage_state_team(member: discord.Member) -> bool:
    """State Leaders (and Rank 8-10 as a staff override) may pick the team."""
    return is_state_leader(member) or can_manage_events(member)


async def event_announce(channel: discord.TextChannel, event_name: str) -> None:
    content = (
        f"<@&{FAMILY_MEMBER_ROLE_ID}> "
        f"**Everyone start flying in city, we have {event_name}!**"
    )
    for index in range(4):
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
            ),
        )
        if index < 3:
            await asyncio.sleep(3)


async def state_registered_rows(session_id: int) -> list[sqlite3.Row]:
    return await db.fetchall(
        """
        SELECT user_id, position, joined_at
        FROM state_control_members
        WHERE session_id = ? AND status = 'registered'
        ORDER BY position, joined_at
        """,
        (session_id,),
    )


async def state_next_position(session_id: int) -> int:
    row = await db.fetchone(
        """
        SELECT COALESCE(MAX(position), 0) + 1 AS next_position
        FROM state_control_members
        WHERE session_id = ? AND status = 'registered'
        """,
        (session_id,),
    )
    return int(row["next_position"])


async def state_normalize(session_id: int) -> None:
    rows = await state_registered_rows(session_id)
    for index, row in enumerate(rows, start=1):
        await db.execute(
            """
            UPDATE state_control_members
            SET position = ?
            WHERE session_id = ? AND user_id = ?
            """,
            (index, session_id, int(row["user_id"])),
        )


async def state_team_rows(session_id: int) -> list[sqlite3.Row]:
    return await db.fetchall(
        """
        SELECT user_id, added_at
        FROM state_team_members
        WHERE session_id = ?
        ORDER BY added_at
        """,
        (session_id,),
    )


async def state_register_embed(session_id: int) -> discord.Embed:
    session = await db.fetchone(
        "SELECT * FROM state_control_sessions WHERE id = ?",
        (session_id,),
    )
    registered = await state_registered_rows(session_id)
    opened = bool(session and session["status"] == "open")

    embed = discord.Embed(
        title="🏛️ State Register",
        description=(
            "Register below to take part in State Control.\n"
            "A **State Leader** will pick the final team of "
            f"**{STATE_TEAM_SIZE}** using **Team Select**."
        ),
        colour=Theme.SUCCESS if opened else Theme.DANGER,
    )
    embed.add_field(
        name="Status",
        value="🟢 Open" if opened else "🔴 Closed",
        inline=True,
    )
    embed.add_field(
        name="Registered",
        value=f"**{len(registered)}**",
        inline=True,
    )
    embed.add_field(
        name="✅ Registered Members",
        value=(
            "\n".join(
                f"`{i:02}.` <@{int(row['user_id'])}>"
                for i, row in enumerate(registered, 1)
            )
            or "*No players registered.*"
        ),
        inline=False,
    )
    return embed


async def state_register_update(session_id: int) -> None:
    session = await db.fetchone(
        """
        SELECT channel_id, message_id, status
        FROM state_control_sessions
        WHERE id = ?
        """,
        (session_id,),
    )
    if not session or not session["message_id"]:
        return
    channel = await get_text_channel(bot, int(session["channel_id"]))
    if channel is None:
        return
    try:
        message = await channel.fetch_message(int(session["message_id"]))
        await message.edit(
            embed=await state_register_embed(session_id),
            view=StateRegisterView(
                session_id,
                disabled=session["status"] != "open",
            ),
        )
    except discord.NotFound:
        log.warning(
            "State Register message for session %s was deleted; nothing to update.",
            session_id,
        )
    except discord.HTTPException:
        log.exception("Could not update State Register message %s", session_id)


async def close_expired_state_registers() -> None:
    """Auto-close any State Register session that has been open for longer
    than STATE_REGISTER_TIMEOUT_MINUTES."""
    cutoff = now_local() - timedelta(minutes=STATE_REGISTER_TIMEOUT_MINUTES)

    expired_sessions = await db.fetchall(
        """
        SELECT id, channel_id, message_id
        FROM state_control_sessions
        WHERE status = 'open'
          AND created_at <= ?
        """,
        (cutoff.isoformat(),),
    )

    for session in expired_sessions:
        session_id = int(session["id"])

        await db.execute(
            """
            UPDATE state_control_sessions
            SET status = 'closed'
            WHERE id = ?
            """,
            (session_id,),
        )

        # Re-renders the embed as "🔴 Closed" and disables all three buttons.
        await state_register_update(session_id)

        channel = await get_text_channel(bot, int(session["channel_id"]))
        if channel is not None:
            try:
                await channel.send(
                    "⏰ State Register has been automatically closed after "
                    f"{STATE_REGISTER_TIMEOUT_MINUTES} minutes."
                )
            except discord.HTTPException:
                log.exception(
                    "Could not post auto-close notice for State Register %s",
                    session_id,
                )


@tasks.loop(minutes=1)
async def state_register_expiry_scheduler() -> None:
    await close_expired_state_registers()


@state_register_expiry_scheduler.before_loop
async def before_state_register_expiry_scheduler() -> None:
    await bot.wait_until_ready()


async def state_final_embed(session_id: int) -> discord.Embed:
    team = await state_team_rows(session_id)

    embed = discord.Embed(
        title="🏆 State Team — Final List",
        description="The State Leader has finalized the team below.",
        colour=Theme.SUCCESS,
        timestamp=now_local(),
    )
    embed.add_field(
        name=f"Team ({len(team)}/{STATE_TEAM_SIZE})",
        value=(
            "\n".join(
                f"`{i:02}.` <@{int(row['user_id'])}>"
                for i, row in enumerate(team, 1)
            )
            or "*No members selected.*"
        ),
        inline=False,
    )
    embed.set_footer(text="A State Leader can press Edit to change the team.")
    return embed


def _member_label(guild: discord.Guild | None, user_id: int) -> str:
    member = guild.get_member(user_id) if guild else None
    return member.display_name if member is not None else f"User {user_id}"


class StateTeamSelect(discord.ui.Select):
    """Dropdown used by the leader to pick (or re-pick) team members."""

    def __init__(
        self,
        parent: "StateTeamPickerView",
        candidates: list[sqlite3.Row],
        preselected: set[int],
        max_pick: int,
    ) -> None:
        limited = candidates[:STATE_SELECT_MAX_OPTIONS]
        options = [
            discord.SelectOption(
                label=_member_label(parent.guild, int(row["user_id"])),
                value=str(row["user_id"]),
                default=int(row["user_id"]) in preselected,
            )
            for row in limited
        ]

        super().__init__(
            placeholder=f"Pick up to {max_pick} member(s)...",
            min_values=0,
            max_values=max(1, min(max_pick, len(options))) if options else 1,
            options=options
            or [discord.SelectOption(label="No players registered", value="none")],
            disabled=not options,
        )
        self.owner_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if "none" in self.values:
            await interaction.response.defer()
            return

        self.owner_view.selected = {int(value) for value in self.values}
        self.owner_view.sync_final_button()
        await interaction.response.edit_message(
            content=self.owner_view.status_text(),
            view=self.owner_view,
        )


class StateTeamFinalButton(discord.ui.Button):
    def __init__(self, parent: "StateTeamPickerView") -> None:
        super().__init__(
            label="Team Final",
            emoji="🏁",
            style=discord.ButtonStyle.success,
            disabled=len(parent.selected) != STATE_TEAM_SIZE,
        )
        self.owner_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner_view.finalize(interaction)


class StateTeamPickerView(discord.ui.View):
    """Ephemeral picker shown to the State Leader. Used both for the
    initial Team Select and for the 'pick replacements' step of Edit."""

    def __init__(
        self,
        session_id: int,
        guild: discord.Guild,
        candidates: list[sqlite3.Row],
        preselected: set[int] | None = None,
        editing: bool = False,
    ) -> None:
        super().__init__(timeout=300)
        self.session_id = session_id
        self.guild = guild
        self.candidates = candidates
        self.selected: set[int] = set(preselected or set())
        self.editing = editing

        self.select_item = StateTeamSelect(
            self,
            candidates,
            self.selected,
            STATE_TEAM_SIZE,
        )
        self.final_button = StateTeamFinalButton(self)

        self.add_item(self.select_item)
        self.add_item(self.final_button)

    def sync_final_button(self) -> None:
        self.final_button.disabled = len(self.selected) != STATE_TEAM_SIZE

    def status_text(self) -> str:
        if len(self.selected) != STATE_TEAM_SIZE:
            return (
                f"**Team Select** — {len(self.selected)}/{STATE_TEAM_SIZE} chosen.\n"
                f"Press **Team Final** once exactly {STATE_TEAM_SIZE} are selected."
            )
        return (
            f"**Team Select** — {len(self.selected)}/{STATE_TEAM_SIZE} chosen.\n"
            "✅ Ready — press **Team Final** to lock it in."
        )

    async def finalize(self, interaction: discord.Interaction) -> None:
        if len(self.selected) != STATE_TEAM_SIZE:
            await interaction.response.send_message(
                f"❌ Select exactly {STATE_TEAM_SIZE} members first.",
                ephemeral=True,
            )
            return

        await db.execute(
            "DELETE FROM state_team_members WHERE session_id = ?",
            (self.session_id,),
        )
        for user_id in self.selected:
            await db.execute(
                """
                INSERT INTO state_team_members(session_id, user_id, added_at)
                VALUES (?, ?, ?)
                """,
                (self.session_id, user_id, now_local().isoformat()),
            )

        session = await db.fetchone(
            "SELECT * FROM state_control_sessions WHERE id = ?",
            (self.session_id,),
        )
        channel = await get_text_channel(bot, int(session["channel_id"]))

        if self.editing and session["final_message_id"] and channel is not None:
            try:
                final_message = await channel.fetch_message(
                    int(session["final_message_id"])
                )
                await final_message.edit(
                    embed=await state_final_embed(self.session_id),
                    view=StateFinalView(self.session_id),
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.exception(
                    "Could not update final State Team message %s",
                    self.session_id,
                )
            await interaction.response.edit_message(
                content="✅ Team updated.",
                view=None,
            )
            return

        # First-time finalization: post a new final message and remove the
        # old registration panel.
        final_message = await channel.send(
            embed=await state_final_embed(self.session_id),
        )
        view = StateFinalView(self.session_id)
        await final_message.edit(view=view)
        bot.add_view(view, message_id=final_message.id)

        old_message_id = session["message_id"]

        await db.execute(
            """
            UPDATE state_control_sessions
            SET status = 'final',
                final_message_id = ?,
                message_id = NULL
            WHERE id = ?
            """,
            (final_message.id, self.session_id),
        )

        if old_message_id and channel is not None:
            try:
                old_message = await channel.fetch_message(int(old_message_id))
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        await interaction.response.edit_message(
            content="✅ Team finalized. The final list has been posted.",
            view=None,
        )


class StateTeamRemoveSelect(discord.ui.Select):
    def __init__(self, parent: "StateTeamEditView", team: list[sqlite3.Row]) -> None:
        options = [
            discord.SelectOption(
                label=_member_label(parent.guild, int(row["user_id"])),
                value=str(row["user_id"]),
            )
            for row in team
        ]
        super().__init__(
            placeholder="Pick member(s) to remove (optional)...",
            min_values=0,
            max_values=max(1, len(options)) if options else 1,
            options=options
            or [discord.SelectOption(label="No team members", value="none")],
            disabled=not options,
        )
        self.owner_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if "none" in self.values:
            await interaction.response.defer()
            return
        self.owner_view.to_remove = {int(value) for value in self.values}
        await interaction.response.defer()


class StateTeamEditConfirmButton(discord.ui.Button):
    def __init__(self, parent: "StateTeamEditView") -> None:
        super().__init__(
            label="Continue",
            emoji="➡️",
            style=discord.ButtonStyle.primary,
        )
        self.owner_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner_view.confirm_and_continue(interaction)


class StateTeamEditView(discord.ui.View):
    """Step 1 of editing: show the current team and let the leader pick who
    to remove."""

    def __init__(
        self,
        session_id: int,
        guild: discord.Guild,
        team: list[sqlite3.Row],
    ) -> None:
        super().__init__(timeout=300)
        self.session_id = session_id
        self.guild = guild
        self.team = team
        self.to_remove: set[int] = set()

        self.add_item(StateTeamRemoveSelect(self, team))
        self.add_item(StateTeamEditConfirmButton(self))

    async def confirm_and_continue(self, interaction: discord.Interaction) -> None:
        if not self.to_remove:
            await interaction.response.edit_message(
                content="ℹ️ No members were removed — the team stays the same.",
                view=None,
            )
            return

        placeholders = ",".join("?" for _ in self.to_remove)
        await db.execute(
            f"""
            DELETE FROM state_team_members
            WHERE session_id = ? AND user_id IN ({placeholders})
            """,
            (self.session_id, *self.to_remove),
        )

        remaining = {
            int(row["user_id"]) for row in await state_team_rows(self.session_id)
        }
        candidates = await state_registered_rows(self.session_id)

        picker = StateTeamPickerView(
            self.session_id,
            self.guild,
            candidates,
            preselected=remaining,
            editing=True,
        )

        await interaction.response.edit_message(
            content=(
                "Full participant list — select replacements to fill the "
                f"team back to {STATE_TEAM_SIZE}.\n\n" + picker.status_text()
            ),
            view=picker,
        )


class StateFinalEditButton(discord.ui.Button):
    def __init__(self, session_id: int) -> None:
        super().__init__(
            label="Edit",
            emoji="✏️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"state:edit:{session_id}",
        )
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            return

        if not can_manage_state_team(interaction.user):
            await interaction.response.send_message(
                "❌ Only a State Leader can edit the team.",
                ephemeral=True,
            )
            return

        team = await state_team_rows(self.session_id)

        await interaction.response.send_message(
            "Current team — pick anyone you want to remove, then press Continue.",
            view=StateTeamEditView(self.session_id, interaction.guild, team),
            ephemeral=True,
        )


class StateFinalView(discord.ui.View):
    def __init__(self, session_id: int) -> None:
        super().__init__(timeout=None)
        self.session_id = session_id
        self.add_item(StateFinalEditButton(session_id))


class StateRegisterView(discord.ui.View):
    def __init__(self, session_id: int, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.session_id = session_id

        participate_button = discord.ui.Button(
            label="Participate",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"state:participate:{session_id}",
            disabled=disabled,
            row=0,
        )
        cancel_button = discord.ui.Button(
            label="Cancel Register",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"state:cancel:{session_id}",
            disabled=disabled,
            row=0,
        )
        team_select_button = discord.ui.Button(
            label="Team Select",
            emoji="🧩",
            style=discord.ButtonStyle.primary,
            custom_id=f"state:teamselect:{session_id}",
            disabled=disabled,
            row=0,
        )

        participate_button.callback = self.participate
        cancel_button.callback = self.cancel
        team_select_button.callback = self.team_select

        self.add_item(participate_button)
        self.add_item(cancel_button)
        self.add_item(team_select_button)

    async def participate(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            return

        family_role = interaction.guild.get_role(FAMILY_MEMBER_ROLE_ID)
        state_role = interaction.guild.get_role(STATE_ROSTER_ROLE_ID)

        if family_role is None or family_role not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ Only members with the Family Member role can participate.",
                ephemeral=True,
            )
            return

        session = await db.fetchone(
            "SELECT status FROM state_control_sessions WHERE id = ?",
            (self.session_id,),
        )
        if session is None or session["status"] != "open":
            await interaction.response.send_message(
                "❌ Registration is closed.",
                ephemeral=True,
            )
            return

        existing = await db.fetchone(
            """
            SELECT 1 FROM state_control_members
            WHERE session_id = ? AND user_id = ?
            """,
            (self.session_id, interaction.user.id),
        )
        if existing is not None:
            await interaction.response.send_message(
                "✅ You are already registered.",
                ephemeral=True,
            )
            return

        position = await state_next_position(self.session_id)
        await db.execute(
            """
            INSERT INTO state_control_members(
                session_id, user_id, status, position, joined_at
            )
            VALUES (?, ?, 'registered', ?, ?)
            """,
            (
                self.session_id,
                interaction.user.id,
                position,
                now_local().isoformat(),
            ),
        )

        if state_role is not None:
            try:
                if state_role not in interaction.user.roles:
                    await interaction.user.add_roles(
                        state_role,
                        reason="State Register participant",
                    )
            except (discord.Forbidden, discord.HTTPException):
                pass

        await interaction.response.send_message(
            "✅ You are now registered for State Control.",
            ephemeral=True,
        )
        await state_register_update(self.session_id)

    async def cancel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            return

        existing = await db.fetchone(
            """
            SELECT 1 FROM state_control_members
            WHERE session_id = ? AND user_id = ?
            """,
            (self.session_id, interaction.user.id),
        )
        if existing is None:
            await interaction.response.send_message(
                "❌ You are not registered.",
                ephemeral=True,
            )
            return

        await db.execute(
            """
            DELETE FROM state_control_members
            WHERE session_id = ? AND user_id = ?
            """,
            (self.session_id, interaction.user.id),
        )
        await state_normalize(self.session_id)

        state_role = interaction.guild.get_role(STATE_ROSTER_ROLE_ID)
        if state_role is not None and state_role in interaction.user.roles:
            try:
                await interaction.user.remove_roles(
                    state_role,
                    reason="Cancelled State Register",
                )
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            "❌ Your State Register entry was cancelled.",
            ephemeral=True,
        )
        await state_register_update(self.session_id)

    async def team_select(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            return

        if not can_manage_state_team(interaction.user):
            await interaction.response.send_message(
                "❌ Only a State Leader can use Team Select.",
                ephemeral=True,
            )
            return

        # Ack immediately. The lookups below can occasionally take longer
        # than Discord's 3-second interaction window (DB contention, etc.),
        # which previously caused the "Something went wrong handling that
        # button" fallback even though nothing was actually broken. Deferring
        # first guarantees we always have a valid token to respond with.
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            session = await db.fetchone(
                "SELECT status FROM state_control_sessions WHERE id = ?",
                (self.session_id,),
            )
            if session is None or session["status"] != "open":
                await interaction.followup.send(
                    "❌ This State Register session is no longer open.",
                    ephemeral=True,
                )
                return

            candidates = await state_registered_rows(self.session_id)
            if not candidates:
                await interaction.followup.send(
                    "❌ No one has registered yet — there is nothing to pick "
                    "a team from.",
                    ephemeral=True,
                )
                return

            existing_team = {
                int(row["user_id"])
                for row in await state_team_rows(self.session_id)
            }

            picker = StateTeamPickerView(
                self.session_id,
                interaction.guild,
                candidates,
                preselected=existing_team,
                editing=False,
            )

            await interaction.followup.send(
                picker.status_text(),
                view=picker,
                ephemeral=True,
            )
        except Exception:
            log.exception(
                "Error building Team Select picker (session %s, user %s)",
                self.session_id,
                interaction.user.id,
            )
            await interaction.followup.send(
                "❌ Something went wrong opening Team Select — please try "
                "again. If it keeps happening, ping the bot owner.",
                ephemeral=True,
            )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        log.exception(
            "Error in StateRegisterView (session %s, item custom_id=%s): %s",
            self.session_id,
            getattr(item, "custom_id", item),
            error,
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Something went wrong handling that button — "
                    "please try again. If it keeps happening, ping the bot owner.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Something went wrong handling that button — "
                    "please try again. If it keeps happening, ping the bot owner.",
                    ephemeral=True,
                )
        except discord.HTTPException:
            pass


async def create_state_control(interaction: discord.Interaction) -> None:
    channel = await get_text_channel(bot, STATE_REGISTER_CHANNEL_ID)

    if channel is None:
        await interaction.followup.send(
            "❌ State register channel is not configured correctly.",
            ephemeral=True,
        )
        return

    session_id = await db.execute(
        """
        INSERT INTO state_control_sessions(
            guild_id, channel_id, created_by, status, created_at
        )
        VALUES (?, ?, ?, 'open', ?)
        """,
        (
            interaction.guild.id,
            channel.id,
            interaction.user.id,
            now_local().isoformat(),
        ),
    )

    view = StateRegisterView(session_id)

    message = await channel.send(
        content=f"<@&{FAMILY_MEMBER_ROLE_ID}>",
        embed=await state_register_embed(session_id),
        view=view,
        allowed_mentions=discord.AllowedMentions(
            roles=True,
            users=False,
            everyone=False,
        ),
    )

    await db.execute(
        """
        UPDATE state_control_sessions
        SET message_id = ?
        WHERE id = ?
        """,
        (message.id, session_id),
    )

    bot.add_view(view, message_id=message.id)


class EventPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

        for label, emoji, key in (
            ("Foundry", "🏭", "foundry"),
            ("RP Factory", "🔧", "rp_factory"),
            ("Bizwar", "⚔️", "bizwar"),
            ("Weps", "🔫", "weps"),
            ("State Control", "🏛️", "state_control"),
        ):
            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=(
                    discord.ButtonStyle.success
                    if key == "state_control"
                    else discord.ButtonStyle.primary
                ),
                custom_id=f"event:{key}",
            )
            button.callback = self.callback_factory(label, key)
            self.add_item(button)

    def callback_factory(self, label: str, key: str):
        async def callback(interaction: discord.Interaction) -> None:
            if not await is_system_enabled("event_panel"):
                await send_system_disabled_interaction(interaction, "event_panel")
                return

            if not isinstance(interaction.user, discord.Member):
                return

            if not can_manage_events(interaction.user):
                await interaction.response.send_message(
                    "❌ Only Rank 8, Rank 9, and Rank 10 members can use the Event Panel.",
                    ephemeral=True,
                )
                return

            await send_event_click_log(
                bot,
                interaction,
                label,
            )

            await interaction.response.send_message(
                f"✅ {label} started.",
                ephemeral=True,
            )

            if key == "state_control":
                await create_state_control(interaction)
            else:
                event_channel = await get_text_channel(
                    bot,
                    EVENT_PING_CHANNEL_ID,
                )

                if event_channel is None:
                    await interaction.followup.send(
                        "❌ Event ping channel is not configured correctly.",
                        ephemeral=True,
                    )
                    return

                await event_announce(
                    event_channel,
                    label,
                )

                if key == "rp_factory":
                    await interaction.followup.send(
                        "ℹ️ RP result panels are automatic at **4:25 PM** and **10:25 PM**.",
                        ephemeral=True,
                    )

        return callback


def event_panel_embed() -> discord.Embed:
    return discord.Embed(
        title=f"{Theme.ICON_EVENT}  FAMILY EVENT PANEL",
        description=(
            f"{Theme.DIVIDER}\n"
            f"{Theme.ICON_LOCK} Restricted to **High Command**\n\n"
            + hud_field_block([
                ("🏭", "Foundry"),
                ("🔧", "RP Factory"),
                ("⚔️", "Bizwar"),
                ("🔫", "Weps"),
                ("🏛️", "State Control"),
            ])
        ),
        colour=Theme.ACCENT,
    )


@bot.command(name="eventpanel")
async def eventpanel_command(ctx: commands.Context) -> None:
    """Deploy the Event Panel from the private control server."""
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if ctx.guild.id != CONTROL_GUILD_ID:
        await ctx.reply(
            "❌ Use this command in the private database/control server.",
            mention_author=False,
        )
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
            "❌ Only the bot owner or a control administrator can use this command.",
            mention_author=False,
        )
        return

    main_guild = bot.get_guild(GUILD_ID)
    target_channel = await get_text_channel(
        bot,
        MAIN_EVENT_PANEL_CHANNEL_ID,
    )

    if main_guild is None:
        await ctx.reply(
            "❌ The main server is unavailable.",
            mention_author=False,
        )
        return

    if target_channel is None or target_channel.guild.id != GUILD_ID:
        await ctx.reply(
            "❌ MAIN_EVENT_PANEL_CHANNEL_ID is invalid.",
            mention_author=False,
        )
        return

    view = EventPanelView()

    try:
        message = await target_channel.send(
            embed=event_panel_embed(),
            view=view,
        )
    except discord.HTTPException:
        await ctx.reply(
            "❌ I could not deploy the Event Panel in the main server.",
            mention_author=False,
        )
        return

    bot.add_view(view, message_id=message.id)

    await ctx.reply(
        f"✅ Event Panel deployed in {target_channel.mention} "
        f"inside **{main_guild.name}**.",
        mention_author=False,
    )


async def restore_event_views(client: commands.Bot) -> None:
    client.add_view(EventPanelView())

    open_sessions = await db.fetchall(
        """
        SELECT id, message_id, status
        FROM state_control_sessions
        WHERE message_id IS NOT NULL
          AND status = 'open'
        """
    )
    for session in open_sessions:
        client.add_view(
            StateRegisterView(
                int(session["id"]),
                disabled=session["status"] != "open",
            ),
            message_id=int(session["message_id"]),
        )

    final_sessions = await db.fetchall(
        """
        SELECT id, final_message_id
        FROM state_control_sessions
        WHERE final_message_id IS NOT NULL
          AND status = 'final'
        """
    )
    for session in final_sessions:
        client.add_view(
            StateFinalView(int(session["id"])),
            message_id=int(session["final_message_id"]),
        )




