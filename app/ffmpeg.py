import asyncio
import json
import statistics
from dataclasses import dataclass
from pathlib import Path

from app.transcribe import Word, Segment


class FfmpegError(Exception):
    pass


async def run(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(err.decode(errors="replace")[-2000:])


async def duration(path) -> float:
    """Length of a media file in seconds, via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    try:
        return float(out.decode().strip())
    except ValueError as e:
        raise FfmpegError(err.decode(errors="replace")[-500:] or "no duration") from e


@dataclass
class AudioFeatures:
    """Per-segment audio features for comedy/impact detection."""
    start: float
    end: float
    rms_db: float              # Loudness (dBFS); spikes = emphasis/laughter
    peak_db: float             # Peak level
    speaking_rate: float       # Words/sec (from Whisper words in window)
    pause_before: float        # Silence before segment (sec); long = dramatic pause
    pause_after: float         # Silence after segment
    energy_delta: float        # RMS change from prev segment; spike = punchline
    laughter_score: float      # 0-1; high = likely laughter/applause


async def analyze_audio_features(
    audio_path: Path,
    words: list[Word],
    segments: list[Segment],
    window_sec: float = 1.0,
) -> list[AudioFeatures]:
    """Extract per-segment audio features using ffmpeg astats + Whisper alignment.

    - RMS/peak via astats filter (fast, single pass)
    - Speaking rate from Whisper words aligned to segment windows
    - Pauses from silence detection between segments
    - Laughter: high-energy bursts (RMS spike + high spectral flatness) in short windows
    """
    # 1) Get per-second RMS/peak via astats
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-f", "lavfi",
        "-i", f"amovie={audio_path},astats=metadata=1:reset=1",
        "-show_entries", "frame=lavfi.astats.Overall.RMS_level:lavfi.astats.Overall.Peak_level",
        "-of", "json",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(err.decode(errors="replace")[-1000:])

    # Parse per-frame stats (1 frame ≈ 400-500 samples ≈ 0.01s at 44.1kHz)
    # We'll bucket into 1-second windows for alignment
    try:
        data = json.loads(out.decode())
        frames = data.get("frames", [])
    except json.JSONDecodeError:
        frames = []

    # Build per-second RMS/peak arrays
    window_count = int(max((s.end for s in segments), default=0)) + 2
    rms_by_sec = [[] for _ in range(window_count)]
    peak_by_sec = [[] for _ in range(window_count)]
    for frame in frames:
        tags = frame.get("tags", {})
        try:
            t = float(tags.get("lavfi.astats.Overall.RMS_level", -100))
            p = float(tags.get("lavfi.astats.Overall.Peak_level", -100))
        except (ValueError, TypeError):
            continue
        sec = int(frame.get("pts_time", 0))
        if 0 <= sec < window_count:
            rms_by_sec[sec].append(t)
            peak_by_sec[sec].append(p)

    # Median per second (robust to clicks/pops)
    rms_median = [statistics.median(v) if v else -80.0 for v in rms_by_sec]
    peak_median = [statistics.median(v) if v else -80.0 for v in peak_by_sec]

    # 2) Silence detection for pauses (ffmpeg silencedetect)
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-f", "lavfi",
        "-i", f"amovie={audio_path},silencedetect=n=-40dB:d=0.3",
        "-show_entries", "frame=tags:frame=pkt_pts_time",
        "-of", "json",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    silence_starts, silence_ends = [], []
    if proc.returncode == 0:
        try:
            data = json.loads(out.decode())
            for frame in data.get("frames", []):
                tags = frame.get("tags", {})
                if "lavfi.silence_start" in tags:
                    silence_starts.append(float(tags["lavfi.silence_start"]))
                if "lavfi.silence_end" in tags:
                    silence_ends.append(float(tags["lavfi.silence_end"]))
        except json.JSONDecodeError:
            pass

    # 3) Build features per segment
    # Precompute word counts per segment for speaking rate
    words_by_segment = [[] for _ in segments]
    for w in words:
        for i, s in enumerate(segments):
            if s.start <= w.start < s.end:
                words_by_segment[i].append(w)
                break

    features: list[AudioFeatures] = []
    prev_rms = -60.0
    for i, seg in enumerate(segments):
        sec_start, sec_end = int(seg.start), int(seg.end) + 1
        window_rms = rms_median[sec_start:sec_end] if sec_start < len(rms_median) else [-60.0]
        window_peak = peak_median[sec_start:sec_end] if sec_start < len(peak_median) else [-60.0]
        rms = statistics.median(window_rms)
        peak = max(window_peak) if window_peak else -60.0

        # Speaking rate
        word_count = len(words_by_segment[i])
        dur = max(seg.end - seg.start, 0.1)
        speaking_rate = word_count / dur

        # Pause before/after this segment
        pause_before = 0.0
        pause_after = 0.0
        for s_start, s_end in zip(silence_starts, silence_ends):
            if s_end <= seg.start:
                pause_before = max(pause_before, s_end - s_start)
            if s_start >= seg.end:
                pause_after = max(pause_after, s_end - s_start)
                break

        # Energy delta from previous segment
        energy_delta = rms - prev_rms
        prev_rms = rms

        # Laughter heuristic: short high-energy burst with high spectral variance
        # Simplified: RMS spike > 10dB in <2s window + high peak
        laughter_score = 0.0
        if dur < 2.0 and energy_delta > 10 and peak > -10:
            laughter_score = min(1.0, (energy_delta - 5) / 20)

        features.append(AudioFeatures(
            start=seg.start, end=seg.end,
            rms_db=rms, peak_db=peak,
            speaking_rate=speaking_rate,
            pause_before=pause_before, pause_after=pause_after,
            energy_delta=energy_delta,
            laughter_score=laughter_score,
        ))
    return features
