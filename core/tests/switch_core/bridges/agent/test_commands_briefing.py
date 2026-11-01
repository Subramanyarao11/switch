"""`!briefing` shows a room what an agent joining it is handed, from the same
reads `connect_to_room` makes, so it can be checked without an agent."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from switch_core.bridges.agent import commands
from switch_core.bridges.agent.commands import dispatch_admin_command
from switch_core.bridges.agent.protocol.instructions import build_room_instructions
from switch_core.bridges.resource.service import ResourceService
from switch_core.clients.command_consumer import CommandConsumer
from switch_core.db.models import (
    Document,
    Package,
    Reference,
    Room,
    User,
    package_documents,
    package_references,
    room_documents,
    room_packages,
    room_references,
)
from switch_core.db.stores.document_store import DocumentStore
from switch_core.db.stores.package_store import PackageStore
from switch_core.db.stores.reference_store import ReferenceStore
from switch_core.db.stores.reference_type_store import ReferenceTypeStore
from switch_core.db.stores.room_link_store import RoomLinkStore
from switch_core.db.stores.room_store import RoomStore

_SHARED = {"read_visibility": "public", "write_visibility": "private"}


async def _room(
    session_factory: async_sessionmaker[AsyncSession], *, instructions: str | None
) -> str:
    """A room, and with `instructions` one of everything an agent is handed."""
    async with session_factory() as db, db.begin():
        owner = User(
            id=f"owner-{uuid.uuid4().hex[:8]}",
            name="Owner",
            email=f"{uuid.uuid4().hex[:8]}@example.test",
            role="user",
        )
        room = Room(
            transport_room_id=f"!{uuid.uuid4().hex[:8]}:test",
            name="payments",
            description="",
            instructions=instructions,
        )
        db.add_all([owner, room])
        await db.flush()
        if instructions is None:
            return room.id
        doc = Document(
            owner_id=owner.id,
            name="runbook",
            description="How we deploy",
            instructions="Read before any deploy.\nAsk before rolling back.",
            content="private body",
            **_SHARED,
        )
        ref = Reference(
            owner_id=owner.id,
            type="github",
            name="payments-repo",
            description="",
            instructions="Open PRs against main.",
            value={"repo": "example/payments"},
            **_SHARED,
        )
        pkg_doc = Document(
            owner_id=owner.id,
            name="glossary",
            description="",
            instructions="",
            content="private body",
            **_SHARED,
        )
        pkg = Package(
            owner_id=owner.id,
            name="Payments context",
            description="",
            instructions="Use for anything about payouts.",
            **_SHARED,
        )
        db.add_all([doc, ref, pkg_doc, pkg])
        await db.flush()
        await db.execute(
            insert(room_documents).values(room_id=room.id, document_id=doc.id)
        )
        await db.execute(
            insert(room_references).values(room_id=room.id, reference_id=ref.id)
        )
        await db.execute(
            insert(room_packages).values(room_id=room.id, package_id=pkg.id)
        )
        await db.execute(
            insert(package_documents).values(package_id=pkg.id, document_id=pkg_doc.id)
        )
        await db.execute(
            insert(package_references).values(package_id=pkg.id, reference_id=ref.id)
        )
        return room.id


def _consumer(
    session_factory: async_sessionmaker[AsyncSession], room_id: str
) -> CommandConsumer:
    consumer = CommandConsumer(
        actor=SimpleNamespace(
            session_factory=session_factory, client_store=MagicMock()
        ),  # type: ignore[arg-type]
        agent_store=MagicMock(),
        room_store=RoomStore(),
        room_role_store=MagicMock(),
        external_user_store=MagicMock(),
        connections=MagicMock(),
        document_store=DocumentStore(),
        reference_store=ReferenceStore(),
        package_store=PackageStore(),
        resource_service=ResourceService(
            reference_store=ReferenceStore(),
            reference_type_store=ReferenceTypeStore(),
            document_store=DocumentStore(),
            package_store=PackageStore(),
            room_link_store=RoomLinkStore(),
            session_factory=session_factory,
        ),
        agent_session_store=MagicMock(),
        room_service=MagicMock(),
        user_store=MagicMock(),
        agent_admin=MagicMock(),
        deletion_requests=MagicMock(),
        frontend_base_url=None,
        follow_the_conversation_enabled=False,
    )
    consumer._resolve_room_meta = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(room_id=room_id)
    )
    consumer._is_direct_room = AsyncMock(return_value=False)  # type: ignore[method-assign]
    consumer.reply_command = AsyncMock()  # type: ignore[method-assign]
    return consumer


@pytest.fixture
def manages(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Whether the sender manages the room, as `_manages_room` would answer."""
    answer = SimpleNamespace(value=False)

    async def _manages_room(*_args: object, **_kwargs: object) -> bool:
        return answer.value

    monkeypatch.setattr(commands, "_manages_room", _manages_room)
    return answer


