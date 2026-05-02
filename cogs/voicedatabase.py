"""
Voice Database cog — commands for user registration, recording control,
and clip retrieval.
"""

import asyncio
import os
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks, voice_recv
from discord.ext.commands import Context

from recording.recorder import VoiceRecorder
from recording.retriever import ClipRetriever
from recording.cleanup import SegmentCleanup

logger = logging.getLogger("discord_bot")

RECORDINGS_PATH = os.getenv("RECORDINGS_PATH", "recordings")
CLIPS_PATH = os.getenv("CLIPS_PATH", "clips")
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))


class VoiceDatabase(commands.Cog, name="voicedatabase"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.recorders: dict[int, VoiceRecorder] = {}  # guild_id -> VoiceRecorder
        self.retriever = None
        self.cleanup = None

    async def cog_load(self):
        """Called when the cog is loaded. Start cleanup task."""
        # Wait until db is ready (setup_hook sets self.bot.database)
        self.retriever = ClipRetriever(self.bot.database, output_path=CLIPS_PATH)
        self.cleanup = SegmentCleanup(self.bot.database, default_retention_days=RETENTION_DAYS)
        self.cleanup.start()
        self.auto_rejoin_loop.start()

    async def cog_unload(self):
        """Called when the cog is unloaded. Stop all recordings."""
        self.auto_rejoin_loop.cancel()
        if self.cleanup:
            self.cleanup.stop()
        for guild_id in list(self.recorders.keys()):
            recorder = self.recorders.pop(guild_id, None)
            if recorder:
                await recorder.stop()

    async def _connect_voice(self, channel: discord.VoiceChannel) -> discord.VoiceClient | None:
        """Connect to a voice channel, clearing any stale session first.
        Returns the VoiceClient or None on failure."""
        guild = channel.guild
        if guild.voice_client:
            await guild.voice_client.disconnect(force=True)
            await asyncio.sleep(1)  # let Discord invalidate the stale session

        for attempt in range(2):
            try:
                return await channel.connect(cls=voice_recv.VoiceRecvClient)
            except (discord.ClientException, discord.errors.ConnectionClosed) as e:
                if attempt == 0:
                    logger.warning(f"Voice connect failed (attempt 1): {e} — retrying after delay")
                    await asyncio.sleep(2)
                else:
                    logger.error(f"Voice connect failed (attempt 2): {e}")
                    return None

    # ── Participation commands ──────────────────────────────────────────

    @commands.hybrid_command(
        name="join",
        description="Opt in to voice recording. Your audio will be recorded when the bot is active.",
    )
    async def register_user(self, context: Context) -> None:
        if context.guild is None:
            await context.send("This command can only be used in a server.")
            return

        newly_registered = await self.bot.database.register_user(
            guild_id=context.guild.id,
            user_id=context.author.id,
            username=str(context.author),
        )

        if newly_registered:
            embed = discord.Embed(
                description=f"{context.author.mention} has opted in to voice recording.",
                color=0x57F287,
            )
            # Refresh consent on active recorder
            recorder = self.recorders.get(context.guild.id)
            if recorder:
                await recorder.refresh_consent()
        else:
            embed = discord.Embed(
                description=f"{context.author.mention} is already opted in.",
                color=0xFEE75C,
            )
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="leave",
        description="Opt out of voice recording. Your audio will no longer be recorded.",
    )
    async def unregister_user(self, context: Context) -> None:
        if context.guild is None:
            await context.send("This command can only be used in a server.")
            return

        was_registered = await self.bot.database.unregister_user(
            guild_id=context.guild.id,
            user_id=context.author.id,
        )

        if was_registered:
            embed = discord.Embed(
                description=f"{context.author.mention} has opted out of voice recording.",
                color=0xED4245,
            )
            recorder = self.recorders.get(context.guild.id)
            if recorder:
                await recorder.refresh_consent()
        else:
            embed = discord.Embed(
                description=f"{context.author.mention} was not opted in.",
                color=0xFEE75C,
            )
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="participants",
        description="List all users opted in to voice recording in this server.",
    )
    async def list_participants(self, context: Context) -> None:
        if context.guild is None:
            await context.send("This command can only be used in a server.")
            return

        participants = await self.bot.database.get_participants(context.guild.id)

        if not participants:
            embed = discord.Embed(
                description="No users are currently opted in to voice recording.",
                color=0xFEE75C,
            )
        else:
            lines = []
            for user_id, username, opted_in_at in participants:
                member = context.guild.get_member(user_id)
                display = member.mention if member else f"{username} (ID: {user_id})"
                lines.append(f"• {display} — since {opted_in_at}")

            embed = discord.Embed(
                title="Voice Recording Participants",
                description="\n".join(lines),
                color=0x5865F2,
            )
            embed.set_footer(text=f"{len(participants)} user(s) opted in")

        await context.send(embed=embed)

    # ── Recording control commands ──────────────────────────────────────

    @commands.hybrid_command(
        name="record",
        description="Start recording in a voice channel. Joins your current channel or the primary channel.",
    )
    @commands.has_permissions(manage_guild=True)
    async def start_recording(self, context: Context) -> None:
        if context.guild is None:
            await context.send("This command can only be used in a server.")
            return

        if context.guild.id in self.recorders:
            await context.send(
                embed=discord.Embed(
                    description="Already recording in this server.",
                    color=0xFEE75C,
                )
            )
            return

        # Determine which channel to join
        channel = None
        if context.author.voice and context.author.voice.channel:
            channel = context.author.voice.channel
        else:
            settings = await self.bot.database.get_settings(context.guild.id)
            if settings["primary_channel_id"]:
                channel = context.guild.get_channel(settings["primary_channel_id"])

        if channel is None:
            await context.send(
                embed=discord.Embed(
                    description="Join a voice channel first, or set a primary channel with `!setchannel`.",
                    color=0xED4245,
                )
            )
            return

        vc = await self._connect_voice(channel)
        if vc is None:
            await context.send(
                embed=discord.Embed(
                    description="Failed to connect to the voice channel. Try again in a moment.",
                    color=0xED4245,
                )
            )
            return

        settings = await self.bot.database.get_settings(context.guild.id)
        recorder = VoiceRecorder(
            bot=self.bot,
            guild=context.guild,
            channel=channel,
            database=self.bot.database,
            recordings_path=RECORDINGS_PATH,
            segment_duration_sec=settings["segment_duration_sec"],
        )
        await recorder.start(vc)
        self.recorders[context.guild.id] = recorder

        await context.send(
            embed=discord.Embed(
                description=f"Recording started in **{channel.name}**.",
                color=0x57F287,
            )
        )

    @commands.hybrid_command(
        name="stoprecord",
        description="Stop recording and disconnect from voice.",
    )
    @commands.has_permissions(manage_guild=True)
    async def stop_recording(self, context: Context) -> None:
        if context.guild is None:
            await context.send("This command can only be used in a server.")
            return

        recorder = self.recorders.pop(context.guild.id, None)
        if recorder is None:
            await context.send(
                embed=discord.Embed(
                    description="Not currently recording in this server.",
                    color=0xFEE75C,
                )
            )
            return

        await recorder.stop()

        # Disconnect from voice
        if context.guild.voice_client:
            await context.guild.voice_client.disconnect(force=True)

        await context.send(
            embed=discord.Embed(
                description="Recording stopped and disconnected.",
                color=0xED4245,
            )
        )

    # ── Clip retrieval command ──────────────────────────────────────────

    @commands.hybrid_command(
        name="clip",
        description="Retrieve a recorded clip. Usage: !clip @user 2026-04-30T13:44:00 10",
    )
    @app_commands.describe(
        user="The user whose audio you want to retrieve",
        start="Start timestamp (YYYY-MM-DDTHH:MM:SS)",
        minutes="Duration in minutes",
    )
    async def retrieve_clip(
        self,
        context: Context,
        user: discord.User,
        start: str,
        minutes: int = 10,
    ) -> None:
        if context.guild is None:
            await context.send("This command can only be used in a server.")
            return

        # Verify the target user has consented
        is_registered = await self.bot.database.is_user_registered(
            context.guild.id, user.id
        )
        if not is_registered:
            await context.send(
                embed=discord.Embed(
                    description=f"{user.mention} is not opted in to recording.",
                    color=0xED4245,
                )
            )
            return

        # Parse the start timestamp
        try:
            start_time = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        except ValueError:
            await context.send(
                embed=discord.Embed(
                    description="Invalid timestamp format. Use `YYYY-MM-DDTHH:MM:SS` (e.g. `2026-04-30T13:44:00`).",
                    color=0xED4245,
                )
            )
            return

        if minutes < 1 or minutes > 60:
            await context.send(
                embed=discord.Embed(
                    description="Duration must be between 1 and 60 minutes.",
                    color=0xED4245,
                )
            )
            return

        await context.defer()  # this might take a while

        clip_path = await self.retriever.retrieve_clip(
            user_id=user.id,
            start_time=start_time,
            duration_minutes=minutes,
            guild_id=context.guild.id,
        )

        if clip_path is None:
            await context.send(
                embed=discord.Embed(
                    description=f"No recorded audio found for {user.mention} at that time.",
                    color=0xFEE75C,
                )
            )
            return

        file_size = os.path.getsize(clip_path)
        if file_size > 25 * 1024 * 1024:  # Discord file upload limit
            await context.send(
                embed=discord.Embed(
                    description=f"Clip is too large ({file_size / 1024 / 1024:.1f} MB). Try a shorter duration.",
                    color=0xED4245,
                )
            )
            return

        embed = discord.Embed(
            title="Audio Clip Retrieved",
            description=f"**User:** {user.mention}\n**Start:** {start}\n**Duration:** {minutes} min",
            color=0x5865F2,
        )
        await context.send(
            embed=embed,
            file=discord.File(clip_path, filename=os.path.basename(clip_path)),
        )

        # Clean up the clip file after sending
        try:
            os.remove(clip_path)
        except OSError:
            pass

    # ── Settings commands ───────────────────────────────────────────────

    @commands.hybrid_command(
        name="setchannel",
        description="Set the primary recording channel for auto-join.",
    )
    @commands.has_permissions(manage_guild=True)
    async def set_channel(self, context: Context, channel: discord.VoiceChannel) -> None:
        if context.guild is None:
            await context.send("This command can only be used in a server.")
            return

        await self.bot.database.set_primary_channel(context.guild.id, channel.id)
        await context.send(
            embed=discord.Embed(
                description=f"Primary recording channel set to **{channel.name}**.",
                color=0x57F287,
            )
        )

    @commands.hybrid_command(
        name="retention",
        description="Set the number of days to keep recordings.",
    )
    @commands.has_permissions(manage_guild=True)
    async def set_retention(self, context: Context, days: int) -> None:
        if context.guild is None:
            await context.send("This command can only be used in a server.")
            return

        if days < 1 or days > 90:
            await context.send(
                embed=discord.Embed(
                    description="Retention must be between 1 and 90 days.",
                    color=0xED4245,
                )
            )
            return

        await self.bot.database.set_retention_days(context.guild.id, days)
        await context.send(
            embed=discord.Embed(
                description=f"Retention policy set to **{days} days**.",
                color=0x57F287,
            )
        )

    @commands.hybrid_command(
        name="recordingstatus",
        description="Show current recording status.",
    )
    async def recording_status(self, context: Context) -> None:
        if context.guild is None:
            await context.send("This command can only be used in a server.")
            return

        recorder = self.recorders.get(context.guild.id)
        settings = await self.bot.database.get_settings(context.guild.id)
        participants = await self.bot.database.get_participants(context.guild.id)

        is_recording = recorder is not None
        channel_name = recorder.channel.name if recorder else "None"
        active_streams = len(recorder.user_streams) if recorder else 0

        primary_ch = None
        if settings["primary_channel_id"]:
            primary_ch = context.guild.get_channel(settings["primary_channel_id"])

        embed = discord.Embed(
            title="Recording Status",
            color=0x57F287 if is_recording else 0x99AAB5,
        )
        embed.add_field(
            name="Status",
            value="🔴 Recording" if is_recording else "⚪ Idle",
            inline=True,
        )
        embed.add_field(name="Channel", value=channel_name, inline=True)
        embed.add_field(name="Active Streams", value=str(active_streams), inline=True)
        embed.add_field(
            name="Primary Channel",
            value=primary_ch.name if primary_ch else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Retention",
            value=f"{settings['retention_days']} days",
            inline=True,
        )
        embed.add_field(
            name="Participants",
            value=str(len(participants)),
            inline=True,
        )

        await context.send(embed=embed)

    # ── Auto-rejoin loop ────────────────────────────────────────────────

    @tasks.loop(minutes=2.0)
    async def auto_rejoin_loop(self):
        """
        Periodically check if the bot should auto-join a primary channel.
        If a primary channel is set, consented users are present,
        and we're not already recording, auto-start.
        """
        for guild in self.bot.guilds:
            if guild.id in self.recorders:
                continue  # already recording

            settings = await self.bot.database.get_settings(guild.id)
            if not settings["enabled"] or not settings["primary_channel_id"]:
                continue

            channel = guild.get_channel(settings["primary_channel_id"])
            if channel is None:
                continue

            # Check if any consented users are in the channel
            consented = await self.bot.database.get_consented_user_ids(guild.id)
            members_in_channel = {m.id for m in channel.members if not m.bot}
            active_consented = consented & members_in_channel

            if not active_consented:
                continue

            try:
                vc = await self._connect_voice(channel)
                if vc is None:
                    continue
                recorder = VoiceRecorder(
                    bot=self.bot,
                    guild=guild,
                    channel=channel,
                    database=self.bot.database,
                    recordings_path=RECORDINGS_PATH,
                    segment_duration_sec=settings["segment_duration_sec"],
                )
                await recorder.start(vc)
                self.recorders[guild.id] = recorder
                logger.info(
                    f"Auto-joined {guild.name} / #{channel.name} ({len(active_consented)} consented users present)"
                )
            except Exception as e:
                logger.error(f"Auto-join failed for {guild.name}: {e}")

    @auto_rejoin_loop.before_loop
    async def before_auto_rejoin(self):
        await self.bot.wait_until_ready()

    # ── Voice state listener ────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Auto-disconnect if all consented users leave the channel."""
        guild_id = member.guild.id
        recorder = self.recorders.get(guild_id)
        if recorder is None:
            return

        # Check if the channel still has consented users
        channel = recorder.channel
        consented = recorder.consented_users
        members_in_channel = {m.id for m in channel.members if not m.bot}
        active_consented = consented & members_in_channel

        if not active_consented:
            # No consented users left — stop recording
            recorder = self.recorders.pop(guild_id, None)
            if recorder:
                await recorder.stop()
            if member.guild.voice_client:
                await member.guild.voice_client.disconnect(force=True)
            logger.info(
                f"Auto-stopped recording in {member.guild.name} — no consented users remain"
            )


async def setup(bot) -> None:
    await bot.add_cog(VoiceDatabase(bot))
