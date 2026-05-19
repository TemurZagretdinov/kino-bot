from collections.abc import Sequence

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.models.channel import Channel
from app.models.movie import Movie


SEARCH_BY_CODE = "🎬 Kod bilan izlash"
SEARCH_BY_NAME = "🔎 Nom bilan izlash"
TOP_MOVIES = "🔥 Top filmlar"
CHANNELS = "📢 Kanallar"
HELP = "ℹ️ Yordam"


def main_user_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SEARCH_BY_CODE), KeyboardButton(text=SEARCH_BY_NAME)],
            [KeyboardButton(text=TOP_MOVIES), KeyboardButton(text=CHANNELS)],
            [KeyboardButton(text=HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Bo‘limni tanlang yoki kino kodini yuboring",
    )


def subscribe_keyboard(channels: Sequence[Channel]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for channel in channels:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📢 {channel.title}",
                    url=channel.invite_link,
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Tekshirish",
                callback_data="check_subscriptions",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def movies_inline_keyboard(movies: Sequence[Movie]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🎬 {_trim_button_text(movie.title)}",
                callback_data=f"movie_by_id:{movie.id}",
            )
        ]
        for movie in movies
    ]
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def top_movies_inline_keyboard(movies: Sequence[Movie]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{index}. 🎬 {_trim_button_text(movie.title)}",
                callback_data=f"movie_by_id:{movie.id}",
            )
        ]
        for index, movie in enumerate(movies, start=1)
    ]
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_back_row()])


def _back_row() -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text="⬅️ Menyuga qaytish",
            callback_data="back_to_menu",
        )
    ]


def _trim_button_text(text: str, max_length: int = 48) -> str:
    clean_text = " ".join(text.split())
    if len(clean_text) <= max_length:
        return clean_text
    return f"{clean_text[: max_length - 1]}…"
