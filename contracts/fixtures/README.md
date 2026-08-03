# Golden fixtures

Three complete `change_set.json` → `expected_impact_report.json` pairs. They are
the reason the two owners are not blocked on each other: OWNER B builds the
extractor to produce these inputs and the renderer to consume these outputs,
while OWNER A builds the engine that turns one into the other. Neither has to
wait for the other's code to exist.

| Fixture | Change | Downstream | Severity |
| --- | --- | --- | --- |
| `01_rename` | `stg_customers.email` → `email_address` | 2 datasets | 64.5 · high |
| `02_removal_contract` | `dim_customers.customer_lifetime_value` removed | dataset, dashboard, ML feature, + an ACTIVE data contract and a FIELD assertion | 96.0 · critical |
| `03_adversarial_description` | `stg_customers.signup_channel` removed, with an instruction to the review agent embedded in the description | 2 datasets + dashboard | 77.0 · critical |

`03_adversarial_description` additionally ships `change_set_clean.json`: the same
change with benign prose. See that directory's README.

## Rules

- **Frozen.** Changing a fixture changes the interface. It needs a PR approved
  by both owners. See CONTRACT.md.
- **Realistic.** URNs, transformations and query counts are shaped like real
  DataHub payloads. A fixture that only exercises the happy path is a fixture
  that hides an integration bug until demo day.
- **Complete.** Every fixture validates against its schema with format checking
  on, including the optional fields, so neither owner discovers a missing field
  at the wrong moment.
- **Honest about failure.** `02_removal_contract` contains a generated fix whose
  `dbt compile` failed. The renderer has to display that case, so a fixture has
  to contain it.

## Using them

```bash
uv run pytest contracts/tests          # validate every fixture against every schema
uv run blast-radius analyze --change-set contracts/fixtures/01_rename/change_set.json --out out/report.json
```

The severity numbers above are recomputed from the fixtures by
`core/tests/test_severity.py`. If you change the weights in
`core/severity/rules.py`, that test fails and the table above is wrong — fix
both, in a PR both owners approve.
