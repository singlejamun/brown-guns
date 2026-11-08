# === Role request system ===
# Source lines 2535-3619 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# PART 3 — ROLE REQUEST INTAKE, EMBED, AND MESSAGE HANDLING
# Paste this directly below Part 2.
# =========================================================


async def role_request_row(
    request_id: int,
) -> sqlite3.Row | None:
    return await db.fetchone(
        """
        SELECT *
        FROM role_requests
        WHERE id = ?
        """,
        (request_id,),
    )


async def role_request_embed(
    request_id: int,
) -> discord.Embed:
    request = await role_request_row(request_id)

    if request is None:
        return discord.Embed(
            title="Role Request Not Found",
            colour=Theme.DANGER,
        )

    rank = int(request["requested_rank"])
    status = str(request["status"])

    status_text = {
        "pending": f"{Theme.ICON_PENDING} Pending",
        "accepted": f"{Theme.ICON_ONLINE} Accepted",
        "denied": f"{Theme.ICON_OFFLINE} Denied",
    }.get(
        status,
        status.title(),
    )

    colour = {
        "pending": Theme.WARNING,
        "accepted": Theme.SUCCESS,
        "denied": Theme.DANGER,
    }.get(
        status,
        Theme.PRIMARY,
    )

    nickname_preview = build_server_nickname(
        request["ingame_name"],
        request["ingame_id"],
    )

    embed = discord.Embed(
        title=f"{Theme.ICON_ROLE}  FAMILY ROLE REQUEST",
        description=f"{Theme.DIVIDER}\n**{status_text}**",
        colour=colour,
        timestamp=datetime.fromisoformat(
            str(request["created_at"])
        ),
    )

    embed.add_field(
        name=f"{Theme.ICON_ARROW}  Applicant",
        value=hud_field_block([
            ("Discord", f"<@{int(request['requester_id'])}>"),
            ("In-Game Name", request["ingame_name"] or "*Not detected*"),
            ("In-Game ID", str(request["ingame_id"]) if request["ingame_id"] else "*Not detected*"),
            ("Invited By", request["invited_by"] or "*Not Provided*"),
        ]),
        inline=False,
    )

    embed.add_field(
        name=f"{Theme.ICON_ARROW}  Requested Rank",
        value=f"**{rank} | {RANK_NAMES.get(rank, 'Unknown Rank')}**",
        inline=True,
    )

    embed.add_field(
        name=f"{Theme.ICON_ARROW}  Nickname On Approval",
        value=(
            f"**{nickname_preview}**"
            if nickname_preview
            else "*Cannot be generated*"
        ),
        inline=True,
    )

    review_pairs = []
    if request["reviewed_by"]:
        review_pairs.append(("Reviewed By", f"<@{int(request['reviewed_by'])}>"))
    if status == "accepted":
        review_pairs.append((
            "Nickname Changed",
            f"{Theme.ICON_ONLINE} Yes" if request["nickname_changed"] else f"{Theme.ICON_WARN} No",
        ))

    if review_pairs:
        embed.add_field(
            name=f"{Theme.ICON_ARROW}  Review",
            value=hud_field_block(review_pairs),
            inline=False,
        )

    if status == "denied" and request["denial_reason"]:
        embed.add_field(
            name=f"{Theme.ICON_ARROW}  Denial Reason",
            value=str(request["denial_reason"])[:1024],
            inline=False,
        )

    if request["proof_url"]:
        embed.set_image(
            url=str(request["proof_url"])
        )
    else:
        embed.add_field(
            name=f"{Theme.ICON_ARROW}  Proof Screenshot",
            value="*No screenshot attached.*",
            inline=False,
        )

    embed.set_footer(
        text=f"Brown Guns Family • Role Request #{request_id}"
    )

    return embed


async def update_role_request_message(
    client: commands.Bot,
    request_id: int,
) -> None:
    request = await role_request_row(request_id)

    if request is None:
        return

    if not request["review_message_id"]:
        return

    channel = await get_text_channel(
        client,
        int(request["review_channel_id"]),
    )

    if channel is None:
        return

    try:
        message = await channel.fetch_message(
            int(request["review_message_id"])
        )

        await message.edit(
            embed=await role_request_embed(request_id),
            view=RoleRequestReviewView(
                client,
                request_id,
                disabled=str(request["status"]) != "pending",
            ),
        )

    except discord.NotFound:
        log.warning(
            "Role review message %s was deleted.",
            request["review_message_id"],
        )

    except discord.Forbidden:
        log.error(
            "Bot cannot edit role review message %s.",
            request["review_message_id"],
        )

    except discord.HTTPException:
        log.exception(
            "Discord error updating role request %s.",
            request_id,
        )


