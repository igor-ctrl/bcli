# Batch Operations

Execute sequences of API calls from YAML files.

## Usage

```bash
bcli batch run operations.yaml
bcli batch run operations.yaml --dry-run
```

## Batch File Format

```yaml
name: "Monthly Engine Utilization Upload"
steps:
  - action: get
    endpoint: engineOverviews
    params:
      filter: "engineModel eq 'CF34-10E'"
      top: 5

  - action: post
    endpoint: engineUtilizations
    data:
      esn: "ESN-123456"
      period: "2026-03"
      flightHours: 350.5
      flightCycles: 210

  - action: post
    endpoint: engineUtilizations
    data:
      esn: "ESN-789012"
      period: "2026-03"
      flightHours: 280.0
      flightCycles: 175

  - action: patch
    endpoint: engineCards
    id: "a1b2c3d4-..."
    data:
      status: "Available"

  - action: delete
    endpoint: tempRecords
    id: "e5f6a7b8-..."
```

## Step Actions

### GET

```yaml
- action: get
  endpoint: customers
  params:
    filter: "city eq 'Chicago'"
    select: "displayName,email"
    top: 10
    orderby: "displayName asc"
```

### POST

```yaml
- action: post
  endpoint: customers
  data:
    displayName: "New Customer"
    email: "new@example.com"
```

### PATCH

```yaml
- action: patch
  endpoint: customers
  id: "a1b2c3d4-..."
  data:
    email: "updated@example.com"
  etag: "*"  # optional, defaults to "*"
```

### DELETE

```yaml
- action: delete
  endpoint: tempRecords
  id: "e5f6a7b8-..."
```

## Dry Run

Preview all steps without executing:

```bash
bcli batch run operations.yaml --dry-run
```

Output:
```
Batch: Monthly Engine Utilization Upload
3 step(s)

  Step 1: GET engineOverviews
    Params: {'filter': "engineModel eq 'CF34-10E'", 'top': 5}
  Step 2: POST engineUtilizations
    Data: {"esn": "ESN-123456", ...}
  Step 3: POST engineUtilizations
    Data: {"esn": "ESN-789012", ...}

--dry-run: 3 step(s) would execute.
```

## Error Handling

If a step fails, the error is reported and subsequent steps continue:

```
  Step 1: GET engineOverviews... ✓ 5 record(s)
  Step 2: POST engineUtilizations... ✓ created
  Step 3: POST engineUtilizations... ✗ HTTP 400: Duplicate record

✓ Batch complete: 2/3 steps succeeded
```
