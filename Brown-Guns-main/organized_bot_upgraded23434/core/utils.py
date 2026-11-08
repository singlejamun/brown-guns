# === Shared utilities ===
# Source lines 211-841 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def slot_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d-%H")


def parse_slot(key: str, minute: int = 0) -> datetime:
    return datetime.strptime(key, "%Y-%m-%d-%H").replace(
        tzinfo=TIMEZONE,
        minute=minute,
        second=0,
        microsecond=0,
    )


def slot_time(key: str, minute: int = 0) -> str:
    return parse_slot(key, minute).strftime("%I:%M %p")


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    return any(role.id in STAFF_ROLE_IDS for role in member.roles)


def is_high_command(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    allowed = HIGH_COMMAND_ROLE_IDS or STAFF_ROLE_IDS
    return any(role.id in allowed for role in member.roles)


def is_jailer(member: discord.Member) -> bool:
    """Only members holding the Jailer role (set with !jailer @role) may give
    punishments, remove strikes and review complaints / POV requests.
    Until a Jailer role is configured this falls back to high command, so the
    bot never locks everyone out before the role has been set."""
    if JAILER_ROLE_ID:
        return any(role.id == JAILER_ROLE_ID for role in member.roles)
    return is_high_command(member)


async def jailer_only(interaction: discord.Interaction) -> bool:
    """Interaction guard for Jailer-only buttons and forms."""
    if not isinstance(interaction.user, discord.Member):
        await respond_or_followup(interaction, "❌ Member information could not be found.")
        return False
    if not is_jailer(interaction.user):
        await respond_or_followup(interaction, "❌ Only members with the Jailer role can use this.")
        return False
    return True


def can_manage_events(member: discord.Member) -> bool:
    """Only Rank 8, Rank 9, or Rank 10 members can manage family events."""
    allowed_role_ids = {
        role_id
        for role_id in (
            RANK_ROLE_IDS.get(8, 0),
            RANK_9_ROLE_ID,
            RANK_10_ROLE_ID,
        )
        if role_id > 0
    }

    return any(
        role.id in allowed_role_ids
        for role in member.roles
    )


async def staff_only(interaction: discord.Interaction) -> bool:
    """Allow only configured staff or members with administrator/manage-server."""
    if not isinstance(interaction.user, discord.Member):
        if interaction.response.is_done():
            await interaction.followup.send(
                "❌ Member information could not be found.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Member information could not be found.",
                ephemeral=True,
            )
        return False

    if not is_staff(interaction.user):
        if interaction.response.is_done():
            await interaction.followup.send(
                "❌ Only authorized family staff can use this control.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Only authorized family staff can use this control.",
                ephemeral=True,
            )
        return False

    return True


async def respond_or_followup(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    ephemeral: bool = True,
) -> None:
    """Send a message on an interaction whether or not it has already
    been responded to (deferred/responded). Prevents 'Unknown interaction'
    (404 / 10062) errors when a callback deferred earlier or already used
    its single initial response."""
    if interaction.response.is_done():
        await interaction.followup.send(
            content=content,
            embed=embed,
            ephemeral=ephemeral,
        )
    else:
        await interaction.response.send_message(
            content=content,
            embed=embed,
            ephemeral=ephemeral,
        )


async def get_text_channel(
    client: discord.Client,
    channel_id: int,
) -> discord.TextChannel | None:
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return channel if isinstance(channel, discord.TextChannel) else None



def is_control_admin(member: discord.Member) -> bool:
    """Allow owner, configured admins, and members granted with !controlaccess."""
    if member.id == BOT_OWNER_ID:
        return True

    if member.id in CONTROL_ACCESS_USER_IDS:
        return True

    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True

    return any(
        role.id in CONTROL_ADMIN_ROLE_IDS
        for role in member.roles
    )


_control_log_locks: "defaultdict[int, asyncio.Lock]" = defaultdict(asyncio.Lock)
CONTROL_LOG_MIN_INTERVAL = 1.0  # seconds between log posts in the same channel


async def send_control_log(
    client: discord.Client,
    channel_id: int,
    embed: discord.Embed,
) -> None:
    """Send a private log to a channel in the control/database server."""
    if channel_id <= 0:
        return

    channel = await get_text_channel(client, channel_id)

    if channel is None:
        log.warning("Control channel %s was not found.", channel_id)
        return

    # Role changes / voice moves can fire dozens of events at once (e.g. when an
    # informal session opens or the voice panel drags everyone). Sending them all
    # at the same time floods Discord's 5-messages-per-5-seconds channel limit and
    # produced hundreds of 429 warnings. Queue them per channel instead.
    async with _control_log_locks[channel_id]:
        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.exception("Could not send a control log to %s.", channel_id)
        await asyncio.sleep(CONTROL_LOG_MIN_INTERVAL)


def control_log_embed(
    title: str,
    *,
    description: str | None = None,
    colour: discord.Colour = Theme.PRIMARY,
) -> discord.Embed:
    """Shared builder for every control-server log embed (rank, complaint, mod, etc.)."""
    embed = discord.Embed(
        title=f"{Theme.ICON_SPARK}  {title}",
        description=description,
        colour=colour,
        timestamp=now_local(),
    )
    embed.set_footer(text="Brown Guns Family • Control Log")
    return embed


async def find_audit_actor(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: int | None = None,
):
    """Best-effort lookup of the moderator responsible for a recent action."""
    try:
        async for entry in guild.audit_logs(limit=8, action=action):
            entry_target_id = getattr(entry.target, "id", None)

            if target_id is not None and entry_target_id != target_id:
                continue

            if now_local().timestamp() - entry.created_at.timestamp() > 25:
                continue

            return entry.user, entry.reason
    except (discord.Forbidden, discord.HTTPException):
        pass

    return None, None


def parse_user_id(value: str) -> int | None:
    match = re.search(r"\d{15,22}", value)
    return int(match.group(0)) if match else None


async def get_main_member(
    client: discord.Client,
    user_id: int,
) -> discord.Member | None:
    guild = client.get_guild(GUILD_ID)

    if guild is None:
        return None

    member = guild.get_member(user_id)

    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_id)
    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
    ):
        return None