async def react_to_original_role_request(
    client: commands.Bot,
    request_id: int,
    emoji: str,
) -> None:
    request = await role_request_row(request_id)

    if request is None:
        return

    if not request["original_channel_id"]:
        return

    if not request["original_message_id"]:
        return

    channel = await get_text_channel(
        client,
        int(request["original_channel_id"]),
    )

    if channel is None:
        return

    try:
        original_message = await channel.fetch_message(
            int(request["original_message_id"])
        )

        await original_message.add_reaction(emoji)

    except discord.NotFound:
        log.warning(
            "Original role request message %s was deleted.",
            request["original_message_id"],
        )

    except discord.Forbidden:
        log.error(
            "Bot cannot react to original role request message %s.",
            request["original_message_id"],
        )

    except discord.HTTPException:
        log.exception(
            "Discord error reacting to role request %s.",
            request_id,
        )


async def send_role_request_confirmation_dm(
    member: discord.Member,
    request_id: int,
    ingame_name: str | None,
    ingame_id: str | None,
    rank: int,
) -> None:
    lines = [
        "✅ **Your family role request was submitted.**",
        "",
        f"**Request ID:** {request_id}",
        (
            f"**Name:** {ingame_name}"
            if ingame_name
            else "**Name:** Not detected"
        ),
        (
            f"**ID:** {ingame_id}"
            if ingame_id
            else "**ID:** Not detected"
        ),
        (
            f"**Requested Rank:** "
            f"{rank} | {RANK_NAMES.get(rank, 'Unknown Rank')}"
        ),
        "",
        "Staff will review your request.",
    ]

    try:
        await member.send(
            "\n".join(lines)
        )
    except discord.HTTPException:
        pass


async def process_role_request(
    client: commands.Bot,
    message: discord.Message,
) -> None:
    if message.guild is None:
        return

    if message.author.bot:
        return

    if message.channel.id != ROLE_REQUEST_CHANNEL_ID:
        return

    rank = detect_rank(message.content)

    if rank is None:
        try:
            await message.author.send(
                "❌ I could not detect a valid rank number or rank name "
                "in your role request."
            )
        except discord.HTTPException:
            pass
        return

    configured_role_id = RANK_ROLE_IDS.get(
        rank,
        0,
    )

    if configured_role_id <= 0:
        try:
            await message.author.send(
                "❌ The requested rank is not configured in the bot."
            )
        except discord.HTTPException:
            pass
        return

    ingame_name = extract_ingame_name(
        message.content
    )
    ingame_id = extract_ingame_id(
        message.content
    )

    image = next(
        (
            attachment
            for attachment in message.attachments
            if valid_image_attachment(attachment)
        ),
        None,
    )

    review_channel = await get_text_channel(
        client,
        ROLE_REVIEW_CHANNEL_ID,
    )

    if review_channel is None:
        log.error(
            "Role review channel %s was not found.",
            ROLE_REVIEW_CHANNEL_ID,
        )

        try:
            await message.author.send(
                "❌ Your request could not be submitted because "
                "the staff review channel is unavailable."
            )
        except discord.HTTPException:
            pass

        return

    request_id = await db.execute(
        """
        INSERT INTO role_requests(
            guild_id,
            requester_id,
            ingame_name,
            ingame_id,
            requested_rank,
            proof_url,
            original_channel_id,
            original_message_id,
            review_channel_id,
            review_message_id,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'pending', ?)
        """,
        (
            message.guild.id,
            message.author.id,
            ingame_name,
            ingame_id,
            rank,
            image.url if image else None,
            message.channel.id,
            message.id,
            review_channel.id,
            now_local().isoformat(),
        ),
    )

    view = RoleRequestReviewView(
        client,
        request_id,
    )

    try:
        review_message = await review_channel.send(
            embed=await role_request_embed(request_id),
            view=view,
        )
    except discord.Forbidden:
        log.error(
            "Bot cannot send role requests in review channel %s.",
            review_channel.id,
        )
        return
    except discord.HTTPException:
        log.exception(
            "Discord error sending role request %s.",
            request_id,
        )
        return

    await db.execute(
        """
        UPDATE role_requests
        SET review_message_id = ?
        WHERE id = ?
        """,
        (
            review_message.id,
            request_id,
        ),
    )

    client.add_view(
        view,
        message_id=review_message.id,
    )

    if isinstance(
        message.author,
        discord.Member,
    ):
        await send_role_request_confirmation_dm(
            message.author,
            request_id,
            ingame_name,
            ingame_id,
            rank,
        )


