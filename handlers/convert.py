import asyncio
import logging
import shutil
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import MAX_FILE_MB, TMP_DIR
from db import db
from keyboards import conversion_options
from services.converter import ACTIONS, AUDIO, PDF, PHOTO, VIDEO, VIDEO_NOTE, VOICE

logger = logging.getLogger(__name__)
router = Router(name="convert")


@dataclass
class Pending:
    user_id: int
    kind: str
    file_id: str


_cache: OrderedDict[str, Pending] = OrderedDict()
_CACHE_MAX = 500


def _cache_put(key: str, pending: Pending) -> None:
    _cache[key] = pending
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


async def _register(message: Message, kind: str, file_id: str, size: int | None) -> None:
    await db.ensure_user(message.from_user.id, message.from_user.username)
    if size and size > MAX_FILE_MB * 1024 * 1024:
        await message.answer(
            f"😔 Файл больше {MAX_FILE_MB} МБ — это ограничение Telegram для ботов.\n"
            "Попробуйте сжать файл перед отправкой."
        )
        return
    key = uuid.uuid4().hex[:8]
    _cache_put(key, Pending(user_id=message.from_user.id, kind=kind, file_id=file_id))
    await message.answer(
        "Что сделать с файлом? 👇",
        reply_markup=conversion_options(kind, key),
    )


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    photo = message.photo[-1]
    await _register(message, PHOTO, photo.file_id, photo.file_size)


@router.message(F.video)
async def on_video(message: Message) -> None:
    await _register(message, VIDEO, message.video.file_id, message.video.file_size)


@router.message(F.video_note)
async def on_video_note(message: Message) -> None:
    vn = message.video_note
    await _register(message, VIDEO_NOTE, vn.file_id, vn.file_size)


@router.message(F.voice)
async def on_voice(message: Message) -> None:
    await _register(message, VOICE, message.voice.file_id, message.voice.file_size)


@router.message(F.audio)
async def on_audio(message: Message) -> None:
    await _register(message, AUDIO, message.audio.file_id, message.audio.file_size)


@router.message(F.document)
async def on_document(message: Message) -> None:
    doc = message.document
    mime = doc.mime_type or ""
    if mime.startswith("image/"):
        kind = PHOTO
    elif mime.startswith("video/"):
        kind = VIDEO
    elif mime.startswith("audio/"):
        kind = AUDIO
    elif mime == "application/pdf":
        kind = PDF
    else:
        await message.answer(
            "🙈 Такой формат пока не умею.\n"
            "Поддерживаю: видео, аудио, голосовые, фото и PDF."
        )
        return
    await _register(message, kind, doc.file_id, doc.file_size)


@router.callback_query(F.data.startswith("x:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    _cache.pop(key, None)
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("c:"))
async def cb_convert(callback: CallbackQuery, bot: Bot) -> None:
    _, action_code, key = callback.data.split(":", 2)
    pending = _cache.get(key)

    if pending is None:
        await callback.answer("Файл устарел — отправьте его заново 🔄", show_alert=True)
        return
    if callback.from_user.id != pending.user_id:
        await callback.answer("Это не ваш файл 🙂", show_alert=True)
        return

    spec = ACTIONS.get(action_code)
    if spec is None or pending.kind not in spec.kinds:
        await callback.answer("Действие недоступно", show_alert=True)
        return

    await callback.answer()

    if not shutil.which("ffmpeg") and spec.send in ("voice", "audio", "animation"):
        await callback.message.edit_text(
            "⚙️ Сервер временно не может обработать медиа. Попробуйте позже."
        )
        return

    status = await callback.message.edit_text("⏳ Конвертирую…")

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    src = TMP_DIR / token
    out_path = None

    try:
        await bot.download(pending.file_id, destination=str(src))
        out_path = await asyncio.wait_for(spec.run(src), timeout=240)
        await db.consume(pending.user_id)
        file_to_send = FSInputFile(out_path)
        senders = {
            "voice": status.answer_voice,
            "audio": status.answer_audio,
            "photo": status.answer_photo,
            "animation": status.answer_animation,
            "document": status.answer_document,
        }
        sender = senders[spec.send]
        kwargs = {"caption": "✅ Готово!"} if spec.send != "voice" else {}
        await sender(file_to_send, **kwargs)
        _cache.pop(key, None)
        try:
            await status.delete()
        except Exception:
            pass
    except Exception:
        logger.exception("conversion failed")
        try:
            await status.edit_text(
                "❌ Не удалось конвертировать этот файл. Попробуйте другой формат."
            )
        except Exception:
            pass
    finally:
        src.unlink(missing_ok=True)
        if out_path:
            out_path.unlink(missing_ok=True)
