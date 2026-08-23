from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.converter import options_for


def conversion_options(kind: str, key: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=spec.label, callback_data=f"c:{spec.code}:{key}")]
        for spec in options_for(kind)
    ]
    rows.append([InlineKeyboardButton(text="✖ Отмена", callback_data=f"x:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
