import asyncio
import logging
import re
import shutil
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import DB_PATH, MAX_PARALLEL, QR_UPLOAD_CHANNEL, START_TIME, TMP_DIR, is_admin
from db import db
from services.queue import get_queue

logger = logging.getLogger(__name__)
router = Router(name="admin")

_BTN_RE = re.compile(r"^\[(.+?)\s*\|\s*(\S+)\]\s*$")
MAX_BUTTONS = 10


class Broadcast(StatesGroup):
    waiting_content = State()
    waiting_buttons = State()


def _panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="a:stats"),
                InlineKeyboardButton(text="👥 Юзеры", callback_data="a:users"),
            ],
            [
                InlineKeyboardButton(text="📢 Рассылка", callback_data="a:bc"),
                InlineKeyboardButton(text="⚙️ Сервис", callback_data="a:srv"),
            ],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="a:close")],
        ]
    )


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="a:cancel_bc")]]
    )


def _after_content_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Дальше без кнопок", callback_data="a:bcskip")],
            [
                InlineKeyboardButton(text="➕ Добавить кнопки", callback_data="a:bcaddbtn"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="a:cancel_bc"),
            ],
        ]
    )


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Отправить всем", callback_data="a:bcgo")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="a:cancel_bc")],
        ]
    )


async def _stats_text() -> str:
    users = await db.users_count()
    total = await db.global_total()
    uptime = int(time.time() - START_TIME)
    h, rem = divmod(uptime, 3600)
    m, s = divmod(rem, 60)
    return (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"🔢 Конвертаций всего: <b>{total}</b>\n"
        f"⏱ Аптайм: <b>{h}ч {m}м {s}с</b>"
    )


async def _srv_text() -> str:
    tmp_size = sum(f.stat().st_size for f in TMP_DIR.rglob("*") if f.is_file()) if TMP_DIR.exists() else 0
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    ffmpeg = shutil.which("ffmpeg") or "❌"
    gs = shutil.which("gs") or "❌"

    def mb(n: float) -> str:
        return f"{n / 1024 / 1024:.1f} МБ"

    return (
        "⚙️ <b>Сервис</b>\n\n"
        f"🎬 ffmpeg: {'✅' if ffmpeg else '❌'}\n"
        f"📄 ghostscript: {'✅' if gs else '❌'}\n"
        f"📢 Канал для инлайна: {QR_UPLOAD_CHANNEL or 'не задан'}\n"
        f"🚦 Очередь: {get_queue().pending()} ждёт · слотов: {MAX_PARALLEL}\n\n"
        f"🗂 tmp: {mb(tmp_size)}\n"
        f"💾 БД: {mb(db_size)}\n\n"
        f"⏱ Аптайм процесса: {int(time.time() - START_TIME)}с"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user):
        return
    await state.clear()
    await message.answer(
        f"🛡 <b>Админ-панель</b>\n\nПривет, @{message.from_user.username}!",
        reply_markup=_panel_kb(),
    )


def _parse_buttons(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    rows: list[tuple[str, str]] = []
    errors: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _BTN_RE.match(line)
        if not m:
            errors.append(raw.strip())
            continue
        label, url = m.group(1).strip(), m.group(2).strip()
        if not url.lower().startswith(("http://", "https://", "tg://")) or len(label) > 64:
            errors.append(line)
            continue
        rows.append((label, url))
    if len(rows) > MAX_BUTTONS:
        errors.extend(f"[{l} | {u}]" for l, u in rows[MAX_BUTTONS:])
        rows = rows[:MAX_BUTTONS]
    return rows, errors


def _build_kb(pairs: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=l, url=u)] for l, u in pairs]
    )


@router.callback_query(F.data.startswith("a:"), F.from_user.func(lambda u: not is_admin(u)))
async def cb_admin_guard(callback: CallbackQuery) -> None:
    return


@router.callback_query(F.data == "a:close")
async def cb_close(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "a:cancel_bc")
async def cb_cancel_bc(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.edit_text(
        "🛡 <b>Админ-панель</b>",
        reply_markup=_panel_kb(),
    )


@router.callback_query(F.data == "a:stats")
async def cb_stats(callback: CallbackQuery) -> None:
    text = await _stats_text()
    await callback.message.edit_text(text, reply_markup=_panel_kb())


@router.callback_query(F.data == "a:users")
async def cb_users(callback: CallbackQuery) -> None:
    rows = await db.recent_users(10)
    lines = ["👥 <b>Последние пользователи</b>\n"]
    for uid, uname, tot, created in rows:
        name = f"@{uname}" if uname else f"<code>{uid}</code>"
        lines.append(f"• {name} — {tot} конв." + (f" · {created}" if created else ""))
    await callback.message.edit_text("\n".join(lines), reply_markup=_panel_kb())


@router.callback_query(F.data == "a:srv")
async def cb_srv(callback: CallbackQuery) -> None:
    text = await _srv_text()
    await callback.message.edit_text(text, reply_markup=_panel_kb())


@router.callback_query(F.data == "a:bc")
async def cb_bc_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Broadcast.waiting_content)
    await callback.answer()
    await callback.message.edit_text(
        "📢 <b>Шаг 1 из 3 · Контент</b>\n\n"
        "Пришли сообщение для рассылки:\n"
        "<blockquote>текст · фото · видео · голосовое · файл — что угодно</blockquote>",
        reply_markup=_cancel_kb(),
    )


