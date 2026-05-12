# PDF Extraction (`bcli extract`)

Extract structured records from PDFs (scans, forms, tabular reports — vendor
invoices, packing slips, statements, anything tabular) via an AI vision
backend, then promote the result to Business Central through the existing
batch runner.

```
PDF + YAML schema
      │
      ▼
bcli extract  ──►  <pdf>.batch.yaml      ◄── operator reviews
                   <pdf>.extracted.json     against the source PDF
      │
      ▼
bcli batch run … --profile sandbox --dry-run
bcli batch run … --profile sandbox
      │ (verify in BC sandbox UI)
      ▼
bcli batch run … --profile production
```

The extraction layer never writes to BC. It produces files; humans
review; the existing `bcli batch run` machinery (with `disable_writes`,
production confirmation, audit log) handles the actual mutation. This
review step matters most when the extracted data has high blast radius
(regulated records, financial postings) — emitting batch.yaml + sidecar
instead of writing directly gives a deterministic, auditable review seam.

## Install

Pick a backend:

```bash
# Claude (Anthropic)
uv pip install -e ".[extract-claude]"
export ANTHROPIC_API_KEY=sk-ant-…

# OpenAI
uv pip install -e ".[extract-openai]"
export OPENAI_API_KEY=sk-…

# Both
uv pip install -e ".[extract]"
```

Then enable the backend in `~/.config/bcli/config.toml`. With the
defaults shown, just setting `backend` is enough — each backend fills
in its own model + env-var name:

```toml
[extract]
backend = "claude"     # or "openai"

# Optional overrides — leave blank to use backend-appropriate defaults:
#   claude:  model = "claude-sonnet-4-6", api_key_env = "ANTHROPIC_API_KEY"
#   openai:  model = "gpt-5",             api_key_env = "OPENAI_API_KEY"
# model = "claude-sonnet-4-6"
# api_key_env = "ANTHROPIC_API_KEY"
# schemas_dir = "~/.config/bcli/extract/schemas"
# max_pdf_bytes = 33554432    # 32 MiB
# max_pdf_pages = 100
# max_output_tokens = 8000
# openai_base_url = ""        # e.g. an Azure OpenAI / proxy endpoint
# openai_organization = ""    # OpenAI org id (optional)
```

Switching between backends is a one-line config change — schemas and
generated batch.yamls are backend-agnostic, so iterating on a
schema with one provider and running production with another is fine.

## Schemas

A schema is a YAML file that tells the backend *what* to extract and
*how* to map the result onto a BC endpoint. Drop one into
`~/.config/bcli/extract/schemas/` and `bcli extract list-schemas` picks
it up.

Minimal example:

```yaml
name: "Purchase invoice line items"
description: "One PDF = one invoice with many line items."
prompt: |
  Extract one record per line item in this vendor invoice. Skip the
  header row, subtotal, tax, and grand-total rows. If a row is
  illegible, OMIT it — do not guess.

list: true     # one PDF → many records

fields:
  item_no:
    type: string
    description: "Item number / SKU."
    required: true
  description:
    type: string
    description: "Item description."
    required: true
  quantity:
    type: number
    description: "Quantity ordered."
    required: true
  unit_price:
    type: number
    description: "Unit price."

output:
  endpoint: purchaseLines
  action: post
  parent_field: documentNo
  parent_param: invoice_no
  field_map:
    "no": item_no
    description: description
    quantity: quantity
    directUnitCost: unit_price
  constants:
    documentType: "Invoice"
    type: "Item"
```

`parent_param` + `parent_field` emit a `${{ params.invoice_no }}`
placeholder in the generated `batch.yaml`. The operator fills it in
before `batch run` (or passes `--set invoice_no=…`). This is the
intentional human-in-the-loop seam: extraction can't know which BC
record the rows belong to, so it asks.

See `examples/extract/purchase_invoice_lines.yaml` for the fully-worked
schema. Author your own under `~/.config/bcli/extract/schemas/` — one
YAML per document type.

