# === Punishment & appeals (v2 — simplified) ===
# Loaded into shared namespace by bot.py — do not run this file alone.
#
# HOW IT WORKS NOW
#   !punish @member [reason]  -> small panel (auto-closes after 5 min)
#       ⚠️ Warn          -> type reason (+ optional days) -> done. ONE DM, ONE board post.
#       ➖ Remove Strike -> pick one of THIS member's strikes -> done.
#       📜 History       -> every warning this member ever got (private to you).
#       ✖ Close          -> deletes the panel.
#   !warnings [@member]   -> clean warning list (own, or anyone's for Jailers)
#   !allwarning           -> every member with active warnings (Jailers)
#   !remove @member 1|2|3|all <reason> -> remove a warning level (or all); the reason + remover
#                            are shown on the public board post
#   Punishment board post -> has an Appeal button; the post updates itself when the
#                            warning expires / is removed / is cleared by an appeal.
#   Appeals               -> 1 appeal per warning, simple form, Accept / Reject.
#
# PERMISSIONS: everything here (punish, remove strike, history, warnings of others,
# !allwarning, !remove, appeal review) is limited to the Jailer role -> see !jailer.
#
# Public names other files rely on (kept): active_warning_rows,
# restore_punishment_views, restore_punishment_appeal_views,
# warning_expiry_scheduler, expire_warnings.

PANEL_TIMEOUT_SECONDS = 300
MAX_WARNING_DAYS = 90


# ---------------------------------------------------------
# Small helpers
# ---------------------------------------------------------

def warning_case_id(warning_id: int) -> str:
    return f"W-{int(warning_id):05d}"


def strike_meter(count: int) -> str:
    count = max(0, min(int(count), MAX_ACTIVE_WARNINGS))
    return "🟥" * count + "⬜" * (MAX_ACTIVE_WARNINGS - count)


def short_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def warning_end_label(warning: sqlite3.Row) -> str:
    """Human label for a warning that is no longer active."""
    keys = warning.keys()
    reason = str(warning["end_reason"]) if "end_reason" in keys and warning["end_reason"] else ""
    if reason == "removed":
        return "✅ Removed by staff"
    if reason == "appeal":
        return "✅ Cleared by appeal"
    return "⌛ Expired"


async def active_warning_rows(
    guild_id: int,
    user_id: int,
) -> list[sqlite3.Row]:
    return await db.fetchall(
        """
        SELECT *
        FROM warnings
        WHERE guild_id = ?
          AND user_id = ?
          AND active = 1
          AND expires_at > ?
        ORDER BY level, issued_at
        """,
        (guild_id, user_id, now_local().isoformat()),
    )


async def fetch_guild_member(
    guild: discord.Guild,
    user_id: int,
) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def log_punishment_event(
    client: commands.Bot,
    title: str,
    description: str,
    colour: discord.Colour,
) -> None:
    """Best-effort log line in #punishment-logs (never raises)."""
    try:
        channel_id = await auto_log_channel_id("punishment_logs", 0)
        if not channel_id:
            return
        channel = await get_text_channel(client, channel_id)
        if channel is None:
            return
        await channel.send(
            embed=discord.Embed(
                title=title,
                description=description,
                colour=colour,
                timestamp=now_local(),
            )
        )
    except Exception:
        log.exception("Could not write punishment log.")


# ---------------------------------------------------------
# Punishment board post (public notice + Appeal button)
# ---------------------------------------------------------

def punishment_board_embed(
    warning: sqlite3.Row,
    member: discord.Member | None,
    issuer: discord.Member | None,
) -> discord.Embed:
    user_id = int(warning["user_id"])
    member_mention = member.mention if member else f"<@{user_id}>"
    issuer_mention = issuer.mention if issuer else f"<@{int(warning['issued_by'])}>"
    issued_at = datetime.fromisoformat(str(warning["issued_at"]))
    expires_at = datetime.fromisoformat(str(warning["expires_at"]))
    reason = short_text(warning["reason"], 1000) or "No reason provided."
    active = int(warning["active"]) == 1
    level = int(warning["level"])

    keys = warning.keys()
    ended_by = int(warning["ended_by"]) if "ended_by" in keys and warning["ended_by"] else 0
    end_note = str(warning["end_note"] or "").strip() if "end_note" in keys else ""
    ended_at = str(warning["ended_at"]) if "ended_at" in keys and warning["ended_at"] else ""

    if active:
        colour = Theme.WARNING
        status = f"Expires <t:{int(expires_at.timestamp())}:R>"
    else:
        colour = Theme.SUCCESS if warning_end_label(warning).startswith("✅") else discord.Colour.dark_grey()
        status = warning_end_label(warning)
        if ended_at:
            status += f" • <t:{int(datetime.fromisoformat(ended_at).timestamp())}:R>"

    embed = discord.Embed(
        title=f"⚠️ Warning {level}/{MAX_ACTIVE_WARNINGS}",
        description=f"{member_mention} was warned by {issuer_mention}.",
        colour=colour,
        timestamp=issued_at,
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Strikes", value=strike_meter(level), inline=True)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Case", value=f"`{warning_case_id(int(warning['id']))}`", inline=True)

    if not active:
        end_kind = str(warning["end_reason"] or "") if "end_reason" in keys else ""
        if end_kind == "removed":
            embed.add_field(
                name="Removed by",
                value=f"<@{ended_by}>" if ended_by else "Staff",
                inline=True,
            )
            embed.add_field(
                name="Removal reason",
                value=short_text(end_note, 1000) or "No reason given.",
                inline=False,
            )
        elif end_kind == "appeal":
            embed.add_field(
                name="Approved by",
                value=f"<@{ended_by}>" if ended_by else "Jailer",
                inline=True,
            )
            if end_note:
                embed.add_field(name="Note", value=short_text(end_note, 1000), inline=False)

    embed.set_footer(
        text=(
            "Think this is unfair? Press Appeal below."
            if active
            else "Brown Guns • Punishment Board"
        )
    )
    return embed


