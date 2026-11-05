/** Local development integration contracts. Business truth comes from FastAPI. */
export type SupportRoute = "routine_support" | "clarify_safety" | "safety_support" | "urgent_real_world_help";
export type Scope = "local_analysis" | "model_context";
export type Workflow = {
  run_id: string | null;
  kind: "chat" | "demo" | null;
  phase: "IDLE" | "RUNNING" | "WAITING_CONFIRMATION" | "COMPLETED" | "CANCELLED" | "FAILED";
  step: string | null;
  step_count: number;
  stop_reason: string | null;
};
export type SupportSession = {
  session_id: string;
  revision: number;
  status: "ACTIVE" | "ENDED";
  state: string;
  messages: { role: "user" | "assistant"; content: string }[];
  workflow: Workflow;
  created_at: string;
};
export type EvidenceReference = { kind: string; source_id: string; revision: number };
export type Understanding = {
  schema_version: string;
  understanding_id: string;
  revision: number;
  status: "proposed" | "confirmed" | "rejected" | "invalidated";
  interpretation: string;
  origin: "agent_hypothesis" | "user_report";
  evidence_refs: EvidenceReference[];
  created_at: string;
  updated_at: string;
  confirmed_revision: number | null;
  confirmed_at: string | null;
  invalidation_reason: string | null;
};
export type UnderstandingView = { understanding: Understanding; confirmation_token: string | null };
export type SupportNotice = { review_status: "unreviewed"; message: string; actions: string[]; automatic_contact: false };
export type SupportResult = {
  route: SupportRoute;
  evidence_refs: string[];
  uncertainties: string[];
  reply: string;
  suggested_next_step: string | null;
  memory_update: null;
  follow_up_offer: string | null;
};
export type SupportResponse = {
  session_id: string;
  state: string;
  reply: string;
  request_id: string;
  run_id: string;
  revision: number;
  workflow: Workflow;
  psychology: { schema_version: string; session_id: string; request_id: string; session_revision: number; result: SupportResult } | null;
  review_status: "unreviewed" | null;
  context_manifest?: { excluded_history_indices: number[]; [key: string]: unknown } | null;
};
export type WearableProvider = {
  provider: string;
  record_count: number;
  available_metrics: string[];
  allowed_metrics: string[];
  scopes: Scope[];
  is_simulated: boolean;
};
export type SupportState = {
  session: SupportSession;
  active_request_id: string | null;
  support_mode: boolean;
  support_route: SupportRoute | null;
  review_status: "unreviewed";
  understandings: UnderstandingView[];
  wearables: { revision: number; record_count: number; providers: WearableProvider[]; available_timezones?: string[] };
};
export type SupportHealth = {
  status: string;
  service: string;
  llm: { provider: string; model: string; key_configured: boolean };
  runtime: {
    psychology_enabled: boolean;
    psychology_demo_mode: boolean;
    psychology_review_status: string;
    prompt_version: string;
    storage: string;
    max_steps: number;
    max_context_chars: number;
  };
};
export type WearableCatalog = {
  metrics: { metric_id: string; unit: string; value_type: string; usage: string; required_method: string | null }[];
  scenarios: { scenario_id: string; label: string }[];
  is_simulated: true;
};
export type TrendSeries = {
  metric_id: string;
  unit: string;
  value_type: string;
  status: string;
  points: { date: string; value: number | string | null; quality: string; record_id: string | null }[];
  source: {
    provider: string; device_id: string; measurement_method: string; source_algorithm_version: string; is_simulated: boolean;
    aggregation_method?: string; window_seconds?: number | null; baseline_ref?: string | null;
    measurement_site?: string | null; namespace?: string | null;
    score_scale?: { minimum: number; maximum: number; direction: "higher_is_more" | "higher_is_less" | "unspecified" } | null;
  } | null;
};
export type WearableTrends = {
  session_id: string;
  revision: number;
  scope: "local_analysis";
  as_of: string;
  timezone: string;
  days: number;
  series: TrendSeries[];
};
export type StateObservation = {
  metric_id: string;
  unit: string;
  baseline_median: number;
  recent_median: number;
  delta: number;
  relative_delta: number | null;
  baseline_valid_days: number;
  recent_valid_days: number;
  baseline_window: { start: string; end: string };
  recent_window: { start: string; end: string };
  record_refs: EvidenceReference[];
  inquiry_candidate: boolean;
  provider: string;
  device_id: string;
  measurement_method: string;
  [key: string]: unknown;
};
export type StateAnalysis = {
  schema_version: string;
  session_id: string;
  analysis_id: string;
  evidence_revision: number;
  algorithm_version: string;
  is_simulated: boolean;
  as_of: string;
  status: string;
  scope: Scope;
  timezone: string;
  metric_statuses: { metric_id: string; status: string; baseline_valid_days: number; recent_valid_days: number; excluded_days: number }[];
  observations: StateObservation[];
  limitations: string[];
};
export type ArchiveInfo = {
  archive_id: string;
  archive_version: number;
  snapshot_version: 1 | 2;
  session_revision: number;
  message_count: number;
  draft_count: number;
  status: "ACTIVE" | "ENDED";
  saved_at: string;
};
export type UserReport = { report_id: string; content: string; revision: number };
export type WearableReceipt = { revision: number; accepted?: number; unchanged?: number; invalid_metrics?: number; changed?: boolean; deleted?: number };
export type Operation = { operation_id: string };
export type SendSupportMessage = { content: string; request_id: string; correction_of?: { understanding_id: string; expected_revision: number } };
export type SimulationRequest = Operation & { expected_revision: number; as_of: string; scenario_id: string; seed: number; days?: number; timezone?: string; replace_existing?: boolean };
export type ConsentRequest = Operation & { expected_revision: number; provider: string; allowed_metrics: string[]; scopes: Scope[] };
export type UnderstandingProposal = Operation & { interpretation: string; origin: "agent_hypothesis" | "user_report"; evidence_refs: EvidenceReference[] };
