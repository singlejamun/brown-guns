# === Complaint system ===
# Source lines 6327-7103 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# COMPLAINT SYSTEM
# =========================================================

async def complaint_row(
    complaint_id: int,
) -> sqlite3.Row | None:
    return await db.fetchone(
        """
        SELECT *
        FROM complaints
        WHERE id = ?
        """,
        (complaint_id,),
    )


async def complaint_embed(
    complaint_id: int,
) -> discord.Embed:
    complaint = await complaint_row(complaint_id)

    if complaint is None:
        return discord.Embed(
            title="Complaint Not Found",
            colour=Theme.DANGER,
        )

    status = str(complaint["status"])

    colour = {
        "pending": Theme.WARNING,
        "resolved": Theme.SUCCESS,
        "rejected": Theme.DANGER,
    }.get(status, Theme.PRIMARY)

    status_text = {
        "pending": "⏳ Pending",
        "resolved": "✅ Solved",
        "rejected": "❌ Rejected",
    }.get(status, status.title())

    embed = discord.Embed(
        title="📨 Player Complaint",
        colour=colour,
        timestamp=datetime.fromisoformat(
            str(complaint["created_at"])
        ),
    )

    embed.add_field(
        name="Submitted By",
        value=f"<@{int(complaint['complainant_user_id'])}>",
        inline=True,
    )
    embed.add_field(
        name="Your ID",
        value=f"**{complaint['complainant_game_id']}**",
        inline=True,
    )
    embed.add_field(
        name="Accused ID",
        value=f"**{complaint['accused_game_id']}**",
        inline=True,
    )
    embed.add_field(
        name="Description",
        value=str(complaint["description"])[:1024],
        inline=False,
    )
    embed.add_field(
        name="Proof",
        value=(
            str(complaint["proof"])[:1024]
            if complaint["proof"]
            else "*No proof provided*"
        ),
        inline=False,
    )
    embed.add_field(
        name="Status",
        value=status_text,
        inline=True,
    )

    if complaint["reviewed_by"]:
        embed.add_field(
            name="Reviewed By",
            value=f"<@{int(complaint['reviewed_by'])}>",
            inline=True,
        )

    if complaint["resolution_reason"]:
        embed.add_field(
            name=(
                "Punishment Given"
                if status == "resolved"
                else "Reject Reason"
            ),
            value=str(complaint["resolution_reason"])[:1024],
            inline=False,
        )

    embed.set_footer(
        text=f"Complaint ID: {complaint_id}"
    )

    return embed


async def update_complaint_message(
    client: commands.Bot,
    complaint_id: int,
) -> None:
    complaint = await complaint_row(complaint_id)

    if complaint is None or not complaint["review_message_id"]:
        return

    channel = await get_text_channel(
        client,
        int(complaint["review_channel_id"]),
    )

    if channel is None:
        return

    try:
        message = await channel.fetch_message(
            int(complaint["review_message_id"])
        )
        await message.edit(
            embed=await complaint_embed(complaint_id),
            view=ComplaintReviewView(
                client,
                complaint_id,
                disabled=str(complaint["status"]) != "pending",
            ),
        )
    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
    ):
        log.exception(
            "Could not update complaint %s.",
            complaint_id,
        )


