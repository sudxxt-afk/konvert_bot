from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from db import db

router = Router(name="start")

WELCOME = (
    "👋 Привет! Я — <b>универсальный конвертер файлов</b>.\n\n"
    "Просто пришли мне файл, и я предложу варианты:\n\n"
    "🎬 Видео → голосовое, MP3, GIF\n"
    "🎧 Аудио и голосовые → MP3, WAV, M4A, голосовое\n"
    "🖼 Фото → JPEG, PNG, WebP + сжатие\n"
    "📄 PDF → текст\n\n"
    "Полностью бесплатно и без лимитов 💸"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await db.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(WELCOME)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    user_id = message.from_user.id
    await db.ensure_user(user_id, message.from_user.username)
    stats = await db.stats(user_id)
    await message.answer(
        "📊 <b>Статус</b>\n"
        f"Всего конвертаций: <b>{stats['total']}</b>\n"
        "План: Free — безлимит 🎉"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(WELCOME)