async def send_rank_review_log(
    client: discord.Client,
    request: sqlite3.Row,
    *,
    decision: str,
    reviewer,
    reason: str | None = None,
    nickname_changed: bool | None = None,
) -> None:
    rank = int(request["requested_rank"])

    accepted = decision == "accepted"

    embed = control_log_embed(
        f"{Theme.ICON_RANK} Rank Request {'Accepted' if accepted else 'Rejected'}",
        colour=Theme.SUCCESS if accepted else Theme.DANGER,
        description=Theme.DIVIDER,
    )

    embed.add_field(
        name=f"{Theme.ICON_ARROW} Request",
        value=hud_field_block([
            ("ID", f"#{int(request['id'])}"),
            ("Member", f"<@{int(request['requester_id'])}>"),
            ("Reviewer", f"<@{reviewer.id}>"),
        ]),
        inline=False,
    )
    embed.add_field(
        name=f"{Theme.ICON_ARROW} In-Game Details",
        value=hud_field_block([
            ("Name", str(request["ingame_name"] or "Unknown")),
            ("ID", str(request["ingame_id"] or "Unknown")),
            ("Invited By", str(request["invited_by"] or "Not Provided")),
        ]),
        inline=False,
    )
    embed.add_field(
        name=f"{Theme.ICON_ARROW} Requested Rank",
        value=f"**{rank} | {RANK_NAMES.get(rank, 'Unknown Rank')}**",
        inline=True,
    )

    if nickname_changed is not None:
        embed.add_field(
            name=f"{Theme.ICON_ARROW} Nickname Updated",
            value=f"{Theme.ICON_ONLINE} Yes" if nickname_changed else f"{Theme.ICON_WARN} No",
            inline=True,
        )

    if reason:
        embed.add_field(name=f"{Theme.ICON_ARROW} Reason", value=reason[:1024], inline=False)

    await send_control_log(
        client,
        await auto_log_channel_id("rank_review_logs", CONTROL_RANK_LOG_CHANNEL_ID),
        embed,
    )


