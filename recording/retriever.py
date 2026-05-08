"""
Clip retriever — finds segments covering a time window,
stitches them with ffmpeg, and trims to the exact requested range.
"""

import asyncio
import logging
import os
import subprocess
import time
from datetime import datetime

logger = logging.getLogger("discord_bot")


class ClipRetriever:
    """Retrieves audio clips from recorded segments."""

    def __init__(self, database, output_path: str = "clips"):
        self.db = database
        self.output_path = output_path
        os.makedirs(output_path, exist_ok=True)
        logger.info(f"ClipRetriever initialized — output: {output_path}")

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

        logger.info(
            f"Retrieving clip: user={user_id} start={start_time.isoformat()} "
            f"duration={duration_minutes}m guild={guild_id}"
        )

        # Find overlapping segments
        segments = await self.db.get_segments_in_range(
            user_id=user_id,
            start_ts=start_ts,
            end_ts=end_ts,
            guild_id=guild_id,
        )

        if not segments:
            logger.warning(f"No segments found in DB for user {user_id} in range [{start_ts:.0f}, {end_ts:.0f}]")
            return None

        logger.debug(f"Found {len(segments)} DB segment(s) for clip retrieval")

        # Filter to segments that actually have files on disk.
        # Fall back to the sibling .pcm file if the .ogg hasn't been remuxed yet.
        valid_segments = []
        for seg in segments:
            # seg: (id, guild_id, channel_id, user_id, start_ts, end_ts, file_path)
            file_path = seg[6]
            if os.path.exists(file_path):
                valid_segments.append(seg)
            else:
                pcm_path = os.path.splitext(file_path)[0] + ".pcm"
                if os.path.exists(pcm_path):
                    # Wrap the row with the pcm path so ffmpeg can read it directly
                    valid_segments.append(seg[:6] + (pcm_path,))
                    logger.debug(f"Using PCM fallback for segment {seg[0]}: {pcm_path}")

        if not valid_segments:
            logger.warning(
                f"No files on disk for user {user_id} in range [{start_ts:.0f}, {end_ts:.0f}]. "
                f"DB returned {len(segments)} segment(s): {[s[6] for s in segments]}"
            )
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
            seg_end_ts = seg[5] if seg[5] is not None else end_ts
            seg_duration = seg_end_ts - seg_start_ts
            trim_start = round(max(0.0, start_ts - seg_start_ts), 3)
            # Clamp offset so we don't trim past the actual audio
            trim_start = round(min(trim_start, max(0.0, seg_duration - 1)), 3)
            trim_duration = duration_minutes * 60

            logger.debug(f"Trimming single segment: {seg[6]} (offset={trim_start}s, dur={trim_duration}s, seg_dur={seg_duration:.1f}s)")
            await asyncio.to_thread(
                self._trim_single, seg[6], output_file, trim_start, trim_duration
            )
        else:
            # Multiple segments — concat then trim
            first_seg_start = valid_segments[0][4]
            last_seg_end = valid_segments[-1][5] if valid_segments[-1][5] is not None else end_ts
            total_duration = last_seg_end - first_seg_start
            trim_start = round(max(0.0, start_ts - first_seg_start), 3)
            # Clamp offset so we don't trim past the concatenated audio
            trim_start = round(min(trim_start, max(0.0, total_duration - 1)), 3)
            trim_duration = duration_minutes * 60

            file_list = [seg[6] for seg in valid_segments]
            logger.debug(
                f"Concatenating {len(file_list)} segments "
                f"(offset={trim_start}s, dur={trim_duration}s, total_audio={total_duration:.1f}s)"
            )
            await asyncio.to_thread(
                self._concat_and_trim, file_list, output_file, trim_start, trim_duration
            )

        # An OGG header with no audio is ~137 bytes — reject anything under 1KB
        MIN_CLIP_SIZE = 1024
        if os.path.exists(output_file):
            out_size = os.path.getsize(output_file)
            if out_size >= MIN_CLIP_SIZE:
                logger.info(f"Clip created: {output_file} ({out_size} bytes)")
                return output_file
            else:
                logger.warning(
                    f"Clip too small ({out_size} bytes), likely empty audio — "
                    f"trim may have overshot the actual content"
                )
                try:
                    os.remove(output_file)
                except OSError:
                    pass
                return None
        logger.warning(f"Clip output file missing: {output_file}")
        return None

    @staticmethod
    def _trim_single(input_path: str, output_path: str, start_sec: float, duration_sec: float):
        """Trim a single OGG or PCM file."""
        if input_path.endswith(".pcm"):
            cmd = [
                "ffmpeg", "-y",
                "-f", "s16le", "-ar", "48000", "-ac", "2",
                "-i", input_path,
                "-ss", str(start_sec),
                "-t", str(duration_sec),
                "-c:a", "libopus", "-b:a", "48k", "-ac", "1",
                output_path,
            ]
        else:
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
        temp_oggs = []
        try:
            # Normalise all inputs to OGG — filter_complex concat needs uniform codecs
            normalized = []
            for f in file_list:
                if f.endswith(".pcm"):
                    tmp_ogg = os.path.splitext(f)[0] + "_tmp.ogg"
                    cmd = [
                        "ffmpeg", "-y",
                        "-f", "s16le", "-ar", "48000", "-ac", "2",
                        "-i", f,
                        "-c:a", "libopus", "-b:a", "48k", "-ac", "1",
                        tmp_ogg,
                    ]
                    result = subprocess.run(cmd, capture_output=True, timeout=120)
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"ffmpeg pcm→ogg failed: {result.stderr.decode(errors='replace')}"
                        )
                    temp_oggs.append(tmp_ogg)
                    normalized.append(tmp_ogg)
                else:
                    normalized.append(f)

            # Build filter_complex concat — no temp text file needed
            n = len(normalized)
            cmd = ["ffmpeg", "-y"]
            for f in normalized:
                cmd.extend(["-i", f])
            cmd.extend([
                "-filter_complex", f"concat=n={n}:v=0:a=1[out]",
                "-map", "[out]",
                "-ss", str(start_sec),
                "-t", str(duration_sec),
                "-c:a", "libopus",
                "-b:a", "48k",
                output_path,
            ])
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg concat failed: {result.stderr.decode(errors='replace')}"
                )
        finally:
            for tmp_ogg in temp_oggs:
                try:
                    os.remove(tmp_ogg)
                except OSError:
                    pass
