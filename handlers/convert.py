import asyncio
import logging
import shutil
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    Message,
    ReactionTypeEmoji,
)

from config import MAX_FILE_MB, MAX_PARALLEL, TMP_DIR
from db import db
from keyboards import conversion_options
from services.queue import get_queue
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
ALBUM_IDLE = 1.5

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
    ".heic": PHOTO,
    ".heif": PHOTO,
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

_UNSUPPORTED_TEXT = (
    "🙈 <b>Такой формат пока не поддерживаю</b>\n\n"
    "<blockquote>🎬 Видео · 🎵 Аудио · 🖼 Фото (вкл. HEIC)\n"
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


@dataclass
class Album:
    user_id: int
    chat_id: int
    files: list = field(default_factory=list)


_cache: OrderedDict[str, Pending] = OrderedDict()
_CACHE_MAX = 500

_albums: dict[str, Album] = {}
_album_timers: dict[str, asyncio.Task] = {}


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


async def _flush_album(gid: str, message: Message) -> None:
    album = _albums.pop(gid, None)
    _album_timers.pop(gid, None)
    if not album or not album.files:
        return
    await db.ensure_user(album.user_id, message.from_user.username)
    key = uuid.uuid4().hex[:8]
    total = sum(s or 0 for _, s in album.files)
    pending = Pending(
        user_id=album.user_id,
        kind=PHOTO,
        file_id=album.files[0][0],
        message_id=message.message_id,
    )
    pending.__dict__["file_ids"] = [fid for fid, _ in album.files]
    pending.__dict__["sizes"] = [s for _, s in album.files]
    _cache_put(key, pending)
    await message.answer(
        f"📸 <b>Альбом · {len(album.files)} фото</b>"
        + (f" · {_fmt_size(total)}" if total else "")
        + "\n\nЧто сделать со всеми? 👇",
        reply_markup=conversion_options(PHOTO, key),
    )


@router.message(F.photo, F.media_group_id)
async def on_album_photo(message: Message) -> None:
    gid = message.media_group_id
    photo = message.photo[-1]
    album = _albums.setdefault(
        gid, Album(user_id=message.from_user.id, chat_id=message.chat.id)
    )
    album.files.append((photo.file_id, photo.file_size))
    old = _album_timers.get(gid)
    if old and not old.done():
        old.cancel()
    _album_timers[gid] = asyncio.create_task(_flush_album(gid, message))


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
        if mime.startswith("image/he"):
            kind = PHOTO
        elif mime.startswith("image/"):
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

    batch_ids = pending.__dict__.get("file_ids") or [pending.file_id]
    is_batch = len(batch_ids) > 1

    ctx = KIND_TITLES.get(pending.kind, "📦 Файл")
    if is_batch:
        ctx = f"📸 Альбом · {len(batch_ids)} фото"
    status = await callback.message.edit_text(f"📥 <b>Скачиваю…</b>\n{ctx}")

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temps: list[Path] = []
    results: list[Path] = []
    texts: list[str] = []
    ok_sizes: list[int] = []
    monitor: asyncio.Task | None = None

    async def job() -> None:
        for i, fid in enumerate(batch_ids):
            src = TMP_DIR / f"{token}_{i}"
            temps.append(src)
            await bot.download(fid, destination=str(src))
            ok_sizes.append(src.stat().st_size)
            if is_batch:
                await status.edit_text(
                    f"⚙️ <b>Обрабатываю…</b>\n{ctx}\n{i + 1} / {len(batch_ids)}"
                )

        async def run_one(s: Path):
            return await asyncio.wait_for(spec.run(s), timeout=300)

        outs = await asyncio.gather(
            *[run_one(TMP_DIR / f"{token}_{i}") for i in range(len(batch_ids))]
        )
        for r in outs:
            if isinstance(r, str):
                texts.append(r)
            else:
                results.append(r)
                temps.append(r)

    queue = get_queue()
    ahead = queue.pending()
    fut = queue.submit(job)

    if ahead:
        try:
            status = await status.edit_text(f"⏳ <b>Ты {ahead + 1}-й в очереди</b>\n{ctx}")
        except TelegramBadRequest:
            pass
        if ahead >= MAX_PARALLEL:

            async def _track():
                while not fut.done():
                    await asyncio.sleep(2)
                    p = queue.pending()
                    if p:
                        try:
                            await status.edit_text(
                                f"⏳ <b>В очереди. Впереди: {p}</b>\n{ctx}"
                            )
                        except TelegramBadRequest:
                            pass

            monitor = asyncio.create_task(_track())

    try:
        await fut
        await db.consume(pending.user_id)
        try:
            await bot.set_message_reaction(
                callback.message.chat.id,
                pending.message_id,
                reaction=[ReactionTypeEmoji(emoji="🔥")],
            )
        except Exception:
            pass

        if texts:
            joined = ("\n\n" + "─" * 12 + "\n\n").join(texts)[:3500]
            await status.edit_text(
                f"{joined}\n\n<i>Что-нибудь ещё?</i>",
                reply_markup=conversion_options(pending.kind, key),
            )
            return

        caption = ""
        if len(results) == 1:
            caption = _build_caption(results[0], ok_sizes[0])
        else:
            saved_total_in = sum(ok_sizes)
            saved_total_out = sum(p.stat().st_size for p in results)
            caption = f"✅ Готово! {len(results)} шт · 📦 {_fmt_size(saved_total_in)} → {_fmt_size(saved_total_out)}"

        await status.edit_text(f"📤 <b>Отправляю…</b>\n{spec.label}")
        await bot.send_chat_action(callback.message.chat.id, CHAT_ACTIONS[spec.send])
        await _send_results(bot, status, spec.send, results, caption, is_batch)

        try:
            await status.edit_text(
                "🔁 <b>Ещё что-нибудь?</b>",
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
        if monitor and not monitor.done():
            monitor.cancel()
        for t in temps:
            t.unlink(missing_ok=True)


async def _send_results(bot: Bot, status: Message, send_type: str, paths, caption: str, is_batch: bool) -> None:
    chat_id = status.chat.id
    if is_batch and send_type in ("photo", "document"):
        media_cls = InputMediaPhoto if send_type == "photo" else InputMediaDocument
        for chunk_start in range(0, len(paths), 10):
            chunk = paths[chunk_start:chunk_start + 10]
            media = [
                media_cls(media=FSInputFile(p), caption=caption if idx == 0 and chunk_start == 0 else None)
                for idx, p in enumerate(chunk)
            ]
            await bot.send_media_group(chat_id, media=media)
        return

    sender_map = {
        "voice": status.answer_voice,
        "audio": status.answer_audio,
        "photo": status.answer_photo,
        "animation": status.answer_animation,
        "document": status.answer_document,
    }
    for idx, path in enumerate(paths):
        sender = sender_map[send_type]
        kwargs = {"caption": caption} if send_type != "voice" and idx == 0 else {}
        try:
            await sender(FSInputFile(path), **kwargs, message_effect_id=SUCCESS_EFFECT)
        except TelegramBadRequest as e:
            if "MESSAGE_EFFECT" in str(e) or "effect" in str(e).lower():
                await sender(FSInputFile(path), **kwargs)
            else:
                raise