async def post_punishment_board(
    client: commands.Bot,
    warning_id: int,
    panel_id: int = 0,
) -> None:
    warning = await db.fetchone("SELECT * FROM warnings WHERE id = ?", (warning_id,))
    if warning is None:
        return

    channel = await get_text_channel(client, PUNISHMENT_BOARD_CHANNEL_ID)
    if channel is None:
        log.error("Punishment board channel %s was not found.", PUNISHMENT_BOARD_CHANNEL_ID)
        return

    guild = client.get_guild(int(warning["guild_id"]))
    member = guild.get_member(int(warning["user_id"])) if guild else None
    issuer = guild.get_member(int(warning["issued_by"])) if guild else None

    view = PunishmentAppealButton(client, warning_id, panel_id, int(warning["user_id"]))
    try:
        message = await channel.send(
            content=member.mention if member else f"<@{int(warning['user_id'])}>",
            embed=punishment_board_embed(warning, member, issuer),
            view=view,
        )
        await db.execute(
            """
            INSERT OR REPLACE INTO punishment_board_posts(
                warning_id, panel_id, channel_id, message_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (warning_id, panel_id, channel.id, message.id, now_local().isoformat()),
        )
        client.add_view(view, message_id=message.id)
    except discord.HTTPException:
        log.exception("Failed to post punishment notice for warning %s.", warning_id)


async def refresh_board_post(client: commands.Bot, warning_id: int) -> None:
    """Re-draw the board post of a warning (status line, remove Appeal button
    once the warning is no longer active)."""
    post = await db.fetchone(
        "SELECT * FROM punishment_board_posts WHERE warning_id = ?", (warning_id,)
    )
    warning = await db.fetchone("SELECT * FROM warnings WHERE id = ?", (warning_id,))
    if post is None or warning is None:
        return

    channel = await get_text_channel(client, int(post["channel_id"]))
    if channel is None:
        return

    guild = client.get_guild(int(warning["guild_id"]))
    member = guild.get_member(int(warning["user_id"])) if guild else None
    issuer = guild.get_member(int(warning["issued_by"])) if guild else None

    try:
        message = await channel.fetch_message(int(post["message_id"]))
        await message.edit(
            embed=punishment_board_embed(warning, member, issuer),
            view=None if int(warning["active"]) != 1 else message_view_or_none(
                client, warning
            ),
        )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        log.warning("Could not refresh punishment board post for warning %s.", warning_id)


def message_view_or_none(client: commands.Bot, warning: sqlite3.Row):
    return PunishmentAppealButton(
        client, int(warning["id"]), 0, int(warning["user_id"])
    )


# ---------------------------------------------------------
# Core actions (used by panel, appeals and the expiry loop)
# ---------------------------------------------------------

async def issue_warning(
    client: commands.Bot,
    guild: discord.Guild,
    member: discord.Member,
    issuer: discord.Member,
    reason: str,
    days: int,
) -> tuple[bool, str, int | None]:
    """Returns (ok, message_for_staff, warning_id)."""
    active = await active_warning_rows(guild.id, member.id)

    if len(active) >= MAX_ACTIVE_WARNINGS:
        return (
            False,
            f"❌ {member.mention} already has the maximum **{MAX_ACTIVE_WARNINGS}/{MAX_ACTIVE_WARNINGS}** warnings.",
            None,
        )

    # Lowest free level — fixes the old bug where removing Warning 2 made the
    # next warning a duplicate Warning 3.
    used_levels = {int(row["level"]) for row in active}
    level = next(n for n in range(1, MAX_ACTIVE_WARNINGS + 1) if n not in used_levels)

    # Warning roles are OPTIONAL. By default nobody gets a role: the warning is
    # tracked by the bot (database + punishment board) only. If you ever set
    # WARN_1/2/3_ROLE_ID with !config, the role is given as before.
    role = guild.get_role(WARNING_ROLE_IDS.get(level, 0))
    if role is not None:
        try:
            if role not in member.roles:
                await member.add_roles(role, reason=f"Warning {level}/{MAX_ACTIVE_WARNINGS} by {issuer}")
        except discord.Forbidden:
            return (
                False,
                "❌ I can't give that role. Move my bot role **above** all warning roles "
                "(or remove the warning roles with `!config clear WARN_1_ROLE_ID`).",
                None,
            )
        except discord.HTTPException:
            return False, "❌ Discord refused to give the warning role. Try again.", None

    issued_at = now_local()
    expires_at = issued_at + timedelta(days=days)

    warning_id = await db.execute(
        """
        INSERT INTO warnings(
            guild_id, user_id, level, role_id, reason,
            issued_by, issued_at, expires_at, active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            guild.id,
            member.id,
            level,
            role.id if role is not None else 0,
            reason,
            issuer.id,
            issued_at.isoformat(),
            expires_at.isoformat(),
        ),
    )

    # One clean DM.
    dm = discord.Embed(
        title=f"⚠️ You received Warning {level}/{MAX_ACTIVE_WARNINGS}",
        description=f"**Reason**\n{short_text(reason, 1500)}",
        colour=Theme.WARNING,
        timestamp=issued_at,
    )
    dm.add_field(name="Strikes", value=strike_meter(level), inline=True)
    dm.add_field(
        name="Expires",
        value=f"<t:{int(expires_at.timestamp())}:R>",
        inline=True,
    )
    if level >= MAX_ACTIVE_WARNINGS:
        dm.add_field(
            name="Heads up",
            value=f"You are now at the maximum **{MAX_ACTIVE_WARNINGS}/{MAX_ACTIVE_WARNINGS}** warnings.",
            inline=False,
        )
    dm.set_footer(text=f"{guild.name} • You can appeal on the punishment board.")
    dm_sent = True
    try:
        await member.send(embed=dm)
    except discord.HTTPException:
        dm_sent = False

    await post_punishment_board(client, warning_id, 0)

    await log_punishment_event(
        client,
        f"⚠️ Warning issued • {warning_case_id(warning_id)}",
        (
            f"**Member:** {member.mention} (`{member.id}`)\n"
            f"**By:** {issuer.mention}\n"
            f"**Level:** {level}/{MAX_ACTIVE_WARNINGS} • **Days:** {days}\n"
            f"**Reason:** {short_text(reason, 900)}"
        ),
        Theme.WARNING,
    )

    note = "" if dm_sent else "\n⚠️ Their DMs are closed, so they were not messaged."
    return (
        True,
        (
            f"✅ {member.mention} received **Warning {level}/{MAX_ACTIVE_WARNINGS}** "
            f"({warning_case_id(warning_id)}). Expires in **{days} day(s)**."
            f"{note}"
        ),
        warning_id,
    )