async def restore_role_request_views(
    client: commands.Bot,
) -> None:
    requests = await db.fetchall(
        """
        SELECT id, review_message_id, status
        FROM role_requests
        WHERE review_message_id IS NOT NULL
        """
    )

    restored = 0

    for request in requests:
        client.add_view(
            RoleRequestReviewView(
                client,
                int(request["id"]),
                disabled=str(request["status"]) != "pending",
            ),
            message_id=int(request["review_message_id"]),
        )
        restored += 1

    log.info(
        "Restored %s role request view(s).",
        restored,
    )


# Part 4 will define RoleRequestReviewView,
# Accept Request, Deny Request, and the denial reason modal.


# =========================================================
# PART 4 — ROLE REQUEST ACCEPT / DENY SYSTEM
# Paste this directly below Part 3.
# =========================================================


class RoleRequestDenyReasonModal(discord.ui.Modal):
    def __init__(
        self,
        client: commands.Bot,
        request_id: int,
    ) -> None:
        super().__init__(
            title="Deny Role Request",
            timeout=180,
        )

        self.client = client
        self.request_id = request_id

        self.reason = discord.ui.TextInput(
            label="Reason",
            placeholder="Write why this role request is being denied",
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=500,
            required=True,
        )

        self.add_item(self.reason)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await staff_only(interaction):
            return

        request = await role_request_row(
            self.request_id
        )

        if request is None:
            await interaction.response.send_message(
                "❌ Role request not found.",
                ephemeral=True,
            )
            return

        if str(request["status"]) != "pending":
            await interaction.response.send_message(
                "❌ This request has already been reviewed.",
                ephemeral=True,
            )
            return

        denial_reason = str(
            self.reason.value
        ).strip()

        await db.execute(
            """
            UPDATE role_requests
            SET status = 'denied',
                reviewed_by = ?,
                reviewed_at = ?,
                denial_reason = ?
            WHERE id = ?
            """,
            (
                interaction.user.id,
                now_local().isoformat(),
                denial_reason,
                self.request_id,
            ),
        )

        await send_rank_review_log(
            self.client,
            request,
            decision="rejected",
            reviewer=interaction.user,
            reason=denial_reason,
        )

        await interaction.response.send_message(
            "❌ Role request denied. "
            "The reason was sent to the requester by DM.",
            ephemeral=True,
        )

        await update_role_request_message(
            self.client,
            self.request_id,
        )

        await react_to_original_role_request(
            self.client,
            self.request_id,
            "❌",
        )

        if interaction.guild is None:
            return

        member = interaction.guild.get_member(
            int(request["requester_id"])
        )

        if member is None:
            try:
                member = await interaction.guild.fetch_member(
                    int(request["requester_id"])
                )
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                member = None

        if member is None:
            return

        try:
            await member.send(
                "❌ **Your family role request was denied.**\n\n"
                f"**Reason:** {denial_reason}\n\n"
                f"**Request ID:** {self.request_id}"
            )
        except discord.HTTPException:
            pass


