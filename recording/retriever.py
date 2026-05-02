"""
Clip retriever — finds segments covering a time window,
stitches them with ffmpeg, and trims to the exact requested range.
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime

logger = logging.getLogger("discord_bot")


class ClipRetriever:
    """Retrieves audio clips from recorded segments."""

    def __init__(self, database, output_path: str = "clips"):
        self.db = database
        self.output_path = output_path
        os.makedirs(output_path, exist_ok=True)

    async def retrieve_clip(
        self,
        user_id: int,
        start_time: datetime,
        duration_minutes: int,
        guild_id: int = None,
    ) -> str:
        """
        Retrieve an audio clip for a user starting at start_time for duration_minutes.

        Returns the path to the output OGG file, or None if no segments found.
        """
        start_ts = start_time.timestamp()
        end_ts = start_ts + (duration_minutes * 60)

        # Find overlapping segments
        segments = await self.db.get_segments_in_range(
            user_id=user_id,
            start_ts=start_ts,
            end_ts=end_ts,
            guild_id=guild_id,
        )

        if not segments:
            return None

        # Filter to segments that actually have files on disk
        valid_segments = []
        for seg in segments:
            # seg: (id, guild_id, channel_id, user_id, start_ts, end_ts, file_path)
            file_path = seg[6]
            if os.path.exists(file_path):
                valid_segments.append(seg)

        if not valid_segments:
            return None

        # Build output filename
        ts_str = start_time.strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(
            self.output_path, f"clip_{user_id}_{ts_str}_{duration_minutes}m.ogg"
        )

        if len(valid_segments) == 1:
            # Single segment — just trim it
            seg = valid_segments[0]
            seg_start_ts = seg[4]
            trim_start = max(0, start_ts - seg_start_ts)
            trim_duration = duration_minutes * 60

            await asyncio.to_thread(
                self._trim_single, seg[6], output_file, trim_start, trim_duration
            )
        else:
            # Multiple segments — concat then trim
            first_seg_start = valid_segments[0][4]
            trim_start = max(0, start_ts - first_seg_start)
            trim_duration = duration_minutes * 60

            file_list = [seg[6] for seg in valid_segments]
            await asyncio.to_thread(
                self._concat_and_trim, file_list, output_file, trim_start, trim_duration
            )

        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            return output_file
        return None

    @staticmethod
    def _trim_single(input_path: str, output_path: str, start_sec: float, duration_sec: float):
        """Trim a single OGG file."""
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-ss", str(start_sec),
            "-t", str(duration_sec),
            "-c:a", "libopus",
            "-b:a", "48k",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg trim failed: {result.stderr.decode(errors='replace')}"
            )

    @staticmethod
    def _concat_and_trim(
        file_list: list[str], output_path: str, start_sec: float, duration_sec: float
    ):
        """Concatenate multiple OGG files and trim to the requested window."""
        # Write a concat file list for ffmpeg
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as tmp:
            for f in file_list:
                # ffmpeg concat requires escaped single quotes in paths
                escaped = f.replace("'", "'\\''")
                tmp.write(f"file '{escaped}'\n")
            concat_file = tmp.name

        try:
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file,
                "-ss", str(start_sec),
                "-t", str(duration_sec),
                "-c:a", "libopus",
                "-b:a", "48k",
                output_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg concat failed: {result.stderr.decode(errors='replace')}"
                )
        finally:
            os.unlink(concat_file)
