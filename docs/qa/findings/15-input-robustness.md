# Findings — Query-param / input robustness

Probed list endpoints with hostile/malformed query params (as an authenticated
user). **No 500s** — all handled gracefully.

| Input | Result | Verdict |
|-------|--------|---------|
| `order_by=DROP;--` | 200 (ignored) | ✅ allow-list neutralizes SQLi |
| `order_by=password&order_dir=sideways` | 200 | ✅ invalid column/dir ignored |
| `page=999999999` (out of range) | 200, empty | ✅ |
| `page=abc` (non-int) | 422 | ✅ type validation |
| `per_page=999999` / `per_page=-5` | 422 | ✅ `ge=10,le=100` enforced |
| `is_active=notabool` | 200 (ignored) | ✅ |
| `party_status=bogus_enum` | 400 | ✅ `validate_enum` rejects |
| `inventory?tab=../../etc/passwd` | 200 (ignored) | ✅ no path traversal |

**Conclusion:** Input validation on list endpoints is robust — `apply_ordering`'s
allow-list defeats `order_by` SQL-injection attempts, Pydantic/Query bounds reject
bad pagination (422), `validate_enum` rejects bad enum filters (400), and unknown
params are ignored rather than crashing. This corroborates the CLAUDE.md rules
(`apply_ordering`, `validate_enum`, `coerce_uuid`) being applied consistently.

(Errors returned as JSON to the browser is the same content-negotiation note as
NOTE-052 / NOTE-132, but the status codes themselves are correct.)