async def end_warning(
    client: commands.Bot,
    warning: sqlite3.Row,
    *,
    end_reason: str,  # 'expired' | 'removed' | 'appeal'
    ended_by: int | None = None,
    note: str = "",
    force: bool = False,
) -> tuple[bool, str]:
    """Take the role off, mark the warning inactive, refresh the board post.
    Returns (ok, error_text). With force=True the warning is closed even if
    the role could not be removed (used by the expiry loop)."""
    guild = client.get_guild(int(warning["guild_id"]))
    role_error = ""

    if guild is not None:
        role = guild.get_role(int(warning["role_id"]))
        member = await fetch_guild_member(guild, int(warning["user_id"]))
        if member is not None and role is not None and role in member.roles:
            try:
                await member.remove_roles(role, reason=f"Warning {warning_case_id(int(warning['id']))} {end_reason}")
            except discord.Forbidden:
                role_error = "I can't remove that role. Move my bot role above all warning roles."
            except discord.HTTPException:
                role_error = "Discord could not remove the warning role."

    if role_error and not force:
        return False, role_error
    if role_error:
        log.error("Warning %s: %s", warning["id"], role_error)

    await db.execute(
        """
        UPDATE warnings
        SET active = 0, end_reason = ?, ended_at = ?, ended_by = ?, end_note = ?
        WHERE id = ?
        """,
        (end_reason, now_local().isoformat(), ended_by, note or None, int(warning["id"])),
    )
    await refresh_board_post(client, int(warning["id"]))
    return True, ""


# ---------------------------------------------------------
# !punish panel
# ---------------------------------------------------------

