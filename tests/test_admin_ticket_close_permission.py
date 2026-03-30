from app.web.admin.tickets import _can_manage_ticket_relationships


def test_can_manage_ticket_relationships_allows_close_permission_without_agent_role():
    current_user = {
        "roles": ["spc"],
        "permissions": ["support:ticket:update", "support:ticket:close"],
    }

    assert _can_manage_ticket_relationships(current_user) is True


def test_can_manage_ticket_relationships_denies_spc_without_close_permission():
    current_user = {
        "roles": ["spc"],
        "permissions": ["support:ticket:update"],
    }

    assert _can_manage_ticket_relationships(current_user) is False
