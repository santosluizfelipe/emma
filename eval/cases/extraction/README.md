# Extraction cases

One YAML file per theme; each file is a list of self-contained cases. A case is a
visit (note, optional task comments, optional care-plan risks) and what the extractor
must and must not find. No database involved, so cases never go stale.

```yaml
- id: falls-near-fall-escalated          # unique
  note: "…"                               # the visit note
  tasks:                                  # optional: [{name, status, comment}]
    - {name: Medication, status: Completed, comment: "…"}
  known_risks: "falls"                    # optional care-plan context
  expect:                                 # every item must be found
    - {category: falls_mobility, severity: high, escalated: true}
    - {category: [skin_injury, safeguarding]}   # a list = any of these is correct
  forbid: [skin_injury]                   # categories that must NOT appear
```

`expect: []` means the visit must produce no signals. `severity` and `escalated` are
optional; when given they are scored too. Add a case whenever you find a miss or a
false positive, or a new way carers phrase something.
