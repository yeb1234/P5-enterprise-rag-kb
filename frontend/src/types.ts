export interface User {
  id: string;
  tenant_id: string;
  department_id: string | null;
  email: string;
  full_name: string;
  role: string;
  status: string;
  tenant_name?: string;
  department_name?: string;
}

export interface Kb { id: string; name: string; description: string; visibility: string; }
export interface Tag { id: string; name: string; color: string; }
export interface Category { id: string; name: string; parent_id: string | null; level: number; children: Category[]; }

export interface DocItem {
  id: string;
  kb_id: string;
  category_id: string | null;
  title: string;
  doc_type: string;
  mime_type: string | null;
  size_bytes: number;
  sensitivity: string;
  status: string;
  current_version: number;
  attributes: Record<string, any>;
  content_text: string | null;
  tags: Tag[];
  page_count: number;
  chunk_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  indexed_at: string | null;
}

export interface Page<T> { items: T[]; total: number; page: number; page_size: number; }

export interface Citation {
  chunk_id: string; document_id: string; document_title: string;
  page_no: number | null; section_path: string; snippet: string; score: number; source: string;
}
export interface SearchHit {
  document_id: string; title: string; category_id: string | null; tags: string[];
  attributes: Record<string, any>; score: number; matched_by: string[]; citations: Citation[];
}
export interface SearchResult {
  query: string; mode: string; total: number; took_ms: number; cache_hit: boolean;
  hits: SearchHit[]; answer?: string | null;
}

export interface FileProgress {
  upload_id?: string; document_id?: string; filename: string; size_bytes: number;
  status: string; phase: string; stage: string; progress: number; stage_progress: number;
  done_units: number; total_units: number; message: string; retry_count: number;
  error: string | null; updated_at: string | null;
}
export interface BatchProgress {
  batch_id: string; status: string; progress: number; total_files: number;
  succeeded: number; failed: number; cancelled: number; running: number;
  files: FileProgress[]; generated_at: string; seq: number;
}

export interface Job {
  id: string; document_id: string; stage: string; state: string; progress: number;
  message: string; retry_count: number; error: string | null; queue: string;
  is_large: boolean; started_at: string | null; finished_at: string | null;
}

export interface Overview {
  total_documents: number; indexed_documents: number; failed_documents: number;
  total_chunks: number; total_storage_bytes: number; knowledge_bases: number;
  queries_today: number; active_users_today: number; ingest_success_rate: number;
  p95_query_latency_ms: number; pending_jobs: number;
  status_distribution: Record<string, number>;
  sensitivity_distribution: Record<string, number>;
}
export interface TrendPoint {
  date: string; docs_created: number; docs_indexed: number; queries: number;
  upload_bytes: number; active_users: number;
}
export interface Report {
  overview: Overview; trend: TrendPoint[];
  by_department: { name: string; value: number }[];
  by_tag: { name: string; value: number }[];
  ingest: Record<string, any>;
}
export interface DiffLine { type: string; old_no: number | null; new_no: number | null; text: string; }
export interface DiffResult { from_version: number; to_version: number; added: number; removed: number; changed_ratio: number; lines: DiffLine[]; }
