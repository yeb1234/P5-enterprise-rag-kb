import axios, { AxiosError } from 'axios';

export const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL || '/api/v1';

const api = axios.create({ baseURL: API_BASE, withCredentials: true, timeout: 120000 });

let accessToken: string | null = null;
export const getAccessToken = () => accessToken;
export const setAccessToken = (token: string | null) => { accessToken = token; };

api.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
  return config;
});

let refreshPromise: Promise<any> | null = null;

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<any>) => {
    const original: any = error.config;
    const url = original?.url || '';
    if (error.response?.status === 401 && !original?._retry && !url.includes('/auth/')) {
      original._retry = true;
      try {
        refreshPromise = refreshPromise || api.post('/auth/refresh');
        const res = await refreshPromise;
        refreshPromise = null;
        setAccessToken(res.data.access_token);
        original.headers.Authorization = `Bearer ${res.data.access_token}`;
        return api(original);
      } catch (e) {
        refreshPromise = null;
        setAccessToken(null);
        window.dispatchEvent(new Event('kb-logout'));
      }
    }
    const message = error.response?.data?.message || error.message || '请求失败';
    return Promise.reject(new Error(message));
  },
);

/** 页面刷新后 access token 丢失，用 httpOnly 刷新 Cookie 静默恢复会话 */
export async function bootstrapSession(): Promise<any | null> {
  try {
    const res = await api.post('/auth/refresh');
    setAccessToken(res.data.access_token);
    return res.data.user;
  } catch {
    return null;
  }
}

export default api;