@router.message(Broadcast.waiting_content)
async def bc_content(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user):
        await state.clear()
        return
    await state.update_data(chat_id=message.chat.id, msg_id=message.message_id)
    await state.set_state(Broadcast.waiting_buttons)
    await message.answer(
        "📢 <b>Шаг 2 из 3 · Кнопки</b>\n\n"
        "Контент принят 👆\n\n"
        "Можно добавить инлайн-кнопки. Пришли строки в формате:\n"
        "<code>[Текст кнопки | https://ссылка]</code>\n"
        "<code>[Канал | https://t.me/KudexConvert]</code>\n\n"
        "До 10 кнопок, каждая — отдельной строкой.\n"
        "Либо жми «Дальше без кнопок».",
        reply_markup=_after_content_kb(),
    )


@router.callback_query(F.data == "a:bcaddbtn")
async def cb_bc_addbtn(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("msg_id"):
        await callback.answer("Сначала пришли контент", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "➕ Пришли строки вида:\n"
        "<code>[Текст | https://ссылка]</code>\n\n"
        "Или жми «Дальше без кнопок».",
        reply_markup=_after_content_kb(),
    )


@router.message(Broadcast.waiting_buttons)
async def bc_buttons(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user):
        await state.clear()
        return
    if message.text and message.text.startswith("/"):
        return

    pairs, errors = _parse_buttons(message.text or "")
    if errors:
        bad = "\n".join(f"<code>{e}</code>" for e in errors[:5])
        await message.answer(
            "⚠️ Не смог разобрать строки:\n"
            f"{bad}\n\n"
            "Формат: <code>[Текст | ссылка]</code>, ссылка начинается с http(s):// или tg://",
        )
        return
    if not pairs:
        await message.answer("Пришли хотя бы одну кнопку или жми «Дальше без кнопок».")
        return

    await state.update_data(buttons=pairs)
    data = await state.get_data()
    users = await db.users_count()
    btn_preview = "\n".join(f"▪️ [{l}]({u})" for l, u in pairs)
    await message.answer(
        f"📢 <b>Шаг 3 из 3 · Подтверждение</b>\n\n"
        f"Получателей: <b>{users}</b>\n"
        f"Кнопок: <b>{len(pairs)}</b>\n{btn_preview}\n\n"
        "Запускаем?",
        reply_markup=_confirm_kb(),
    )


@router.callback_query(F.data == "a:bcskip")
async def cb_bc_skip(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("msg_id"):
        await callback.answer("Сначала пришли контент", show_alert=True)
        return
    await state.update_data(buttons=[])
    await callback.answer()
    users = await db.users_count()
    await callback.message.edit_text(
        f"📢 <b>Шаг 3 из 3 · Подтверждение</b>\n\n"
        f"Получателей: <b>{users}</b>\n"
        f"Кнопки: без кнопок\n\n"
        "Запускаем?",
        reply_markup=_confirm_kb(),
    )


@router.callback_query(F.data == "a:bcgo")
async def cb_broadcast(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    src_chat = data.get("chat_id")
    src_msg = data.get("msg_id")
    buttons = data.get("buttons") or []
    await state.clear()
    if not src_chat or not src_msg:
        await callback.answer("Нет сообщения для рассылки", show_alert=True)
        return

    await callback.answer()
    status = await callback.message.edit_text("📣 <b>Рассылка запущена…</b>\nОтправлено: 0")

    user_ids = await db.all_user_ids()
    markup = _build_kb(buttons) if buttons else None
    sent = failed = 0

    async def deliver(uid: int) -> bool:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=src_chat,
                message_id=src_msg,
                reply_markup=markup,
            )
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=src_chat,
                    message_id=src_msg,
                    reply_markup=markup,
                )
                return True
            except Exception:
                return False
        except TelegramForbiddenError:
            return False
        except Exception:
            logger.exception("broadcast to %s failed", uid)
            return False

    for i, uid in enumerate(user_ids, start=1):
        ok = await deliver(uid)
        sent += int(ok)
        failed += int(not ok)
        if i % 25 == 0 or i == len(user_ids):
            try:
                await status.edit_text(
                    f"📣 <b>Рассылка…</b>\nПрогресс: {i}/{len(user_ids)}\n✅ {sent} · ❌ {failed}"
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status.edit_text(
        f"📣 <b>Рассылка завершена</b>\n\n"
        f"✅ Доставлено: <b>{sent}</b>\n"
        f"❌ Не доставлено: <b>{failed}</b>",
        reply_markup=_panel_kb(),
    )