class RoleRequestReviewView(discord.ui.View):
    def __init__(
        self,
        client: commands.Bot,
        request_id: int,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            timeout=None,
        )

        self.client = client
        self.request_id = request_id

        accept_button = discord.ui.Button(
            label="Accept Request",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=(
                f"role-request:accept:{request_id}"
            ),
            disabled=disabled,
        )

        deny_button = discord.ui.Button(
            label="Deny Request",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=(
                f"role-request:deny:{request_id}"
            ),
            disabled=disabled,
        )

        accept_button.callback = self.accept_callback
        deny_button.callback = self.deny_callback

        self.add_item(accept_button)
        self.add_item(deny_button)

    async def validate_request(
        self,
        interaction: discord.Interaction,
    ) -> sqlite3.Row | None:
        if not await staff_only(interaction):
            return None

        request = await role_request_row(
            self.request_id
        )

        if request is None:
            await respond_or_followup(
                interaction,
                "❌ Role request not found.",
            )
            return None

        if str(request["status"]) != "pending":
            await respond_or_followup(
                interaction,
                "❌ This request has already been reviewed.",
            )
            return None

        return request

    async def accept_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        # Acknowledge immediately: the role edits, nickname change, and
        # DB/log work below can take longer than Discord's 3-second
        # interaction window, which would otherwise cause a 404
        # "Unknown interaction" error when we finally try to respond.
        await interaction.response.defer(ephemeral=True, thinking=True)

        request = await self.validate_request(
            interaction
        )

        if request is None:
            return

        if interaction.guild is None:
            await respond_or_followup(
                interaction,
                "❌ Server not found.",
            )
            return

        member = interaction.guild.get_member(
            int(request["requester_id"])
        )

        if member is None:
            try:
                member = await interaction.guild.fetch_member(
                    int(request["requester_id"])
                )
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                await respond_or_followup(
                    interaction,
                    "❌ The requester is no longer in the server.",
                )
                return

        requested_rank = int(
            request["requested_rank"]
        )

        requested_role_id = RANK_ROLE_IDS.get(
            requested_rank,
            0,
        )

        requested_role = interaction.guild.get_role(
            requested_role_id
        )
        family_member_role = interaction.guild.get_role(
            FAMILY_MEMBER_ROLE_ID
        )

        if requested_role is None:
            await respond_or_followup(
                interaction,
                "❌ The requested rank role is not configured correctly.",
            )
            return

        if family_member_role is None:
            await respond_or_followup(
                interaction,
                "❌ The Family Member role is not configured correctly.",
            )
            return

        configured_rank_ids = {
            role_id
            for role_id in RANK_ROLE_IDS.values()
            if role_id > 0
        }

        old_rank_roles = [
            role
            for role in member.roles
            if role.id in configured_rank_ids
            and role.id != requested_role.id
        ]

        try:
            if old_rank_roles:
                await member.remove_roles(
                    *old_rank_roles,
                    reason=(
                        f"Role request #{self.request_id} accepted"
                    ),
                )

            roles_to_add = []

            if requested_role not in member.roles:
                roles_to_add.append(requested_role)

            if family_member_role not in member.roles:
                roles_to_add.append(family_member_role)

            if roles_to_add:
                await member.add_roles(
                    *roles_to_add,
                    reason=(
                        f"Role request #{self.request_id} accepted"
                    ),
                )

        except discord.Forbidden:
            await respond_or_followup(
                interaction,
                "❌ I cannot manage that rank role. "
                "Move my bot role above all family rank roles.",
            )
            return

        except discord.HTTPException:
            await respond_or_followup(
                interaction,
                "❌ Discord could not update the member's roles.",
            )
            return

        final_nickname = build_server_nickname(
            request["ingame_name"],
            request["ingame_id"],
        )

        nickname_changed = False
        nickname_error = None

        if final_nickname:
            try:
                await member.edit(
                    nick=final_nickname,
                    reason=(
                        f"Role request #{self.request_id} accepted"
                    ),
                )

                nickname_changed = True

            except discord.Forbidden:
                nickname_error = (
                    "The role was assigned, but the nickname could not be changed. "
                    "Give the bot Manage Nicknames permission and move its role higher."
                )

            except discord.HTTPException:
                nickname_error = (
                    "The role was assigned, but Discord rejected the nickname change."
                )

        await db.execute(
            """
            UPDATE role_requests
            SET status = 'accepted',
                reviewed_by = ?,
                reviewed_at = ?,
                denial_reason = NULL,
                nickname_changed = ?
            WHERE id = ?
            """,
            (
                interaction.user.id,
                now_local().isoformat(),
                int(nickname_changed),
                self.request_id,
            ),
        )

        await send_rank_review_log(
            self.client,
            request,
            decision="accepted",
            reviewer=interaction.user,
            nickname_changed=nickname_changed,
        )

        response = (
            f"✅ {member.mention}'s role request was accepted.\n"
            f"Assigned: **{requested_rank} | "
            f"{RANK_NAMES.get(requested_rank, 'Unknown Rank')}**\n"
            f"Also assigned: **{family_member_role.name}**"
        )

        if nickname_changed:
            response += (
                f"\nNickname changed to **{final_nickname}**."
            )

        elif nickname_error:
            response += (
                f"\n⚠️ {nickname_error}"
            )

        elif not final_nickname:
            response += (
                "\n⚠️ No valid name was detected, so the nickname was unchanged."
            )

        await respond_or_followup(
            interaction,
            response,
        )

        await update_role_request_message(
            self.client,
            self.request_id,
        )

        await react_to_original_role_request(
            self.client,
            self.request_id,
            "✅",
        )

        try:
            dm_lines = [
                "✅ **Your family role request was accepted.**",
                "",
                (
                    f"**Role:** {requested_rank} | "
                    f"{RANK_NAMES.get(requested_rank, 'Unknown Rank')}\n"
                    f"**Mutual Role:** {family_member_role.name}"
                ),
            ]

            if nickname_changed:
                dm_lines.extend(
                    [
                        "",
                        (
                            f"**Your server nickname is now:** "
                            f"{final_nickname}"
                        ),
                    ]
                )

            elif nickname_error:
                dm_lines.extend(
                    [
                        "",
                        f"⚠️ {nickname_error}",
                    ]
                )

            dm_lines.extend(
                [
                    "",
                    f"**Request ID:** {self.request_id}",
                ]
            )

            await member.send(
                "\n".join(dm_lines)
            )

        except discord.HTTPException:
            pass

    async def deny_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        request = await self.validate_request(
            interaction
        )

        if request is None:
            return

        await interaction.response.send_modal(
            RoleRequestDenyReasonModal(
                self.client,
                self.request_id,
            )
        )


