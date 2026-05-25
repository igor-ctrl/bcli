## CRONUS demo tenant orientation (bcli cronus-demo pack)

Microsoft ships a "Cronus" demo dataset on every fresh BC sandbox.
This pack assumes it's loaded — if you see "no companies returned"
or zero CRONUS customers, the tenant is empty and the demo queries
won't return data.

### Recognising a CRONUS-loaded tenant

```bash
# Companies named "CRONUS UK" / "CRONUS USA" / "CRONUS International Ltd"
bcli env list-companies

# Demo customers — Adatum, Trey Research, Fabrikam, Relecloud, School of Fine Art
bcli get customers --filter "startswith(displayName, 'Adatum')" -f table
```

### Entities the demo workflow touches

- `customers` — Adatum / Trey / Fabrikam are the usual reviewer
  targets
- `salesInvoices` — postings tagged with the `month` parameter
- `customerPayments` — payment lines linked back via `invoiceId`
- `vendors` + `purchaseOrders` — for the AP side of month-end

### Where this pack came from

Imported from `examples/month-end-cronus.yaml` and
`examples/queries/sample.yaml` in the bcli repo. The original files
remain in `examples/` for source-level reference.
