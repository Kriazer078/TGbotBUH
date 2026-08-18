"""Safe, allow-listed delivery of the primary-documents reminder image."""

from __future__ import annotations

import re
import logging
import os

from aiogram.types import FSInputFile

logger = logging.getLogger(__name__)

_TARGET_PATTERN = re.compile(r"(?P<chat_id>\d{8,12})/(?P<thread_id>[1-9]\d*)$")


def parse_broadcast_targets(raw_targets: str) -> list[tuple[int, int | None]]:
    """Convert the supplied ``internal_chat_id/topic_id`` list to Bot API IDs.

    Telegram private-supergroup links expose the internal id without the
    ``-100`` prefix.  Only this explicit list is accepted, so the scheduled
    job cannot be redirected to an arbitrary chat.
    """
    tokens = raw_targets.replace(",", " ").split()
    if not tokens:
        raise ValueError("PRIMARY_DOCUMENT_TARGETS must not be empty")

    targets: list[tuple[int, int | None]] = []
    seen: set[int] = set()
    for token in tokens:
        match = _TARGET_PATTERN.fullmatch(token)
        if not match:
            raise ValueError(f"Invalid broadcast target: {token}")
        internal_chat_id = int(match.group("chat_id"))
        if internal_chat_id in seen:
            raise ValueError(f"Duplicate broadcast chat: {internal_chat_id}")
        seen.add(internal_chat_id)
        thread_id = int(match.group("thread_id"))
        # /1 identifies the General section in a copied forum link. The Bot
        # API addresses General by omitting message_thread_id.
        targets.append((-1000000000000 - internal_chat_id, None if thread_id == 1 else thread_id))

    return targets


async def publish_primary_documents(
    bot, raw_targets: str | None = None
) -> dict[str, int]:
    """Send exactly one configured image to every configured General topic.

    Each target is independent: a single inaccessible chat is logged but
    cannot block the remaining client chats or cause a global retry that
    duplicates already delivered images.
    """
    targets = parse_broadcast_targets(
        raw_targets if raw_targets is not None else os.getenv("PRIMARY_DOCUMENT_TARGETS", "")
    )
    image_path = os.getenv("PRIMARY_DOCUMENT_IMAGE_PATH", "/app/bot/assets/primary-documents.png")
    sent = 0
    failed = 0

    for chat_id, thread_id in targets:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                message_thread_id=thread_id,
                photo=FSInputFile(image_path),
            )
            sent += 1
        except Exception:
            failed += 1
            logger.exception("[primary_documents] Delivery failed for configured chat %s", chat_id)

    return {"sent": sent, "failed": failed}
