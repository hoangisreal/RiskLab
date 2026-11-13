"use client";

import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useConfirmation } from "@/components/confirm";
import { Badge } from "@/components/ui/badge";
import {
  ApiError,
  api,
  apiPage,
  mutate,
  mutateWithKey,
  messageFor,
} from "@/lib/api";
import type { ApplicationDetail, HistoryEvent, Prediction } from "@/lib/types";
import { useSession } from "@/components/session";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

type Analyst = { id: string; username: string };

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

  return (
    <>
      {dialog}
      <Link href="/applications" className="eyebrow">
        ← Applications
      </Link>
      <div className="page-heading">
        <h1>Hồ sơ {detail.external_reference}</h1>
        <p>
          Trạng thái review: {detail.review_state} · Scoring:{" "}
          {detail.scoring_state} · Nguồn dữ liệu: {detail.feature_availability}.
          Đây là mô phỏng hỗ trợ quyết định; quyết định cuối thuộc về Manager.
        </p>
      </div>
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

      <div className="workflow-steps" aria-label="Tiến trình hồ sơ">
        {["ASSIGNED", "IN_REVIEW", "PENDING_DECISION", "DECIDED"].map(
          (state, i) => (
            <span
              key={state}
              className={detail.review_state === state ? "active" : ""}
            >
              {i + 1}. {state}
            </span>
          ),
        )}
      </div>
      <div className="detail-grid">
        <section className="sheet" aria-labelledby="prediction-heading">
          <p className="eyebrow">ML prediction · Tham khảo</p>
          <Badge variant="secondary">{detail.scoring_state}</Badge>
          <h2 id="prediction-heading">Kết quả model đã lưu</h2>
          {detail.feature_availability === "INCOMPLETE" ? (
            <p className="notice" role="status">
              Hồ sơ thiếu nguồn dữ liệu bắt buộc (
              {detail.missing_sources.join(", ")}) nên bị chặn scoring. Thiếu
              nguồn không đồng nghĩa khách hàng không có lịch sử tín dụng.
            </p>
          ) : detail.scoring_state === "FAILED" ? (
            <>
              <ErrorState>
                Chấm điểm thất bại. Hồ sơ không có điểm số suy đoán thay thế.
              </ErrorState>
              {user.role === "MANAGER" && (
                <button
                  disabled={busy || conflicted}
                  onClick={() => {
                    const reason = window
                      .prompt("Lý do thử lại scoring?")
                      ?.trim();
                    if (!reason) return;
                    void act(
                      () =>
                        mutate(`/api/v1/applications/${id}/scoring/retry`, {
                          expected_revision: detail.revision,
                          reason,
                        }),
                      "Đã xếp hàng chấm điểm lại với cùng model đã ghim.",
                    );
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
                  <dd>{prediction.probability.toFixed(4)}</dd>
                </div>
                <div>
                  <dt>Ngưỡng chính sách</dt>
                  <dd>
                    {prediction.threshold.toFixed(4)} ·{" "}
                    {prediction.policy_version}
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
                TARGET là khó khăn thanh toán trong dữ liệu lịch sử, không phải
                xác suất vỡ nợ thực tế đã kiểm định. Xác suất trên không tự tạo
                quyết định.
              </p>
              {prediction.warnings.length > 0 && (
                <p className="notice" role="status">
                  Cảnh báo: {prediction.warnings.join("; ")}
                </p>
              )}
              <h3>Đóng góp nổi bật (log-odds)</h3>
              <p>
                Explanation mô tả đóng góp của model trong không gian{" "}
                {prediction.explanation.space}, không phải quan hệ nhân quả hay
                lý do pháp lý. Base{" "}
                {prediction.explanation.base_value.toFixed(3)} · Raw log-odds{" "}
                {prediction.explanation.raw_log_odds.toFixed(3)}.
              </p>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Feature</th>
                      <th>Đóng góp log-odds</th>
                      <th>Giá trị đầu vào</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prediction.explanation.top_contributions.map((item) => (
                      <tr key={item.feature}>
                        <td data-label="Feature">{item.feature}</td>
                        <td data-label="Đóng góp log-odds">
                          {item.contribution.toFixed(4)}
                        </td>
                        <td data-label="Giá trị đầu vào">
                          {item.value == null ? "—" : String(item.value)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>
                Nguồn sử dụng: {prediction.sources_used.join(", ")} · Input{" "}
                {prediction.input_sha256.slice(0, 12)}… · Feature contract{" "}
                {prediction.feature_contract_version}
              </p>
            </>
          )}
          <p>
            Nguồn:{" "}
            {Object.entries(detail.source_availability)
              .map(([name, status]) => `${name}: ${status}`)
              .join(" · ")}
          </p>
        </section>

        {user.role === "MANAGER" && detail.review_state !== "DECIDED" && (
          <section
            className="sheet section-gap"
            aria-labelledby="assign-heading"
          >
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
            <section
              className="sheet section-gap"
              aria-labelledby="review-heading"
            >
              <h2 id="review-heading">Review của Analyst</h2>
              <button
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
            <section
              className="sheet section-gap"
              aria-labelledby="draft-heading"
            >
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
                          proposal: String(form.get("proposal") ?? "") || null,
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
                    submit ? "Đã gửi đề xuất cho Manager." : "Đã lưu bản nháp.",
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
                    disabled={busy || conflicted}
                    onClick={() => setIntent("submit")}
                  >
                    Gửi đề xuất
                  </button>
                </div>
              </form>
            </section>
          )}

        {review?.submitted_at && (
          <section
            className="sheet section-gap"
            aria-labelledby="submission-heading"
          >
            <h2 id="submission-heading">
              Đề xuất đã gửi #{review.review_number}
            </h2>
            <p>
              Đề xuất: {review.proposal} · Gửi lúc {review.submitted_at}
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
                    <option value="APPROVED_SIMULATED">
                      APPROVED_SIMULATED
                    </option>
                    <option value="DECLINED_SIMULATED">
                      DECLINED_SIMULATED
                    </option>
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
          <section
            className="sheet section-gap"
            aria-labelledby="final-heading"
          >
            <h2 id="final-heading">Quyết định cuối (mô phỏng)</h2>
            <p>
              {detail.decision.outcome} · {detail.decision.created_at}
            </p>
            <p>{detail.decision.reason}</p>
            <p>
              Quyết định đã khóa; MVP không mở lại hoặc sửa quyết định cuối.
            </p>
          </section>
        )}
      </div>
      <section className="sheet section-gap" aria-labelledby="history-heading">
        <h2 id="history-heading">Lịch sử hồ sơ</h2>
        {historyError && <ErrorState>{historyError}</ErrorState>}
        {history.length === 0 ? (
          <EmptyState>Chưa có sự kiện lịch sử.</EmptyState>
        ) : (
          <ol className="timeline">
            {history.map((event) => (
              <li key={event.id}>
                <strong>{event.action}</strong> · {event.actor_type} ·{" "}
                {event.created_at}
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
