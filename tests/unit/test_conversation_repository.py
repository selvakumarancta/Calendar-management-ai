"""
Unit tests for SQLAlchemyConversationRepository.

All SQLAlchemy calls are mocked — no DB needed.
Covers lines 25-140 (all 39 missing lines).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.conversation import Conversation, Message, MessageRole
from src.infrastructure.persistence.conversation_repository import (
    SQLAlchemyConversationRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UID = uuid.uuid4()
_USER_UID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)


def _conv(**kw) -> Conversation:
    return Conversation(
        id=kw.pop("id", _UID),
        user_id=kw.pop("user_id", _USER_UID),
        **kw,
    )


def _conv_model(conv_id=None, user_id=None) -> MagicMock:
    m = MagicMock()
    m.id = conv_id or _UID
    m.user_id = user_id or _USER_UID
    m.summary = None
    m.created_at = _NOW
    m.updated_at = _NOW
    return m


def _msg_model(role: str = "user", content: str = "hello") -> MagicMock:
    m = MagicMock()
    m.role = role
    m.content = content
    m.tool_name = None
    m.tool_call_id = None
    m.token_count = 5
    m.created_at = _NOW
    return m


def _scalars_mock(items: list) -> MagicMock:
    """A MagicMock that is both iterable and has .all()."""
    sm = MagicMock()
    sm.__iter__ = MagicMock(return_value=iter(items))
    sm.__len__ = MagicMock(return_value=len(items))
    sm.all.return_value = items
    return sm


def _exec_result(scalar=None, scalars_list=None, scalar_one_val: int = 0) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value = _scalars_mock(scalars_list or [])
    r.scalar_one.return_value = scalar_one_val
    return r


def _session(*execute_results) -> AsyncMock:
    sess = AsyncMock()
    sess.execute = AsyncMock(side_effect=list(execute_results))
    sess.add = MagicMock()
    sess.flush = AsyncMock()
    sess.delete = AsyncMock()
    return sess


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_not_found():
    """Lines 25-30: returns None when ConversationModel not found."""
    sess = _session(_exec_result(scalar=None))
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.get_by_id(_UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_by_id_returns_conversation_with_messages():
    """Lines 25-38: found path — model + messages → Conversation entity."""
    cm = _conv_model()
    mm = _msg_model(role="assistant", content="reply")
    sess = _session(
        _exec_result(scalar=cm),
        _exec_result(scalars_list=[mm]),
    )
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.get_by_id(_UID)
    assert result is not None
    assert result.id == _UID
    assert len(result.messages) == 1
    assert result.messages[0].content == "reply"
    assert result.messages[0].role == MessageRole.ASSISTANT


# ---------------------------------------------------------------------------
# get_active_by_user
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_active_by_user_returns_none_when_not_found():
    """Lines 40-49: returns None when no conversation found for user."""
    sess = _session(_exec_result(scalar=None))
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.get_active_by_user(_USER_UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_active_by_user_returns_conversation_when_found():
    """Lines 40-50: found case → calls get_by_id internally."""
    cm = _conv_model()
    mm = _msg_model()
    sess = _session(
        _exec_result(scalar=cm),  # get_active query
        _exec_result(scalar=cm),  # get_by_id → ConversationModel
        _exec_result(scalars_list=[mm]),  # get_by_id → MessageModels
    )
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.get_active_by_user(_USER_UID)
    assert result is not None
    assert result.id == _UID


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_with_messages():
    """Lines 52-74: create adds ConversationModel + MessageModel(s), flushes."""
    conv = _conv()
    conv.messages = [
        Message(role=MessageRole.USER, content="Schedule a call"),
    ]
    sess = AsyncMock()
    sess.add = MagicMock()
    sess.flush = AsyncMock()
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.create(conv)
    assert result is conv
    sess.flush.assert_called_once()
    assert sess.add.call_count == 2  # ConversationModel + 1 MessageModel


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_with_no_messages():
    """Lines 52-74: create with empty messages list."""
    conv = _conv()
    sess = AsyncMock()
    sess.add = MagicMock()
    sess.flush = AsyncMock()
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.create(conv)
    assert result is conv
    sess.flush.assert_called_once()
    sess.add.assert_called_once()  # only the ConversationModel


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_patches_model_and_appends_new_messages():
    """Lines 76-107: found model, summary updated, new messages appended."""
    conv = _conv(summary="compressed summary")
    conv.messages = [
        Message(role=MessageRole.USER, content="old"),
        Message(role=MessageRole.ASSISTANT, content="new"),
    ]
    cm = _conv_model()
    existing_msg_id = uuid.uuid4()
    sess = _session(
        _exec_result(scalar=cm),  # select ConversationModel
        _exec_result(scalars_list=[existing_msg_id]),  # existing message IDs
    )
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.update(conv)
    assert result is conv
    assert cm.summary == "compressed summary"
    sess.flush.assert_called_once()
    # new messages beyond existing_count=1 → 1 add call
    assert sess.add.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_when_model_not_found():
    """Lines 76-107: model not found — still processes messages."""
    conv = _conv()
    conv.messages = []
    sess = _session(
        _exec_result(scalar=None),
        _exec_result(scalars_list=[]),
    )
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.update(conv)
    assert result is conv
    sess.flush.assert_called_once()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_returns_true_when_model_found():
    """Lines 109-123: delete messages, delete model, flush → True."""
    cm = _conv_model()
    sess = _session(
        _exec_result(),  # DELETE MessageModels
        _exec_result(scalar=cm),  # SELECT ConversationModel
    )
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.delete(_UID)
    assert result is True
    sess.delete.assert_called_once_with(cm)
    sess.flush.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_returns_false_when_model_not_found():
    """Lines 109-123: model not found → False, no delete called."""
    sess = _session(
        _exec_result(),  # DELETE MessageModels
        _exec_result(scalar=None),  # model not found
    )
    repo = SQLAlchemyConversationRepository(sess)
    result = await repo.delete(_UID)
    assert result is False
    sess.delete.assert_not_called()


# ---------------------------------------------------------------------------
# _to_entity  static method (lines 125-147)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_entity_maps_all_fields():
    """Lines 125-147: _to_entity builds a Conversation from model + messages."""
    cm = _conv_model()
    mm = _msg_model(role="user", content="test")
    conv = SQLAlchemyConversationRepository._to_entity(cm, [mm])
    assert conv.id == cm.id
    assert conv.user_id == cm.user_id
    assert len(conv.messages) == 1
    assert conv.messages[0].role == MessageRole.USER
    assert conv.messages[0].content == "test"
    assert conv.messages[0].token_count == 5


@pytest.mark.unit
def test_to_entity_with_no_messages():
    """Lines 125-147: _to_entity with empty message list."""
    cm = _conv_model()
    conv = SQLAlchemyConversationRepository._to_entity(cm, [])
    assert conv.messages == []
