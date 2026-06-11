"""
Voice Database cog — commands for user registration, recording control,
and clip retrieval.
"""

import asyncio
import os
import logging
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

import discord
from discord import app_commands
from discord.ext import commands, tasks, voice_recv
from discord.ext.commands import Context

from recording.recorder import VoiceRecorder
from recording.retriever import ClipRetriever
from recording.cleanup import SegmentCleanup
from recording.transcriber import Transcriber

logger = logging.getLogger("discord_bot")

RECORDINGS_PATH = os.getenv("DiscordVoiceDatabase_RECORDINGS_PATH", "recordings")
CLIPS_PATH = os.getenv("DiscordVoiceDatabase_CLIPS_PATH", "clips")
RETENTION_DAYS = int(os.getenv("DiscordVoiceDatabase_RETENTION_DAYS", "7"))

# Chime WAV files — played on recording start/stop and clip playback boundaries.
# Paths are resolved relative to the project root (one level above this cog).
_BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHIME_START = os.path.join(_BOT_ROOT, "assets", "chimes", "start.wav")
CHIME_END   = os.path.join(_BOT_ROOT, "assets", "chimes", "end.wav")

async def _play_chime(vc: discord.VoiceClient, path: str) -> None:
    """Play a short WAV chime on *vc* and await its completion."""
    if not vc or not vc.is_connected() or not os.path.exists(path):
        return
    if vc.is_playing():
        return  # never interrupt an ongoing clip with a chime
    loop = asyncio.get_running_loop()
    done = asyncio.Event()

    def _after(err):
        if err:
            logger.debug(f"Chime playback error ({os.path.basename(path)}): {err}")
        loop.call_soon_threadsafe(done.set)

    try:
        vc.play(discord.FFmpegPCMAudio(path), after=_after)
        await asyncio.wait_for(done.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.debug(f"Chime timed out: {path}")
        if vc.is_playing():
            vc.stop()
    except Exception as e:
        logger.debug(f"Chime play failed ({os.path.basename(path)}): {e}")


class VoiceDatabase(commands.Cog, name="voicedatabase"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.recorders: dict[int, VoiceRecorder] = {}  # guild_id -> VoiceRecorder
        self.retriever = None
        self.cleanup = None
        self.transcriber = None
        # Tracks guilds where a soft reconnect was already attempted this cycle.
        # If still stale on the next health check the hard reconnect path is used.
        self._soft_reconnect_tried: set[int] = set()

    async def cog_load(self):
        """Called when the cog is loaded. Start cleanup task."""
        logger.info("VoiceDatabase cog loading...")
        # Wait until db is ready (setup_hook sets self.bot.database)
        self.retriever = ClipRetriever(self.bot.database, output_path=CLIPS_PATH)
        self.cleanup = SegmentCleanup(self.bot.database, default_retention_days=RETENTION_DAYS)
        self.cleanup.start()
        self.transcriber = Transcriber(self.bot.database)
        self.transcriber.start()
        self.auto_rejoin_loop.start()
        logger.info("VoiceDatabase cog loaded — cleanup, transcriber, and auto-rejoin active")

    async def cog_unload(self):
        """Called when the cog is unloaded. Stop all recordings."""
        logger.info("VoiceDatabase cog unloading...")
        self.auto_rejoin_loop.cancel()
        if self.cleanup:
            self.cleanup.stop()
        if self.transcriber:
            self.transcriber.stop()
        for guild_id in list(self.recorders.keys()):
            recorder = self.recorders.pop(guild_id, None)
            if recorder:
                await recorder.stop()
        logger.info("VoiceDatabase cog unloaded")

    async def cog_check(self, ctx: Context) -> bool:
        """All commands in this cog require a guild context and optionally a specific role."""
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return False
        required_role_id = self.bot.required_role_id
        if required_role_id is not None:
            if not any(role.id == required_role_id for role in ctx.author.roles):
                await ctx.send("You don't have the required role to use this bot.")
                return False
        return True

    async def cog_command_error(self, ctx: Context, error: Exception) -> None:
        """Suppress CheckFailure from cog_check — the message was already sent."""
        if isinstance(error, commands.CheckFailure):
            return
        # Let other errors propagate to the bot-level handler
        raise error

    async def _connect_voice(self, channel: discord.VoiceChannel) -> discord.VoiceClient | None:
        """Connect to a voice channel, clearing any stale session first.
        Returns the VoiceClient or None on failure."""
        logger.debug(f"Connecting to voice channel #{channel.name} in {channel.guild.name}")
        guild = channel.guild
        if guild.voice_client:
            try:
                await asyncio.wait_for(guild.voice_client.disconnect(force=False), timeout=5.0)
            except Exception:
                await guild.voice_client.disconnect(force=True)
            await asyncio.sleep(2)  # let Discord process the leave before rejoining

        for attempt in range(3):
            try:
                vc = await channel.connect(cls=voice_recv.VoiceRecvClient, timeout=30.0, reconnect=False)
                if not vc.is_connected():
                    logger.warning(f"Voice connect returned but is_connected() is False (attempt {attempt + 1})")
                    await vc.disconnect(force=True)
                    await asyncio.sleep(2)
                    continue
                logger.info(f"Voice connected to #{channel.name} (attempt {attempt + 1})")
                return vc
            except (discord.ClientException, discord.errors.ConnectionClosed) as e:
                logger.warning(f"Voice connect failed (attempt {attempt + 1}): {e}")
                if attempt < 2:
                    await asyncio.sleep(3)
                else:
                    logger.error("Voice connect failed after 3 attempts")
                    return None

    # ── Helper methods ─────────────────────────────────────────────

    @staticmethod
    def _parse_date_range(date: str | None, end_date: str | None) -> tuple[float, float, str]:
        """Parse date/end_date strings into (start_ts, end_ts, date_label).
        Raises ValueError with a user-facing message on invalid input."""
        if not date:
            date = datetime.now(EASTERN).strftime("%Y-%m-%d")

        try:
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=EASTERN)
        except ValueError:
            raise ValueError("Invalid date format. Use `YYYY-MM-DD` (e.g. `2026-05-05`).")

        if end_date:
            try:
                range_end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=EASTERN)
            except ValueError:
                raise ValueError("Invalid end date format. Use `YYYY-MM-DD` (e.g. `2026-05-05`).")
            if range_end < day_start:
                raise ValueError("End date must be on or after the start date.")
            day_end = range_end + timedelta(days=1)
            date_label = f"{date} to {end_date}"
        else:
            day_end = day_start + timedelta(days=1)
            date_label = date

        return day_start.timestamp(), day_end.timestamp(), date_label

    @staticmethod
    def _parse_search_date_range(
        date: str | None, end_date: str | None, query: str
    ) -> tuple[float, float, str, str]:
        """Like _parse_date_range but folds invalid dates into the query for search commands.
        Returns (start_ts, end_ts, date_label, updated_query).
        Raises ValueError only for end_date < start_date."""
        if not date:
            date = datetime.now(EASTERN).strftime("%Y-%m-%d")

        try:
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=EASTERN)
        except ValueError:
            # Treat the invalid date as part of the search query
            query = f"{date} {end_date} {query}".strip() if end_date else f"{date} {query}".strip()
            date = datetime.now(EASTERN).strftime("%Y-%m-%d")
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=EASTERN)
            end_date = None

        range_end = None
        if end_date:
            try:
                range_end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=EASTERN)
            except ValueError:
                query = f"{end_date} {query}".strip()
                end_date = None

        if range_end is not None:
            if range_end < day_start:
                raise ValueError("End date must be on or after the start date.")
            day_end = range_end + timedelta(days=1)
            date_label = f"{date} to {end_date}"
        else:
            day_end = day_start + timedelta(days=1)
            date_label = date

        return day_start.timestamp(), day_end.timestamp(), date_label, query

    @staticmethod
    def _filter_blank_segments(segments: list) -> list:
        """Remove segments with 'Blank' transcripts."""
        return [
            seg for seg in segments
            if not (len(seg) > 8 and seg[8] == "Blank")
        ]

    async def _play_audio_in_voice(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel,
        clip_path: str,
        *,
        timeout: float = 300.0,
    ) -> str | None:
        """
        Play clip_path in voice_channel. Manages VC connection, recorder
        pause/resume, chimes, and file cleanup.

        Returns None on success, or an error message string on failure.
        Always deletes clip_path when done.
        """
        recorder = self.recorders.get(guild.id)
        is_recording = recorder is not None
        joined_for_playback = False
        vc = guild.voice_client

        # ── Connect or reuse voice ────────────────────────────────
        if vc is None or not vc.is_connected():
            if is_recording:
                self._cleanup_file(clip_path)
                return ("Recording session lost its voice connection. "
                        "Use `/stoprecord` then `/record` to restart.")
            try:
                vc = await voice_channel.connect()
                joined_for_playback = True
                await asyncio.sleep(2)
            except Exception as e:
                self._cleanup_file(clip_path)
                return f"Failed to connect to voice: {e}"
        elif not is_recording and vc.channel != voice_channel:
            # Only move when not recording; moving while recording breaks the session.
            await vc.move_to(voice_channel)
            await asyncio.sleep(1)
        # If recording is active and user is in a different channel, the clip plays
        # in the recording channel — the bot does not move.

        if vc.is_playing():
            vc.stop()

        # ── Play with chimes ──────────────────────────────────────
        loop = asyncio.get_running_loop()
        done_event = asyncio.Event()
        playback_error = [None]

        def after_play(error):
            playback_error[0] = error
            if error:
                logger.error(f"Playback error: {error}")
            loop.call_soon_threadsafe(done_event.set)

        if recorder:
            recorder.pause_listening()

        await _play_chime(vc, CHIME_START)

        try:
            source = discord.FFmpegOpusAudio(clip_path)
            vc.play(source, after=after_play)
        except Exception as e:
            logger.error(f"vc.play() failed: {e}")
            if recorder:
                recorder.resume_listening()
            self._cleanup_file(clip_path)
            return f"Failed to start playback: `{e}`"

        try:
            await asyncio.wait_for(done_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Playback timed out after {timeout}s, stopping")
            if vc.is_playing():
                vc.stop()

        await _play_chime(vc, CHIME_END)

        # Always resume the recording sink
        if recorder:
            recorder.resume_listening()

        error_msg = f"Playback failed: `{playback_error[0]}`" if playback_error[0] else None

        self._cleanup_file(clip_path)

        if joined_for_playback and not is_recording:
            await vc.disconnect()

        return error_msg

    @staticmethod
    def _cleanup_file(path: str):
        """Remove a file, ignoring errors if it doesn't exist."""
        try:
            os.remove(path)
        except OSError:
            pass

    # ── Participation commands ──────────────────────────────────────────

    @commands.hybrid_command(
        name="join",
        description="Opt in to voice recording. Your audio will be recorded when the bot is active.",
    )
    async def register_user(self, context: Context) -> None:
        newly_registered = await self.bot.database.register_user(
            guild_id=context.guild.id,
            user_id=context.author.id,
            username=str(context.author),
        )

        if newly_registered:
            logger.info(f"/join: {context.author} ({context.author.id}) opted in to guild {context.guild.id}")
            embed = discord.Embed(
                description=f"{context.author.mention} has opted in to voice recording.",
                color=0x57F287,
            )
            # Refresh consent on active recorder
            recorder = self.recorders.get(context.guild.id)
            if recorder:
                await recorder.refresh_consent()
        else:
            logger.debug(f"/join: {context.author} ({context.author.id}) already opted in")
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
        was_registered = await self.bot.database.unregister_user(
            guild_id=context.guild.id,
            user_id=context.author.id,
        )

        if was_registered:
            logger.info(f"/leave: {context.author} ({context.author.id}) opted out of guild {context.guild.id}")
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

        logger.info(f"/record: {context.author} starting recording in #{channel.name} (guild {context.guild.id})")
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
            transcriber=self.transcriber,
        )
        await recorder.start(vc)
        self.recorders[context.guild.id] = recorder
        logger.info(f"/record: Recording active in #{channel.name} (segment_dur={settings['segment_duration_sec']}s)")

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
        recorder = self.recorders.pop(context.guild.id, None)
        if recorder is None:
            await context.send(
                embed=discord.Embed(
                    description="Not currently recording in this server.",
                    color=0xFEE75C,
                )
            )
            return

        logger.info(f"/stoprecord: Stopping recording in guild {context.guild.id}")
        vc = context.guild.voice_client
        await recorder.stop()

        # Disconnect from voice
        if context.guild.voice_client:
            await context.guild.voice_client.disconnect(force=True)

        logger.info(f"/stoprecord: Recording stopped and disconnected in {context.guild.name}")
        await context.send(
            embed=discord.Embed(
                description="Recording stopped and disconnected.",
                color=0xED4245,
            )
        )

    # ── Clip retrieval command ──────────────────────────────────────────

    @commands.hybrid_command(
        name="clip",
        description="Browse and download a recorded clip for a user on a given day or date range.",
    )
    @app_commands.describe(
        user="The user whose audio you want to retrieve",
        date="Date or start date in Eastern time (YYYY-MM-DD, defaults to today)",
        end_date="End date for range search (YYYY-MM-DD, optional)",
    )
    async def retrieve_clip(
        self,
        context: Context,
        user: discord.User,
        date: str = None,
        end_date: str = None,
    ) -> None:
        try:
            start_ts, end_ts, date_label = self._parse_date_range(date, end_date)
        except ValueError as e:
            await context.send(embed=discord.Embed(description=str(e), color=0xED4245))
            return

        all_segments = await self.bot.database.get_segments_in_range(
            user_id=user.id, start_ts=start_ts, end_ts=end_ts, guild_id=context.guild.id,
        )
        segments = self._filter_blank_segments(all_segments)

        logger.info(f"/clip: user={user.id} date={date_label} — {len(all_segments)} total, {len(segments)} after filter")

        if not segments:
            await context.send(
                embed=discord.Embed(
                    description=f"No recordings found for {user.mention} on {date_label}.",
                    color=0xFEE75C,
                )
            )
            return

        view = _ClipSelectView(
            cog=self, segments=segments, target_user=user,
            invoker_id=context.author.id, date=date_label, mode="download",
        )
        embed = view.build_embed()
        view.message = await context.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="transcribe",
        description="Backfill transcriptions for all past recordings that have none.",
    )
    @commands.has_permissions(manage_guild=True)
    async def transcribe_past(self, context: Context) -> None:
        if not self.transcriber:
            await context.send(embed=discord.Embed(description="Transcription service is not running.", color=0xED4245))
            return

        segments = await self.bot.database.get_untranscribed_segments(guild_id=context.guild.id)
        valid = [(seg_id, fp) for seg_id, fp in segments if fp and os.path.exists(fp)]
        logger.info(f"/transcribe: {len(segments)} untranscribed, {len(valid)} with files on disk")
        if not valid:
            await context.send(embed=discord.Embed(description="No untranscribed segments found.", color=0xFEE75C))
            return

        total = len(valid)
        progress_msg = await context.send(embed=discord.Embed(
            title="📝 Transcribing...",
            description=f"**0 / {total}** complete",
            color=0x5865F2,
        ))

        done = 0
        for seg_id, file_path in valid:
            try:
                transcript = await asyncio.to_thread(self.transcriber.transcribe_file, file_path)
            except Exception as e:
                logger.error(f"Backfill transcription failed for segment {seg_id}: {e}")
                transcript = ""

            result = transcript.strip() if transcript and transcript.strip() else "Blank"
            await self.bot.database.set_segment_transcript(seg_id, result)
            done += 1

            preview = result[:120] + ("..." if len(result) > 120 else "")
            await progress_msg.edit(embed=discord.Embed(
                title="📝 Transcribing...",
                description=f"**{done} / {total}** complete\n\n`Segment {seg_id}` — *{preview}*",
                color=0x5865F2,
            ))

        await progress_msg.edit(embed=discord.Embed(
            title="✅ Transcription Complete",
            description=f"Processed **{done}** segment(s).",
            color=0x57F287,
        ))

    @commands.hybrid_command(
        name="listclips",
        description="List all recorded segments for a user on a given day or date range.",
    )
    @app_commands.describe(
        user="The user to list recordings for",
        date="Date or start date in Eastern time (YYYY-MM-DD, defaults to today)",
        end_date="End date for range search (YYYY-MM-DD, optional)",
    )
    async def list_clips(
        self,
        context: Context,
        user: discord.User,
        date: str = None,
        end_date: str = None,
    ) -> None:
        try:
            start_ts, end_ts, date_label = self._parse_date_range(date, end_date)
        except ValueError as e:
            await context.send(embed=discord.Embed(description=str(e), color=0xED4245))
            return

        all_segments = await self.bot.database.get_segments_in_range(
            user_id=user.id, start_ts=start_ts, end_ts=end_ts, guild_id=context.guild.id,
        )
        segments = self._filter_blank_segments(all_segments)

        logger.info(f"/listclips: user={user.id} date={date_label} — {len(all_segments)} total, {len(segments)} after filter")

        if not segments:
            await context.send(
                embed=discord.Embed(
                    description=f"No recordings with voice activity found for {user.mention} on {date_label}.",
                    color=0xFEE75C,
                )
            )
            return

        view = _ClipSelectView(
            cog=self, segments=segments, target_user=user,
            invoker_id=context.author.id, date=date_label,
        )
        embed = view.build_embed()
        view.message = await context.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="listtext",
        description="List recorded segments and view the transcript text for a user on a given day or date range.",
    )
    @app_commands.describe(
        user="The user whose transcripts to view",
        date="Date or start date in Eastern time (YYYY-MM-DD, defaults to today)",
        end_date="End date for range search (YYYY-MM-DD, optional)",
    )
    async def list_text(
        self,
        context: Context,
        user: discord.User,
        date: str = None,
        end_date: str = None,
    ) -> None:
        try:
            start_ts, end_ts, date_label = self._parse_date_range(date, end_date)
        except ValueError as e:
            await context.send(embed=discord.Embed(description=str(e), color=0xED4245))
            return

        all_segments = await self.bot.database.get_segments_in_range(
            user_id=user.id, start_ts=start_ts, end_ts=end_ts, guild_id=context.guild.id,
        )
        segments = self._filter_blank_segments(all_segments)

        logger.info(f"/listtext: user={user.id} date={date_label} — {len(all_segments)} total, {len(segments)} after filter")

        if not segments:
            await context.send(
                embed=discord.Embed(
                    description=f"No recordings with voice activity found for {user.mention} on {date_label}.",
                    color=0xFEE75C,
                )
            )
            return

        view = _ClipSelectView(
            cog=self, segments=segments, target_user=user,
            invoker_id=context.author.id, date=date_label, mode="text",
        )
        embed = view.build_embed()
        view.message = await context.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="search",
        description="Search recordings by transcript text for a user on a date or date range.",
    )
    @app_commands.describe(
        user="The user to search recordings for",
        date="Date or start date in Eastern time (YYYY-MM-DD, defaults to today)",
        query="Text to search for in transcripts",
        end_date="End date for range search (YYYY-MM-DD, optional)",
    )
    async def search_clips(
        self,
        context: Context,
        user: discord.User,
        date: str = None,
        end_date: str = None,
        *,
        query: str = "",
    ) -> None:
        try:
            start_ts, end_ts, date_label, query = self._parse_search_date_range(date, end_date, query)
        except ValueError as e:
            await context.send(embed=discord.Embed(description=str(e), color=0xED4245))
            return

        if not query:
            await context.send(
                embed=discord.Embed(description="Please provide a search query.", color=0xED4245)
            )
            return

        segments = await self.bot.database.search_segments_by_transcript(
            user_id=user.id, start_ts=start_ts, end_ts=end_ts, query=query, guild_id=context.guild.id,
        )

        logger.info(f"/search: user={user.id} date={date_label} query=\"{query}\" — {len(segments)} result(s)")

        if not segments:
            await context.send(
                embed=discord.Embed(
                    description=f"No results for **\"{query}\"** in {user.mention}'s recordings on {date_label}.",
                    color=0xFEE75C,
                )
            )
            return

        view = _ClipSelectView(
            cog=self, segments=segments, target_user=user,
            invoker_id=context.author.id, date=f"{date_label} — \"{query}\"",
        )
        embed = view.build_embed()
        view.message = await context.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="searchtext",
        description="Search recordings by transcript and view the matching text.",
    )
    @app_commands.describe(
        user="The user to search recordings for",
        date="Date or start date in Eastern time (YYYY-MM-DD, defaults to today)",
        query="Text to search for in transcripts",
        end_date="End date for range search (YYYY-MM-DD, optional)",
    )
    async def search_text(
        self,
        context: Context,
        user: discord.User,
        date: str = None,
        end_date: str = None,
        *,
        query: str = "",
    ) -> None:
        try:
            start_ts, end_ts, date_label, query = self._parse_search_date_range(date, end_date, query)
        except ValueError as e:
            await context.send(embed=discord.Embed(description=str(e), color=0xED4245))
            return

        if not query:
            await context.send(
                embed=discord.Embed(description="Please provide a search query.", color=0xED4245)
            )
            return

        segments = await self.bot.database.search_segments_by_transcript(
            user_id=user.id, start_ts=start_ts, end_ts=end_ts, query=query, guild_id=context.guild.id,
        )

        logger.info(f"/searchtext: user={user.id} date={date_label} query=\"{query}\" — {len(segments)} result(s)")

        if not segments:
            await context.send(
                embed=discord.Embed(
                    description=f"No results for **\"{query}\"** in {user.mention}'s recordings on {date_label}.",
                    color=0xFEE75C,
                )
            )
            return

        view = _ClipSelectView(
            cog=self, segments=segments, target_user=user,
            invoker_id=context.author.id, date=f"{date_label} — \"{query}\"",
            mode="text",
        )
        embed = view.build_embed()
        view.message = await context.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="searchclips",
        description="Search recordings by transcript text and download a matching clip.",
    )
    @app_commands.describe(
        user="The user to search recordings for",
        date="Date or start date in Eastern time (YYYY-MM-DD, defaults to today)",
        query="Text to search for in transcripts",
        end_date="End date for range search (YYYY-MM-DD, optional)",
    )
    async def search_clips_download(
        self,
        context: Context,
        user: discord.User,
        date: str = None,
        end_date: str = None,
        *,
        query: str = "",
    ) -> None:
        try:
            start_ts, end_ts, date_label, query = self._parse_search_date_range(date, end_date, query)
        except ValueError as e:
            await context.send(embed=discord.Embed(description=str(e), color=0xED4245))
            return

        if not query:
            await context.send(
                embed=discord.Embed(description="Please provide a search query.", color=0xED4245)
            )
            return

        segments = await self.bot.database.search_segments_by_transcript(
            user_id=user.id, start_ts=start_ts, end_ts=end_ts, query=query, guild_id=context.guild.id,
        )

        logger.info(f"/searchclips: user={user.id} date={date_label} query=\"{query}\" — {len(segments)} result(s)")

        if not segments:
            await context.send(
                embed=discord.Embed(
                    description=f"No results for **\"{query}\"** in {user.mention}'s recordings on {date_label}.",
                    color=0xFEE75C,
                )
            )
            return

        view = _ClipSelectView(
            cog=self, segments=segments, target_user=user,
            invoker_id=context.author.id, date=f"{date_label} — \"{query}\"",
            mode="download",
        )
        embed = view.build_embed()
        view.message = await context.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="favorites",
        description="Browse and download your saved favorite clips.",
    )
    async def favorites(self, context: Context) -> None:
        favs = await self.bot.database.get_favorites(context.author.id, context.guild.id)
        logger.info(f"/favorites: user={context.author.id} guild={context.guild.id} — {len(favs)} favorite(s)")
        if not favs:
            await context.send(
                embed=discord.Embed(
                    description="You have no favorites yet. Use the ⭐ button on any clip menu to save one.",
                    color=0xFEE75C,
                )
            )
            return
        view = _FavoriteSelectView(cog=self, favorites=favs, invoker_id=context.author.id)
        embed = view.build_embed()
        view.message = await context.send(embed=embed, view=view)

    @commands.hybrid_command(
        name="playclip",
        description="Play a recorded clip in your current voice channel.",
    )
    @app_commands.describe(
        user="The user whose audio you want to play",
        start="Start timestamp (YYYY-MM-DDTHH:MM:SS EST/EDT)",
        minutes="Duration in minutes",
    )
    async def play_clip(
        self,
        context: Context,
        user: discord.User,
        start: str,
        minutes: int = 10,
    ) -> None:
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

        try:
            start_time = datetime.fromisoformat(start).replace(tzinfo=EASTERN)
        except ValueError:
            await context.send(
                embed=discord.Embed(
                    description="Invalid timestamp format. Use `YYYY-MM-DDTHH:MM:SS` in EST/EDT (e.g. `2026-05-02T22:00:00`).",
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

        # Determine voice channel — prefer the invoker's channel
        voice_channel = None
        if context.author.voice and context.author.voice.channel:
            voice_channel = context.author.voice.channel
        elif context.guild.id in self.recorders:
            voice_channel = self.recorders[context.guild.id].channel

        if voice_channel is None:
            await context.send(
                embed=discord.Embed(
                    description="Join a voice channel first (or start recording so the bot is already connected).",
                    color=0xED4245,
                )
            )
            return

        await context.defer()

        logger.info(f"/playclip: user={user.id} start={start} minutes={minutes}")
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

        await context.send(
            embed=discord.Embed(
                description=f"Playing clip for {user.mention} ({minutes} min from `{start}`).",
                color=0x5865F2,
            )
        )

        logger.info(
            f"play_clip: starting playback user={user.id} "
            f"channel=#{voice_channel.name}"
        )
        error = await self._play_audio_in_voice(context.guild, voice_channel, clip_path)
        if error:
            await context.send(embed=discord.Embed(description=error, color=0xED4245))

    @commands.hybrid_command(
        name="stop",
        description="Stop the currently playing clip.",
    )
    async def stop_playback(self, context: Context) -> None:
        vc = context.guild.voice_client
        if vc is None or not vc.is_playing():
            await context.send(embed=discord.Embed(
                description="Nothing is currently playing.",
                color=0xFEE75C,
            ))
            return

        logger.info(f"/stop: Playback stopped by {context.author} in {context.guild.name}")
        vc.stop()
        await context.send(embed=discord.Embed(
            description="Playback stopped.",
            color=0x57F287,
        ))

    # ── Settings commands ───────────────────────────────────────────────

    @commands.hybrid_command(
        name="setchannel",
        description="Set the primary recording channel for auto-join.",
    )
    @commands.has_permissions(manage_guild=True)
    async def set_channel(self, context: Context, channel: discord.VoiceChannel) -> None:
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
        if days < 1 or days > 365:
            await context.send(
                embed=discord.Embed(
                    description="Retention must be between 1 and 365 days.",
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
        recorder = self.recorders.get(context.guild.id)
        settings = await self.bot.database.get_settings(context.guild.id)
        participants = await self.bot.database.get_participants(context.guild.id)

        is_recording = recorder is not None
        channel_name = recorder.channel.name if recorder else "None"
        active_streams = len(recorder.user_streams) if recorder else 0

        primary_ch = None
        if settings["primary_channel_id"]:
            primary_ch = context.guild.get_channel(settings["primary_channel_id"])

        # Fetch talk time stats for past week and all time
        week_ago = time.time() - 7 * 86400
        week_stats = await self.bot.database.get_talk_time_by_user(context.guild.id, since_ts=week_ago)
        alltime_stats = await self.bot.database.get_talk_time_by_user(context.guild.id, since_ts=0.0)

        def _format_duration(seconds: float) -> str:
            seconds = int(seconds)
            if seconds < 60:
                return f"{seconds}s"
            elif seconds < 3600:
                return f"{seconds // 60}m {seconds % 60}s"
            else:
                h = seconds // 3600
                m = (seconds % 3600) // 60
                return f"{h}h {m}m"

        def _build_talk_field(stats: list) -> str:
            if not stats:
                return "*No data*"
            total = sum(s for _, s in stats)
            if total == 0:
                return "*No data*"
            lines = []
            for user_id, secs in stats:
                member = context.guild.get_member(user_id)
                name = member.display_name if member else f"User {user_id}"
                name = name[:16]  # truncate long names
                pct = secs / total * 100
                bar_filled = round(pct / 10)  # 0–10 blocks
                bar = "█" * bar_filled + "░" * (10 - bar_filled)
                lines.append(f"`{bar}` **{name}** {_format_duration(secs)} ({pct:.0f}%)")
            return "\n".join(lines)

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
        embed.add_field(
            name="🗣️ Share of Voice — Past Week",
            value=_build_talk_field(week_stats),
            inline=False,
        )
        embed.add_field(
            name="🗣️ Share of Voice — All Time",
            value=_build_talk_field(alltime_stats),
            inline=False,
        )

        await context.send(embed=embed)

    # ── Performance stats command ──────────────────────────────────────

    @commands.hybrid_command(
        name="perfstats",
        description="Show running averages of processing times per pipeline stage.",
    )
    @app_commands.describe(days="Lookback window in days (default: 7)")
    async def perf_stats(self, context: Context, days: int = 7) -> None:
        days = max(days, 1)
        now = time.time()
        since_ts = now - (days * 86400)
        since_24h = now - 86400

        stats = await self.bot.database.get_perf_stats(since_ts, since_24h)
        if not stats:
            await context.send("No performance data recorded yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Performance Stats",
            description=f"Processing time averages per 60s audio segment (last {days} days)",
            color=0x3498DB,
        )

        total = 0.0
        for method, count, avg_60s, avg_24h in stats:
            avg_24h_str = f"{avg_24h:.2f}s" if avg_24h is not None else "—"
            embed.add_field(
                name=method.capitalize(),
                value=f"`{avg_60s:.2f}s` avg ({count} samples) · 24h: `{avg_24h_str}`",
                inline=False,
            )
            total += avg_60s or 0

        embed.set_footer(text=f"Pipeline total: {total:.2f}s per 60s segment")
        await context.send(embed=embed)

    # ── Auto-rejoin loop ────────────────────────────────────────────────

    AUDIO_STALE_THRESHOLD = 180  # seconds — 3 min with no packets = dead UDP

    @tasks.loop(minutes=2.0)
    async def auto_rejoin_loop(self):
        """
        Periodically check voice health and auto-join voice channels.

        For guilds WITH an active recorder: detect stale audio (voice UDP
        died silently) and reconnect.
        For guilds WITHOUT a recorder: scan for populated channels to join.
        """
        for guild in self.bot.guilds:
            recorder = self.recorders.get(guild.id)

            if recorder is not None:
                await self._check_voice_health(guild, recorder)
                continue

            await self._try_auto_join(guild)

    async def _check_voice_health(self, guild: discord.Guild, recorder: VoiceRecorder):
        stale = recorder.seconds_since_last_packet
        if stale < self.AUDIO_STALE_THRESHOLD:
            # Packets are flowing again — clear any pending soft-reconnect flag.
            self._soft_reconnect_tried.discard(guild.id)
            return

        members_in_channel = [m for m in recorder.channel.members if not m.bot]
        if len(members_in_channel) < 1:
            return

        channel = recorder.channel
        vc = guild.voice_client

        # ── Soft reconnect (first attempt) ──────────────────────────────
        if guild.id not in self._soft_reconnect_tried and vc and vc.is_connected():
            logger.warning(
                f"Voice audio stale for {stale:.0f}s in {guild.name} / "
                f"#{channel.name} — soft reconnect"
            )
            self._soft_reconnect_tried.add(guild.id)
            try:
                recorder.pause_listening()
                await vc.move_to(channel)
                await asyncio.sleep(3)   # allow new voice session to establish
                recorder.resume_listening()
                logger.info(f"Soft voice reconnect completed in {guild.name} / #{channel.name}")
            except Exception as e:
                logger.warning(
                    f"Soft reconnect failed in {guild.name}: {e} — "
                    f"will hard reconnect on next health check"
                )
            return

        # ── Hard reconnect (soft failed or vc already gone) ─────────────
        # Fully stops the recorder, disconnects, and rejoins the channel.
        self._soft_reconnect_tried.discard(guild.id)
        logger.warning(
            f"Voice audio stale for {stale:.0f}s in {guild.name} / "
            f"#{channel.name} — hard reconnect"
        )

        settings = await self.bot.database.get_settings(guild.id)

        old_recorder = self.recorders.pop(guild.id, None)
        if old_recorder:
            await old_recorder.stop()
        if guild.voice_client:
            try:
                await guild.voice_client.disconnect(force=True)
            except Exception:
                pass
        await asyncio.sleep(2)

        try:
            vc = await self._connect_voice(channel)
            if vc is None:
                logger.error(f"Voice health hard reconnect failed for {guild.name}")
                return
            new_recorder = VoiceRecorder(
                bot=self.bot,
                guild=guild,
                channel=channel,
                database=self.bot.database,
                recordings_path=RECORDINGS_PATH,
                segment_duration_sec=settings["segment_duration_sec"],
                transcriber=self.transcriber,
            )
            await new_recorder.start(vc)
            self.recorders[guild.id] = new_recorder
            logger.info(
                f"Voice health hard reconnect successful in {guild.name} / #{channel.name}"
            )
        except Exception as e:
            logger.error(f"Voice health hard reconnect failed for {guild.name}: {e}")

    async def _try_auto_join(self, guild: discord.Guild):
        settings = await self.bot.database.get_settings(guild.id)
        if not settings["enabled"]:
            return

        consented = await self.bot.database.get_consented_user_ids(guild.id)

        best_channel = None
        best_count = 0
        for channel in guild.voice_channels:
            members_in_channel = [m for m in channel.members if not m.bot]
            if len(members_in_channel) < 2:
                continue
            active_consented = consented & {m.id for m in members_in_channel}
            if not active_consented:
                continue
            if len(members_in_channel) > best_count:
                best_count = len(members_in_channel)
                best_channel = channel

        if best_channel is None:
            return

        try:
            vc = await self._connect_voice(best_channel)
            if vc is None:
                return
            recorder = VoiceRecorder(
                bot=self.bot,
                guild=guild,
                channel=best_channel,
                database=self.bot.database,
                recordings_path=RECORDINGS_PATH,
                segment_duration_sec=settings["segment_duration_sec"],
                transcriber=self.transcriber,
            )
            await recorder.start(vc)
            self.recorders[guild.id] = recorder
            logger.info(
                f"Auto-joined {guild.name} / #{best_channel.name} ({best_count} members present)"
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
        if member.bot:
            return

        guild_id = member.guild.id
        recorder = self.recorders.get(guild_id)
        if recorder is None:
            return

        channel = recorder.channel

        if before.channel != channel:
            return

        consented = recorder.consented_users
        members_in_channel = {m.id for m in channel.members if not m.bot}
        active_consented = consented & members_in_channel
        logger.debug(
            f"Voice state update in #{channel.name}: {len(members_in_channel)} member(s), "
            f"{len(active_consented)} consented"
        )

        if not active_consented:
            # No consented users left — stop recording
            vc = member.guild.voice_client
            recorder = self.recorders.pop(guild_id, None)
            if recorder:
                await recorder.stop()
            if member.guild.voice_client:
                await member.guild.voice_client.disconnect(force=True)
            logger.info(
                f"Auto-stopped recording in {member.guild.name} — no consented users remain"
            )


class _PlayModal(discord.ui.Modal, title="Play Clip"):
    duration = discord.ui.TextInput(
        label="Duration (minutes)",
        placeholder="e.g. 5",
        required=True,
        max_length=3,
    )
    start_offset = discord.ui.TextInput(
        label="Start offset from segment start (minutes)",
        placeholder="0 = play from beginning",
        required=False,
        default="0",
        max_length=3,
    )

    def __init__(self, cog, seg, target_user, default_duration):
        super().__init__()
        self.cog = cog
        self.seg = seg
        self.target_user = target_user
        self.duration.default = default_duration

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration_minutes = int(self.duration.value)
        except ValueError:
            await interaction.response.send_message("Duration must be a number.", ephemeral=True)
            return

        try:
            offset_minutes = int(self.start_offset.value or "0")
        except ValueError:
            await interaction.response.send_message("Offset must be a number.", ephemeral=True)
            return

        if duration_minutes < 1 or duration_minutes > 60:
            await interaction.response.send_message("Duration must be between 1 and 60.", ephemeral=True)
            return

        await interaction.response.defer()

        guild = interaction.guild
        member = guild.get_member(interaction.user.id)
        voice_channel = None
        if member and member.voice and member.voice.channel:
            voice_channel = member.voice.channel
        elif guild.id in self.cog.recorders:
            voice_channel = self.cog.recorders[guild.id].channel

        if voice_channel is None:
            await interaction.followup.send(
                embed=discord.Embed(description="Join a voice channel first.", color=0xED4245),
                ephemeral=True,
            )
            return

        start_time = datetime.fromtimestamp(self.seg[4], tz=EASTERN)

        clip_path = await self.cog.retriever.retrieve_clip(
            user_id=self.target_user.id,
            start_time=start_time,
            duration_minutes=duration_minutes,
            guild_id=guild.id,
            anchor_seg=self.seg,
            offset_sec=offset_minutes * 60,
        )

        if clip_path is None:
            logger.warning(f"PlayModal: No audio for segment {self.seg[0]} user={self.target_user.id}")
            await interaction.followup.send(
                embed=discord.Embed(
                    description="No audio found for that segment. The file may have been cleaned up or is still processing.",
                    color=0xFEE75C,
                ),
                ephemeral=True,
            )
            return

        play_from = datetime.fromtimestamp(self.seg[4] + (offset_minutes * 60), tz=EASTERN)
        start_str = play_from.strftime("%I:%M %p")
        logger.info(
            f"PlayModal: starting playback seg={self.seg[0]} user={self.target_user.id} "
            f"at {start_str} ({duration_minutes} min)"
        )

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"Playing for {self.target_user.mention} ({start_str}, {duration_minutes} min).",
                color=0x57F287,
            )
        )

        error = await self.cog._play_audio_in_voice(guild, voice_channel, clip_path)
        if error:
            await interaction.followup.send(
                embed=discord.Embed(description=error, color=0xED4245),
                ephemeral=True,
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"PlayModal error: {error}", exc_info=True)
        try:
            await interaction.followup.send(
                embed=discord.Embed(description=f"Something went wrong: `{error}`", color=0xED4245),
                ephemeral=True,
            )
        except Exception:
            pass


class _DownloadModal(discord.ui.Modal, title="Download Clip"):
    duration = discord.ui.TextInput(
        label="Duration (minutes)",
        placeholder="e.g. 5",
        required=True,
        max_length=3,
    )
    start_offset = discord.ui.TextInput(
        label="Start offset from segment start (minutes)",
        placeholder="0 = from beginning",
        required=False,
        default="0",
        max_length=3,
    )

    def __init__(self, cog, seg, target_user, default_duration):
        super().__init__()
        self.cog = cog
        self.seg = seg
        self.target_user = target_user
        self.duration.default = default_duration

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration_minutes = int(self.duration.value)
        except ValueError:
            await interaction.response.send_message("Duration must be a number.", ephemeral=True)
            return

        try:
            offset_minutes = int(self.start_offset.value or "0")
        except ValueError:
            await interaction.response.send_message("Offset must be a number.", ephemeral=True)
            return

        if duration_minutes < 1 or duration_minutes > 60:
            await interaction.response.send_message("Duration must be between 1 and 60.", ephemeral=True)
            return

        await interaction.response.defer()

        start_time = datetime.fromtimestamp(self.seg[4], tz=EASTERN)

        clip_path = await self.cog.retriever.retrieve_clip(
            user_id=self.target_user.id,
            start_time=start_time,
            duration_minutes=duration_minutes,
            guild_id=interaction.guild.id,
            anchor_seg=self.seg,
            offset_sec=offset_minutes * 60,
        )

        if clip_path is None:
            logger.warning(f"DownloadModal: No audio for segment {self.seg[0]} user={self.target_user.id}")
            await interaction.followup.send(
                embed=discord.Embed(
                    description="No audio found for that segment. The file may have been cleaned up or is still processing.",
                    color=0xFEE75C,
                ),
                ephemeral=True,
            )
            return

        file_size = os.path.getsize(clip_path)
        if file_size > 25 * 1024 * 1024:
            await interaction.followup.send(
                embed=discord.Embed(
                    description=f"Clip is too large ({file_size / 1024 / 1024:.1f} MB). Try a shorter duration.",
                    color=0xED4245,
                ),
                ephemeral=True,
            )
            try:
                os.remove(clip_path)
            except OSError:
                pass
            return

        play_from = datetime.fromtimestamp(self.seg[4] + (offset_minutes * 60), tz=EASTERN)
        start_str = play_from.strftime("%I:%M %p")
        logger.info(f"DownloadModal: Sending clip for user {self.target_user.id} at {start_str} ({duration_minutes} min, {file_size} bytes)")
        embed = discord.Embed(
            title="Audio Clip Retrieved",
            description=f"**User:** {self.target_user.mention}\n**Start:** {start_str}\n**Duration:** {duration_minutes} min",
            color=0x5865F2,
        )
        await interaction.followup.send(
            embed=embed,
            file=discord.File(clip_path, filename=os.path.basename(clip_path)),
        )

        try:
            os.remove(clip_path)
        except OSError:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"DownloadModal error: {error}", exc_info=True)
        try:
            await interaction.followup.send(
                embed=discord.Embed(description=f"Something went wrong: `{error}`", color=0xED4245),
                ephemeral=True,
            )
        except Exception:
            pass


class _FavoriteModal(discord.ui.Modal, title="Add to Favorites"):
    duration = discord.ui.TextInput(
        label="Duration (minutes)",
        placeholder="e.g. 5",
        required=True,
        max_length=3,
    )
    start_offset = discord.ui.TextInput(
        label="Start offset from segment start (minutes)",
        placeholder="0 = from beginning",
        required=False,
        default="0",
        max_length=3,
    )

    def __init__(self, cog, owner_id, seg, target_user, default_duration):
        super().__init__()
        self.cog = cog
        self.owner_id = owner_id
        self.seg = seg
        self.target_user = target_user
        self.duration.default = default_duration

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration_minutes = int(self.duration.value)
        except ValueError:
            await interaction.response.send_message("Duration must be a number.", ephemeral=True)
            return

        try:
            offset_minutes = int(self.start_offset.value or "0")
        except ValueError:
            await interaction.response.send_message("Offset must be a number.", ephemeral=True)
            return

        if duration_minutes < 1 or duration_minutes > 60:
            await interaction.response.send_message("Duration must be between 1 and 60.", ephemeral=True)
            return

        start_dt = datetime.fromtimestamp(self.seg[4], tz=EASTERN)
        time_str = start_dt.strftime("%Y-%m-%d %I:%M %p")
        transcript = self.seg[8] if len(self.seg) > 8 and self.seg[8] and self.seg[8] != "Blank" else None
        snippet = f" — {transcript[:60]}" if transcript else ""
        label = f"{self.target_user.display_name} · {time_str}{snippet}"

        added = await self.cog.bot.database.add_favorite(
            user_id=self.owner_id,
            guild_id=interaction.guild.id,
            segment_id=self.seg[0],
            target_user_id=self.target_user.id,
            offset_sec=offset_minutes * 60,
            duration_min=duration_minutes,
            label=label,
        )

        if added:
            logger.info(f"Favorite saved: user={self.owner_id} segment={self.seg[0]} ({duration_minutes}m, offset={offset_minutes}m)")
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"⭐ Saved to your favorites — **{self.target_user.display_name}** ({time_str}, {duration_minutes} min).",
                    color=0xF1C40F,
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="This clip is already in your favorites.",
                    color=0xFEE75C,
                ),
                ephemeral=True,
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"FavoriteModal error: {error}", exc_info=True)
        try:
            await interaction.response.send_message(
                embed=discord.Embed(description=f"Something went wrong: `{error}`", color=0xED4245),
                ephemeral=True,
            )
        except Exception:
            pass


