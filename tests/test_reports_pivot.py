"""Tests for app.services.reports — pivot_data() helper."""

from app.services.reports import pivot_data


class TestPivotData:
    """Tests for the pivot_data() utility."""

    def test_basic_sum(self) -> None:
        data = [
            {"dept": "Sales", "month": "Jan", "amount": 10},
            {"dept": "Sales", "month": "Feb", "amount": 20},
            {"dept": "Support", "month": "Jan", "amount": 5},
            {"dept": "Support", "month": "Feb", "amount": 15},
        ]
        result = pivot_data(data, "dept", "month", "amount")

        assert result["rows"] == ["Sales", "Support"]
        assert result["cols"] == ["Feb", "Jan"]
        assert result["values"]["Sales"]["Jan"] == 10
        assert result["values"]["Sales"]["Feb"] == 20
        assert result["values"]["Support"]["Jan"] == 5
        assert result["values"]["Support"]["Feb"] == 15

    def test_totals(self) -> None:
        data = [
            {"dept": "Sales", "month": "Jan", "amount": 10},
            {"dept": "Sales", "month": "Feb", "amount": 20},
            {"dept": "Support", "month": "Jan", "amount": 5},
        ]
        result = pivot_data(data, "dept", "month", "amount")

        assert result["totals"]["row_totals"]["Sales"] == 30
        assert result["totals"]["row_totals"]["Support"] == 5
        assert result["totals"]["col_totals"]["Jan"] == 15
        assert result["totals"]["col_totals"]["Feb"] == 20
        assert result["totals"]["grand_total"] == 35

    def test_count_aggregation(self) -> None:
        data = [
            {"status": "open", "channel": "email", "id": 1},
            {"status": "open", "channel": "email", "id": 2},
            {"status": "open", "channel": "sms", "id": 3},
            {"status": "closed", "channel": "email", "id": 4},
        ]
        result = pivot_data(data, "status", "channel", "id", agg="count")

        assert result["values"]["open"]["email"] == 2
        assert result["values"]["open"]["sms"] == 1
        assert result["values"]["closed"]["email"] == 1
        assert result["values"]["closed"]["sms"] == 0

    def test_avg_aggregation(self) -> None:
        data = [
            {"agent": "Alice", "day": "Mon", "score": 80},
            {"agent": "Alice", "day": "Mon", "score": 90},
            {"agent": "Bob", "day": "Mon", "score": 70},
        ]
        result = pivot_data(data, "agent", "day", "score", agg="avg")

        assert result["values"]["Alice"]["Mon"] == 85.0
        assert result["values"]["Bob"]["Mon"] == 70.0

    def test_empty_input(self) -> None:
        result = pivot_data([], "a", "b", "c")

        assert result["rows"] == []
        assert result["cols"] == []
        assert result["values"] == {}
        assert result["totals"]["grand_total"] == 0.0

    def test_missing_values_default_to_zero(self) -> None:
        data = [
            {"dept": "Sales", "month": "Jan", "amount": 10},
            {"dept": "Support", "month": "Feb", "amount": 5},
        ]
        result = pivot_data(data, "dept", "month", "amount")

        # Sparse cells should be 0
        assert result["values"]["Sales"]["Feb"] == 0
        assert result["values"]["Support"]["Jan"] == 0

    def test_none_values_treated_as_zero(self) -> None:
        data = [
            {"dept": "Sales", "month": "Jan", "amount": None},
            {"dept": "Sales", "month": "Jan", "amount": 10},
        ]
        result = pivot_data(data, "dept", "month", "amount")

        assert result["values"]["Sales"]["Jan"] == 10

    def test_duplicate_rows_summed(self) -> None:
        data = [
            {"dept": "Sales", "month": "Jan", "amount": 10},
            {"dept": "Sales", "month": "Jan", "amount": 5},
        ]
        result = pivot_data(data, "dept", "month", "amount")

        assert result["values"]["Sales"]["Jan"] == 15
