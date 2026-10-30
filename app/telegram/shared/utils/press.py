"""Handle a button press as if the user had typed the button's text."""

from __future__ import annotations

from datetime import UTC, datetime

from telethon.tl import types


async def dispatch_text(client, event, text: str) -> None:
    """Run every NewMessage handler on ``text`` as a message from the user behind ``event``.

    ``event`` is the callback event of the tapped inline button; the message is built as a reply-sized
    stand-in on the same chat and message id, so handlers that ``respond`` or ``reply`` behave exactly as
    they do for a typed message. It goes through the whole pipeline (middlewares and step filters).
    """
    peer = types.PeerUser(event.sender_id)
    message = types.Message(
        id=event.original_update.msg_id,
        peer_id=peer,
        from_id=peer,
        message=text,
        date=datetime.now(UTC),
        out=False,
    )
    update = types.UpdateNewMessage(message=message, pts=0, pts_count=0)
    update._entities = {}
    await client._dispatch_update(update)
