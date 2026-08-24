import asyncio
import inspect
import logging
import shutil
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
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
    ZIP,
    _probe_duration,
    _trim_clip,
)
from services.queue import get_queue

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
    ".zip": ZIP,
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
    ZIP: "🗜 ZIP-архив",
}

_UNSUPPORTED_TEXT = (
    "🙈 <b>Такой формат пока не поддерживаю</b>\n\n"
    "<blockquote>🎬 Видео · 🎵 Аудио · 🖼 Фото (вкл. HEIC)\n"
    "📄 PDF · 📝 DOCX · 📊 XLSX/CSV\n"
    "💬 Субтитры SRT/VTT · 📃 TXT · 🗜 ZIP</blockquote>\n\n"
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

TRIMS: dict[str, dict] = {}

MERGES: dict[int, dict] = {}

LAST_ACTION: dict[int, str] = {}


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


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


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


def _in_group(message: Message) -> bool:
    return (message.chat.type or "private") != "private"


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
    preferred = LAST_ACTION.get(message.from_user.id)
    if preferred:
        pref_spec = ACTIONS.get(preferred)
        if not pref_spec or kind not in pref_spec.kinds:
            preferred = None
    hint = (
        "\n\n<i>💬 Реплай на это сообщение — предложу форматы снова.</i>"
        if _in_group(message)
        else ""
    )
    await message.answer(
        f"<b>{card}</b>\n\nЧто с ним сделать? 👇{hint}",
        reply_markup=conversion_options(kind, key, preferred),
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
    if _in_group(message) and not message.reply_to_message:
        return
    photo = message.photo[-1]
    await _register(message, PHOTO, photo.file_id, photo.file_size)


@router.message(F.video)
async def on_video(message: Message) -> None:
    if _in_group(message) and not message.reply_to_message:
        return
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


@router.message(Command("merge"))
async def cmd_merge(message: Message) -> None:
    if _in_group(message):
        await message.answer("📄 Склейка PDF работает только в личке со мной.")
        return
    uid = message.from_user.id
    MERGES[uid] = {"files": [], "chat_id": message.chat.id}
    await message.answer(
        "📄 <b>Склейка PDF</b>\n\n"
        "Пришли несколько PDF-файлов по одному.\n"
        "Когда закончишь — жми «✅ Склеить».\n\n"
        "<i>Максимум 10 файлов.</i>",
        reply_markup=_merge_kb(0),
    )


def _merge_kb(count: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    if count >= 2:
        rows.append([InlineKeyboardButton(text=f"✅ Склеить ({count})", callback_data="mg:go")])
    elif count == 1:
        rows.append([InlineKeyboardButton(text="➕ Пришли ещё один PDF…", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="mg:x")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.document)
async def on_document(message: Message) -> None:
    doc = message.document
    name = doc.file_name or ""
    ext_low = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    uid = message.from_user.id
    if uid in MERGES and ext_low == "pdf" and not _in_group(message):
        entry = MERGES[uid]
        if len(entry["files"]) >= 10:
            await message.answer("😅 Хватит, максимум 10 файлов!")
            return
        entry["files"].append(doc.file_id)
        count = len(entry["files"])
        await message.answer(
            f"➕ Добавил <b>{name[:40]}</b>\nВсего файлов: <b>{count}</b>",
            reply_markup=_merge_kb(count),
        )
        return

    if _in_group(message) and not message.reply_to_message:
        return

    ext = ext_low.upper() or None
    kind = _DOC_KINDS.get("." + ext_low if ext_low else "")
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
            kind = ZIP
        else:
            await message.answer(_UNSUPPORTED_TEXT)
            return

    await _register(message, kind, doc.file_id, doc.file_size, ext=ext)


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("mg:"))
async def cb_merge(callback: CallbackQuery, bot: Bot) -> None:
    action = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    entry = MERGES.get(uid)

    if action == "x":
        MERGES.pop(uid, None)
        await callback.answer("Отменено")
        try:
            await callback.message.delete()
        except Exception:
            pass
        return

    if action == "go":
        if not entry or len(entry["files"]) < 2:
            await callback.answer("Нужно минимум 2 PDF", show_alert=True)
            return
        await callback.answer()
        status = await callback.message.edit_text(f"📎 Склеиваю {len(entry['files'])} PDF…")
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        parts: list[Path] = []
        try:
            from pypdf import PdfWriter

            for i, fid in enumerate(entry["files"]):
                p = TMP_DIR / f"{token}_{i}.pdf"
                await bot.download(fid, destination=str(p))
                parts.append(p)
            writer = PdfWriter()
            for p in parts:
                writer.append(str(p))
            out = TMP_DIR / f"{token}_merged.pdf"
            with open(out, "wb") as f:
                writer.write(f)
            await db.consume(uid)
            try:
                await bot.set_message_reaction(
                    callback.message.chat.id,
                    callback.message.message_id,
                    reaction=[ReactionTypeEmoji(emoji="🔥")],
                )
            except Exception:
                pass
            await status.edit_text(f"✅ Готово! {len(parts)} PDF → один")
            await bot.send_document(
                callback.message.chat.id,
                document=FSInputFile(out),
                caption="📄 <b>Склеенный PDF</b>",
                message_effect_id=SUCCESS_EFFECT,
            )
            MERGES.pop(uid, None)
            try:
                await status.delete()
            except Exception:
                pass
        except Exception:
            logger.exception("merge failed")
            try:
                await status.edit_text("💔 Не удалось склеить эти PDF.")
            except Exception:
                pass
        finally:
            for p in parts:
                p.unlink(missing_ok=True)
            Path(TMP_DIR / f"{token}_merged.pdf").unlink(missing_ok=True)


@router.callback_query(F.data.startswith("t:"))
async def cb_trim(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, act, key = parts
    state = TRIMS.get(key)

    if state is None:
        await callback.answer("Сессия обрезки устарела 🔄", show_alert=True)
        return
    if callback.from_user.id != state["user_id"] and _in_group(callback.message):
        pass
    elif callback.from_user.id != state["user_id"]:
        await callback.answer("Это не твой файл 🙂", show_alert=True)
        return

    if act == "cx":
        TRIMS.pop(key, None)
        state["path"].unlink(missing_ok=True)
        await callback.answer("Отменено")
        try:
            await callback.message.delete()
        except Exception:
            pass
        return

    if callback.from_user.id != state["user_id"] and not _in_group(callback.message):
        await callback.answer("Это не твой файл 🙂", show_alert=True)
        return

    dur = state["dur"]
    if callback.from_user.id != state["user_id"] and not _in_group(callback.message):
        await callback.answer("Это не твой файл 🙂", show_alert=True)
        return

    if act.startswith("s"):
        delta = -15 if act.endswith("-") else 15
        state["start"] = max(0, min(dur - 1, state["start"] + delta))
        if state["end"] > dur:
            state["end"] = dur
        if state["end"] - state["start"] < 1:
            state["end"] = min(dur, state["start"] + 1)
    elif act.startswith("e"):
        delta = -15 if act.endswith("-") else 15
        state["end"] = max(state["start"] + 1, min(dur, state["end"] + delta))

    if act in ("s-", "s+", "e-", "e+"):
        await callback.answer()
        await _render_trim(callback.message, key)
        return

    if act == "go":
        start = state["start"]
        length = state["end"] - state["start"]
        kind = state["kind"]
        await callback.answer()
        status = await callback.message.edit_text(
            f"✂️ Режу {_fmt_time(start)} – {_fmt_time(state['end'])}…"
        )
        out = None
        try:
            out = await asyncio.wait_for(
                _trim_clip(state["path"], start, length, kind), timeout=300
            )
            await db.consume(state["user_id"])
            send_type = "audio" if kind in (AUDIO, VOICE) else "animation"
            cap = f"✂️ <b>Кусок вырезан!</b>\n⏱ {_fmt_time(start)} – {_fmt_time(state['end'])}"
            sender = status.answer_audio if send_type == "audio" else status.answer_animation
            kwargs = {"caption": cap}
            await sender(FSInputFile(out), **kwargs)
            try:
                await status.delete()
            except Exception:
                pass
        except Exception:
            logger.exception("trim failed")
            try:
                await status.edit_text("💔 Не получилось вырезать кусок.")
            except Exception:
                pass
        finally:
            if out:
                out.unlink(missing_ok=True)
            st = TRIMS.pop(key, None)
            if st:
                st["path"].unlink(missing_ok=True)


def _trim_kb(key: str):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀️ Начало −15с", callback_data=f"t:s-:{key}"),
                InlineKeyboardButton(text="Начало +15с ▶️", callback_data=f"t:s+:{key}"),
            ],
            [
                InlineKeyboardButton(text="◀️ Конец −15с", callback_data=f"t:e-:{key}"),
                InlineKeyboardButton(text="Конец +15с ▶️", callback_data=f"t:e+:{key}"),
            ],
            [
                InlineKeyboardButton(text="✂️ Вырезать этот кусок", callback_data=f"t:go:{key}"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"t:cx:{key}")],
        ]
    )


async def _render_trim(msg: Message, key: str) -> None:
    st = TRIMS[key]
    await msg.edit_text(
        f"✂️ <b>Обрезка</b> · всего {_fmt_time(st['dur'])}\n\n"
        f"Выбранный кусок: <b>{_fmt_time(st['start'])} – {_fmt_time(st['end'])}</b>"
        f" ({_fmt_time(st['end'] - st['start'])})\n\n"
        "Двигай границы стрелками по ±15 секунд.",
        reply_markup=_trim_kb(key),
    )


@router.callback_query(F.data.startswith("trim:"))
async def cb_trim_start(callback: CallbackQuery, bot: Bot) -> None:
    key = callback.data.split(":", 1)[1]
    pending = _cache.get(key)
    if pending is None:
        await callback.answer("Файл устарел — отправь заново 🔄", show_alert=True)
        return
    if pending.kind not in (VIDEO, AUDIO, VOICE, VIDEO_NOTE):
        await callback.answer("Обрезка доступна для видео и аудио", show_alert=True)
        return
    await callback.answer()

    status = await callback.message.edit_text("📥 Скачиваю для обрезки…")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / f"trim_{uuid.uuid4().hex}"
    try:
        await bot.download(pending.file_id, destination=str(path))
        dur = await asyncio.wait_for(_probe_duration(path), timeout=120)
    except Exception:
        logger.exception("trim download failed")
        path.unlink(missing_ok=True)
        try:
            await status.edit_text("💔 Не смог прочитать длительность файла.")
        except Exception:
            pass
        return

    TRIMS[key] = {
        "user_id": pending.user_id,
        "path": path,
        "dur": dur,
        "start": 0.0,
        "end": min(30.0, dur),
        "kind": pending.kind,
    }
    await _render_trim(status, key)


@router.callback_query(F.data.startswith("x:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    _cache.pop(key, None)
    await callback.answer("Отменено")
    try:
        await callback.message.delete()
    except Exception:
        pass


def _resolve_send(spec_send: str, kind: str) -> str:
    if spec_send == "auto":
        return "animation" if kind == VIDEO else "audio"
    return spec_send


@router.callback_query(F.data.startswith("c:"))
async def cb_convert(callback: CallbackQuery, bot: Bot) -> None:
    _, action_code, key = callback.data.split(":", 2)
    pending = _cache.get(key)

    if pending is None:
        await callback.answer("Файл устарел — отправь его заново 🔄", show_alert=True)
        return
    if callback.from_user.id != pending.user_id and not _in_group(callback.message):
        await callback.answer("Это не твой файл 🙂", show_alert=True)
        return

    spec = ACTIONS.get(action_code)
    if spec is None or pending.kind not in spec.kinds:
        await callback.answer("Действие недоступно", show_alert=True)
        return

    await callback.answer()

    send_type = _resolve_send(spec.send, pending.kind)
    if not shutil.which("ffmpeg") and send_type in ("voice", "audio", "animation"):
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

    n_params = len(inspect.signature(spec.run).parameters)

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
            if n_params >= 2:
                raw = spec.run(s, pending.kind)
            else:
                raw = spec.run(s)
            return await asyncio.wait_for(raw, timeout=300)

        outs = await asyncio.gather(
            *[run_one(TMP_DIR / f"{token}_{i}") for i in range(len(batch_ids))]
        )
        for r in outs:
            if isinstance(r, str):
                texts.append(r)
            elif isinstance(r, tuple) and len(r) == 2 and isinstance(r[1], int):
                files, skipped_n = r
                results.extend(files)
                temps.extend(files)
                if skipped_n:
                    texts.append(f"⚠️ Пропущено файлов: {skipped_n}")
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

    out_path = None
    try:
        await fut
        await db.consume(pending.user_id)
        LAST_ACTION[pending.user_id] = spec.code
        try:
            await bot.set_message_reaction(
                callback.message.chat.id,
                pending.message_id,
                reaction=[ReactionTypeEmoji(emoji="🔥")],
            )
        except Exception:
            pass

        if texts and not results:
            joined = ("\n\n" + "─" * 12 + "\n\n").join(texts)[:3500]
            await status.edit_text(
                f"{joined}\n\n<i>Что-нибудь ещё?</i>",
                reply_markup=conversion_options(pending.kind, key),
            )
            return

        caption = ""
        if len(results) == 1:
            out_path = results[0]
            caption = _build_caption(results[0], ok_sizes[0])
        else:
            saved_total_in = sum(ok_sizes)
            saved_total_out = sum(p.stat().st_size for p in results)
            caption = (
                f"✅ Готово! {len(results)} шт · "
                f"📦 {_fmt_size(saved_total_in)} → {_fmt_size(saved_total_out)}"
            )
            if texts:
                caption += "\n" + "\n".join(texts)[:300]

        await status.edit_text(f"📤 <b>Отправляю…</b>\n{spec.label}")
        await bot.send_chat_action(
            callback.message.chat.id, CHAT_ACTIONS.get(send_type, ChatAction.TYPING)
        )
        await _send_results(bot, status, send_type, results, caption, is_batch)

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


PHOTO_MAX_BYTES = 10 * 1024 * 1024


def _oversized_as_document(path: Path) -> bool:
    return path.stat().st_size > PHOTO_MAX_BYTES


async def _send_results(bot: Bot, status: Message, send_type: str, paths, caption: str, is_batch: bool) -> None:
    chat_id = status.chat.id

    if is_batch and any(_oversized_as_document(p) for p in paths):
        send_type = "document"

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
        st = send_type
        cap = caption if idx == 0 else None
        if st == "photo" and _oversized_as_document(path):
            st = "document"
            if cap:
                cap += "\n📎 Отправил файлом — для фото лимит Telegram 10 МБ"
        sender = sender_map[st]
        kwargs = {"caption": cap} if st != "voice" else {}
        try:
            await sender(FSInputFile(path), **kwargs, message_effect_id=SUCCESS_EFFECT)
        except TelegramBadRequest as e:
            msg = str(e)
            if "MESSAGE_EFFECT" in msg or "effect" in msg.lower():
                await sender(FSInputFile(path), **kwargs)
            elif "too big" in msg.lower() and st != "document":
                await status.answer_document(
                    FSInputFile(path),
                    caption=(cap or "") + "\n📎 Отправил файлом — не влезло как медиа",
                    message_effect_id=SUCCESS_EFFECT,
                )
            else:
                raise


def _ext_from_mime(mime: str | None) -> str | None:
    if not mime or "/" not in mime:
        return None
    sub = mime.split("/", 1)[1]
    return sub.upper() if len(sub) <= 5 else None
