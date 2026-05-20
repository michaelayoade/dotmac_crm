from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.workqueue.permissions import (
    can_act_on_item,
    has_workqueue_view,
    resolve_audience,
)
from app.services.workqueue.types import WorkqueueAudience


def _user(*permissions: str, person_id=None):
    return SimpleNamespace(person_id=person_id or uuid4(), permissions=set(permissions), roles=set())


def _admin(*permissions: str, person_id=None):
    return SimpleNamespace(person_id=person_id or uuid4(), permissions=set(permissions), roles={"admin"})


def test_default_audience_is_self():
    assert resolve_audience(_user("workqueue:view")) is WorkqueueAudience.self_


def test_team_permission_resolves_to_team():
    assert resolve_audience(_user("workqueue:view", "workqueue:audience:team")) is WorkqueueAudience.self_


def test_org_outranks_team():
    assert (
        resolve_audience(_user("workqueue:view", "workqueue:audience:team", "workqueue:audience:org"))
        is WorkqueueAudience.self_
    )


def test_explicit_team_permission_resolves_to_team():
    assert resolve_audience(_user("workqueue:view", "workqueue:audience:team"), "team") is WorkqueueAudience.team


def test_explicit_org_outranks_team():
    assert (
        resolve_audience(_user("workqueue:view", "workqueue:audience:team", "workqueue:audience:org"), "org")
        is WorkqueueAudience.org
    )


def test_admin_role_can_request_org_audience_without_org_permission():
    assert resolve_audience(_admin("workqueue:view"), "org") is WorkqueueAudience.org


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("self", WorkqueueAudience.self_),
        ("team", WorkqueueAudience.team),
        ("org", WorkqueueAudience.org),
        ("garbage", WorkqueueAudience.org),
    ],
)
def test_explicit_downscope(requested, expected):
    user = _user("workqueue:view", "workqueue:audience:team", "workqueue:audience:org")
    assert resolve_audience(user, requested) is expected


def test_cannot_upscope_via_query_param():
    user = _user("workqueue:view")
    assert resolve_audience(user, "team") is WorkqueueAudience.self_


def test_has_workqueue_view():
    assert has_workqueue_view(_user("workqueue:view")) is True
    assert has_workqueue_view(_user()) is False


def test_can_act_on_item_self_assigned():
    user = _user("workqueue:view", person_id=uuid4())
    assert can_act_on_item(user, item_assignee_id=user.person_id, audience=WorkqueueAudience.self_) is True


def test_can_act_on_item_self_not_assigned():
    user = _user("workqueue:view")
    assert can_act_on_item(user, item_assignee_id=uuid4(), audience=WorkqueueAudience.self_) is False


def test_can_act_on_item_team_or_org():
    user = _user("workqueue:view", "workqueue:audience:team")
    assert can_act_on_item(user, item_assignee_id=uuid4(), audience=WorkqueueAudience.team) is True

    org_user = _user("workqueue:view", "workqueue:audience:org")
    assert can_act_on_item(org_user, item_assignee_id=None, audience=WorkqueueAudience.org) is True


def test_can_act_on_item_unassigned_with_claim_perm():
    user = _user("workqueue:view", "workqueue:claim", "workqueue:audience:team")
    assert can_act_on_item(user, item_assignee_id=None, audience=WorkqueueAudience.team) is True
