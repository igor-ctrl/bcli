# bcli-site

Astro + Tailwind landing site for [bcli](https://github.com/igor-ctrl/bcli).
Single page (v0): hero + install + example commands + features.

## Development

```bash
cd bcli-site/
corepack enable          # ensures pnpm is on PATH
pnpm install             # one-time
pnpm dev                 # http://localhost:4321
pnpm build               # static output → dist/
pnpm preview             # preview the built site
```

## Deploy

Intended target is Vercel; the project root in Vercel should be set
to `bcli-site/`. The GH workflow at `.github/workflows/site.yml`
performs a build + (commented-out) deploy step — uncomment and add
`VERCEL_TOKEN` + `VERCEL_PROJECT_ID` + `VERCEL_ORG_ID` to the repo
secrets to enable auto-deploy from `main`.

## Structure

```
bcli-site/
  astro.config.mjs        # Astro + Tailwind
  tailwind.config.mjs     # palette + fonts (matches the terminal-y bcli vibe)
  tsconfig.json           # extends astro/tsconfigs/strict
  src/
    pages/index.astro     # hero + install + 3 examples + feature grid
    components/
      Hero.astro
      CodeBlock.astro
    styles/global.css     # Tailwind base + small layer overrides
  public/
    og.png.placeholder    # TODO: real OG card
```

## Content rules (matches Part 3 / R9)

The copy must describe what's actually shipped — packs, ask, MCP
server, describe. Do NOT oversell the `bcli agent` mode (deferred
to Part 4). Once the agent loop lands, add a section here.
