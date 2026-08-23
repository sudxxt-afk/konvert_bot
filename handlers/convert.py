import asyncio
import logging
import shutil
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, Message, ReactionTypeEmoji

from config import MAX_FILE_MB, TMP_DIR
from db import db
from keyboards import conversion_options
from services.converter import (
    ACTIONS,
    AUDIO,
    CSV,
    DOCX,
    PDF,
    PHOTO,
    SUB,
    TEXT,
    VIDEO,
    VIDEO_NOTE,
    VOICE,
    XLSX,
)

logger = logging.getLogger(__name__)
router = Router(name="convert")

SUCCESS_EFFECT = "5104841245755180586"

CHAT_ACTIONS = {
    "voice": ChatAction.UPLOAD_VOICE,
    "audio": ChatAction.UPLOAD_VOICE,
    "photo": ChatAction.UPLOAD_PHOTO,
    "animation": ChatAction.UPLOAD_DOCUMENT,
    "document": ChatAction.UPLOAD_DOCUMENT,
}

_DOC_KINDS = {
    ".pdf": PDF,
    ".docx": DOCX,
    ".xlsx": XLSX,
    ".xlsm": XLSX,
    ".csv": CSV,
    ".txt": TEXT,
    ".srt": SUB,
    ".vtt": SUB,
}

KIND_TITLES = {
    VIDEO: "🎬 Видео",
    VIDEO_NOTE: "⭕️ Кружочек",
    AUDIO: "🎵 Аудио",
    VOICE: "🎤 Голосовое",
    PHOTO: "🖼 Фото",
    PDF: "📄 PDF-документ",
    DOCX: "📝 DOCX-документ",
    XLSX: "📊 Таблица Excel",
    CSV: "📊 CSV-таблица",
    TEXT: "📃 Текстовый файл",
    SUB: "💬 Субтитры",
}


def _ext_from_mime(mime: str | None) -> str | None:
    if not mime or "/" not in mime:
        return None
    sub = mime.split("/", 1)[1]
    return sub.upper() if len(sub) <= 5 else None

_UNSUPPORTED_TEXT = (
    "🙈 <b>Такой формат пока не поддерживаю</b>\n\n"
    "<blockquote>🎬 Видео · 🎵 Аудио · 🖼 Фото\n"
    "📄 PDF · 📝 DOCX · 📊 XLSX/CSV\n"
    "💬 Субтитры SRT/VTT · 📃 TXT</blockquote>\n\n"
    "Или отправь <code>/qr текст</code> — сделаю QR-код."
)


@dataclass
class Pending:
    user_id: int
    kind: str
    file_id: str
    message_id: int


_cache: OrderedDict[str, Pending] = OrderedDict()
_CACHE_MAX = 500


def _cache_put(key: str, pending: Pending) -> None:
    _cache[key] = pending
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def _fmt_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{int(value)} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def _build_caption(out_path, src_size: int) -> str:
    out_size = out_path.stat().st_size
    lines = ["✅ <b>Готово!</b>"]
    if out_path.suffix:
        lines[0] += f" <code>{out_path.suffix.lstrip('.').upper()}</code>"
    size_line = f"📦 {_fmt_size(src_size)} → {_fmt_size(out_size)}"
    if src_size > 0 and out_size < src_size:
        saved = round((1 - out_size / src_size) * 100)
        if saved > 0:
            size_line += f" (<b>−{saved}%</b>)"
    lines.append(size_line)
    return "\n".join(lines)


async def _register(message: Message, kind: str, file_id: str, size: int | None, ext: str | None = None) -> None:
    await db.ensure_user(message.from_user.id, message.from_user.username)
    if size and size > MAX_FILE_MB * 1024 * 1024:
        await message.answer(
            "😔 <b>Файл слишком большой</b>\n"
            f"Лимит Telegram для ботов — {MAX_FILE_MB} МБ.\n"
            "Сожми файл и попробуй снова 🙏"
        )
        return
    key = uuid.uuid4().hex[:8]
    _cache_put(
        key,
        Pending(
            user_id=message.from_user.id,
            kind=kind,
            file_id=file_id,
            message_id=message.message_id,
        ),
    )
    title = KIND_TITLES.get(kind, "📦 Файл")
    meta = []
    if ext:
        meta.append(f"<code>{ext}</code>")
    if size:
        meta.append(_fmt_size(size))
    card = f"{title}" + (" · ".join([""] + meta) if meta else "")
    await message.answer(
        f"<b>{card}</b>\n\nЧто с ним сделать? 👇",
        reply_markup=conversion_options(kind, key),
    )


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    photo = message.photo[-1]
    await _register(message, PHOTO, photo.file_id, photo.file_size)


@router.message(F.video)
async def on_video(message: Message) -> None:
    await _register(
        message, VIDEO, message.video.file_id, message.video.file_size,
        ext=_ext_from_mime(message.video.mime_type),
    )


@router.message(F.video_note)
async def on_video_note(message: Message) -> None:
    vn = message.video_note
    await _register(message, VIDEO_NOTE, vn.file_id, vn.file_size, ext="MP4")


