from pathlib import Path

from app.selector import Clip
from app.transcribe import Word

# Output sizes per aspect ratio; the source is center-cropped to the ratio, then scaled.
ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}
DEFAULT_ASPECT = "9:16"

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,1,2,80,80,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""  # noqa: E501


def size_for(aspect: str) -> tuple[int, int]:
    return ASPECTS.get(aspect, ASPECTS[DEFAULT_ASPECT])


def ass_header(aspect: str) -> str:
    w, h = size_for(aspect)
    # Font about 4% of the height, caption sitting in the lower fifth.
    return ASS_HEADER.format(w=w, h=h, size=round(h * 0.04), margin=round(h * 0.18))


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(
    words: list[Word],
    clip_start: float,
    clip_end: float,
    words_per_cue: int = 4,
    aspect: str = DEFAULT_ASPECT,
) -> str:
    inside = [w for w in words if w.start >= clip_start and w.end <= clip_end and w.text]
    lines = [ass_header(aspect)]
    for i in range(0, len(inside), words_per_cue):
        group = inside[i : i + words_per_cue]
        start = ass_time(group[0].start - clip_start)
        end = ass_time(group[-1].end - clip_start)
        text = " ".join(w.text for w in group).replace("\n", " ")
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def video_filter(aspect: str) -> str:
    """Center-crop to the ratio (whatever the source shape) and scale to the output size."""
    w, h = size_for(aspect)
    return f"crop=w='min(iw,ih*{w}/{h})':h='min(ih,iw*{h}/{w})',scale={w}:{h}"


def render_args(
    video: Path, out: Path, clip: Clip, ass: Path | None, aspect: str = DEFAULT_ASPECT
) -> list[str]:
    vf = video_filter(aspect)
    if ass is not None:
        vf += f",subtitles={_escape_filter_path(ass)}"
    return [
        "ffmpeg", "-y",
        "-ss", f"{clip.start:.3f}", "-to", f"{clip.end:.3f}", "-i", str(video),
        "-vf", vf,
        # crf quality with a 5 Mbps ceiling: a 60 s short stays under ~40 MB, well inside
        # storage's 50 MB per-object cap even for high-motion footage.
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2",
        "-maxrate", "5M", "-bufsize", "10M",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]  # fmt: skip
