# README application screenshots

These four real screenshots were captured with Playwright against the local
Docker demo at `http://localhost:3000` on 2026-11-17, after the UI
visualization and motion refresh. The frontend is a production build of the
current source, using the real API, PostgreSQL and worker without mocked
responses. One synthetic case was driven through the full workflow
(assign → Analyst proposal → Manager simulated decision) so the dashboard
shows more than one workflow state.

- `overview.jpg`: Manager dashboard with animated workload metrics, the
  workflow funnel and the scoring distribution.
- `case-review.jpg`: case detail with the probability gauge against the
  policy threshold, log-odds contributions and analyst assignment.
- `evidence.jpg`: historical ML evidence page — locked Test metrics with
  95% confidence intervals, the 12-pipeline comparison and the temporal
  benchmark, labelled as distinct from the synthetic demo model.
- `mobile-review.jpg`: Analyst case view at a 390px viewport after the
  simulated decision, with proposal, decision and history timeline.

Cases, accounts and the LightGBM bundle are synthetic. Scores shown in the
images do not represent the evaluated Home Credit metrics. The images contain
no real borrower data, secrets or AI-generated content. Screenshot content was
not edited; captures waited for entrance animations to settle, and only
viewport settings and JPEG encoding were applied.
