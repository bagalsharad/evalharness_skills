# summary.json format

```json
{
  "rows": 3,
  "columns": {
    "price": {"type": "number", "min": 1.5, "max": 9.0, "mean": 4.83, "missing": 0},
    "name":  {"type": "text", "distinct": 3, "missing": 0}
  }
}
```

A column is `number` when every non-empty value parses as a float. Means are rounded to 2 decimals.
