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

export interface IngestionErrorEnvelope {
  detail?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
}
