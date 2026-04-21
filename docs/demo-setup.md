# Demo Setup (CRONUS)

A zero-to-demo walkthrough for standing up a free BC sandbox preloaded with
Microsoft's **CRONUS** demo company, then running `bcli` against it. Useful for
talks, conference demos, and anyone evaluating bcli before pointing it at a
real tenant.

## 1. Get a CRONUS-loaded BC sandbox

Three options — pick one.

| Option | Cost | Renewable | Notes |
|--------|------|-----------|-------|
| **Microsoft 365 Developer Program** (recommended) | Free | Yes | Best for repeat demos. Sign up at [developer.microsoft.com/microsoft-365/dev-program](https://developer.microsoft.com/microsoft-365/dev-program), then provision a BC sandbox from the BC admin center. CRONUS is preloaded. |
| **Business Central 30-day trial** | Free | No | Sign up at [dynamics.microsoft.com/business-central](https://dynamics.microsoft.com/business-central/overview/). CRONUS preloaded. Clock starts immediately. |
| **Existing tenant sandbox** | Free | — | If you already have a BC tenant, its `Sandbox` environment typically has CRONUS. Cheapest but couples the demo to prod infra. |

After provisioning, confirm you can sign into the BC web client and see
**CRONUS USA, Inc.** (or regional variants: CRONUS International Ltd., CRONUS
FR, CRONUS DE, etc.).

## 2. Register an Azure AD app

You'll typically want two app registrations — one for automation (service-to-service)
and one for interactive browser demos.

### App A — `bcli-demo-daemon` (client_credentials)

For `bcli etl sync`, CI/CD demos, and automation.

1. Azure portal -> Entra ID -> App registrations -> **New registration**
2. Name: `bcli-demo-daemon`, single tenant, no redirect URI
3. **Certificates & secrets** -> New client secret -> copy it (you won't see it again)
4. **API permissions** -> Add -> Dynamics 365 Business Central -> **Application permissions** -> `app_access`, `Automation.ReadWrite.All` -> **Grant admin consent**

Then grant the app in BC itself:

1. Sign into BC -> search **Microsoft Entra Applications** page
2. **New** -> paste the app's client ID, set State = Enabled
3. Add permission set `D365 FULL ACCESS` (or narrower for a read-only demo, e.g. `D365 READ`)

### App B — `bcli-demo-interactive` (PKCE / browser)

For the live browser-login demo.

1. **New registration** — name `bcli-demo-interactive`, single tenant
2. **Redirect URI** = `http://localhost:8400/callback` (Public client/native)
3. **Authentication** -> enable **Allow public client flows** = Yes
4. **API permissions** -> Add -> Dynamics 365 Business Central -> **Delegated permissions** -> `Financials.ReadWrite.All`, `user_impersonation` -> **Grant admin consent**

No client secret needed — PKCE handles it.

## 3. Configure bcli

Stash the daemon secret in your OS keychain:

```bash
export BCLI_SECRET='<paste daemon client secret>'
bcli auth store-secret   # pulls from BCLI_SECRET into the keychain
unset BCLI_SECRET
```

Create two profiles — one per app. Swap tenant/client IDs with your own.

```bash
# Automation profile — client_credentials
bcli config set profiles.cronus-daemon.auth_method client_credentials
bcli config set profiles.cronus-daemon.tenant_id "<tenant-guid>"
bcli config set profiles.cronus-daemon.client_id "<daemon-app-client-id>"
bcli config set profiles.cronus-daemon.environment "Sandbox"

# Interactive profile — browser/PKCE
bcli config set profiles.cronus-live.auth_method browser
bcli config set profiles.cronus-live.tenant_id "<tenant-guid>"
bcli config set profiles.cronus-live.client_id "<interactive-app-client-id>"
bcli config set profiles.cronus-live.environment "Sandbox"
```

Authenticate and discover the CRONUS company id:

```bash
bcli -p cronus-daemon auth login --method client_credentials
bcli -p cronus-daemon env list-companies
# -> copy the CRONUS USA, Inc. company id
bcli config set profiles.cronus-daemon.company_id "<cronus-company-id>"
bcli config set profiles.cronus-live.company_id   "<cronus-company-id>"
```

Pre-warm the interactive profile too so the demo doesn't gate on a browser
redirect:

```bash
bcli -p cronus-live auth login --method browser
```

## 4. The golden-path demo

The eight-minute live sequence:

```bash
# --- Setup shown up front ---
bcli --version
bcli config list

# --- Standard v2.0 queries — zero config required ---
bcli -p cronus-live get customers --top 5
bcli -p cronus-live get customers --filter "country eq 'US'" -f table
bcli -p cronus-live get salesInvoices --select number,customerName,totalAmountIncludingTax --top 10

# --- The SDK / agent story ---
bcli ai-context                          # LLM-ready usage instructions
bcli endpoint list | head -20            # 79 standard entities

# --- Batch workflow — params + step chaining + result capture ---
bcli -p cronus-daemon batch run examples/month-end-cronus.yaml \
  --set customer_name=Adatum --set month=2026-03 --dry-run

bcli -p cronus-daemon batch run examples/month-end-cronus.yaml \
  --set customer_name=Adatum --set month=2026-03 \
  -o /tmp/review.json -f table

jq '.[] | {step: .id, count: .record_count}' /tmp/review.json

# --- ETL pipeline — one command to a local warehouse ---
bcli -p cronus-daemon etl sync \
  --destination duckdb --entities customers,vendors,salesInvoices

duckdb bc_raw.duckdb "SELECT COUNT(*) FROM customers;"
```

## 5. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `AADSTS700016: Application not found in directory` | App registration is in a different tenant. Double-check `tenant_id` in the profile. |
| `401 Unauthorized` from BC with a valid token | You missed the **Microsoft Entra Applications** step in BC — the app registration exists in Azure AD but isn't granted in BC. |
| Browser auth redirects to `localhost:8400` but nothing happens | Port 8400 is already in use (e.g. another bcli login in flight). Kill the stale process. |
| `Company not found` | `bcli env list-companies` to get the exact id for CRONUS; update the profile. |
| Demo Wi-Fi dies mid-talk | Have a screencast of the golden path as backup. Tokens are cached locally so read demos can survive brief network blips. |

## 6. Cleanup after the demo

```bash
bcli auth logout              # clear token cache
bcli auth delete-secret       # remove keychain entry
bcli config delete profiles.cronus-daemon
bcli config delete profiles.cronus-live
```

If the demo tenant was created just for this event, decommission it from the
M365 Developer Program dashboard to avoid the renewal ping emails.
