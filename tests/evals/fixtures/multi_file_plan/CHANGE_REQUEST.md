# Change Request

Add optional `status` filtering to the catalog listing flow.

## Expected Behavior

- `catalog.list_items(...)` should accept an optional `status` filter.
- `service.list_catalog(...)` should pass the filter through when present.
- Existing callers without a filter should keep current behavior.
- Tests should cover the default path plus a filtered path for `"active"` items.

## Constraints

- Keep the change minimal.
- No database layer exists yet; the feature is in-memory only.
- Preserve current item ordering.
