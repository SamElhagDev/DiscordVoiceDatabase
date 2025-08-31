from discord.ext import commands
from discord.ext.commands import Context
import os, time, io
import discord
import glob, subprocess, shlex, os, math


class VoiceDatabase(commands.Cog, name="voicedatabase"):
    def __init__(self, bot) -> None:
        self.bot = bot

    # Here you can just add your own commands, you'll always need to provide "self" as first parameter.

    @commands.hybrid_command(
        name="testcommand",
        description="This is a testing command that does nothing.",
    )
    async def testcommand(self, context: Context) -> None:
        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        # Do your stuff here

        # Don't forget to remove "pass", I added this just because there's no content in the method.
        pass
    
    
    @commands.hybrid_command(
        name="registeruser",
        description="This command opts in a user to the voice database.",
    )
    async def testcommand(self, context: Context) -> None:
        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        # Do your stuff here

        # Don't forget to remove "pass", I added this just because there's no content in the method.
        pass
    
    
    @commands.hybrid_command(
        name="unregisteruser",
        description="This command opts out a user from the voice database.",
    )
    async def testcommand(self, context: Context) -> None:
        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        # Do your stuff here

        # Don't forget to remove "pass", I added this just because there's no content in the method.
        pass
    
    @commands.hybrid_command(
        name="playsegment",
        description="This command plays a requested segment from the voice database.",
    )
    async def testcommand(self, context: Context) -> None:
        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        # Do your stuff here

        # Don't forget to remove "pass", I added this just because there's no content in the method.
        pass


# And then we finally add the cog to the bot so that it can load, unload, reload and use it's content.
async def setup(bot) -> None:
    await bot.add_cog(VoiceDatabase(bot))

class RollingOpusSegmentSink(discord):
    def __init__(self, segment_sec: int = 10):
        super().__init__(filters=None)  # record everyone
        self.segment_sec = segment_sec
        self._per_user = {}  # user_id -> {start_ts, ogg_file, bytes_io}

    def init_user(self, user_id: int):
        now = time.time()
        return {
            "start_ts": now,
            "buf": io.BytesIO(),  # collect raw opus frames; we’ll dump to file
            "path": None,
        }

    def wants_opus(self) -> bool:
        # keep Discord's opus packets as-is (no decode/re-encode)
        return True

    def write(self, data, user, *_):
        # data is an opus frame (bytes)
        u = self._per_user.get(user.id)
        if u is None:
            u = self._per_user[user.id] = self.init_user(user.id)

        u["buf"].write(data)

        # rotate?
        if time.time() - u["start_ts"] >= self.segment_sec:
            self._flush_segment(user.id)
            self._per_user[user.id] = self.init_user(user.id)

    def cleanup(self):
        # called when recording stops; flush whatever is open
        for uid in list(self._per_user.keys()):
            self._flush_segment(uid)
        self._per_user.clear()

    def _flush_segment(self, user_id: int):
        u = self._per_user[user_id]
        if u["buf"].tell() == 0:
            return
        u["buf"].seek(0)

        # Write a well-formed OGG/Opus file from raw opus frames.
        # Easiest: pipe frames through ffmpeg to wrap them in OGG.
        # Safer: stash raw to a temp .opusbin and let a background task remux.
        ts_ms = int(u["start_ts"] * 1000)
        folder = f"segments/{self.vc.guild.id}/{self.vc.channel.id}/{self.start_time}/{user_id}"
        os.makedirs(folder, exist_ok=True)
        raw_path = f"{folder}/{ts_ms}.opusbin"
        with open(raw_path, "wb") as f:
            f.write(u["buf"].read())

        # enqueue remux job (producer/consumer) to turn .opusbin -> .ogg
        # leave it simple for now: a helper can poll folder and remux asap.