@router.message(F.voice)
async def on_voice(message: Message) -> None:
    await _register(message, VOICE, message.voice.file_id, message.voice.file_size, ext="OGG")


@router.message(F.audio)
async def on_audio(message: Message) -> None:
    audio = message.audio
    name_ext = audio.file_name.rsplit(".", 1)[-1].upper() if audio.file_name and "." in audio.file_name else None
    await _register(
        message, AUDIO, audio.file_id, audio.file_size,
        ext=name_ext or _ext_from_mime(audio.mime_type),
    )


@router.message(F.document)
async def on_document(message: Message) -> None:
    doc = message.document
    name = doc.file_name or ""
    ext = name.rsplit(".", 1)[-1].upper() if "." in name else None

    kind = _DOC_KINDS.get("." + name.rsplit(".", 1)[-1].lower() if "." in name else "")
    if kind is None:
        mime = doc.mime_type or ""
        if mime.startswith("image/"):
            kind = PHOTO
        elif mime.startswith("video/"):
            kind = VIDEO
        elif mime.startswith("audio/"):
            kind = AUDIO
        elif mime == "application/pdf":
            kind = PDF
        elif mime == "application/zip":
            await message.answer(
                "🗜 <b>Архивы пока не умею</b>\nПришли файлы по одному 🙂"
            )
            return
        else:
            await message.answer(_UNSUPPORTED_TEXT)
            return

    await _register(message, kind, doc.file_id, doc.file_size, ext=ext)


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("x:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    _cache.pop(key, None)
    await callback.answer("Отменено")
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("c:"))
async def cb_convert(callback: CallbackQuery, bot: Bot) -> None:
    _, action_code, key = callback.data.split(":", 2)
    pending = _cache.get(key)

    if pending is None:
        await callback.answer("Файл устарел — отправь его заново 🔄", show_alert=True)
        return
    if callback.from_user.id != pending.user_id:
        await callback.answer("Это не твой файл 🙂", show_alert=True)
        return

    spec = ACTIONS.get(action_code)
    if spec is None or pending.kind not in spec.kinds:
        await callback.answer("Действие недоступно", show_alert=True)
        return

    await callback.answer()

    if not shutil.which("ffmpeg") and spec.send in ("voice", "audio", "animation"):
        await callback.message.edit_text(
            "⚙️ Сервер временно не может обработать медиа. Попробуй позже."
        )
        return

    ctx = KIND_TITLES.get(pending.kind, "📦 Файл")
    status = await callback.message.edit_text(f"📥 <b>Скачиваю…</b>\n{ctx}")

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    src = TMP_DIR / token
    out_path = None

    try:
        await bot.download(pending.file_id, destination=str(src))
        src_size = src.stat().st_size
        await status.edit_text(f"⚙️ <b>Обрабатываю…</b>\n{ctx} → {spec.label}")
        result = await asyncio.wait_for(spec.run(src), timeout=300)

        await db.consume(pending.user_id)
        try:
            await bot.set_message_reaction(
                callback.message.chat.id,
                pending.message_id,
                reaction=[ReactionTypeEmoji(emoji="🔥")],
            )
        except Exception:
            pass

        if spec.send == "text":
            preview = result[:3500]
            await status.edit_text(
                f"{preview}\n\n<i>Что-нибудь ещё?</i>",
                reply_markup=conversion_options(pending.kind, key),
            )
            return

        out_path = result
        caption = _build_caption(out_path, src_size)
        await status.edit_text(f"📤 <b>Отправляю…</b>\n{spec.label}")
        await bot.send_chat_action(callback.message.chat.id, CHAT_ACTIONS[spec.send])
        await _send_result(status, spec.send, out_path, caption)
        try:
            await status.edit_text(
                "🔁 <b>Ещё что-нибудь с этим файлом?</b>",
                reply_markup=conversion_options(pending.kind, key),
            )
        except TelegramBadRequest:
            pass
    except Exception:
        logger.exception("conversion failed")
        try:
            await status.edit_text(
                "💔 <b>Не получилось</b>\n"
                "Этот файл не поддался. Попробуй другой формат или файл."
            )
        except Exception:
            pass
    finally:
        src.unlink(missing_ok=True)
        if out_path:
            out_path.unlink(missing_ok=True)


async def _send_result(status: Message, send_type: str, path, caption: str) -> None:
    senders = {
        "voice": status.answer_voice,
        "audio": status.answer_audio,
        "photo": status.answer_photo,
        "animation": status.answer_animation,
        "document": status.answer_document,
    }
    sender = senders[send_type]
    kwargs = {"caption": caption} if send_type != "voice" else {}
    try:
        await sender(FSInputFile(path), **kwargs, message_effect_id=SUCCESS_EFFECT)
    except TelegramBadRequest as e:
        if "MESSAGE_EFFECT" in str(e) or "effect" in str(e).lower():
            await sender(FSInputFile(path), **kwargs)
        else:
            raise
