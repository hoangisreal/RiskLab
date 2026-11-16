# RiskLab — documentation and evidence

RiskLab is an ML Engineer portfolio project for simulated credit decision support:
Manager import → worker scoring → Analyst proposal → Manager simulated decision.
The model estimates payment difficulties on Home Credit data; the results do not
establish real lending effectiveness or generalization over time.

| Topic | Documentation |
|---|---|
| Run the synthetic application and understand its limits | [Project README](../README.md) |
| Published metrics and experiments E1–E5 | [RESULTS](RESULTS.md) |
| Artifact, population, subgroups and Test-opening history | [Model card](model_card.md) |
| ML/scoring operations and reproduction | [Operations](OPERATIONS.md) |
| Leakage controls and historical evidence | [Leakage controls](archive/leakage.md) |
| Fresh reproduction and portfolio research | [Portfolio verification](portfolio/verification.md) |
| Extended model comparison | [Model comparison](portfolio/model-comparison.md) |
| Source identity after GitHub cleanup | [Source revisions](portfolio/source-revisions.md) |
| Dataset and chronological evaluation | [Temporal validation](portfolio/temporal-validation.md) |

Historical metrics belong to their own run/lock identities and must not be
attributed to new models. E6 is descriptive research on Train/Selection; it does
not change the champion or threshold, register/promote a model, or open real Test.

Public documentation contains reader guides and aggregate evidence only.
Raw data, per-row predictions, tracking stores, secrets and coding-assistant
settings/instructions remain outside Git. Check the public file set with
`uv run python scripts/check_public_files.py`; use `--revision COMMIT` to audit
one commit. Auditing a tree is different from auditing the entire Git history.

Historical decision records and product guides remain in the
[documentation snapshot](https://github.com/hoangisreal/RiskLab/tree/b9f85066c30b62d739ee8ff29aa631f8142b3642/docs).
That snapshot records history rather than current acceptance status.
