export type PreviewKind = "page_text" | "sheet_grid" | "image";

export type AllowedAction =
  | "create_document"
  | "upload_version"
  | "process"
  | "retry"
  | "release"
  | "reject";

export type DocumentStatus = "processing" | "ready" | "blocked" | "failed";
export type WorkflowStatus =
  | "received"
  | "processing"
  | "ready"
  | "blocked"
  | "failed";
export type QuarantineStatus = "held" | "released" | "blocked";
export type ScanStatus =
  | "queued"
  | "scanning"
  | "clean"
  | "infected"
  | "error"
  | "unavailable";
export type PreviewStatus =
  | "blocked"
  | "queued"
  | "generating"
  | "ready"
  | "failed";

export interface IngestionCapabilities {
  upload_enabled: boolean;
  disabled_reason_code: string | null;
  allowed_types: Array<{
    content_type: string;
    extensions: string[];
    preview_kind: PreviewKind;
    max_file_bytes: number;
  }>;
  limits: {
    max_file_bytes: number;
    max_versions_per_document: number;
    max_pdf_pages: number;
    max_docx_pages: number;
    max_xlsx_sheets: number;
    max_xlsx_rows_per_sheet: number;
    max_xlsx_columns: number;
    max_image_pixels: number;
  };
  scanner: {
    mode: "local";
    state: "ready" | "degraded" | "unavailable";
    last_checked_at: string | null;
  };
}

export type KnowledgeScopeKind = "service_provider" | "client";

export interface KnowledgeScope {
  id: string;
  kind: KnowledgeScopeKind;
  client_account_id: string | null;
  client_display_name: string | null;
}

export interface KnowledgeScopeTarget {
  kind: KnowledgeScopeKind;
  client_account_id: string | null;
  client_display_name?: string | null;
}

export interface VersionSummary {
  id: string;
  document_id: string;
  version_number: number;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  workflow_status: WorkflowStatus;
  quarantine_status: QuarantineStatus;
  scan_status: ScanStatus;
  preview_status: PreviewStatus;
  reason_code: string | null;
  retryable: boolean;
  created_at: string;
  updated_at: string;
  allowed_actions: AllowedAction[];
}

export interface DocumentSummary {
  id: string;
  display_name: string;
  declared_material_kind: MaterialKind;
  knowledge_scope: KnowledgeScope;
  status: DocumentStatus;
  version_count: number;
  latest_version: VersionSummary | null;
  created_at: string;
  updated_at: string;
  allowed_actions: AllowedAction[];
}

export interface DocumentCollection {
  items: DocumentSummary[];
  next_cursor: string | null;
  allowed_actions: AllowedAction[];
}

export interface DocumentDetail extends DocumentSummary {
  versions: VersionSummary[];
}

export type AutoPipelineStageStatus =
  | "disabled"
  | "pending"
  | "running"
  | "ready"
  | "failed"
  | "skipped";

export interface AutoPipelineStage {
  status: AutoPipelineStageStatus;
  reason_code: string | null;
}

export interface AutoPipelineStatus {
  schema: "anhuan-material-auto-pipeline-v1";
  version_id: string;
  enabled: boolean;
  scope_kind: KnowledgeScopeKind;
  ingestion: AutoPipelineStage;
  analysis: AutoPipelineStage;
  index: AutoPipelineStage;
  report: AutoPipelineStage;
}

export interface PreviewUnit {
  id: string;
  kind: "page_text" | "worksheet_grid" | "image";
  ordinal: number;
  label: string;
  width_px: number | null;
  height_px: number | null;
  row_count: number | null;
  column_count: number | null;
}

export interface PreviewManifest {
  version_id: string;
  status: "blocked" | "generating" | "ready" | "failed";
  kind: PreviewKind;
  units: PreviewUnit[];
  reason_code: string | null;
  retryable: boolean;
  generated_at: string | null;
}

export type WorksheetCell = string | number | boolean | null;

export interface PageText {
  lines: string[];
  truncated: boolean;
}

export interface WorksheetGrid {
  unit_id: string;
  row_offset: number;
  total_rows: number;
  total_columns: number;
  rows: WorksheetCell[][];
  truncated: boolean;
}

export type MaterialAnalysisStatus = "ready" | "failed" | "confirmed";
export type MaterialKind = "policy" | "report" | "unknown";
export type MaterialClassificationSource =
  | "upload_selection"
  | "machine_pending"
  | "human_review";
export type MaterialPageKind = "text" | "scanned" | "mixed" | "unknown";
export type MaterialDocumentProfile =
  | MaterialPageKind
  | "table"
  | "two_column";
export type MaterialAnalysisAllowedAction =
  | "set_material_kind"
  | "confirm_policy_draft"
  | "view_policy_source"
  | "view_policy_version";

export interface MaterialPageClassification {
  page_number: number;
  primary_kind: MaterialPageKind;
  ocr_required: boolean;
  table_candidate: boolean;
  two_column_candidate: boolean;
  text_character_count: number;
  text_confidence_ppm: number | null;
  scan_confidence_ppm: number | null;
  table_confidence_ppm: number | null;
  two_column_confidence_ppm: number | null;
  reason_codes: string[];
}

export interface MaterialFieldCandidate {
  id: string;
  field_name: string;
  candidate_value: string;
  page_number: number;
  evidence_snippet: string;
  confidence_ppm: number | null;
  confidence_basis: string;
  calibrated: false;
  producer: "pypdf_heuristic" | "pdf_inspector_shadow";
}

export interface MaterialIntakeAnalysis {
  id: string;
  document_version_id: string;
  source_sha256: string;
  analysis_version: string;
  parser_backend: string;
  document_profile: MaterialDocumentProfile;
  suggested_kind: MaterialKind;
  suggested_kind_confidence_ppm: number | null;
  resolved_kind: MaterialKind;
  classification_source: MaterialClassificationSource;
  classification_by_user_id: string | null;
  classification_at: string | null;
  knowledge_scope: KnowledgeScope;
  status: MaterialAnalysisStatus;
  reason_code: string | null;
  shadow_status: "disabled" | "unavailable" | "ready" | "failed";
  page_count: number;
  candidate_count: number;
  pages: MaterialPageClassification[];
  candidates: MaterialFieldCandidate[];
  policy_source_id: string | null;
  policy_version_id: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
  allowed_actions: MaterialAnalysisAllowedAction[];
  boundaries: string[];
}

export interface IngestionErrorEnvelope {
  detail?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
}
