/** Wire types for business API v1; the server remains the authority. */

export type ImportJob = {
  id: string;
  revision: number;
  state: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  catalog_version: string;
  import_contract_version: string;
  created_at: string;
  attempt: number;
  total_rows: number | null;
  created_count: number;
  ready_count: number;
  incomplete_count: number;
  retryable: boolean;
  errors: { row: number | null; field: string | null; code: string }[];
  errors_truncated: boolean;
};

export type ApplicationSummary = {
  id: string;
  import_id: string;
  external_reference: string;
  assignee: { id: string; username: string } | null;
  revision: number;
  feature_availability: "READY" | "INCOMPLETE" | "INVALID";
  scoring_state:
    "NOT_REQUESTED" | "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  review_state:
    "UNASSIGNED" | "ASSIGNED" | "IN_REVIEW" | "PENDING_DECISION" | "DECIDED";
  prediction_id: string | null;
  created_at: string;
};

export type Review = {
  id: string;
  application_id: string;
  author_id: string;
  review_number: number;
  prediction_id: string;
  notes: string;
  proposal: "APPROVE" | "DECLINE" | null;
  submitted_at: string | null;
};

export type Decision = {
  id: string;
  application_id: string;
  review_id: string;
  prediction_id: string;
  decided_by: string;
  outcome: "APPROVED_SIMULATED" | "DECLINED_SIMULATED";
  reason: string;
  created_at: string;
};

export type ApplicationDetail = ApplicationSummary & {
  source_availability: Record<string, string>;
  missing_sources: string[];
  supersedes_application_id: string | null;
  catalog_version: string;
  snapshot_id: string;
  input_sha256: string;
  input_revision: number;
  current_review: Review | null;
  decision: Decision | null;
};

export type Prediction = {
  id: string;
  application_id: string;
  input_revision: number;
  input_sha256: string;
  feature_schema_hash: string;
  model_version: string;
  model_sha256: string;
  model_run_id: string;
  lock_id: string;
  feature_contract_version: string;
  protocol_hashes: Record<string, string>;
  probability: number;
  threshold: number;
  policy_version: string;
  sources_used: string[];
  warnings: string[];
  explanation: {
    space: string;
    base_value: number;
    raw_log_odds: number;
    top_contributions: {
      feature: string;
      contribution: number;
      value?: unknown;
    }[];
  };
  created_at: string;
  initiated_by: string | null;
  job_id: string;
};

export type HistoryEvent = {
  id: string;
  actor_id: string | null;
  actor_type: string;
  action: string;
  resource_id: string;
  created_at: string;
  request_id: string | null;
  job_id: string | null;
  reason: string | null;
  metadata: Record<string, unknown>;
};