class _ClipSelectView(discord.ui.View):
    def __init__(self, cog, segments, target_user, invoker_id, date, mode="play"):
        super().__init__(timeout=120)
        self.cog = cog
        self.segments = segments
        self.target_user = target_user
        self.invoker_id = invoker_id
        self.date = date
        self.mode = mode  # "play", "download", or "text"
        self.favorite_mode = False  # when True, selecting a segment saves it to favorites
        self.page = 0
        self.per_page = 10
        self.total_pages = max(1, (len(segments) + self.per_page - 1) // self.per_page)
        self.message = None  # set by caller for on_timeout
        self._rebuild_items()

    def _page_segments(self):
        start = self.page * self.per_page
        return self.segments[start:start + self.per_page]

    def _rebuild_items(self):
        self.clear_items()
        options = []
        for seg in self._page_segments():
            start_dt = datetime.fromtimestamp(seg[4], tz=EASTERN)
            label = start_dt.strftime("%I:%M:%S %p")
            if seg[5] is not None:
                end_dt = datetime.fromtimestamp(seg[5], tz=EASTERN)
                dur = int(seg[5] - seg[4])
                time_desc = f"→ {end_dt.strftime('%I:%M:%S %p')} ({dur // 60}m {dur % 60}s)"
            else:
                time_desc = "→ ongoing"
            transcript = seg[8] if len(seg) > 8 and seg[8] else None
            if transcript:
                desc = transcript[:100]
            else:
                desc = time_desc
            options.append(discord.SelectOption(
                label=label, description=desc, value=str(seg[0]),
            ))
        if self.favorite_mode:
            placeholder = "Pick a segment to favorite..."
        elif self.mode == "download":
            placeholder = "Pick a segment to download..."
        elif self.mode == "text":
            placeholder = "Pick a segment to view transcript..."
        else:
            placeholder = "Pick a segment to play..."
        select = discord.ui.Select(placeholder=placeholder, options=options)
        select.callback = self.on_select
        self.add_item(select)

        if self.total_pages > 1:
            first_btn = discord.ui.Button(label="⏮ First", style=discord.ButtonStyle.secondary, disabled=(self.page == 0))
            first_btn.callback = self._first_page
            self.add_item(first_btn)

            prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=(self.page == 0))
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)

            page_btn = discord.ui.Button(label=f"{self.page + 1}/{self.total_pages}", style=discord.ButtonStyle.primary, disabled=True)
            self.add_item(page_btn)

            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, disabled=(self.page >= self.total_pages - 1))
            next_btn.callback = self._next_page
            self.add_item(next_btn)

            last_btn = discord.ui.Button(label="Last ⏭", style=discord.ButtonStyle.secondary, disabled=(self.page >= self.total_pages - 1))
            last_btn.callback = self._last_page
            self.add_item(last_btn)

        # ⭐ favorite toggle — lets the user save a clip without playing/downloading it.
        # Auto-flows to its own row (the select fills row 0, pagination fills row 1).
        if self.mode in ("play", "download"):
            if self.favorite_mode:
                fav_btn = discord.ui.Button(label="⬅ Back", style=discord.ButtonStyle.secondary)
            else:
                fav_btn = discord.ui.Button(label="⭐ Favorite", style=discord.ButtonStyle.success)
            fav_btn.callback = self._toggle_favorite
            self.add_item(fav_btn)

    async def _toggle_favorite(self, interaction: discord.Interaction):
        self.favorite_mode = not self.favorite_mode
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    def build_embed(self):
        title = f"Recordings for {self.target_user.display_name} on {self.date}"
        if self.total_pages > 1:
            title += f" (Page {self.page + 1}/{self.total_pages})"
        embed = discord.Embed(title=title, color=0x5865F2)
        for seg in self._page_segments():
            start_dt = datetime.fromtimestamp(seg[4], tz=EASTERN)
            if seg[5] is not None:
                end_dt = datetime.fromtimestamp(seg[5], tz=EASTERN)
                duration_sec = int(seg[5] - seg[4])
                duration_str = f"{duration_sec // 60}m {duration_sec % 60}s"
                field_name = f"{start_dt.strftime('%I:%M:%S %p')} → {end_dt.strftime('%I:%M:%S %p')}  ({duration_str})"
            else:
                field_name = f"{start_dt.strftime('%I:%M:%S %p')} → ongoing"
            transcript = seg[8] if len(seg) > 8 and seg[8] else None
            field_value = f"*{transcript[:200]}{'...' if len(transcript) > 200 else ''}*" if transcript else "*No transcript yet*"
            embed.add_field(name=field_name, value=field_value, inline=False)
        if self.favorite_mode:
            action = "save to your favorites"
        elif self.mode == "download":
            action = "download"
        elif self.mode == "text":
            action = "view its transcript"
        else:
            action = "play"
        embed.set_footer(text=f"{len(self.segments)} segment(s) • Select one below to {action}")
        return embed

    async def on_timeout(self):
        """Disable all items when the view times out so stale menus don't confuse users."""
        for item in self.children:
            item.disabled = True
        try:
            if self.message:
                embed = self.build_embed()
                embed.set_footer(text="This menu has timed out. Run the command again to browse.")
                await self.message.edit(embed=embed, view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _first_page(self, interaction: discord.Interaction):
        self.page = 0
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next_page(self, interaction: discord.Interaction):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _last_page(self, interaction: discord.Interaction):
        self.page = self.total_pages - 1
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use this menu.", ephemeral=True,
            )
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        selected_id = int(interaction.data["values"][0])
        seg = next((s for s in self.segments if s[0] == selected_id), None)
        if seg is None:
            logger.warning(f"ClipSelectView: Segment not found for id={selected_id}")
            await interaction.response.send_message("Segment not found.", ephemeral=True)
            return
        logger.debug(f"ClipSelectView: Selected segment id={selected_id} mode={self.mode} favorite={self.favorite_mode}")

        if self.favorite_mode:
            if seg[5] is not None:
                default_dur = str(max(1, int((seg[5] - seg[4]) // 60) + (1 if (seg[5] - seg[4]) % 60 else 0)))
            else:
                default_dur = "1"
            modal = _FavoriteModal(
                cog=self.cog,
                owner_id=self.invoker_id,
                seg=seg,
                target_user=self.target_user,
                default_duration=default_dur,
            )
            await interaction.response.send_modal(modal)
            return

        if self.mode == "text":
            transcript = seg[8] if len(seg) > 8 and seg[8] else None
            if not transcript or transcript == "Blank":
                await interaction.response.send_message(
                    "No transcript available for this segment.", ephemeral=True,
                )
                return
            start_dt = datetime.fromtimestamp(seg[4], tz=EASTERN)
            time_str = start_dt.strftime("%I:%M:%S %p")
            header = f"[{self.target_user.display_name} — {time_str}]"
            full_text = f"{header}\n{transcript}"
            chunks = [full_text[i:i + 2000] for i in range(0, len(full_text), 2000)]
            await interaction.response.send_message(chunks[0])
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk)
            return

        if seg[5] is not None:
            default_dur = str(max(1, int((seg[5] - seg[4]) // 60) + (1 if (seg[5] - seg[4]) % 60 else 0)))
        else:
            default_dur = "1"

        if self.mode == "download":
            modal = _DownloadModal(
                cog=self.cog,
                seg=seg,
                target_user=self.target_user,
                default_duration=default_dur,
            )
        else:
            modal = _PlayModal(
                cog=self.cog,
                seg=seg,
                target_user=self.target_user,
                default_duration=default_dur,
            )
        await interaction.response.send_modal(modal)


class _FavoriteSelectView(discord.ui.View):
    """Browse a user's saved favorites: select to download, or toggle remove mode."""

    def __init__(self, cog, favorites, invoker_id):
        super().__init__(timeout=120)
        self.cog = cog
        # rows: (id, segment_id, target_user_id, offset_sec, duration_min, label, created_at)
        self.favorites = favorites
        self.invoker_id = invoker_id
        self.remove_mode = False
        self.page = 0
        self.per_page = 10
        self.total_pages = max(1, (len(favorites) + self.per_page - 1) // self.per_page)
        self.message = None  # set by caller for on_timeout
        self._rebuild_items()

    def _page_favorites(self):
        start = self.page * self.per_page
        return self.favorites[start:start + self.per_page]

    def _rebuild_items(self):
        self.clear_items()
        options = []
        for fav in self._page_favorites():
            fav_id, segment_id, _target, offset_sec, duration_min, label, _created = fav
            disp = (label or f"Segment {segment_id}")[:100]
            desc = f"{duration_min} min" + (f", +{offset_sec // 60}m offset" if offset_sec else "")
            options.append(discord.SelectOption(label=disp, description=desc[:100], value=str(fav_id)))
        placeholder = "Pick a favorite to remove..." if self.remove_mode else "Pick a favorite to download..."
        select = discord.ui.Select(placeholder=placeholder, options=options)
        select.callback = self.on_select
        self.add_item(select)

        if self.total_pages > 1:
            first_btn = discord.ui.Button(label="⏮ First", style=discord.ButtonStyle.secondary, disabled=(self.page == 0))
            first_btn.callback = self._first_page
            self.add_item(first_btn)

            prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=(self.page == 0))
            prev_btn.callback = self._prev_page
            self.add_item(prev_btn)

            page_btn = discord.ui.Button(label=f"{self.page + 1}/{self.total_pages}", style=discord.ButtonStyle.primary, disabled=True)
            self.add_item(page_btn)

            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, disabled=(self.page >= self.total_pages - 1))
            next_btn.callback = self._next_page
            self.add_item(next_btn)

            last_btn = discord.ui.Button(label="Last ⏭", style=discord.ButtonStyle.secondary, disabled=(self.page >= self.total_pages - 1))
            last_btn.callback = self._last_page
            self.add_item(last_btn)

        if self.remove_mode:
            toggle = discord.ui.Button(label="⬅ Back", style=discord.ButtonStyle.secondary)
        else:
            toggle = discord.ui.Button(label="🗑 Remove", style=discord.ButtonStyle.danger)
        toggle.callback = self._toggle_remove
        self.add_item(toggle)

    def build_embed(self):
        title = "⭐ Your Favorites"
        if self.total_pages > 1:
            title += f" (Page {self.page + 1}/{self.total_pages})"
        embed = discord.Embed(title=title, color=0xF1C40F)
        for fav in self._page_favorites():
            fav_id, segment_id, _target, offset_sec, duration_min, label, created_at = fav
            created_dt = datetime.fromtimestamp(created_at, tz=EASTERN)
            name = (label or f"Segment {segment_id}")[:256]
            meta = f"{duration_min} min"
            if offset_sec:
                meta += f" · +{offset_sec // 60}m offset"
            meta += f" · saved {created_dt.strftime('%Y-%m-%d')}"
            embed.add_field(name=name, value=meta, inline=False)
        action = "remove it" if self.remove_mode else "download it"
        embed.set_footer(text=f"{len(self.favorites)} favorite(s) • Select one below to {action}")
        return embed

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if self.message:
                embed = self.build_embed()
                embed.set_footer(text="This menu has timed out. Run the command again to browse.")
                await self.message.edit(embed=embed, view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _first_page(self, interaction: discord.Interaction):
        self.page = 0
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next_page(self, interaction: discord.Interaction):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _last_page(self, interaction: discord.Interaction):
        self.page = self.total_pages - 1
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _toggle_remove(self, interaction: discord.Interaction):
        self.remove_mode = not self.remove_mode
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use this menu.", ephemeral=True,
            )
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        fav_id = int(interaction.data["values"][0])
        fav = next((f for f in self.favorites if f[0] == fav_id), None)
        if fav is None:
            await interaction.response.send_message("Favorite not found.", ephemeral=True)
            return
        _, segment_id, target_user_id, offset_sec, duration_min, label, _created = fav

        if self.remove_mode:
            await self.cog.bot.database.remove_favorite(self.invoker_id, fav_id)
            self.favorites = [f for f in self.favorites if f[0] != fav_id]
            self.total_pages = max(1, (len(self.favorites) + self.per_page - 1) // self.per_page)
            self.page = min(self.page, self.total_pages - 1)
            if not self.favorites:
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(
                    embed=discord.Embed(description="All favorites removed.", color=0xFEE75C),
                    view=self,
                )
                return
            self._rebuild_items()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            return

        # Download mode — re-derive the exact clip from the anchor segment.
        seg = await self.cog.bot.database.get_segment_by_id(segment_id)
        if seg is None:
            await interaction.response.send_message(
                "That clip's audio is no longer available (it may have been cleaned up). "
                "Use 🗑 Remove to delete this favorite.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        start_time = datetime.fromtimestamp(seg[4], tz=EASTERN)
        clip_path = await self.cog.retriever.retrieve_clip(
            user_id=target_user_id,
            start_time=start_time,
            duration_minutes=duration_min,
            guild_id=interaction.guild.id,
            anchor_seg=seg,
            offset_sec=offset_sec,
        )

        if clip_path is None:
            logger.warning(f"Favorites: no audio for favorite {fav_id} segment={segment_id}")
            await interaction.followup.send(
                embed=discord.Embed(
                    description="No audio found for that favorite. The file may have been cleaned up.",
                    color=0xFEE75C,
                ),
                ephemeral=True,
            )
            return

        file_size = os.path.getsize(clip_path)
        if file_size > 25 * 1024 * 1024:
            await interaction.followup.send(
                embed=discord.Embed(
                    description=f"Clip is too large ({file_size / 1024 / 1024:.1f} MB).",
                    color=0xED4245,
                ),
                ephemeral=True,
            )
            self.cog._cleanup_file(clip_path)
            return

        logger.info(f"Favorites: sending favorite {fav_id} ({duration_min} min, {file_size} bytes)")
        await interaction.followup.send(
            embed=discord.Embed(
                title="⭐ Favorite Clip",
                description=label or f"Segment {segment_id}",
                color=0xF1C40F,
            ),
            file=discord.File(clip_path, filename=os.path.basename(clip_path)),
        )
        self.cog._cleanup_file(clip_path)


async def setup(bot) -> None:
    await bot.add_cog(VoiceDatabase(bot))