async def punishment_panel_embed(
    guild: discord.Guild,
    target_id: int,
    saved_reason: str = "",
    expired: bool = False,
) -> discord.Embed:
    member = guild.get_member(target_id)
    active = await active_warning_rows(guild.id, target_id)

    embed = discord.Embed(
        title="⚠️ Punish Member",
        description=(
            f"{member.mention if member else f'<@{target_id}>'}\n"
            f"{strike_meter(len(active))}  **{len(active)}/{MAX_ACTIVE_WARNINGS} warnings**"
        ),
        colour=discord.Colour.dark_grey() if expired else Theme.WARNING,
    )
    if member is not None:
        embed.set_thumbnail(url=member.display_avatar.url)

    if active:
        lines = []
        for w in active:
            exp = datetime.fromisoformat(str(w["expires_at"]))
            lines.append(
                f"`{warning_case_id(int(w['id']))}` • **Warning {int(w['level'])}** • "
                f"expires <t:{int(exp.timestamp())}:R>\n"
                f"└ {short_text(w['reason'], 90) or '*no reason*'}"
            )
        embed.add_field(name="Active warnings", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Active warnings", value="✅ None — clean record.", inline=False)

    if saved_reason:
        embed.add_field(name="Reason (from command)", value=short_text(saved_reason, 500), inline=False)

    embed.set_footer(
        text=(
            "Panel expired — run !punish again."
            if expired
            else f"Press Warn, write the reason, done. Default length: {WARNING_DURATION_DAYS} days."
        )
    )
    return embed


class WarnModal(discord.ui.Modal):
    def __init__(self, panel: "PunishmentPanel") -> None:
        super().__init__(title="Issue Warning", timeout=300)
        self.panel = panel

        self.reason = discord.ui.TextInput(
            label="Reason (the member will see this)",
            placeholder="What did they do?",
            style=discord.TextStyle.paragraph,
            default=panel.saved_reason or None,
            min_length=3,
            max_length=500,
            required=True,
        )
        self.days = discord.ui.TextInput(
            label=f"Days until it expires (blank = {WARNING_DURATION_DAYS})",
            placeholder=str(WARNING_DURATION_DAYS),
            style=discord.TextStyle.short,
            max_length=3,
            required=False,
        )
        self.add_item(self.reason)
        self.add_item(self.days)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not is_jailer(interaction.user):
            await interaction.response.send_message("❌ Only the Jailer role can do this.", ephemeral=True)
            return

        raw_days = str(self.days.value).strip()
        if raw_days:
            if not raw_days.isdigit() or not (1 <= int(raw_days) <= MAX_WARNING_DAYS):
                await interaction.response.send_message(
                    f"❌ Days must be a number from 1 to {MAX_WARNING_DAYS}.",
                    ephemeral=True,
                )
                return
            days = int(raw_days)
        else:
            days = WARNING_DURATION_DAYS

        await interaction.response.defer(ephemeral=True, thinking=True)

        member = await fetch_guild_member(guild, self.panel.target_id)
        if member is None:
            await respond_or_followup(interaction, "❌ That member is no longer in the server.")
            return

        ok, text, _ = await issue_warning(
            self.panel.client,
            guild,
            member,
            interaction.user,
            str(self.reason.value).strip(),
            days,
        )
        await respond_or_followup(interaction, text)
        if ok:
            await self.panel.refresh()


async def remove_warning_with_notice(
    client: commands.Bot,
    guild: discord.Guild | None,
    warning: sqlite3.Row,
    removed_by: discord.Member,
    note: str,
) -> tuple[bool, str]:
    """Remove one active warning (role, DB, board post), DM the member and
    log it. Shared by the Remove Strike button and the !remove command.
    The board post is edited to show who removed it and why."""
    ok, error = await end_warning(
        client, warning, end_reason="removed", ended_by=removed_by.id, note=note
    )
    if not ok:
        return False, f"❌ {error}"

    member = await fetch_guild_member(guild, int(warning["user_id"])) if guild else None
    if member is not None:
        try:
            await member.send(
                f"✅ **Warning {int(warning['level'])}/{MAX_ACTIVE_WARNINGS} was removed** "
                f"by {removed_by.display_name}.\n**Reason:** {short_text(note, 900)}"
            )
        except discord.HTTPException:
            pass

    await log_punishment_event(
        client,
        f"➖ Strike removed • {warning_case_id(int(warning['id']))}",
        (
            f"**Member:** <@{int(warning['user_id'])}>\n"
            f"**Removed by:** {removed_by.mention}\n"
            f"**Reason:** {short_text(note, 900)}"
        ),
        Theme.SUCCESS,
    )
    return True, (
        f"✅ Removed **Warning {int(warning['level'])}** from <@{int(warning['user_id'])}>."
    )


class RemoveStrikeSelect(discord.ui.Select):
    def __init__(self, panel: "PunishmentPanel", warnings: list[sqlite3.Row]) -> None:
        self.panel = panel
        options = [
            discord.SelectOption(
                label=f"{warning_case_id(int(w['id']))} • Warning {int(w['level'])}/{MAX_ACTIVE_WARNINGS}",
                value=str(int(w["id"])),
                description=short_text(w["reason"], 95) or "No reason",
                emoji="➖",
            )
            for w in warnings
        ]
        super().__init__(placeholder="Which strike should be removed?", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_jailer(interaction.user):
            await interaction.response.send_message("❌ Only the Jailer role can do this.", ephemeral=True)
            return

        warning = await db.fetchone(
            "SELECT * FROM warnings WHERE id = ? AND active = 1", (int(self.values[0]),)
        )
        if warning is None:
            await interaction.response.edit_message(content="❌ That strike is already gone.", view=None)
            return

        await interaction.response.send_modal(RemoveReasonModal(self.panel, warning))


class RemoveReasonModal(discord.ui.Modal):
    def __init__(self, panel: "PunishmentPanel", warning: sqlite3.Row) -> None:
        super().__init__(
            title=f"Remove Warning {int(warning['level'])}/{MAX_ACTIVE_WARNINGS}",
            timeout=300,
        )
        self.panel = panel
        self.warning_id = int(warning["id"])
        self.reason = discord.ui.TextInput(
            label="Why are you removing this strike?",
            placeholder="Shown publicly on the board post and sent to the member.",
            style=discord.TextStyle.paragraph,
            min_length=3,
            max_length=500,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_jailer(interaction.user):
            await interaction.response.send_message("❌ Only the Jailer role can do this.", ephemeral=True)
            return

        await interaction.response.edit_message(content="⏳ Removing strike…", view=None)

        warning = await db.fetchone(
            "SELECT * FROM warnings WHERE id = ? AND active = 1", (self.warning_id,)
        )
        if warning is None:
            await interaction.edit_original_response(content="❌ That strike is already gone.")
            return

        ok, text = await remove_warning_with_notice(
            self.panel.client,
            interaction.guild,
            warning,
            interaction.user,
            str(self.reason.value).strip(),
        )
        await interaction.edit_original_response(content=text)
        if ok:
            await self.panel.refresh()


class RemoveStrikeView(discord.ui.View):
    def __init__(self, panel: "PunishmentPanel", warnings: list[sqlite3.Row]) -> None:
        super().__init__(timeout=120)
        self.add_item(RemoveStrikeSelect(panel, warnings))


class PunishmentPanel(discord.ui.View):
    """Temporary panel. Not persistent on purpose: no clutter, nothing to
    restore after a restart — it simply closes itself."""

    def __init__(
        self,
        client: commands.Bot,
        target_id: int,
        saved_reason: str = "",
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.client = client
        self.target_id = target_id
        self.saved_reason = saved_reason
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and is_jailer(interaction.user):
            return True
        await interaction.response.send_message("❌ Only the Jailer role can use this panel.", ephemeral=True)
        return False

    async def refresh(self) -> None:
        if self.message is None or self.message.guild is None:
            return
        try:
            await self.message.edit(
                embed=await punishment_panel_embed(self.message.guild, self.target_id, self.saved_reason),
                view=self,
            )
        except discord.HTTPException:
            pass

    async def on_timeout(self) -> None:
        if self.message is None or self.message.guild is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(
                embed=await punishment_panel_embed(
                    self.message.guild, self.target_id, self.saved_reason, expired=True
                ),
                view=self,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Warn", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def warn_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        active = await active_warning_rows(interaction.guild.id, self.target_id)
        if len(active) >= MAX_ACTIVE_WARNINGS:
            await interaction.response.send_message(
                f"❌ This member is already at **{MAX_ACTIVE_WARNINGS}/{MAX_ACTIVE_WARNINGS}** warnings.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(WarnModal(self))

    @discord.ui.button(label="Remove Strike", emoji="➖", style=discord.ButtonStyle.success)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        active = await active_warning_rows(interaction.guild.id, self.target_id)
        if not active:
            await interaction.response.send_message("✅ This member has no active strikes.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Pick the strike to remove:",
            view=RemoveStrikeView(self, active),
            ephemeral=True,
        )

    @discord.ui.button(label="History", emoji="📜", style=discord.ButtonStyle.secondary)
    async def history_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        await interaction.response.send_message(
            embed=await warning_history_embed(interaction.guild, self.target_id),
            ephemeral=True,
        )

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.defer()
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass


async def warning_history_embed(guild: discord.Guild, user_id: int) -> discord.Embed:
    rows = await db.fetchall(
        """
        SELECT * FROM warnings
        WHERE guild_id = ? AND user_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (guild.id, user_id),
    )
    embed = discord.Embed(
        title="📜 Warning history",
        description=f"<@{user_id}> • last {len(rows)} warning(s)",
        colour=Theme.PRIMARY,
    )
    if not rows:
        embed.add_field(name="Nothing here", value="No warnings on record.", inline=False)
        return embed

    now_iso = now_local().isoformat()
    for w in rows:
        if int(w["active"]) == 1 and str(w["expires_at"]) > now_iso:
            exp = datetime.fromisoformat(str(w["expires_at"]))
            status = f"🟠 Active • expires <t:{int(exp.timestamp())}:R>"
        else:
            status = warning_end_label(w)
        issued = datetime.fromisoformat(str(w["issued_at"]))
        embed.add_field(
            name=f"{warning_case_id(int(w['id']))} • Warning {int(w['level'])}/{MAX_ACTIVE_WARNINGS}",
            value=(
                f"{status}\n"
                f"By <@{int(w['issued_by'])}> • <t:{int(issued.timestamp())}:d>\n"
                f"{short_text(w['reason'], 200) or '*No reason*'}"
            ),
            inline=False,
        )
    return embed


@bot.command(name="punish")
async def punish_command(
    ctx: commands.Context,
    member: discord.Member,
    *,
    reason: str = "",
) -> None:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if not is_jailer(ctx.author):
        await ctx.reply("❌ Only the Jailer role can use this command.", mention_author=False)
        return

    if member.bot:
        await ctx.reply("❌ Bots cannot receive punishments.", mention_author=False)
        return

    if member.id == ctx.author.id:
        await ctx.reply("❌ You can't punish yourself.", mention_author=False)
        return

    saved_reason = short_text(reason, 500)
    view = PunishmentPanel(bot, member.id, saved_reason)
    view.message = await ctx.send(
        embed=await punishment_panel_embed(ctx.guild, member.id, saved_reason),
        view=view,
    )


# ---------------------------------------------------------
# !warnings
# ---------------------------------------------------------

@bot.command(name="warnings", aliases=["warns"])
async def warnings_command(
    ctx: commands.Context,
    member: discord.Member | None = None,
) -> None:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    target = member or ctx.author
    if target.id != ctx.author.id and not is_jailer(ctx.author):
        await ctx.reply("❌ You can only check your own warnings.", mention_author=False)
        return

    warnings = await active_warning_rows(ctx.guild.id, target.id)

    embed = discord.Embed(
        title="⚠️ Warnings",
        description=(
            f"{target.mention}\n"
            f"{strike_meter(len(warnings))}  **{len(warnings)}/{MAX_ACTIVE_WARNINGS}**"
        ),
        colour=Theme.WARNING if warnings else Theme.SUCCESS,
    )
    embed.set_thumbnail(url=target.display_avatar.url)

    if warnings:
        for w in warnings:
            exp = datetime.fromisoformat(str(w["expires_at"]))
            embed.add_field(
                name=f"Warning {int(w['level'])}/{MAX_ACTIVE_WARNINGS} • {warning_case_id(int(w['id']))}",
                value=(
                    f"{short_text(w['reason'], 300) or '*No reason provided.*'}\n"
                    f"Expires <t:{int(exp.timestamp())}:R>"
                ),
                inline=False,
            )
    else:
        embed.add_field(name="Status", value="✅ No active warnings.", inline=False)

    await ctx.reply(embed=embed, mention_author=False)


# ---------------------------------------------------------
# !allwarning  +  !remove @member <1|2|3|all>
# ---------------------------------------------------------

ALLWARNING_MEMBERS_PER_PAGE = 6


class AllWarningsView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed]) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.pages = pages
        self.index = 0
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and is_jailer(interaction.user):
            return True
        await interaction.response.send_message("❌ Only the Jailer role can use this.", ephemeral=True)
        return False

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = (self.index - 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = (self.index + 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


@bot.command(name="allwarning", aliases=["allwarnings", "warninglist"])
async def allwarning_command(ctx: commands.Context) -> None:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if not is_jailer(ctx.author):
        await ctx.reply("❌ Only the Jailer role can use this command.", mention_author=False)
        return

    rows = await db.fetchall(
        """
        SELECT * FROM warnings
        WHERE guild_id = ? AND active = 1 AND expires_at > ?
        ORDER BY user_id, level
        """,
        (ctx.guild.id, now_local().isoformat()),
    )

    if not rows:
        await ctx.reply("✅ Nobody has an active warning right now.", mention_author=False)
        return

    by_user: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        by_user.setdefault(int(row["user_id"]), []).append(row)

    # Members with the most strikes first.
    ordered = sorted(by_user.items(), key=lambda item: (-len(item[1]), item[0]))

    blocks: list[str] = []
    for user_id, warnings in ordered:
        lines = [
            f"<@{user_id}> • {strike_meter(len(warnings))} **{len(warnings)}/{MAX_ACTIVE_WARNINGS}**"
        ]
        for w in warnings:
            exp = datetime.fromisoformat(str(w["expires_at"]))
            lines.append(
                f"└ **W{int(w['level'])}** `{warning_case_id(int(w['id']))}` • "
                f"<t:{int(exp.timestamp())}:R> • {short_text(w['reason'], 45) or 'no reason'}"
            )
        blocks.append("\n".join(lines))

    pages: list[discord.Embed] = []
    total_pages = (len(blocks) + ALLWARNING_MEMBERS_PER_PAGE - 1) // ALLWARNING_MEMBERS_PER_PAGE
    for page_no in range(total_pages):
        chunk = blocks[
            page_no * ALLWARNING_MEMBERS_PER_PAGE : (page_no + 1) * ALLWARNING_MEMBERS_PER_PAGE
        ]
        embed = discord.Embed(
            title=f"⚠️ Active warnings • {len(by_user)} member(s), {len(rows)} warning(s)",
            description="\n\n".join(chunk),
            colour=Theme.WARNING,
        )
        embed.set_footer(
            text=(
                f"Page {page_no + 1}/{total_pages} • "
                "Remove with !remove @member 1/2/3/all <reason> or !punish @member"
            )
        )
        pages.append(embed)

    if len(pages) == 1:
        await ctx.reply(embed=pages[0], mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        return

    view = AllWarningsView(pages)
    view.message = await ctx.reply(
        embed=pages[0],
        view=view,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="remove", aliases=["unwarn"])
async def remove_warning_command(
    ctx: commands.Context,
    member: discord.Member,
    level: str,
    *,
    reason: str = "",
) -> None:
    """!remove @member 2 <reason>   -> removes that member's Warning 2
       !remove @member all <reason> -> removes every active warning
       The reason is required: it is shown on the public board post."""
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if not is_jailer(ctx.author):
        await ctx.reply("❌ Only the Jailer role can use this command.", mention_author=False)
        return

    quiet = discord.AllowedMentions.none()
    choice = level.strip().lower()
    reason = reason.strip()

    if len(reason) < 3:
        await ctx.reply(
            "❌ A reason is required (it is shown on the board post).\n"
            "Example: `!remove @member 2 Warned the wrong person`",
            mention_author=False,
        )
        return
    if len(reason) > 500:
        await ctx.reply("❌ Keep the reason under 500 characters.", mention_author=False)
        return

    active = await active_warning_rows(ctx.guild.id, member.id)

    if not active:
        await ctx.reply(f"✅ {member.mention} has no active warnings.", mention_author=False, allowed_mentions=quiet)
        return

    if choice == "all":
        targets = list(active)
    elif choice.isdigit() and 1 <= int(choice) <= MAX_ACTIVE_WARNINGS:
        targets = [w for w in active if int(w["level"]) == int(choice)]
        if not targets:
            have = ", ".join(str(int(w["level"])) for w in active)
            await ctx.reply(
                f"❌ {member.mention} has no Warning {int(choice)}. Their active warnings: **{have}**.",
                mention_author=False,
                allowed_mentions=quiet,
            )
            return
    else:
        await ctx.reply(
            f"❌ Use `!remove @member <1|2|3|all> <reason>` (levels 1–{MAX_ACTIVE_WARNINGS}).",
            mention_author=False,
        )
        return

    removed: list[str] = []
    errors: list[str] = []
    for warning in targets:
        ok, text = await remove_warning_with_notice(bot, ctx.guild, warning, ctx.author, reason)
        (removed if ok else errors).append(f"W{int(warning['level'])}" if ok else text)

    left = await active_warning_rows(ctx.guild.id, member.id)
    parts = []
    if removed:
        parts.append(
            f"✅ Removed **{', '.join(removed)}** from {member.mention}. "
            f"Now {strike_meter(len(left))} **{len(left)}/{MAX_ACTIVE_WARNINGS}**."
        )
    parts.extend(errors)
    await ctx.reply("\n".join(parts), mention_author=False, allowed_mentions=quiet)


# ---------------------------------------------------------
# Warning roles survive leave + rejoin
# ---------------------------------------------------------

@bot.listen("on_member_join")
async def reapply_warning_roles_on_join(member: discord.Member) -> None:
    """BUG FIX: a warned member who left and rejoined used to come back with
    a clean role list while the warning was still active in the database."""
    if member.bot or member.guild.id != GUILD_ID:
        return
    try:
        for warning in await active_warning_rows(member.guild.id, member.id):
            role = member.guild.get_role(int(warning["role_id"]))
            if role is not None and role not in member.roles:
                await member.add_roles(
                    role,
                    reason=f"Active warning {warning_case_id(int(warning['id']))} re-applied after rejoin",
                )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not re-apply warning roles to %s.", member.id)


# ---------------------------------------------------------
# Expiry loop
# ---------------------------------------------------------

async def expire_warnings(client: commands.Bot) -> None:
    expired = await db.fetchall(
        "SELECT * FROM warnings WHERE active = 1 AND expires_at <= ?",
        (now_local().isoformat(),),
    )
    for warning in expired:
        await end_warning(client, warning, end_reason="expired", force=True)


@tasks.loop(minutes=5)
async def warning_expiry_scheduler() -> None:
    await expire_warnings(bot)


@warning_expiry_scheduler.before_loop
async def before_warning_expiry_scheduler() -> None:
    await bot.wait_until_ready()


# ---------------------------------------------------------
# Appeals
# ---------------------------------------------------------

async def punishment_appeal_row(appeal_id: int) -> sqlite3.Row | None:
    return await db.fetchone("SELECT * FROM punishment_appeals WHERE id = ?", (appeal_id,))


def appeal_review_channel_id() -> int:
    return PUNISHMENT_APPEAL_CHANNEL_ID or COMPLAINT_REVIEW_CHANNEL_ID


class PunishmentAppealButton(discord.ui.View):
    def __init__(
        self,
        client: commands.Bot,
        warning_id: int,
        panel_id: int,
        target_user_id: int,
    ) -> None:
        super().__init__(timeout=None)
        self.client = client
        self.warning_id = warning_id
        self.panel_id = panel_id
        self.target_user_id = target_user_id

        button = discord.ui.Button(
            label="Appeal",
            emoji="📩",
            style=discord.ButtonStyle.primary,
            custom_id=f"punishment:appeal:{warning_id}",
        )
        button.callback = self.appeal_callback
        self.add_item(button)

    async def appeal_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message(
                "❌ Only the warned member can appeal this.", ephemeral=True
            )
            return

        warning = await db.fetchone("SELECT * FROM warnings WHERE id = ?", (self.warning_id,))
        if warning is None or int(warning["active"]) != 1:
            await interaction.response.send_message("❌ This warning is no longer active.", ephemeral=True)
            return

        existing = await db.fetchone(
            "SELECT status FROM punishment_appeals WHERE warning_id = ? ORDER BY id DESC LIMIT 1",
            (self.warning_id,),
        )
        if existing is not None:
            text = (
                "⚠️ Your appeal is already waiting for review."
                if str(existing["status"]) == "pending"
                else "❌ This warning was already appealed and reviewed. You can appeal each warning once."
            )
            await interaction.response.send_message(text, ephemeral=True)
            return

        await interaction.response.send_modal(
            PunishmentAppealModal(self.client, self.warning_id, self.panel_id)
        )


class PunishmentAppealModal(discord.ui.Modal):
    def __init__(self, client: commands.Bot, warning_id: int, panel_id: int) -> None:
        super().__init__(title="Appeal Warning", timeout=600)
        self.client = client
        self.warning_id = warning_id
        self.panel_id = panel_id

        self.description = discord.ui.TextInput(
            label="Why should this warning be reviewed?",
            placeholder="Explain what happened from your side.",
            style=discord.TextStyle.paragraph,
            min_length=10,
            max_length=1500,
            required=True,
        )
        self.proof = discord.ui.TextInput(
            label="Proof links (optional)",
            placeholder="Screenshots / video links",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=False,
        )
        self.game_id = discord.ui.TextInput(
            label="Your in-game ID (optional)",
            style=discord.TextStyle.short,
            max_length=50,
            required=False,
        )
        self.add_item(self.description)
        self.add_item(self.proof)
        self.add_item(self.game_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ Server not found.", ephemeral=True)
            return

        warning = await db.fetchone("SELECT * FROM warnings WHERE id = ?", (self.warning_id,))
        if warning is None or int(warning["active"]) != 1:
            await interaction.response.send_message("❌ This warning is no longer active.", ephemeral=True)
            return
        if int(warning["user_id"]) != interaction.user.id:
            await interaction.response.send_message("❌ You can't appeal someone else's warning.", ephemeral=True)
            return

        existing = await db.fetchone(
            "SELECT id FROM punishment_appeals WHERE warning_id = ? LIMIT 1", (self.warning_id,)
        )
        if existing is not None:
            await interaction.response.send_message("❌ This warning already has an appeal.", ephemeral=True)
            return

        review_channel_id = appeal_review_channel_id()
        review_channel = await get_text_channel(self.client, review_channel_id)
        if review_channel is None:
            await interaction.response.send_message(
                "❌ The appeal review channel is not set up. Tell a Jailer.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        created_at = now_local()
        reason = str(warning["reason"] or "").strip() or "No reason provided."
        proof = str(self.proof.value).strip()
        game_id = str(self.game_id.value).strip()

        appeal_id = await db.execute(
            """
            INSERT INTO punishment_appeals(
                guild_id, punishment_panel_id, warning_id,
                appellant_user_id, appellant_name, appellant_game_id,
                punished_by, punishment_reason, appeal_description, proof,
                review_channel_id, review_message_id, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'pending', ?)
            """,
            (
                interaction.guild.id,
                self.panel_id,
                self.warning_id,
                interaction.user.id,
                interaction.user.display_name,
                game_id or "-",
                int(warning["issued_by"]),
                reason,
                str(self.description.value).strip(),
                proof or None,
                review_channel_id,
                created_at.isoformat(),
            ),
        )

        embed = discord.Embed(
            title=f"📩 Appeal • PA-{appeal_id:05d}",
            description=f"{interaction.user.mention} is appealing **{warning_case_id(self.warning_id)}**.",
            colour=Theme.WARNING,
            timestamp=created_at,
        )
        embed.add_field(name="Status", value="🟡 **Pending review**", inline=True)
        embed.add_field(
            name="Warning",
            value=f"{int(warning['level'])}/{MAX_ACTIVE_WARNINGS}",
            inline=True,
        )
        embed.add_field(name="Warned by", value=f"<@{int(warning['issued_by'])}>", inline=True)
        if game_id:
            embed.add_field(name="In-game ID", value=game_id[:100], inline=True)
        embed.add_field(name="Original reason", value=short_text(reason, 1000), inline=False)
        embed.add_field(name="Their side", value=short_text(self.description.value, 1000), inline=False)
        embed.add_field(name="Proof", value=short_text(proof, 1000) if proof else "*None provided.*", inline=False)
        embed.set_footer(text="Jailers • Appeal Review")

        view = PunishmentAppealReviewView(self.client, appeal_id)
        message = await review_channel.send(embed=embed, view=view)
        await db.execute(
            "UPDATE punishment_appeals SET review_message_id = ? WHERE id = ?",
            (message.id, appeal_id),
        )
        self.client.add_view(view, message_id=message.id)

        await interaction.followup.send(
            "✅ Appeal sent to the Jailers. You'll get a DM with the result.", ephemeral=True
        )


async def finish_appeal_review_message(
    client: commands.Bot,
    appeal: sqlite3.Row,
    *,
    status_text: str,
    colour: discord.Colour,
    reviewer: discord.Member,
    result_name: str,
    result_value: str,
    footer: str,
) -> None:
    """Edit the review message (found by stored ids) and lock its buttons."""
    if not appeal["review_message_id"]:
        return
    channel = await get_text_channel(client, int(appeal["review_channel_id"]))
    if channel is None:
        return
    try:
        message = await channel.fetch_message(int(appeal["review_message_id"]))
        if not message.embeds:
            return
        embed = message.embeds[0]
        embed.colour = colour
        for index, field in enumerate(embed.fields):
            if field.name == "Status":
                embed.set_field_at(index, name="Status", value=status_text, inline=True)
        embed.add_field(name="Reviewed by", value=reviewer.mention, inline=True)
        embed.add_field(name=result_name, value=short_text(result_value, 1000), inline=False)
        embed.set_footer(text=footer)
        await message.edit(
            embed=embed,
            view=PunishmentAppealReviewView(client, int(appeal["id"]), disabled=True),
        )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        log.warning("Could not update appeal review message %s.", appeal["id"])


async def dm_appellant(
    client: commands.Bot,
    appeal: sqlite3.Row,
    text: str,
) -> None:
    try:
        user = client.get_user(int(appeal["appellant_user_id"])) or await client.fetch_user(
            int(appeal["appellant_user_id"])
        )
        await user.send(text)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


class PunishmentAppealRejectModal(discord.ui.Modal):
    def __init__(self, client: commands.Bot, appeal_id: int) -> None:
        super().__init__(title="Reject Appeal", timeout=300)
        self.client = client
        self.appeal_id = appeal_id
        self.reason = discord.ui.TextInput(
            label="Why is it rejected? (sent to the member)",
            style=discord.TextStyle.paragraph,
            min_length=3,
            max_length=1000,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_jailer(interaction.user):
            await interaction.response.send_message("❌ Only the Jailer role can review appeals.", ephemeral=True)
            return

        appeal = await punishment_appeal_row(self.appeal_id)
        if appeal is None or str(appeal["status"]) != "pending":
            await interaction.response.send_message("❌ This appeal is no longer pending.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        reason = str(self.reason.value).strip()

        await db.execute(
            """
            UPDATE punishment_appeals
            SET status='rejected', reviewed_by=?, reviewed_at=?, rejection_reason=?
            WHERE id=?
            """,
            (interaction.user.id, now_local().isoformat(), reason, self.appeal_id),
        )
        await finish_appeal_review_message(
            self.client,
            appeal,
            status_text="🔴 **Rejected**",
            colour=Theme.DANGER,
            reviewer=interaction.user,
            result_name="Rejection reason",
            result_value=reason,
            footer="Jailers • Appeal Rejected",
        )
        await dm_appellant(
            self.client,
            appeal,
            f"❌ **Your appeal PA-{self.appeal_id:05d} was rejected.**\n\n**Reason:** {reason}",
        )
        await log_punishment_event(
            self.client,
            f"❌ Appeal rejected • PA-{self.appeal_id:05d}",
            f"**Member:** <@{int(appeal['appellant_user_id'])}>\n**By:** {interaction.user.mention}\n**Reason:** {short_text(reason, 800)}",
            Theme.DANGER,
        )
        await interaction.followup.send("❌ Appeal rejected. The member was notified.", ephemeral=True)


class PunishmentAppealReviewView(discord.ui.View):
    def __init__(self, client: commands.Bot, appeal_id: int, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.client = client
        self.appeal_id = appeal_id

        accept = discord.ui.Button(
            label="Accept",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"punishment_appeal:accept:{appeal_id}",
            disabled=disabled,
        )
        reject = discord.ui.Button(
            label="Reject",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"punishment_appeal:reject:{appeal_id}",
            disabled=disabled,
        )
        accept.callback = self.accept_callback
        reject.callback = self.reject_callback
        self.add_item(accept)
        self.add_item(reject)

    async def check_hc(self, interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and is_jailer(interaction.user):
            return True
        await respond_or_followup(interaction, "❌ Only the Jailer role can review appeals.")
        return False

    async def accept_callback(self, interaction: discord.Interaction) -> None:
        if not (isinstance(interaction.user, discord.Member) and is_jailer(interaction.user)):
            await interaction.response.send_message("❌ Only the Jailer role can review appeals.", ephemeral=True)
            return

        # Ack first: role removal + edits can outlast Discord's 3-second window.
        await interaction.response.defer(ephemeral=True, thinking=True)

        appeal = await punishment_appeal_row(self.appeal_id)
        if appeal is None or str(appeal["status"]) != "pending":
            await respond_or_followup(interaction, "❌ This appeal is no longer pending.")
            return

        warning = await db.fetchone("SELECT * FROM warnings WHERE id = ?", (int(appeal["warning_id"]),))
        if warning is not None and int(warning["active"]) == 1:
            ok, error = await end_warning(
                self.client,
                warning,
                end_reason="appeal",
                ended_by=interaction.user.id,
                note=f"Appeal PA-{self.appeal_id:05d} accepted.",
            )
            if not ok:
                await respond_or_followup(interaction, f"❌ {error}")
                return

        await db.execute(
            """
            UPDATE punishment_appeals
            SET status='accepted', reviewed_by=?, reviewed_at=?
            WHERE id=?
            """,
            (interaction.user.id, now_local().isoformat(), self.appeal_id),
        )
        await finish_appeal_review_message(
            self.client,
            appeal,
            status_text="🟢 **Accepted**",
            colour=Theme.SUCCESS,
            reviewer=interaction.user,
            result_name="Result",
            result_value="Appeal accepted — the warning was removed.",
            footer="Jailers • Appeal Accepted",
        )
        await dm_appellant(
            self.client,
            appeal,
            f"✅ **Your appeal PA-{self.appeal_id:05d} was accepted.**\nYour warning has been removed.",
        )
        await log_punishment_event(
            self.client,
            f"✅ Appeal accepted • PA-{self.appeal_id:05d}",
            f"**Member:** <@{int(appeal['appellant_user_id'])}>\n**By:** {interaction.user.mention}",
            Theme.SUCCESS,
        )
        await respond_or_followup(interaction, "✅ Appeal accepted. Warning removed and member notified.")

    async def reject_callback(self, interaction: discord.Interaction) -> None:
        if not await self.check_hc(interaction):
            return
        appeal = await punishment_appeal_row(self.appeal_id)
        if appeal is None or str(appeal["status"]) != "pending":
            await interaction.response.send_message("❌ This appeal is no longer pending.", ephemeral=True)
            return
        await interaction.response.send_modal(PunishmentAppealRejectModal(self.client, self.appeal_id))


# ---------------------------------------------------------
# Startup restore (called from systems/startup.py)
# ---------------------------------------------------------

async def restore_punishment_appeal_views(client: commands.Bot) -> None:
    board_rows = await db.fetchall(
        """
        SELECT p.warning_id, p.panel_id, p.message_id, w.user_id
        FROM punishment_board_posts p
        JOIN warnings w ON w.id = p.warning_id
        WHERE w.active = 1
        """
    )
    for row in board_rows:
        client.add_view(
            PunishmentAppealButton(
                client,
                int(row["warning_id"]),
                int(row["panel_id"]),
                int(row["user_id"]),
            ),
            message_id=int(row["message_id"]),
        )

    appeal_rows = await db.fetchall(
        """
        SELECT id, review_message_id
        FROM punishment_appeals
        WHERE status = 'pending' AND review_message_id IS NOT NULL
        """
    )
    for row in appeal_rows:
        client.add_view(
            PunishmentAppealReviewView(client, int(row["id"])),
            message_id=int(row["review_message_id"]),
        )


async def restore_punishment_views(client: commands.Bot) -> None:
    # Panels are temporary now (they close themselves), so there is nothing
    # to restore. Kept so systems/startup.py keeps working unchanged.
    log.info("Punishment panels are temporary — nothing to restore.")