class ComplaintRejectReasonModal(discord.ui.Modal):
    def __init__(
        self,
        client: commands.Bot,
        complaint_id: int,
    ) -> None:
        super().__init__(
            title="Reject Complaint",
            timeout=180,
        )

        self.client = client
        self.complaint_id = complaint_id

        self.reason = discord.ui.TextInput(
            label="Reject Reason",
            placeholder="Write why this complaint is being rejected",
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
        if not await jailer_only(interaction):
            return

        complaint = await complaint_row(
            self.complaint_id
        )

        if complaint is None:
            await interaction.response.send_message(
                "❌ Complaint not found.",
                ephemeral=True,
            )
            return

        if str(complaint["status"]) != "pending":
            await interaction.response.send_message(
                "❌ This complaint has already been reviewed.",
                ephemeral=True,
            )
            return

        reason = str(self.reason.value).strip()

        # BUG FIX: claim the complaint atomically. Before, two reviewers could
        # both pass the status check above and both send a result DM.
        claimed = await db.execute_count(
            """
            UPDATE complaints
            SET status = 'rejected',
                reviewed_by = ?,
                reviewed_at = ?,
                resolution_reason = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                interaction.user.id,
                now_local().isoformat(),
                reason,
                self.complaint_id,
            ),
        )
        if claimed != 1:
            await interaction.response.send_message(
                "❌ This complaint has already been reviewed.",
                ephemeral=True,
            )
            return

        await send_complaint_review_log(
            self.client,
            complaint,
            decision="rejected",
            reviewer=interaction.user,
            result_text=reason,
        )

        await interaction.response.send_message(
            "❌ Complaint rejected. The reason was sent to the complainant.",
            ephemeral=True,
        )

        await update_complaint_message(
            self.client,
            self.complaint_id,
        )

        if interaction.guild is not None:
            member = interaction.guild.get_member(
                int(complaint["complainant_user_id"])
            )

            if member is None:
                try:
                    member = await interaction.guild.fetch_member(
                        int(complaint["complainant_user_id"])
                    )
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    member = None

            if member is not None:
                try:
                    await member.send(
                        "❌ **Your complaint was rejected.**\n\n"
                        f"**Reason:** {reason}"
                    )
                except discord.HTTPException:
                    pass


class ComplaintSolvedPunishmentModal(discord.ui.Modal):
    def __init__(
        self,
        client: commands.Bot,
        complaint_id: int,
    ) -> None:
        super().__init__(
            title="Solve Complaint",
            timeout=180,
        )

        self.client = client
        self.complaint_id = complaint_id

        self.punishment = discord.ui.TextInput(
            label="Punishment Given",
            placeholder="Example: Warning 2, demotion, 3-day suspension",
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=500,
            required=True,
        )

        self.add_item(self.punishment)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await jailer_only(interaction):
            return

        complaint = await complaint_row(
            self.complaint_id
        )

        if complaint is None:
            await interaction.response.send_message(
                "❌ Complaint not found.",
                ephemeral=True,
            )
            return

        if str(complaint["status"]) != "pending":
            await interaction.response.send_message(
                "❌ This complaint has already been reviewed.",
                ephemeral=True,
            )
            return

        punishment_text = str(
            self.punishment.value
        ).strip()

        # BUG FIX: atomic claim (see reject modal).
        claimed = await db.execute_count(
            """
            UPDATE complaints
            SET status = 'resolved',
                reviewed_by = ?,
                reviewed_at = ?,
                resolution_reason = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                interaction.user.id,
                now_local().isoformat(),
                punishment_text,
                self.complaint_id,
            ),
        )
        if claimed != 1:
            await interaction.response.send_message(
                "❌ This complaint has already been reviewed.",
                ephemeral=True,
            )
            return

        await send_complaint_review_log(
            self.client,
            complaint,
            decision="solved",
            reviewer=interaction.user,
            result_text=punishment_text,
        )

        await interaction.response.send_message(
            "✅ Complaint marked as solved and the punishment "
            "was sent to the complainant.",
            ephemeral=True,
        )

        await update_complaint_message(
            self.client,
            self.complaint_id,
        )

        if interaction.guild is None:
            return

        member = interaction.guild.get_member(
            int(complaint["complainant_user_id"])
        )

        if member is None:
            try:
                member = await interaction.guild.fetch_member(
                    int(complaint["complainant_user_id"])
                )
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                member = None

        if member is not None:
            try:
                await member.send(
                    "✅ **Your complaint has been solved.**\n\n"
                    f"**Punishment given to the accused:**\n"
                    f"{punishment_text}\n\n"
                    f"**Complaint ID:** {self.complaint_id}"
                )
            except discord.HTTPException:
                pass