# =========================================================
# OPTIONAL ROLE REQUEST TEST COMMANDS
# These are useful while setting up the bot.
# They can be removed later.
# =========================================================

@bot.command(
    name="requestinfo",
)
async def request_info_command(
    ctx: commands.Context,
    request_id: int,
) -> None:
    if ctx.guild is None:
        return

    if not isinstance(
        ctx.author,
        discord.Member,
    ):
        return

    if not is_staff(ctx.author):
        await ctx.reply(
            "❌ Only authorized staff can use this command.",
            mention_author=False,
        )
        return

    request = await role_request_row(
        request_id
    )

    if request is None:
        await ctx.reply(
            "❌ Role request not found.",
            mention_author=False,
        )
        return

    await ctx.reply(
        embed=await role_request_embed(request_id),
        mention_author=False,
    )


@bot.command(
    name="pendingrequests",
    aliases=["requests"],
)
async def pending_requests_command(
    ctx: commands.Context,
) -> None:
    if ctx.guild is None:
        return

    if not isinstance(
        ctx.author,
        discord.Member,
    ):
        return

    if not is_staff(ctx.author):
        await ctx.reply(
            "❌ Only authorized staff can use this command.",
            mention_author=False,
        )
        return

    rows = await db.fetchall(
        """
        SELECT
            id,
            requester_id,
            ingame_name,
            ingame_id,
            requested_rank,
            created_at
        FROM role_requests
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT 20
        """
    )

    if not rows:
        await ctx.reply(
            "✅ There are no pending role requests.",
            mention_author=False,
        )
        return

    lines = []

    for row in rows:
        rank = int(
            row["requested_rank"]
        )

        nickname = build_server_nickname(
            row["ingame_name"],
            row["ingame_id"],
        )

        lines.append(
            f"`#{int(row['id'])}` "
            f"<@{int(row['requester_id'])}> — "
            f"**{rank} | {RANK_NAMES.get(rank, 'Unknown Rank')}**\n"
            f"└ {nickname or 'Name not detected'}"
        )

    embed = discord.Embed(
        title="📋 Pending Role Requests",
        description="\n\n".join(lines),
        colour=Theme.WARNING,
    )

    embed.set_footer(
        text=(
            f"Showing {len(rows)} pending request(s)"
        )
    )

    await ctx.reply(
        embed=embed,
        mention_author=False,
    )


# Part 5 will add the punishment panel and warning system.


