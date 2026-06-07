"""
Clip retriever — finds segments covering a time window,
stitches them with ffmpeg, and trims to the exact requested range.
"""

import asyncio
import logging
import os
import subprocess
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
        anchor_seg: tuple = None,
        offset_sec: float = 0.0,
    ) -> str | None:
        """
        Retrieve an audio clip for a user starting at start_time for duration_minutes.

        If *anchor_seg* is provided (a DB segment tuple), the DB query starts
        from that segment and extends forward only.  *offset_sec* is applied
        as an FFmpeg trim within the resulting audio (skip the first N seconds).

        Returns the path to the output OGG file, or None if no audio found.
        """
        # When we have an anchor segment, query FORWARD from its start —
        # never look backward at earlier segments.
        if anchor_seg is not None:
            start_ts = anchor_seg[4]  # anchor segment's start is our start
            # Extend the query window to cover offset + duration
            end_ts = start_ts + offset_sec + (duration_minutes * 60)
        else:
            start_ts = start_time.timestamp()
            end_ts = start_ts + (duration_minutes * 60)

        logger.info(
            f"Retrieving clip: user={user_id} start_ts={start_ts:.0f} "
            f"duration={duration_minutes}m guild={guild_id}"
            + (f" anchor_seg={anchor_seg[0]}" if anchor_seg else "")
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

        # With an anchor, drop anything that started before the anchor —
        # the DB overlap query can still pull in an earlier segment whose
        # end_ts crosses into our window.
        if anchor_seg is not None:
            anchor_start = anchor_seg[4]
            before = len(segments)
            segments = [s for s in segments if s[4] >= anchor_start]
            if before != len(segments):
                logger.debug(
                    f"Anchor filter: dropped {before - len(segments)} segment(s) "
                    f"that started before anchor ts={anchor_start:.0f}"
                )

        logger.debug(f"Found {len(segments)} DB segment(s) for clip retrieval")

        # Filter to segments that actually have files on disk.
        # Fall back to the sibling .pcm file if the .ogg hasn't been remuxed yet.
        valid_segments = []
        for seg in segments:
            file_path = seg[6]
            if os.path.exists(file_path):
                valid_segments.append(seg)
            else:
                pcm_path = os.path.splitext(file_path)[0] + ".pcm"
                if os.path.exists(pcm_path):
                    valid_segments.append(seg[:6] + (pcm_path,) + seg[7:])
                    logger.debug(f"Using PCM fallback for segment {seg[0]}: {pcm_path}")
                else:
                    logger.warning(f"Segment {seg[0]} file missing: {file_path} (and no PCM fallback)")

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

        # Calculate trim offset:
        #  - With anchor: offset_sec is the user's requested skip (already in seconds)
        #  - Without anchor: compute from timestamp difference
        if len(valid_segments) == 1:
            seg = valid_segments[0]
            seg_start_ts = seg[4]
            seg_end_ts = seg[5] if seg[5] is not None else end_ts
            seg_duration = seg_end_ts - seg_start_ts

            if anchor_seg is not None:
                trim_start = round(offset_sec, 3)
            else:
                trim_start = round(max(0.0, start_ts - seg_start_ts), 3)

            if trim_start >= seg_duration:
                logger.warning(
                    f"Trim offset {trim_start:.1f}s >= segment duration {seg_duration:.1f}s — "
                    f"resetting to 0"
                )
                trim_start = 0.0
            trim_duration = duration_minutes * 60

            logger.debug(
                f"Trimming single segment: {seg[6]} "
                f"(offset={trim_start}s, dur={trim_duration}s, seg_dur={seg_duration:.1f}s)"
            )
            await asyncio.to_thread(
                self._trim_single, seg[6], output_file, trim_start, trim_duration
            )
        else:
            first_seg_start = valid_segments[0][4]
            last_seg_end = valid_segments[-1][5] if valid_segments[-1][5] is not None else end_ts
            total_duration = last_seg_end - first_seg_start

            if anchor_seg is not None:
                trim_start = round(offset_sec, 3)
            else:
                trim_start = round(max(0.0, start_ts - first_seg_start), 3)

            if trim_start >= total_duration:
                logger.warning(
                    f"Trim offset {trim_start:.1f}s >= total audio {total_duration:.1f}s — "
                    f"resetting to 0"
                )
                trim_start = 0.0
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

    # Low-level comfort noise mixed into every clip so VAD-induced silence
    # regions have a natural noise floor rather than a hard gate to zero.
    _COMFORT_NOISE = "anoisesrc=r=48000:amplitude=0.001,aformat=channel_layouts=mono"
    _NOISE_MIX = "amix=inputs=2:weights=1 1:normalize=0:duration=shortest"
    _COMPAND = "compand=attacks=0.02:decays=0.3:points=-80/-80|-55/-45|-30/-20|0/0:soft-knee=3"

    @staticmethod
    def _trim_single(input_path: str, output_path: str, start_sec: float, duration_sec: float):
        """Trim a single OGG or PCM file with adeclick and comfort noise."""
        _cn = ClipRetriever._COMFORT_NOISE
        _nm = ClipRetriever._NOISE_MIX
        _cp = ClipRetriever._COMPAND
        if input_path.endswith(".pcm"):
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "s16le", "-ar", "48000", "-ac", "2",
                "-i", input_path,
                "-filter_complex",
                f"[0:a]adeclick,aformat=channel_layouts=mono[clean];{_cn}[noise];[clean][noise]{_nm}[mixed];[mixed]{_cp}[out]",
                "-map", "[out]",
                "-ss", str(start_sec),
                "-t", str(duration_sec),
                "-c:a", "libopus", "-b:a", "128k", "-application", "audio",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", input_path,
                "-filter_complex",
                f"[0:a]adeclick[clean];{_cn}[noise];[clean][noise]{_nm}[mixed];[mixed]{_cp}[out]",
                "-map", "[out]",
                "-ss", str(start_sec),
                "-t", str(duration_sec),
                "-c:a", "libopus", "-b:a", "128k", "-application", "audio",
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
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-f", "s16le", "-ar", "48000", "-ac", "2",
                        "-i", f,
                        "-c:a", "libopus", "-b:a", "128k", "-ac", "1",
                        "-application", "audio",
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

            # Build filter_complex with acrossfade chained between each segment.
            # Hard concat leaves a brief encoder pre-roll glitch at every boundary;
            # a short crossfade (20 ms) covers it without being audible as an effect.
            # adeclick is applied after the final crossfade (can't mix -af with
            # -filter_complex in FFmpeg); on clean audio it is effectively a no-op.
            n = len(normalized)
            cmd = ["ffmpeg", "-y", "-loglevel", "error"]
            for f in normalized:
                cmd.extend(["-i", f])

            _xf = "acrossfade=d=0.02:c1=tri:c2=tri"
            _parts = []
            _prev = "[0:a]"
            for _i in range(1, n):
                _label = f"[x{_i}]"
                _parts.append(f"{_prev}[{_i}:a]{_xf}{_label}")
                _prev = _label
            _cn = ClipRetriever._COMFORT_NOISE
            _nm = ClipRetriever._NOISE_MIX
            _cp = ClipRetriever._COMPAND
            _parts.append(f"{_prev}adeclick[clean];{_cn}[noise];[clean][noise]{_nm}[mixed];[mixed]{_cp}[out]")
            filter_graph = ";".join(_parts)

            cmd.extend([
                "-filter_complex", filter_graph,
                "-map", "[out]",
                "-ss", str(start_sec),
                "-t", str(duration_sec),
                "-c:a", "libopus",
                "-b:a", "128k",
                "-application", "audio",
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