class ComplaintReviewView(discord.ui.View):
    def __init__(
        self,
        client: commands.Bot,
        complaint_id: int,
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)

        self.client = client
        self.complaint_id = complaint_id

        solved_button = discord.ui.Button(
            label="Solved",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"complaint:solved:{complaint_id}",
            disabled=disabled,
        )

        reject_button = discord.ui.Button(
            label="Reject",
            emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"complaint:reject:{complaint_id}",
            disabled=disabled,
        )

        solved_button.callback = self.solved_callback
        reject_button.callback = self.reject_callback

        self.add_item(solved_button)
        self.add_item(reject_button)

    async def solved_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await jailer_only(interaction):
            return

        complaint = await complaint_row(
            self.complaint_id
        )

        if complaint is None:
            await interaction.response.send_message(
                "❌ Complaint not found.",
                ephemeral=True,
            )
            return

        if str(complaint["status"]) != "pending":
            await interaction.response.send_message(
                "❌ This complaint has already been reviewed.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            ComplaintSolvedPunishmentModal(
                self.client,
                self.complaint_id,
            )
        )

    async def reject_callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await jailer_only(interaction):
            return

        complaint = await complaint_row(
            self.complaint_id
        )

        if complaint is None:
            await interaction.response.send_message(
                "❌ Complaint not found.",
                ephemeral=True,
            )
            return

        if str(complaint["status"]) != "pending":
            await interaction.response.send_message(
                "❌ This complaint has already been reviewed.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            ComplaintRejectReasonModal(
                self.client,
                self.complaint_id,
            )
        )


