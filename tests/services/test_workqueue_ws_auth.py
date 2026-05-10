"""Unit tests for the workqueue WS subscription auth helper."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.websocket.workqueue_router import (
    WORKQUEUE_ORG_CHANNEL,
    is_subscription_allowed,
)


def test_user_can_subscribe_to_own_user_channel():
    user_id = str(uuid4())
    assert is_subscription_allowed(
        user_id=user_id,
        permissions=set(),
        channel=f"workqueue:user:{user_id}",
    )


def test_user_cannot_subscribe_to_other_users_channel():
    user_id = str(uuid4())
    other_id = str(uuid4())
    assert not is_subscription_allowed(
        user_id=user_id,
        permissions={"workqueue:audience:org"},
        channel=f"workqueue:user:{other_id}",
    )


def test_team_channel_requires_team_permission():
    user_id = str(uuid4())
    team_id = str(uuid4())
    chan = f"workqueue:audience:team:{team_id}"
    assert is_subscription_allowed(
        user_id=user_id,
        permissions={"workqueue:audience:team"},
        channel=chan,
    )
    assert not is_subscription_allowed(
        user_id=user_id, permissions=set(), channel=chan
    )


def test_org_channel_requires_org_permission():
    user_id = str(uuid4())
    assert is_subscription_allowed(
        user_id=user_id,
        permissions={"workqueue:audience:org"},
        channel=WORKQUEUE_ORG_CHANNEL,
    )
    assert not is_subscription_allowed(
        user_id=user_id,
        permissions={"workqueue:audience:team"},
        channel=WORKQUEUE_ORG_CHANNEL,
    )


@pytest.mark.parametrize(
    "channel",
    [
        "",
        "inbox_ws:user:1234",
        "workqueue:user:",
        "random:channel",
        "workqueue:audience:other",
    ],
)
def test_unknown_or_malformed_channels_are_denied(channel):
    assert not is_subscription_allowed(
        user_id=str(uuid4()),
        permissions={"workqueue:audience:org", "workqueue:audience:team"},
        channel=channel,
    )


def test_empty_user_id_denies_user_channel():
    user_id = str(uuid4())
    assert not is_subscription_allowed(
        user_id="",
        permissions=set(),
        channel=f"workqueue:user:{user_id}",
    )
