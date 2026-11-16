"use client";

import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useConfirmation } from "@/components/confirm";
import {
  ApiError,
  api,
  apiPage,
  mutate,
  mutateWithKey,
  messageFor,
} from "@/lib/api";
import type { ApplicationDetail, HistoryEvent, Prediction } from "@/lib/types";
import {
  ACTION_LABEL,
  FEATURE_AVAILABILITY_LABEL,
  OUTCOME_LABEL,
  PROPOSAL_LABEL,
  REVIEW_STATE_LABEL,
  SCORING_STATE_LABEL,
  formatDateTime,
  labelOf,
  toPercent,
} from "@/lib/labels";
import { useSession } from "@/components/session";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { PageHeader, StatusBadge } from "@/components/primitives";

type Analyst = { id: string; username: string };

const STEPS = [
  "UNASSIGNED",
  "ASSIGNED",
  "IN_REVIEW",
  "PENDING_DECISION",
  "DECIDED",
] as const;

export default function ApplicationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { user } = useSession();
  return (
    <ApplicationWorkspace key={`${id}:${user?.id ?? "anonymous"}`} id={id} />
  );
}

function ApplicationWorkspace({ id }: { id: string }) {
  const { confirm, dialog } = useConfirmation();
  const { user, pending } = useSession();
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [history, setHistory] = useState<HistoryEvent[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [analysts, setAnalysts] = useState<Analyst[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflicted, setConflicted] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [intent, setIntent] = useState("save");
  const [decisionIntent, setDecisionIntent] = useState("decide");
  const historyRequest = useRef(0);
  const decisionKey = useRef<string>("");
  const savedRevision = useRef<number | null>(null);

  useEffect(() => {
    if (!user || user.role === "ADMIN") return;
    let cancelled = false;
    historyRequest.current += 1;
    void (async () => {
      try {
        const loaded = await api<ApplicationDetail>(
          `/api/v1/applications/${id}`,
        );
        if (cancelled) return;
        savedRevision.current = loaded.revision;
        const loadedPrediction = loaded.prediction_id
          ? await api<Prediction>(`/api/v1/applications/${id}/prediction`)
          : null;
        if (cancelled) return;
        setDetail(loaded);
        setPrediction(loadedPrediction);
        const events = await apiPage<HistoryEvent>(
          `/api/v1/applications/${id}/history?page=1&page_size=100`,
        );
        if (!cancelled) {
          setHistory(events.data);
          setHistoryTotal(events.pagination.total);
          setHistoryPage(1);
          setHistoryError("");
          setHistoryBusy(false);
        }
        if (user.role === "MANAGER") {
          const list = await apiPage<Analyst>(
            "/api/v1/analysts?page=1&page_size=100",
          );
          if (!cancelled) setAnalysts(list.data);
        }
        if (!cancelled) setLoadError("");
      } catch (failure) {
        if (!cancelled) setLoadError(messageFor(failure));
      } finally {
        if (!cancelled) {
          setLoading(false);
          setBusy(false);
        }
      }
    })();
    return () => {
      cancelled = true;
      historyRequest.current += 1;
    };
  }, [user, id, refreshKey]);

  async function act(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(done);
      setLoading(true);
      setRefreshKey((value) => value + 1);
    } catch (failure) {
      // Keep stale edits visible but block further writes until explicit reload.
      setError(messageFor(failure));
      if (failure instanceof ApiError && failure.status === 409)
        setConflicted(true);
      setBusy(false);
    }
  }

  if (pending) return <LoadingState />;
  if (!user)
    return (
      <>
        <h1>Phiên đăng nhập không còn hiệu lực</h1>
        <p>
          <Link href="/login">Đăng nhập lại</Link>
        </p>
      </>
    );
  if (user.role === "ADMIN")
    return (
      <>
        <h1>Không có quyền truy cập hồ sơ</h1>
        <p>Admin không có quyền nghiệp vụ mặc định.</p>
      </>
    );
  if (loading) return <LoadingState />;
  if (loadError || !detail)
    return (
      <ErrorState
        retry={() => {
          setLoading(true);
          setLoadError("");
          setRefreshKey((v) => v + 1);
        }}
      >
        {loadError || "Không tải được hồ sơ."}
      </ErrorState>
    );

  const review = detail.current_review;
  const isAssignee = detail.assignee?.id === user.id;
  const decisionBody = (form: FormData) => ({
    expected_revision: detail.revision,
    review_id: review?.id ?? "",
    outcome: String(form.get("outcome")),
    reason: String(form.get("reason") ?? ""),
  });
  const stepIndex = Math.max(0, STEPS.indexOf(detail.review_state));

  return (
    <>
      {dialog}
      <Link href="/applications" aria-label="Về danh sách hồ sơ">
        ← Hồ sơ
      </Link>
      <PageHeader
        title={`Hồ sơ ${detail.external_reference}`}
        intro={
          <>
            Người phụ trách:{" "}
            <strong>{detail.assignee?.username ?? "Chưa phân công"}</strong>
            {" · "}Đây là mô phỏng hỗ trợ quyết định; quyết định cuối thuộc về
            Manager.
          </>
        }
        actions={
          <>
            <StatusBadge
              value={detail.review_state}
              label={labelOf(REVIEW_STATE_LABEL, detail.review_state)}
            />
            <StatusBadge
              value={detail.scoring_state}
              label={labelOf(SCORING_STATE_LABEL, detail.scoring_state)}
            />
          </>
        }
      />
      {message && (
        <p className="notice" role="status">
          {message}
        </p>
      )}
      {error && <ErrorState>{error}</ErrorState>}
      {conflicted && (
        <button
          onClick={async () => {
            if (!(await confirm("Bỏ thay đổi chưa lưu và tải bản mới nhất?")))
              return;
            setConflicted(false);
            decisionKey.current = "";
            setLoading(true);
            setRefreshKey((value) => value + 1);
          }}
        >
          Tải bản mới nhất
        </button>
      )}

      <ol className="workflow-steps" aria-label="Tiến trình hồ sơ">
        {STEPS.map((state, i) => (
          <li
            key={state}
            className={i < stepIndex ? "done" : i === stepIndex ? "active" : ""}
            aria-current={i === stepIndex ? "step" : undefined}
          >
            {i + 1}. {labelOf(REVIEW_STATE_LABEL, state)}
          </li>
        ))}
      </ol>
      <div className="detail-grid">
        <div className="info-col">
          <PredictionPanel
            detail={detail}
            prediction={prediction}
            isManager={user.role === "MANAGER"}
            busy={busy}
            conflicted={conflicted}
            onRetry={(reason) =>
              void act(
                () =>
                  mutate(`/api/v1/applications/${id}/scoring/retry`, {
                    expected_revision: detail.revision,
                    reason,
                  }),
                "Đã xếp hàng chấm điểm lại với cùng model đã ghim.",
              )
            }
          />
        </div>
        <div className="actions-col actions-first">
          {user.role === "MANAGER" && detail.review_state !== "DECIDED" && (
            <section className="sheet" aria-labelledby="assign-heading">
              <h2 id="assign-heading">Phân công Analyst</h2>
              <form
                className="form-sheet"
                onSubmit={(event) => {
                  event.preventDefault();
                  const form = new FormData(event.currentTarget);
                  void act(
                    () =>
                      mutate(
                        `/api/v1/applications/${id}/assignment`,
                        {
                          expected_revision: detail.revision,
                          assignee_id: String(form.get("assignee_id")),
                          reason: String(form.get("reason") ?? ""),
                        },
                        "PUT",
                      ),
                    "Đã phân công hồ sơ.",
                  );
                }}
              >
                <div className="field">
                  <label htmlFor="assignee">Analyst đang hoạt động</label>
                  <select id="assignee" name="assignee_id" required>
                    {analysts.map((analyst) => (
                      <option key={analyst.id} value={analyst.id}>
                        {analyst.username}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="assign-reason">Lý do phân công</label>
                  <input
                    id="assign-reason"
                    name="reason"
                    required
                    maxLength={2000}
                  />
                </div>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={busy || conflicted || analysts.length === 0}
                >
                  Lưu phân công
                </button>
              </form>
            </section>
          )}

          {user.role === "ANALYST" &&
            isAssignee &&
            detail.review_state === "ASSIGNED" && (
              <section className="sheet" aria-labelledby="review-heading">
                <h2 id="review-heading">Review của Analyst</h2>
                <p>
                  Bắt đầu review để ghi nhận xét và đề xuất. Đề xuất không phải
                  quyết định cuối.
                </p>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={busy || conflicted}
                  onClick={() =>
                    void act(
                      () =>
                        mutate(`/api/v1/applications/${id}/reviews`, {
                          expected_revision: detail.revision,
                        }),
                      "Đã bắt đầu review.",
                    )
                  }
                >
                  Bắt đầu review
                </button>
              </section>
            )}

          {user.role === "ANALYST" &&
            isAssignee &&
            detail.review_state === "IN_REVIEW" &&
            review &&
            review.submitted_at === null && (
              <section className="sheet" aria-labelledby="draft-heading">
                <h2 id="draft-heading">
                  Bản nháp review #{review.review_number}
                </h2>
                <form
                  key={`${review.id}:${detail.revision}`}
                  className="form-sheet"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const form = new FormData(event.currentTarget);
                    const submit = intent === "submit";
                    if (
                      submit &&
                      (!String(form.get("notes") ?? "").trim() ||
                        !form.get("proposal"))
                    ) {
                      setError(
                        "Gửi đề xuất cần nhận xét và lựa chọn APPROVE hoặc DECLINE.",
                      );
                      return;
                    }
                    void act(
                      async () => {
                        const saved = await mutate<{
                          application_revision: number;
                        }>(
                          `/api/v1/applications/${id}/reviews/${review.id}`,
                          {
                            expected_revision:
                              savedRevision.current ?? detail.revision,
                            notes: String(form.get("notes") ?? ""),
                            proposal:
                              String(form.get("proposal") ?? "") || null,
                          },
                          "PATCH",
                        );
                        savedRevision.current = saved.application_revision;
                        if (submit) {
                          await mutate(
                            `/api/v1/applications/${id}/reviews/${review.id}/submit`,
                            { expected_revision: saved.application_revision },
                          );
                        }
                      },
                      submit
                        ? "Đã gửi đề xuất cho Manager."
                        : "Đã lưu bản nháp.",
                    );
                  }}
                >
                  <div className="field">
                    <label htmlFor="review-notes">Nhận xét review</label>
                    <textarea
                      id="review-notes"
                      name="notes"
                      rows={5}
                      maxLength={10000}
                      defaultValue={review.notes}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="review-proposal">Đề xuất của Analyst</label>
                    <select
                      id="review-proposal"
                      name="proposal"
                      defaultValue={review.proposal ?? ""}
                    >
                      <option value="">Chưa chọn</option>
                      <option value="APPROVE">Đề xuất APPROVE</option>
                      <option value="DECLINE">Đề xuất DECLINE</option>
                    </select>
                  </div>
                  <p>
                    Đề xuất không phải quyết định cuối; Manager là người chốt mô
                    phỏng.
                  </p>
                  <div className="inline">
                    <button
                      type="submit"
                      disabled={busy || conflicted}
                      onClick={() => setIntent("save")}
                    >
                      Lưu nháp
                    </button>
                    <button
                      type="submit"
                      className="btn-primary"
                      disabled={busy || conflicted}
                      onClick={() => setIntent("submit")}
                    >
                      Gửi đề xuất
                    </button>
                  </div>
                </form>
              </section>
            )}
        </div>
      </div>

      {review?.submitted_at && (
        <section
          className="sheet section-gap"
          aria-labelledby="submission-heading"
        >
          <p className="eyebrow">Đề xuất Analyst</p>
          <h2 id="submission-heading">
            Đề xuất đã gửi #{review.review_number}
          </h2>
          <p>
            Đề xuất:{" "}
            <StatusBadge
              value={review.proposal ?? "PENDING"}
              label={labelOf(PROPOSAL_LABEL, review.proposal)}
            />{" "}
            · Gửi lúc {formatDateTime(review.submitted_at)}
          </p>
          <p>{review.notes}</p>
        </section>
      )}

      {user.role === "MANAGER" &&
        detail.review_state === "PENDING_DECISION" &&
        review && (
          <section
            className="sheet section-gap"
            aria-labelledby="decision-heading"
          >
            <p className="eyebrow">Quyết định Manager</p>
            <h2 id="decision-heading">Quyết định mô phỏng của Manager</h2>
            <form
              className="form-sheet"
              onSubmit={async (event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                const mode = decisionIntent;
                if (mode === "return") {
                  void act(
                    () =>
                      mutate(`/api/v1/applications/${id}/return`, {
                        expected_revision: detail.revision,
                        review_id: review.id,
                        reason: String(form.get("reason") ?? ""),
                      }),
                    "Đã trả review cho Analyst.",
                  );
                  return;
                }
                if (
                  !(await confirm(
                    "Chốt quyết định mô phỏng này? Quyết định cuối không sửa hoặc mở lại được.",
                  ))
                )
                  return;
                if (!decisionKey.current)
                  decisionKey.current = `web-decision-${crypto.randomUUID()}`;
                void act(
                  () =>
                    mutateWithKey(
                      `/api/v1/applications/${id}/decisions`,
                      decisionBody(form),
                      decisionKey.current,
                    ),
                  "Đã chốt quyết định mô phỏng.",
                );
              }}
            >
              <div className="field">
                <label htmlFor="decision-outcome">Kết quả mô phỏng</label>
                <select id="decision-outcome" name="outcome">
                  <option value="APPROVED_SIMULATED">Duyệt (mô phỏng)</option>
                  <option value="DECLINED_SIMULATED">Từ chối (mô phỏng)</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="decision-reason">
                  Lý do quyết định / trả lại
                </label>
                <textarea
                  id="decision-reason"
                  name="reason"
                  rows={4}
                  required
                  maxLength={2000}
                />
              </div>
              <div className="inline">
                <button
                  type="submit"
                  disabled={busy || conflicted}
                  onClick={() => setDecisionIntent("return")}
                >
                  Trả lại review
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={busy || conflicted}
                  onClick={() => setDecisionIntent("decide")}
                >
                  Chốt quyết định mô phỏng
                </button>
              </div>
            </form>
          </section>
        )}

      {detail.decision && (
        <section className="sheet section-gap" aria-labelledby="final-heading">
          <p className="eyebrow">Quyết định Manager</p>
          <h2 id="final-heading">Quyết định cuối (mô phỏng)</h2>
          <p>
            <StatusBadge
              value={detail.decision.outcome}
              label={labelOf(OUTCOME_LABEL, detail.decision.outcome)}
            />{" "}
            · {formatDateTime(detail.decision.created_at)}
          </p>
          <p>{detail.decision.reason}</p>
          <p>Quyết định đã khóa; MVP không mở lại hoặc sửa quyết định cuối.</p>
        </section>
      )}
      <section className="sheet section-gap" aria-labelledby="history-heading">
        <h2 id="history-heading">Lịch sử hồ sơ</h2>
        {historyError && <ErrorState>{historyError}</ErrorState>}
        {history.length === 0 ? (
          <EmptyState>Chưa có sự kiện lịch sử.</EmptyState>
        ) : (
          <ol className="timeline">
            {history.map((event) => (
              <li key={event.id}>
                <strong>{labelOf(ACTION_LABEL, event.action)}</strong>{" "}
                <span className="event-meta">
                  · {event.actor_type} ·{" "}
                  <time title={event.created_at}>
                    {formatDateTime(event.created_at)}
                  </time>
                </span>
                <div className="event-meta">
                  {labelOf(ACTION_LABEL, event.action) !== event.action ? (
                    <>
                      <code>{event.action}</code> ·{" "}
                    </>
                  ) : null}
                  id {event.id.slice(0, 8)}…
                </div>
                {event.reason ? <p>{event.reason}</p> : null}
              </li>
            ))}
          </ol>
        )}
        {historyPage * 100 < historyTotal && (
          <button
            disabled={historyBusy}
            onClick={async () => {
              const request = historyRequest.current;
              setHistoryBusy(true);
              setHistoryError("");
              try {
                const next = await apiPage<HistoryEvent>(
                  `/api/v1/applications/${id}/history?page=${historyPage + 1}&page_size=100`,
                );
                if (request !== historyRequest.current) return;
                setHistory((previous) => {
                  const existing = new Set(previous.map((event) => event.id));
                  return [
                    ...previous,
                    ...next.data.filter((event) => !existing.has(event.id)),
                  ];
                });
                setHistoryPage((previous) => previous + 1);
                setHistoryTotal(next.pagination.total);
              } catch (failure) {
                if (request === historyRequest.current)
                  setHistoryError(messageFor(failure));
              } finally {
                if (request === historyRequest.current) setHistoryBusy(false);
              }
            }}
          >
            {historyBusy ? "Đang tải lịch sử…" : "Tải thêm lịch sử"}
          </button>
        )}
      </section>
    </>
  );
}

function PredictionPanel({
  detail,
  prediction,
  isManager,
  busy,
  conflicted,
  onRetry,
}: {
  detail: ApplicationDetail;
  prediction: Prediction | null;
  isManager: boolean;
  busy: boolean;
  conflicted: boolean;
  onRetry: (reason: string) => void;
}) {
  return (
    <section className="sheet" aria-labelledby="prediction-heading">
      <p className="eyebrow">Kết quả model · Tham khảo</p>
      <StatusBadge
        value={detail.scoring_state}
        label={labelOf(SCORING_STATE_LABEL, detail.scoring_state)}
      />
      <h2 id="prediction-heading">Kết quả model đã lưu</h2>
      {detail.feature_availability === "INCOMPLETE" ? (
        <p className="notice" role="status">
          Hồ sơ thiếu nguồn dữ liệu bắt buộc (
          {detail.missing_sources.join(", ")}) nên bị chặn scoring. Thiếu nguồn
          không đồng nghĩa khách hàng không có lịch sử tín dụng.
        </p>
      ) : detail.scoring_state === "FAILED" ? (
        <>
          <ErrorState>
            Chấm điểm thất bại. Hồ sơ không có điểm số suy đoán thay thế.
          </ErrorState>
          {isManager && (
            <button
              disabled={busy || conflicted}
              onClick={() => {
                const reason = window.prompt("Lý do thử lại scoring?")?.trim();
                if (!reason) return;
                onRetry(reason);
              }}
            >
              Thử lại scoring
            </button>
          )}
        </>
      ) : !prediction ? (
        <LoadingState>Đang chờ worker chấm điểm…</LoadingState>
      ) : (
        <>
          <dl className="responsibilities">
            <div>
              <dt>Xác suất model</dt>
              <dd>{toPercent(prediction.probability)}</dd>
            </div>
            <div>
              <dt>Ngưỡng chính sách</dt>
              <dd>
                {toPercent(prediction.threshold)} · {prediction.policy_version}
              </dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>
                {prediction.model_version} · run {prediction.model_run_id} ·
                lock {prediction.lock_id}
              </dd>
            </div>
          </dl>
          <p>
            TARGET là khó khăn thanh toán trong dữ liệu lịch sử, không phải xác
            suất vỡ nợ thực tế đã kiểm định. Xác suất trên không tự tạo quyết
            định.
          </p>
          {prediction.warnings.length > 0 && (
            <p className="notice" role="status">
              Cảnh báo thiếu nguồn: {prediction.warnings.join("; ")}
            </p>
          )}
          <h3>Đóng góp nổi bật (log-odds)</h3>
          <p>
            Explanation mô tả đóng góp của model trong không gian{" "}
            {prediction.explanation.space}, không phải quan hệ nhân quả hay lý
            do pháp lý. Base {prediction.explanation.base_value.toFixed(3)} ·
            Raw log-odds {prediction.explanation.raw_log_odds.toFixed(3)}.
          </p>
          <ContributionBars items={prediction.explanation.top_contributions} />
          <details className="tech">
            <summary>Nguồn dữ liệu và provenance</summary>
            <div>
              <p>
                Nguồn sử dụng: {prediction.sources_used.join(", ")} · Trạng thái
                dữ liệu:{" "}
                {labelOf(
                  FEATURE_AVAILABILITY_LABEL,
                  detail.feature_availability,
                )}
              </p>
              <p>
                Nguồn:{" "}
                {Object.entries(detail.source_availability)
                  .map(([name, status]) => `${name}: ${status}`)
                  .join(" · ")}
              </p>
              <p>
                Snapshot {detail.snapshot_id} · catalog {detail.catalog_version}{" "}
                · input {detail.input_sha256.slice(0, 12)}… · feature contract{" "}
                {prediction.feature_contract_version} · schema{" "}
                {prediction.feature_schema_hash.slice(0, 12)}…
              </p>
            </div>
          </details>
        </>
      )}
    </section>
  );
}

function ContributionBars({
  items,
}: {
  items: { feature: string; contribution: number; value?: unknown }[];
}) {
  const max = Math.max(1e-9, ...items.map((i) => Math.abs(i.contribution)));
  return (
    <div role="list" aria-label="Đóng góp log-odds theo feature">
      {items.map((item) => {
        const positive = item.contribution >= 0;
        const width = `${(Math.abs(item.contribution) / max) * 50}%`;
        return (
          <div key={item.feature} className="contrib" role="listitem">
            <span>{item.feature}</span>
            <span className="contrib-bar" aria-hidden="true">
              <span
                className={`contrib-fill ${positive ? "positive" : "negative"}`}
                style={
                  positive ? { left: "50%", width } : { right: "50%", width }
                }
              />
            </span>
            <span className="contrib-value">
              {positive ? "+" : "−"}
              {Math.abs(item.contribution).toFixed(4)} ·{" "}
              {item.value == null ? "—" : String(item.value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
