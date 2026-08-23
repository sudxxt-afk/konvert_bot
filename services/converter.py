import asyncio
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pypdf import PdfReader


async def _ffmpeg(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='ignore')[:500]}")


def _out(src: Path, suffix: str) -> Path:
    return src.with_name(src.stem + suffix)


async def _to_voice(src: Path) -> Path:
    out = _out(src, ".ogg")
    await _ffmpeg(
        "-i", str(src), "-vn", "-ac", "1", "-ar", "32000",
        "-c:a", "libopus", "-b:a", "32k", str(out),
    )
    return out


async def _to_audio(src: Path, suffix: str, *codec_args: str) -> Path:
    out = _out(src, suffix)
    await _ffmpeg("-i", str(src), "-vn", *codec_args, str(out))
    return out


async def _to_gif(src: Path) -> Path:
    out = src.with_suffix(".gif")
    await _ffmpeg(
        "-i", str(src), "-t", "15",
        "-vf", "fps=12,scale=480:-2:flags=lanczos",
        str(out),
    )
    return out


def _convert_image(src: Path, fmt: str, max_side: int | None = None, quality: int | None = None) -> Path:
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[fmt]
    out = _out(src, ext)
    with Image.open(src) as im:
        im.load()
        if fmt in ("JPEG", "WEBP") and im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if max_side and max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        kwargs: dict = {}
        if fmt in ("JPEG", "WEBP") and quality:
            kwargs["quality"] = quality
        if fmt == "WEBP":
            kwargs.setdefault("method", 5)
        im.save(out, fmt, **kwargs)
    return out


def _pdf_to_txt(src: Path) -> Path:
    out = _out(src, ".txt")
    reader = PdfReader(str(src))
    text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    out.write_text(text, encoding="utf-8")
    return out


@dataclass(frozen=True)
class ActionSpec:
    code: str
    label: str
    kinds: frozenset
    send: str
    run: object


PHOTO = "photo"
VIDEO = "video"
VIDEO_NOTE = "video_note"
AUDIO = "audio"
VOICE = "voice"
PDF = "pdf"

ACTIONS: dict[str, ActionSpec] = {
    spec.code: spec
    for spec in (
        ActionSpec("voice", "🎤 В голосовое", frozenset({VIDEO, VIDEO_NOTE, AUDIO, VOICE}), "voice", lambda s: _to_voice(s)),
        ActionSpec("mp3", "🎵 В MP3", frozenset({VIDEO, VIDEO_NOTE, AUDIO, VOICE}), "audio", lambda s: _to_audio(s, ".mp3", "-c:a", "libmp3lame", "-q:a", "4")),
        ActionSpec("wav", "🎼 В WAV", frozenset({AUDIO, VOICE}), "audio", lambda s: _to_audio(s, ".wav", "-c:a", "pcm_s16le")),
        ActionSpec("m4a", "🎧 В M4A", frozenset({AUDIO, VOICE}), "audio", lambda s: _to_audio(s, ".m4a", "-c:a", "aac", "-b:a", "128k")),
        ActionSpec("gif", "🎞 Видео в GIF (до 15 сек)", frozenset({VIDEO}), "animation", lambda s: _to_gif(s)),
        ActionSpec("jpg", "🖼 В JPEG", frozenset({PHOTO}), "photo", lambda s: asyncio.to_thread(_convert_image, s, "JPEG", quality=90)),
        ActionSpec("png", "🎨 В PNG", frozenset({PHOTO}), "photo", lambda s: asyncio.to_thread(_convert_image, s, "PNG")),
        ActionSpec("webp", "🌐 В WebP", frozenset({PHOTO}), "document", lambda s: asyncio.to_thread(_convert_image, s, "WEBP", quality=88)),
        ActionSpec("compress", "📦 Сжать фото", frozenset({PHOTO}), "document", lambda s: asyncio.to_thread(_convert_image, s, "JPEG", max_side=1600, quality=60)),
        ActionSpec("txt", "📄 PDF → Текст", frozenset({PDF}), "document", lambda s: asyncio.to_thread(_pdf_to_txt, s)),
    )
}


def options_for(kind: str) -> list[ActionSpec]:
    return [spec for spec in ACTIONS.values() if kind in spec.kinds]
