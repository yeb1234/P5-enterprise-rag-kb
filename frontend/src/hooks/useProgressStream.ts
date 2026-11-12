import { useEffect, useRef, useState } from 'react';
import { getAccessToken } from '../api/client';
import { uploadApi } from '../api/endpoints';
import type { BatchProgress } from '../types';

/**
 * 进度订阅：用 fetch + ReadableStream 手解析 SSE，而不是 EventSource。
 * 原因：EventSource 不能自定义 Authorization 头；企业系统不该把 JWT 放到 query string
 * （会被 nginx / 浏览器历史 / 日志记下来）。fetch 可以带 Bearer，也能拿到断流异常。
 *
 * 三级容错：
 *  1. 建连先拉一次 /progress 权威快照（刷新页面不丢进度）；
 *  2. 正常走 SSE，收到事件后节流刷新快照；
 *  3. 流失败自动降级成 2s 轮询 PG，用户看到的进度不会「假死」。
 */
export function useProgressStream(batchId: string | null) {
  const [snapshot, setSnapshot] = useState<BatchProgress | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [connected, setConnected] = useState(false);
  const [degraded, setDegraded] = useState(false);
  const refreshTimer = useRef<any>(null);
  const pollTimer = useRef<any>(null);

  useEffect(() => {
    if (!batchId) return;
    let stopped = false;
    let controller: AbortController | null = null;

    const poll = async () => {
      try {
        const res = await uploadApi.progress(batchId);
        if (!stopped) setSnapshot(res.data);
      } catch { /* 网络抖动由下一次轮询/快照兜底 */ }
    };

    const scheduleRefresh = () => {
      if (refreshTimer.current) return;
      refreshTimer.current = setTimeout(() => {
        refreshTimer.current = null;
        poll();
      }, 600);
    };

    const handleEvent = (event: string, payload: any) => {
      if (stopped) return;
      if (event === 'snapshot') {
        setSnapshot(payload);
      } else {
        setEvents((prev) => [...prev.slice(-40), { event, ...payload }]);
        scheduleRefresh();
      }
      if (event === 'close') {
        stopped = true;
        poll();
      }
    };

    const startPollingFallback = () => {
      if (pollTimer.current) return;
      setDegraded(true);
      pollTimer.current = setInterval(() => { if (!stopped) poll(); }, 2000);
    };

    const start = async () => {
      await poll();
      try {
        controller = new AbortController();
        const res = await fetch(uploadApi.streamUrl(batchId), {
          headers: { Authorization: `Bearer ${getAccessToken() || ''}` },
          credentials: 'include',
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`);
        setConnected(true);
        setDegraded(false);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (!stopped) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split('\n\n');
          buffer = frames.pop() || '';
          for (const frame of frames) {
            if (!frame.trim() || frame.trim().startsWith(':')) continue;
            let event = 'message';
            let data = '';
            for (const line of frame.split('\n')) {
              if (line.startsWith('event:')) event = line.slice(6).trim();
              else if (line.startsWith('data:')) data += line.slice(5).trim();
            }
            if (data) {
              try { handleEvent(event, JSON.parse(data)); } catch { /* 忽略坏帧 */ }
            }
          }
        }
      } catch (err) {
        if (!stopped) startPollingFallback();
      } finally {
        setConnected(false);
      }
    };

    start();
    return () => {
      stopped = true;
      controller?.abort();
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      if (pollTimer.current) clearInterval(pollTimer.current);
      refreshTimer.current = null;
      pollTimer.current = null;
    };
  }, [batchId]);

  return { snapshot, events, connected, degraded };
}