async def send_complaint_review_log(
    client: discord.Client,
    complaint: sqlite3.Row,
    *,
    decision: str,
    reviewer,
    result_text: str,
) -> None:
    solved = decision == "solved"

    embed = control_log_embed(
        f"{Theme.ICON_COMPLAINT} Complaint {'Solved' if solved else 'Rejected'}",
        colour=Theme.SUCCESS if solved else Theme.DANGER,
        description=Theme.DIVIDER,
    )

    embed.add_field(
        name=f"{Theme.ICON_ARROW} Case",
        value=hud_field_block([
            ("ID", f"#{int(complaint['id'])}"),
            ("Complainant", f"<@{int(complaint['complainant_user_id'])}>"),
            ("Reviewer", f"<@{reviewer.id}>"),
        ]),
        inline=False,
    )
    embed.add_field(
        name=f"{Theme.ICON_ARROW} In-Game IDs",
        value=hud_field_block([
            ("Complainant", str(complaint["complainant_game_id"])),
            ("Accused", str(complaint["accused_game_id"])),
        ]),
        inline=False,
    )
    embed.add_field(
        name=f"{Theme.ICON_ARROW} {'Punishment Given' if solved else 'Reject Reason'}",
        value=result_text[:1024],
        inline=False,
    )

    await send_control_log(
        client,
        await auto_log_channel_id("complaint_logs", CONTROL_COMPLAINT_LOG_CHANNEL_ID),
        embed,
    )