## Use

```bash
# Drop the schema in the well-known location, or pass a path.
bcli extract list-schemas

# Run extraction. Emits two files next to the PDF.
bcli extract run ./invoice-acme-1234.pdf --schema purchase_invoice_lines

# Output:
#   invoice-acme-1234.batch.yaml        ← workflow to run
#   invoice-acme-1234.extracted.json    ← traceability sidecar

# Promote to sandbox (dry-run first, then real).
bcli batch run invoice-acme-1234.batch.yaml \
    --set invoice_no=<bc-invoice-number> \
    --profile sandbox --dry-run

bcli batch run invoice-acme-1234.batch.yaml \
    --set invoice_no=<bc-invoice-number> \
    --profile sandbox

# Eyeball in the BC sandbox UI, then production.
bcli batch run invoice-acme-1234.batch.yaml \
    --set invoice_no=<bc-invoice-number> \
    --profile production
```

## Traceability sidecar

Every `bcli extract` run drops `<pdf>.extracted.json` next to the
batch.yaml. It contains:

- The schema name + endpoint.
- The Claude model + token usage.
- Every record, including the raw model output and the 1-indexed PDF
  pages the values were read from.
- Any warnings (e.g. `list: false` schema with multiple records).

Reviewers open this side by side with the PDF to verify each value
before the batch runs. The sidecar is the deterministic, auditable
artifact your reviewer signs off on — never run the batch without
it for high-blast-radius data (regulated records, financial postings,
anything where a wrong identifier has real-world consequences).

## PDF size limits

Anthropic caps each document block at **32 MB** and **100 pages**.
`bcli extract` checks both before sending. If your PDF is too big:

```bash
# Split with qpdf (homebrew: brew install qpdf)
qpdf --split-pages=50 big_report.pdf split-%d.pdf

# Extract each split, then concatenate batch.yamls (or run them serially).
for f in split-*.pdf; do
    bcli extract run "$f" --schema <your_schema_slug>
done
```

Chunked-extract orchestration is a follow-up; the primitive is shipped
first.

## Pluggable backends

`[extract] backend` accepts:

- `"null"`   — no extraction (default). Returns an empty result with a warning.
- `"claude"` — Anthropic Claude (built-in, `[extract-claude]`).
- `"openai"` — OpenAI Responses API + Files API (built-in, `[extract-openai]`).
- `"my_pkg.module:MyExtractor"` — any class implementing
  `bcli.extract.ExtractorBackend`. The class needs `is_active`,
  `extract(pdf_path, schema)`, and a `from_config(cls, config)`
  classmethod. AWS Textract, Firecrawl, OpenDataLoader, a Vertex AI
  Gemini wrapper, or a self-hosted vision model all fit this shape.

Custom-backend failures fall back to `NullExtractor` with a one-shot
warning — extraction never crashes the CLI on a config mistake.

### Backend choice tips

- Both built-ins accept the same schema. Switching is a one-line
  config change; you can iterate a schema cheaply on one provider and
  promote with the other.
- Aviation/regulated data: pick the provider with the residency /
  compliance posture your org accepts. Neither built-in routes through
  Beautech infrastructure — your API key, your traffic.
- Cost: at time of writing, both providers price PDF input in the same
  ballpark for short documents. Long tabular reports tend to favor
  whichever provider has the cheaper input-token rate.

## Safety / regulated-data note

If the extracted data has real-world consequences (regulated records,
financial postings, anything where a wrong identifier matters), the
design enforces four reviews before bytes hit production:

1. **Sidecar review** — `extracted.json` against the source PDF, by a human.
2. **Sandbox dry-run** — `bcli batch run … --dry-run` against a non-prod profile.
3. **Sandbox write + UI verification** — `bcli batch run … --profile sandbox` then eyeball.
4. **Production** — only after the first three pass.

Skipping any of these defeats the design. The CLI doesn't enforce the
sequence (yet); the schema-author and the operator do.
