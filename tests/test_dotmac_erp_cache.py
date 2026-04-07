from app.services.dotmac_erp.cache import ExpenseTotals


def test_expense_totals_from_dict_coerces_string_amounts():
    totals = ExpenseTotals.from_dict(
        {
            "draft": "1,234.50",
            "submitted": "200",
            "approved": "",
            "paid": None,
            "erp_available": True,
        }
    )

    assert totals.draft == 1234.5
    assert totals.submitted == 200.0
    assert totals.approved == 0.0
    assert totals.paid == 0.0
    assert totals.erp_available is True
    assert totals.cached is True
