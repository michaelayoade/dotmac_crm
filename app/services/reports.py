"""Report data helpers — pivot, cross-tab, and aggregation utilities.

Provides ``pivot_data()`` for transforming flat query results into the
shape expected by the ``pivot_table()`` Jinja2 macro.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def pivot_data(
    rows: list[dict[str, Any]],
    row_key: str,
    col_key: str,
    value_key: str,
    agg: str = "sum",
) -> dict[str, Any]:
    """Transform flat query results into pivot_table() macro format.

    Parameters
    ----------
    rows:
        List of dicts from a query (e.g. ``[{"dept": "Sales", "month": "Jan", "amount": 10}, ...]``).
    row_key:
        Dict key whose values become row headers.
    col_key:
        Dict key whose values become column headers.
    value_key:
        Dict key containing the numeric value to aggregate.
    agg:
        Aggregation function — ``"sum"``, ``"count"``, or ``"avg"``.

    Returns
    -------
    dict with keys:
        - ``rows``: sorted unique row labels
        - ``cols``: sorted unique column labels
        - ``values``: ``{row_label: {col_label: aggregated_value, ...}, ...}``
        - ``totals``: ``{"row_totals": {...}, "col_totals": {...}, "grand_total": N}``

    Example
    -------
    >>> data = [
    ...     {"dept": "Sales", "month": "Jan", "count": 12},
    ...     {"dept": "Sales", "month": "Feb", "count": 15},
    ...     {"dept": "Support", "month": "Jan", "count": 8},
    ... ]
    >>> result = pivot_data(data, "dept", "month", "count")
    >>> result["values"]["Sales"]["Jan"]
    12
    """
    # Collect raw values per cell
    cells: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    row_labels: set[str] = set()
    col_labels: set[str] = set()

    for row in rows:
        r = str(row.get(row_key, ""))
        c = str(row.get(col_key, ""))
        v = row.get(value_key, 0)
        try:
            v = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            v = 0.0
        cells[r][c].append(v)
        row_labels.add(r)
        col_labels.add(c)

    sorted_rows = sorted(row_labels)
    sorted_cols = sorted(col_labels)

    # Aggregate
    values: dict[str, dict[str, float]] = {}
    for r in sorted_rows:
        values[r] = {}
        for c in sorted_cols:
            cell_values = cells[r][c]
            if not cell_values:
                values[r][c] = 0
            elif agg == "count":
                values[r][c] = len(cell_values)
            elif agg == "avg":
                values[r][c] = round(sum(cell_values) / len(cell_values), 2)
            else:  # sum
                values[r][c] = round(sum(cell_values), 2)

    # Compute totals
    row_totals: dict[str, float] = {}
    col_totals: dict[str, float] = {}
    grand_total = 0.0

    for r in sorted_rows:
        row_totals[r] = round(sum(values[r].get(c, 0) for c in sorted_cols), 2)

    for c in sorted_cols:
        col_totals[c] = round(sum(values[r].get(c, 0) for r in sorted_rows), 2)

    grand_total = round(sum(row_totals.values()), 2)

    return {
        "rows": sorted_rows,
        "cols": sorted_cols,
        "values": values,
        "totals": {
            "row_totals": row_totals,
            "col_totals": col_totals,
            "grand_total": grand_total,
        },
    }
