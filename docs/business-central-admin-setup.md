# Business Central Admin Setup

This guide is for users who do not already have an Entra app registration or
Business Central API permissions ready for bcli.

## Recommended Local Setup: Browser Auth

Use this path for humans and local AI agents. It needs no client secret.

### 1. Create An Entra App Registration

1. Open the Microsoft Entra admin center.
2. Go to **Identity** -> **Applications** -> **App registrations**.
3. Select **New registration**.
4. Name it something clear, such as `bcli-local`.
5. Choose the supported account type for your tenant.
6. Register the app.
7. Copy the **Application (client) ID** and **Directory (tenant) ID**.

### 2. Configure Browser Redirect

1. Open the app registration.
2. Go to **Authentication**.
3. Add a platform: **Mobile and desktop applications**.
4. Add redirect URI: `http://localhost`.
5. Save.

bcli binds an available localhost port at login time. Entra accepts localhost
redirects for native clients without requiring one fixed port.

### 3. Add Business Central API Permission

1. Open **API permissions**.
2. Select **Add a permission**.
3. Choose **Dynamics 365 Business Central**.
4. Choose **Delegated permissions**.
5. Add the Business Central delegated permission your tenant requires, commonly
   `user_impersonation` or `Financials.ReadWrite.All`.
6. Grant admin consent if your tenant requires it.

### 4. Assign Business Central Permissions

The browser token carries the signed-in user. Business Central still decides
what that user can see or change.

1. Open Business Central.
2. Search for **Users**.
3. Open the user who will run bcli.
4. Assign the required permission sets for the companies and pages they need.
5. Start read-only when possible, then add write permissions deliberately.

### 5. Configure bcli

```bash
bcli config init
```

Use:

- Tenant ID: the Directory tenant ID from Entra.
- Environment: `Production`, `Sandbox`, or your BC environment name.
- Client ID: the Application client ID from Entra.
- Auth method: browser auth is the default.

When bcli asks to authenticate, accept. It opens a browser, completes Microsoft
sign-in, discovers companies, and lets you choose a default company.

### 6. Verify

```bash
bcli test connection
bcli get customers --top 5
```

If this fails with `403 Forbidden`, authentication worked but Business Central
permissions are missing for that user.

## Automation Setup: Client Credentials

Use this path for CI/CD, servers, and scheduled jobs.

### 1. Create Or Reuse A Confidential App

Create an Entra app registration for automation and add Business Central
**application** permissions. Generate a client secret or certificate according
to your organization's policy.

### 2. Grant Consent And BC Access

Grant admin consent for the application permission. In Business Central, ensure
the application identity has the required API access and permission sets for the
target companies.

### 3. Configure bcli

```bash
bcli config init --automation
bcli auth store-secret
```

For CI, store the secret in your pipeline secret manager and expose it as
`BCLI_SECRET` or the `client_secret_env` name you chose during setup.

## Headless Fallback: Device Code

Use device code only when browser callback auth cannot work, such as SSH hosts.

```bash
bcli config init --headless
bcli auth login --method device
```

## Troubleshooting

| Error | Meaning | Fix |
|-------|---------|-----|
| Redirect URI mismatch | Entra does not allow the localhost callback | Add `http://localhost` under Mobile and desktop applications |
| Consent required | Tenant policy blocks unconsented API permissions | Ask an admin to grant consent |
| 403 Forbidden | BC rejected the user or app authorization | Assign the right BC permission sets and company access |
| Wrong account | Browser reused another Microsoft login | Run `bcli auth login --incognito` |
| Secret missing | Automation profile cannot find a secret | Run `bcli auth store-secret` or set the configured env var |
