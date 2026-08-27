"""Inline klavyeler."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..models import Category
from ..util.dates import Bucket

# callback_data biçimi: "b:<bucket>[:free]" / "c:<kategori>" / "x:<eylem>"
CB_BUCKET = "b"
CB_CATEGORY = "c"
CB_ACTION = "x"

MAIN_BUCKETS: list[tuple[Bucket, str]] = [
    (Bucket.TODAY, "📅 Bugün"),
    (Bucket.TOMORROW, "📆 Yarın"),
    (Bucket.WEEKEND, "🎉 Hafta Sonu"),
    (Bucket.THIS_WEEK, "🗓 Bu Hafta"),
    (Bucket.NEXT_WEEK, "⏭ Gelecek Hafta"),
    (Bucket.THIS_MONTH, "📊 Bu Ay"),
]


def main_menu(free_only: bool = False) -> InlineKeyboardMarkup:
    """Ana menü: tarih kovaları + kısayollar."""
    suffix = ":free" if free_only else ""
    rows = []
    for i in range(0, len(MAIN_BUCKETS), 2):
        rows.append(
            [
                InlineKeyboardButton(label, callback_data=f"{CB_BUCKET}:{bucket.value}{suffix}")
                for bucket, label in MAIN_BUCKETS[i : i + 2]
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "💰 Sadece ücretsiz" if not free_only else "💸 Tüm etkinlikler",
                callback_data=f"{CB_ACTION}:toggle_free:{int(not free_only)}",
            ),
            InlineKeyboardButton("🏷 Kategoriler", callback_data=f"{CB_ACTION}:categories"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def category_menu() -> InlineKeyboardMarkup:
    """Kategori seçimi."""
    items = [c for c in Category if c is not Category.OTHER]
    rows = []
    for i in range(0, len(items), 2):
        rows.append(
            [
                InlineKeyboardButton(
                    f"{c.emoji} {c.label}", callback_data=f"{CB_CATEGORY}:{c.value}"
                )
                for c in items[i : i + 2]
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ Geri", callback_data=f"{CB_ACTION}:menu")])
    return InlineKeyboardMarkup(rows)


def settings_menu(digest_enabled: bool, notify_new: bool, free_only: bool) -> InlineKeyboardMarkup:
    """Abonelik ayarları."""

    def mark(value: bool) -> str:
        return "✅" if value else "❌"

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{mark(digest_enabled)} Günlük bülten",
                    callback_data=f"{CB_ACTION}:set_digest:{int(not digest_enabled)}",
                )
            ],
            [
                InlineKeyboardButton(
                    f"{mark(notify_new)} Yeni etkinlik bildirimi",
                    callback_data=f"{CB_ACTION}:set_notify:{int(not notify_new)}",
                )
            ],
            [
                InlineKeyboardButton(
                    f"{mark(free_only)} Sadece ücretsiz etkinlikler",
                    callback_data=f"{CB_ACTION}:set_freeonly:{int(not free_only)}",
                )
            ],
            [InlineKeyboardButton("⬅️ Menü", callback_data=f"{CB_ACTION}:menu")],
        ]
    )