class ComplaintModal(discord.ui.Modal):
    def __init__(
        self,
        client: commands.Bot,
    ) -> None:
        super().__init__(
            title="Submit Complaint",
            timeout=300,
        )

        self.client = client

        self.your_id = discord.ui.TextInput(
            label="Your ID",
            placeholder="Example: 243859",
            min_length=1,
            max_length=12,
            required=True,
        )

        self.accused_id = discord.ui.TextInput(
            label="Accused ID",
            placeholder="Example: 123456",
            min_length=1,
            max_length=12,
            required=True,
        )

        self.description = discord.ui.TextInput(
            label="Tell us what happened",
            placeholder="Explain the complaint clearly",
            style=discord.TextStyle.paragraph,
            min_length=10,
            max_length=1000,
            required=True,
        )

        self.proof = discord.ui.TextInput(
            label="Proof Link",
            placeholder="Paste an image/video link, or write No proof",
            style=discord.TextStyle.paragraph,
            min_length=2,
            max_length=500,
            required=True,
        )

        self.add_item(self.your_id)
        self.add_item(self.accused_id)
        self.add_item(self.description)
        self.add_item(self.proof)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ This complaint form must be used in the server.",
                ephemeral=True,
            )
            return

        your_id = str(self.your_id.value).strip()
        accused_id = str(self.accused_id.value).strip()

        if not your_id.isdigit() or not accused_id.isdigit():
            await interaction.response.send_message(
                "❌ Both IDs must contain numbers only.",
                ephemeral=True,
            )
            return

        review_channel = await get_text_channel(
            self.client,
            COMPLAINT_REVIEW_CHANNEL_ID,
        )

        if review_channel is None:
            await interaction.response.send_message(
                "❌ Complaint review channel is not configured.",
                ephemeral=True,
            )
            return

        complaint_id = await db.execute(
            """
            INSERT INTO complaints(
                guild_id,
                complainant_user_id,
                complainant_game_id,
                accused_game_id,
                description,
                proof,
                review_channel_id,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                interaction.guild.id,
                interaction.user.id,
                your_id,
                accused_id,
                str(self.description.value).strip(),
                str(self.proof.value).strip(),
                review_channel.id,
                now_local().isoformat(),
            ),
        )

        view = ComplaintReviewView(
            self.client,
            complaint_id,
        )

        try:
            review_message = await review_channel.send(
                embed=await complaint_embed(complaint_id),
                view=view,
            )
        except discord.HTTPException:
            # BUG FIX: don't leave a ghost complaint in the database that no
            # reviewer can ever see; tell the member instead of "interaction failed".
            log.exception("Could not post complaint %s to the review channel.", complaint_id)
            await db.execute("DELETE FROM complaints WHERE id = ?", (complaint_id,))
            await interaction.response.send_message(
                "❌ I couldn't send your complaint to staff (missing channel permission). "
                "Please tell a Jailer.",
                ephemeral=True,
            )
            return

        await db.execute(
            """
            UPDATE complaints
            SET review_message_id = ?
            WHERE id = ?
            """,
            (
                review_message.id,
                complaint_id,
            ),
        )

        self.client.add_view(
            view,
            message_id=review_message.id,
        )

        await interaction.response.send_message(
            "✅ Your complaint was submitted to staff.",
            ephemeral=True,
        )


class ComplaintPanelView(discord.ui.View):
    def __init__(
        self,
        client: commands.Bot,
    ) -> None:
        super().__init__(timeout=None)
        self.client = client

        button = discord.ui.Button(
            label="Submit Complaint",
            emoji="📨",
            style=discord.ButtonStyle.primary,
            custom_id="complaint:open-form",
        )

        button.callback = self.open_form
        self.add_item(button)

        pov_button = discord.ui.Button(
            label="POV Request",
            emoji="🎥",
            style=discord.ButtonStyle.secondary,
            custom_id="pov:open-form",
        )
        pov_button.callback = self.open_pov_form
        self.add_item(pov_button)

    async def open_form(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.send_modal(
            ComplaintModal(self.client)
        )

    async def open_pov_form(
        self,
        interaction: discord.Interaction,
    ) -> None:
        # PovRequestModal lives in systems/pov_requests.py (same shared namespace).
        await interaction.response.send_modal(
            PovRequestModal(self.client)
        )


def complaint_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📨 Player Complaint",
        description=(
            "Use the buttons below.\n\n"
            "**📨 Submit Complaint** — you will be asked for:\n"
            "• Your ID\n"
            "• Accused ID\n"
            "• What happened\n"
            "• Proof link\n\n"
            "**🎥 POV Request** — ask for a player's POV of an event:\n"
            "• Your ID and the suspect's ID\n"
            "• Event name and date/time (optional)\n"
            "• Why you need the POV"
        ),
        colour=Theme.PRIMARY,
    )

    embed.set_footer(
        text="False complaints may result in punishment."
    )

    return embed


async def ensure_complaint_panel(
    client: commands.Bot,
) -> None:
    channel = await get_text_channel(
        client,
        COMPLAINT_CHANNEL_ID,
    )

    if channel is None:
        log.error("Complaint channel was not found.")
        return

    message_id = await get_setting(
        "complaint_panel_message_id"
    )

    if message_id:
        existing = None
        try:
            existing = await channel.fetch_message(int(message_id))
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
            ValueError,
        ):
            existing = None

        if existing is not None:
            # Keep the panel up to date (this is how the new POV button shows up
            # on the panel that is already posted — no need to recreate it).
            try:
                await existing.edit(
                    embed=complaint_panel_embed(),
                    view=ComplaintPanelView(client),
                )
            except discord.HTTPException:
                log.warning("Could not refresh the complaint panel message.")
            return

    view = ComplaintPanelView(client)

    message = await channel.send(
        embed=complaint_panel_embed(),
        view=view,
    )

    await set_setting(
        "complaint_panel_message_id",
        str(message.id),
    )

    client.add_view(
        view,
        message_id=message.id,
    )

    log.info("Created complaint panel.")


async def restore_complaint_views(
    client: commands.Bot,
) -> None:
    complaints = await db.fetchall(
        """
        SELECT id, review_message_id, status
        FROM complaints
        WHERE review_message_id IS NOT NULL
        """
    )

    for complaint in complaints:
        client.add_view(
            ComplaintReviewView(
                client,
                int(complaint["id"]),
                disabled=str(complaint["status"]) != "pending",
            ),
            message_id=int(complaint["review_message_id"]),
        )


@bot.command(name="complaintpanel")
async def complaint_panel_command(
    ctx: commands.Context,
) -> None:
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        return

    if not is_staff(ctx.author):
        await ctx.reply(
            "❌ Only authorized staff can recreate the complaint panel.",
            mention_author=False,
        )
        return

    await set_setting(
        "complaint_panel_message_id",
        "0",
    )

    await ensure_complaint_panel(bot)

    await ctx.reply(
        "✅ Complaint panel checked/recreated.",
        mention_author=False,
    )






