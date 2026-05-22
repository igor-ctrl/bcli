## Common BC errors and how to read them (bcli starter pack)

bcli surfaces every error with a remediation hint where one is
known. The most common categories:

### 400 — Bad request (`ValidationError`)

- "**The filter is malformed**" → run `bcli endpoint fields <name>`
  to list real field names, then retry. The pre-flight validator
  suggests close matches.
- "**…cannot be resolved**" → check the entity name with
  `bcli endpoint search`. Custom endpoints need the profile that
  imported them; standard ones (`vendors`, `customers`, …) work
  everywhere.

### 401 — Authentication failure (`AuthError`)

- "**AADSTS70011: invalid scope**" → the API permission on the
  client app is wrong. Use `bcli auth check` to see which permission
  is being requested vs granted.
- "**token expired**" → just retry; bcli refreshes automatically. If
  it keeps failing, `bcli auth purge` clears the cached token.

### 403 — Forbidden (`ForbiddenError`)

The token is valid but the BC user doesn't have permission for that
entity. Adding a permission set is a BC admin task — bcli can't fix
this. The error message usually names the missing permission.

### 404 — Not found (`NotFoundError`)

- Wrong company id → `bcli company list` then `bcli company use X`.
- The record really doesn't exist.

### 429 / 503 — Rate limiting / server hiccups (`ThrottledError` / `ServerError`)

bcli retries automatically (up to 3 with exponential backoff). If you
see this surface to the user, BC is genuinely throttling — wait a
few seconds and retry.

### Where to look next

```bash
# Replay the last error with full HTTP detail
bcli --debug <prev command>

# bcli leaves a redacted snapshot of every failure here for the
# `bcli ask` reflex command (Part 2):
cat ~/.config/bcli/last-error.json
```
