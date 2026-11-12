import api, { API_BASE } from './client';
import type {
  BatchProgress, Category, DiffResult, DocItem, Job, Kb, Page,
  Report, SearchResult, Tag, User,
} from '../types';

export const authApi = {
  login: (email: string, password: string, tenant_slug = 'demo') =>
    api.post<{ access_token: string; user: User }>('/auth/login', { email, password, tenant_slug }),
  logout: () => api.post('/auth/logout'),
  me: () => api.get<User>('/auth/me'),
  bootstrap: () => api.post('/auth/bootstrap'),
};

export const kbApi = {
  list: () => api.get<Page<Kb>>('/knowledge-bases', { params: { page_size: 100 } }),
  create: (data: any) => api.post('/knowledge-bases', data),
};

export const categoryApi = {
  tree: (kbId?: string) => api.get<Category[]>('/categories/tree', { params: { kb_id: kbId } }),
  create: (data: any) => api.post('/categories', data),
  tags: () => api.get<Tag[]>('/tags'),
};

export const docApi = {
  list: (params: any) => api.get<Page<DocItem>>('/documents', { params }),
  get: (id: string) => api.get<DocItem>(`/documents/${id}`),
  create: (data: any) => api.post<DocItem>('/documents', data),
  patch: (id: string, data: any) => api.patch<DocItem>(`/documents/${id}`, data),
  remove: (id: string) => api.delete(`/documents/${id}`),
  download: (id: string) => api.get(`/documents/${id}/download`),
  versions: (id: string) => api.get<any[]>(`/documents/${id}/versions`),
  versionDetail: (id: string, v: number) => api.get<any>(`/documents/${id}/versions/${v}`),
  compare: (id: string, from_version: number, to_version: number) =>
    api.post<DiffResult>(`/documents/${id}/versions/compare`, { from_version, to_version }),
  restoreVersion: (id: string, v: number) => api.post(`/documents/${id}/versions/${v}/restore`),
};

export const searchApi = {
  query: (data: any) => api.post<SearchResult>('/search', data),
  answerUrl: `${API_BASE}/search/answer`,
};

export interface InitUploadResult {
  upload_id: string; batch_id: string; document_id: string | null; status: string;
  instant: boolean; chunk_size: number; total_chunks: number;
  received_chunks: number[]; expires_at: string;
}

export const uploadApi = {
  initBatch: (kb_id: string, total_files: number, total_bytes: number) =>
    api.post<{ batch_id: string }>('/uploads/batches', { kb_id, total_files, total_bytes }),
  init: (data: any) => api.post<InitUploadResult>('/uploads/init', data),
  putChunk: (uploadId: string, idx: number, blob: Blob) =>
    api.put(`/uploads/${uploadId}/chunks/${idx}`, blob, {
      headers: { 'Content-Type': 'application/octet-stream' },
      timeout: 300000,
    }),
  status: (uploadId: string) => api.get(`/uploads/${uploadId}/status`),
  complete: (uploadId: string) => api.post(`/uploads/${uploadId}/complete`),
  cancel: (uploadId: string) => api.delete(`/uploads/${uploadId}`),
  progress: (batchId: string) => api.get<BatchProgress>(`/uploads/batches/${batchId}/progress`),
  streamUrl: (batchId: string) => `${API_BASE}/uploads/batches/${batchId}/stream`,
};

export const taskApi = {
  list: (params: any) => api.get<Page<Job>>('/tasks', { params }),
  retry: (id: string) => api.post(`/tasks/${id}/retry`),
  cancel: (id: string) => api.post(`/tasks/${id}/cancel`),
};

export const statsApi = {
  report: (days = 14) => api.get<Report>('/stats/report', { params: { days } }),
  exportCsv: () => `${API_BASE}/stats/export.csv`,
};

export const auditApi = {
  list: (params: any) => api.get('/audit-logs', { params }),
};
