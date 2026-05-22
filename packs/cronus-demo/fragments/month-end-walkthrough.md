## Month-end walkthrough (bcli cronus-demo pack)

The `month-end-cronus.yaml` batch is the canonical "drive bcli end
to end" demo. Walk an operator (or another agent) through it like
this:

### 1. Preview without making API calls

```bash
bcli batch run month-end-cronus.yaml \
  --set customer_name=Adatum \
  --set month=2026-03 \
  --dry-run
```

The dry-run resolves every parameter, expands step-chained
references (`${{ steps.find_customer.0.id }}`), and prints the HTTP
requests it would issue. No network traffic; safe on read-only
profiles.

### 2. Execute and capture a JSON bundle

```bash
bcli batch run month-end-cronus.yaml \
  --set customer_name=Adatum \
  --set month=2026-03 \
  -o month-end-adatum.json
```

The `-o` flag writes a single JSON document containing every step's
result envelope — useful as an attachment to a review email or as
input for `bcli ask` (Part 2 of the pack/ask plan).

### 3. Try different customers

CRONUS ships with Adatum, Trey, Fabrikam, Relecloud, and School of
Fine Art as the headline customers. Each has distinct invoice /
payment patterns — Adatum is the simplest, School is the most
complex (multi-currency).

### Where step chaining shines

The batch's `find_customer` step returns a list. Downstream steps
reference `${{ steps.find_customer.0.id }}` to pluck the first hit's
GUID — that's the pattern any "find then drill in" workflow uses.
