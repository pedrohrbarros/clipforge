from pathlib import Path

from app.selector import Clip
from app.transcribe import Word

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,1,2,80,80,350,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""  # noqa: E501


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(words: list[Word], clip_start: float, clip_end: float, words_per_cue: int = 4) -> str:
    inside = [w for w in words if w.start >= clip_start and w.end <= clip_end and w.text]
    lines = [ASS_HEADER]
    for i in range(0, len(inside), words_per_cue):
        group = inside[i : i + words_per_cue]
        start = ass_time(group[0].start - clip_start)
        end = ass_time(group[-1].end - clip_start)
        text = " ".join(w.text for w in group).replace("\n", " ")
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_args(video: Path, out: Path, clip: Clip, ass: Path | None) -> list[str]:
    vf = "crop=ih*9/16:ih,scale=1080:1920"
    if ass is not None:
        vf += f",subtitles={_escape_filter_path(ass)}"
    return [
        "ffmpeg", "-y",
        "-ss", f"{clip.start:.3f}", "-to", f"{clip.end:.3f}", "-i", str(video),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]  # fmt: skip