async def send_welcome_message(member: discord.Member) -> None:
    """Send a simple welcome message in the configured main-server channel."""
    if WELCOME_CHANNEL_ID <= 0:
        return

    channel = await get_text_channel(bot, WELCOME_CHANNEL_ID)
    if channel is None or channel.guild.id != GUILD_ID:
        log.warning("WELCOME_CHANNEL_ID is not a valid main-server text channel.")
        return

    embed = discord.Embed(
        title="👋 Welcome to Brown Guns Family!",
        description=(
            f"Hey {member.mention}, glad to have you here.\n\n"
            f"📜 Rules: <#{RULES_CHANNEL_ID}>\n"
            f"💬 General: <#{GENERAL_CHAT_CHANNEL_ID}>\n"
            f"🎖️ Rank Request: <#{ROLE_REQUEST_CHANNEL_ID}>"
        ),
        colour=Theme.ACCENT,
        timestamp=now_local(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if WELCOME_BANNER_URL:
        embed.set_image(url=WELCOME_BANNER_URL)
    embed.set_footer(text=f"Member #{member.guild.member_count or 0}")

    try:
        await channel.send(
            content=member.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except discord.HTTPException:
        log.exception("Could not send welcome message for %s.", member.id)


MAIN_INVITE_USES: dict[str, int] = {}


async def refresh_main_invite_cache() -> None:
    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        return

    try:
        invites = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        log.warning("Invite tracking requires Manage Server permission.")
        return

    MAIN_INVITE_USES.clear()
    MAIN_INVITE_USES.update({
        invite.code: int(invite.uses or 0)
        for invite in invites
    })


async def detect_member_inviter(
    member: discord.Member,
):
    try:
        invites = await member.guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        return None, None

    used = None

    for invite in invites:
        if int(invite.uses or 0) > MAIN_INVITE_USES.get(invite.code, 0):
            used = invite
            break

    MAIN_INVITE_USES.clear()
    MAIN_INVITE_USES.update({
        invite.code: int(invite.uses or 0)
        for invite in invites
    })

    if used is None:
        return None, None

    return used.inviter, used.code


async def save_member_inviter(
    member: discord.Member,
    inviter,
    invite_code: str | None,
) -> None:
    await db.execute(
        """
        INSERT OR REPLACE INTO member_invites(
            guild_id,
            member_id,
            inviter_id,
            invite_code,
            joined_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            member.guild.id,
            member.id,
            inviter.id if inviter is not None else None,
            invite_code,
            now_local().isoformat(),
        ),
    )


async def log_staff_action(
    *,
    action: str,
    controller: discord.Member,
    target: str,
    result: str,
    reason: str | None = None,
) -> None:
    embed = control_log_embed(
        f"🛡️ Staff Action: {action}",
        colour=Theme.WARNING,
    )
    embed.add_field(
        name="Executed By",
        value=f"{controller.mention}\n`{controller.id}`",
        inline=True,
    )
    embed.add_field(name="Target", value=target[:1024], inline=True)
    embed.add_field(name="Result", value=result[:1024], inline=False)

    if reason:
        embed.add_field(
            name="Reason / Message",
            value=reason[:1024],
            inline=False,
        )

    await send_control_log(
        bot,
        await auto_log_channel_id("system_control", CONTROL_SERVER_LOG_CHANNEL_ID),
        embed,
    )


async def send_event_click_log(
    client: discord.Client,
    interaction: discord.Interaction,
    button_name: str,
) -> None:
    """DM the configured bot owner whenever an event button is used."""
    if BOT_OWNER_ID <= 0:
        log.warning(
            "BOT_OWNER_ID is not configured; event click log was not sent."
        )
        return

    owner = client.get_user(BOT_OWNER_ID)

    if owner is None:
        try:
            owner = await client.fetch_user(BOT_OWNER_ID)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            log.exception(
                "Could not fetch bot owner %s for event click log.",
                BOT_OWNER_ID,
            )
            return

    clicked_at = now_local()
    guild_name = (
        interaction.guild.name
        if interaction.guild is not None
        else "Unknown Server"
    )
    guild_id = (
        interaction.guild.id
        if interaction.guild is not None
        else 0
    )
    channel_mention = (
        interaction.channel.mention
        if isinstance(interaction.channel, discord.abc.GuildChannel)
        else "Unknown Channel"
    )

    embed = discord.Embed(
        title="🎮 Event Panel Button Used",
        colour=Theme.PRIMARY,
        timestamp=clicked_at,
    )

    embed.add_field(
        name="Clicked By",
        value=(
            f"{interaction.user.mention}\n"
            f"`{interaction.user}`\n"
            f"User ID: `{interaction.user.id}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Button",
        value=f"**{button_name}**",
        inline=True,
    )
    embed.add_field(
        name="Time",
        value=clicked_at.strftime("%d %B %Y, %I:%M:%S %p"),
        inline=True,
    )
    embed.add_field(
        name="Server",
        value=f"**{guild_name}**\nGuild ID: `{guild_id}`",
        inline=False,
    )
    embed.add_field(
        name="Channel",
        value=channel_mention,
        inline=True,
    )

    try:
        await owner.send(embed=embed)
    except discord.HTTPException:
        log.exception(
            "Could not DM event click log to bot owner %s.",
            BOT_OWNER_ID,
        )


def valid_image_attachment(attachment: discord.Attachment) -> bool:
    if attachment.content_type and attachment.content_type.startswith("image/"):
        return True
    return Path(attachment.filename).suffix.lower() in {
        ".png", ".jpg", ".jpeg", ".webp", ".gif"
    }


def detect_rank(content: str) -> int | None:
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    aliases = [
        (7, ["under deputy", "rank 7", "7 |", "7|"]),
        (6, ["business man", "businessman", "rank 6", "6 |", "6|"]),
        (1, ["new member", "rank 1", "1 |", "1|"]),
        (2, ["recruiter", "rank 2", "2 |", "2|"]),
        (3, ["soldier", "rank 3", "3 |", "3|"]),
        (4, ["enforcer", "rank 4", "4 |", "4|"]),
        (5, ["turfer", "rank 5", "5 |", "5|"]),
        (8, ["deputy", "rank 8", "8 |", "8|"]),
    ]
    for rank, values in aliases:
        if any(value in normalized for value in values):
            return rank
    match = re.search(r"(?im)^\s*[○•*\-]*\s*rank\s*[:\-]\s*([1-8])\s*$", content)
    return int(match.group(1)) if match else None


def extract_ingame_name(content: str) -> str | None:
    match = re.search(
        r"(?im)^\s*[○•*\-]*\s*(?:in[\s-]*game\s+)?name\s*[:\-]\s*(.+?)\s*$",
        content,
    )
    if not match:
        return None
    value = re.split(r"\s*\|\s*\d+\s*$", match.group(1).strip())[0]
    return re.sub(r"\s+", " ", value).strip() or None


def extract_ingame_id(content: str) -> str | None:
    match = re.search(
        r"(?im)^\s*[○•*\-]*\s*(?:(?:game|in[\s-]*game)\s+)?id\s*[:\-]\s*(\d{1,12})\s*$",
        content,
    )
    if match:
        return match.group(1)
    old = re.search(
        r"(?im)^\s*[○•*\-]*\s*name\s*[:\-]\s*.+?\|\s*(\d{1,12})\s*$",
        content,
    )
    return old.group(1) if old else None


def build_server_nickname(name: str | None, game_id: str | None) -> str | None:
    if not name:
        return None
    nickname = f"{name} | {game_id}" if game_id else name
    return nickname[:32]