async def _briefing(consumer: CommandConsumer) -> list[str]:
    await dispatch_admin_command(
        consumer,
        SimpleNamespace(room_id="!room:test"),  # type: ignore[arg-type]
        SimpleNamespace(  # type: ignore[arg-type]
            command="briefing", args="", thread_id=None, user_id="@a:test"
        ),
    )
    [reply] = consumer.reply_command.await_args_list
    lines: list[str] = reply.args[1].splitlines()
    return lines


async def test_a_briefing_shows_what_a_joining_agent_is_handed(
    session_factory: async_sessionmaker[AsyncSession], manages: SimpleNamespace
) -> None:
    room_id = await _room(session_factory, instructions="Payments work only.")

    lines = await _briefing(_consumer(session_factory, room_id))

    assert lines == [
        "**What an agent joining payments is handed**",
        "_Besides Switch's general guidance, which is the same in every room._",
        "",
        "**Room instructions**",
        "Payments work only.",
        "",
        "**Documents**",
        "- **runbook** — How we deploy",
        "  Instructions: Read before any deploy.",
        "  Ask before rolling back.",
        "",
        "**References**",
        "- **payments-repo** _(github)_",
        "  Instructions: Open PRs against main.",
        "",
        "**Packages**",
        "- **Payments context**",
        "  Instructions: Use for anything about payouts.",
        "  - **glossary**",
        "    _No instructions._",
        "  - **payments-repo** _(github)_",
        "    Instructions: Open PRs against main.",
        "",
        "**Linked rooms**",
        "_None._",
        "",
        "_What each reference points at is shown to whoever manages the room._",
    ]
    assert not any("private body" in line for line in lines)


async def test_whoever_manages_the_room_also_sees_what_references_point_at(
    session_factory: async_sessionmaker[AsyncSession], manages: SimpleNamespace
) -> None:
    manages.value = True
    room_id = await _room(session_factory, instructions="Payments work only.")

    lines = await _briefing(_consumer(session_factory, room_id))

    assert lines.count("  Points at: repo: `example/payments`") == 1
    assert lines.count("    Points at: repo: `example/payments`") == 1
    assert not any("shown to whoever manages" in line for line in lines)


async def test_an_empty_room_says_an_agent_gets_only_the_general_guidance(
    session_factory: async_sessionmaker[AsyncSession], manages: SimpleNamespace
) -> None:
    room_id = await _room(session_factory, instructions=None)

    lines = await _briefing(_consumer(session_factory, room_id))

    assert lines[3:5] == [
        "**Room instructions**",
        "_None. An agent joining this room gets only Switch's general guidance._",
    ]
    assert lines.count("_None._") == 4


async def test_the_room_instructions_are_the_ones_connect_to_room_hands_over(
    session_factory: async_sessionmaker[AsyncSession], manages: SimpleNamespace
) -> None:
    room_id = await _room(session_factory, instructions="  Payments work only.\n\n")
    async with session_factory() as db:
        room = await RoomStore().get(db, room_id)
    assert room is not None
    handed = build_room_instructions(
        MagicMock(), room, [], None, include_general=False
    ).removeprefix("## Room-specific instructions\n\n")

    lines = await _briefing(_consumer(session_factory, room_id))

    assert lines[4] == handed
