from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import BUTTON_ICONS
from services.converter import options_for

CATEGORIES = [
    ("video", "🎬 Видео", "primary"),
    ("audio", "🎵 Аудио", "success"),
    ("photo", "🖼 Фото", "danger"),
    ("doc", "📄 Документы", None),
]

_OPTION_STYLES = ["primary", "success", "danger", None]


def _btn(text: str, callback_data: str, style: str | None = None, icon_key: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        style=style,
        icon_custom_emoji_id=BUTTON_ICONS.get(icon_key) if icon_key else None,
    )


def categories_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("🎬 Видео", "cat:video", "primary", "cat:video"),
                _btn("🎵 Аудио", "cat:audio", "success", "cat:audio"),
            ],
            [
                _btn("🖼 Фото", "cat:photo", "danger", "cat:photo"),
                _btn("📄 Документы", "cat:doc", None, "cat:doc"),
            ],
            [
                _btn("🔗 QR-код из текста", "cat:qr", "primary", "cat:qr"),
                _btn("#️⃣ Хеши и инфо", "cat:tools", "success", "cat:tools"),
            ],
            [
                InlineKeyboardButton(text="📤 Поделиться ботом", switch_inline_query="share"),
            ],
            [
                _btn("📊 Статистика", "my_stats"),
                _btn("❓ Как пользоваться", "howto"),
            ],
        ]
    )


def category_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("⬅️ Все категории", "cats")]]
    )


def conversion_options(kind: str, key: str, preferred: str | None = None) -> InlineKeyboardMarkup:
    specs = options_for(kind)
    rows: list[list[InlineKeyboardButton]] = []
    current_group = None
    buffer: list[InlineKeyboardButton] = []

    def flush():
        nonlocal buffer
        for i in range(0, len(buffer) - len(buffer) % 2, 2):
            rows.append([buffer[i], buffer[i + 1]])
        if len(buffer) % 2:
            rows.append([buffer[-1]])
        buffer = []

    for spec in specs:
        if preferred and spec.code == preferred:
            continue
        if spec.group != current_group:
            flush()
            current_group = spec.group
            if spec.group:
                rows.append([InlineKeyboardButton(text=f"— {spec.group} —", callback_data="noop")])
        buffer.append(
            _btn(spec.label, f"c:{spec.code}:{key}", _OPTION_STYLES[len(rows) % 4], f"opt:{spec.code}")
        )
    flush()

    if preferred:
        pref = next((s for s in specs if s.code == preferred), None)
        if pref:
            rows.insert(
                0,
                [
                    InlineKeyboardButton(
                        text=f"⚡️ {pref.label} — как в прошлый раз",
                        callback_data=f"c:{pref.code}:{key}",
                        style="success",
                        icon_custom_emoji_id=BUTTON_ICONS.get(f"opt:{pref.code}"),
                    )
                ],
            )

    rows.append([InlineKeyboardButton(text="✖ Отмена", callback_data=f"x:{key}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
