import { useState } from 'react';
import { Button, Card, Empty, Input, Select, Space, Spin, Tag, Collapse, message } from 'antd';
import { SearchOutlined, RobotOutlined } from '@ant-design/icons';
import { kbApi, searchApi } from '../api/endpoints';
import { getAccessToken } from '../api/client';
import { useEffect } from 'react';
import type { SearchResult } from '../types';

export default function SearchPage() {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('hybrid');
  const [kbId, setKbId] = useState<string | undefined>();
  const [kbs, setKbs] = useState<any[]>([]);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState('');
  const [answering, setAnswering] = useState(false);
  const [answerMeta, setAnswerMeta] = useState<any>(null);

  useEffect(() => { kbApi.list().then((r) => setKbs(r.data.items)).catch(() => {}); }, []);

  const payload = () => ({
    query, mode, top_k: 20, page: 1, page_size: 20,
    filters: { kb_ids: kbId ? [kbId] : [], status: 'indexed' },
  });

  const doSearch = async () => {
    if (!query.trim()) { message.warning('请输入问题'); return; }
    setLoading(true);
    try { const res = await searchApi.query(payload()); setResult(res.data); }
    catch (e: any) { message.error(e.message); }
    finally { setLoading(false); }
  };

  /** 流式问答：fetch + ReadableStream 解析 SSE，EventSource 不支持自定义 Authorization 头 */
  const doAnswer = async () => {
    if (!query.trim()) { message.warning('请输入问题'); return; }
    setAnswering(true); setAnswer(''); setAnswerMeta(null);
    try {
      const res = await fetch(searchApi.answerUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getAccessToken() || ''}`,
        },
        credentials: 'include',
        body: JSON.stringify({ query, filters: { kb_ids: kbId ? [kbId] : [] }, top_k: 8 }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';
        for (const frame of frames) {
          if (!frame.trim() || frame.startsWith(':')) continue;
          let event = 'message', data = '';
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim();
            else if (line.startsWith('data:')) data += line.slice(5).trim();
          }
          if (!data) continue;
          const parsed = JSON.parse(data);
          if (event === 'token') setAnswer((prev) => prev + parsed.text);
          else if (event === 'done') setAnswerMeta(parsed);
          else if (event === 'error') message.error(parsed.message);
        }
      }
    } catch (e: any) { message.error(e.message); }
    finally { setAnswering(false); }
  };

  return (
    <div>
      <div className="page-header"><h2>知识检索与问答</h2>
        <p>PostgreSQL 全文 + 标签/属性筛选 ⊕ Milvus 语义召回 → RRF 融合 → 权限二次过滤 → 带引用生成</p>
      </div>

      <Card style={{ marginBottom: 16 }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input size="large" placeholder="输入问题，例如：G502 的 DPI 切换键在哪里？"
            value={query} onChange={(e) => setQuery(e.target.value)} onPressEnter={doSearch} />
          <Select size="large" style={{ width: 150 }} value={mode} onChange={setMode}
            options={[{ value: 'hybrid', label: '混合检索' }, { value: 'keyword', label: '仅关键词' },
                      { value: 'semantic', label: '仅语义' }]} />
          <Button size="large" type="primary" icon={<SearchOutlined />} onClick={doSearch} loading={loading}>检索</Button>
          <Button size="large" icon={<RobotOutlined />} onClick={doAnswer} loading={answering}>生成答案</Button>
        </Space.Compact>
        <div style={{ marginTop: 10 }}>
          <Select allowClear placeholder="限定知识库（可选）" style={{ width: 260 }} value={kbId}
            onChange={setKbId} options={kbs.map((k) => ({ value: k.id, label: k.name }))} />
        </div>
      </Card>

      {(answering || answer) && (
        <Card title={<span><RobotOutlined /> DeepSeek 生成（SSE 流式）</span>} style={{ marginBottom: 16 }}>
          <div className="answer-box">{answer || <span className="muted">正在检索并生成…</span>}
            {answering && <Spin size="small" style={{ marginLeft: 8 }} />}
          </div>
          {answerMeta && (
            <div style={{ marginTop: 10 }}>
              <Space wrap>
                <Tag color={answerMeta.blocked ? 'red' : 'green'}>{answerMeta.blocked ? '已拦截' : '完成'}</Tag>
                <Tag>意图 {answerMeta.intent}</Tag>
                <Tag>模型 {answerMeta.metrics?.model}</Tag>
                <Tag>引用 {answerMeta.citations?.length || 0} 条</Tag>
              </Space>
            </div>
          )}
        </Card>
      )}

      <Card title={result ? `检索结果 · ${result.total} 篇文档 · ${result.took_ms} ms ${result.cache_hit ? '（缓存命中）' : ''}` : '检索结果'}>
        {!result ? <Empty description="输入问题开始检索" /> : result.hits.length === 0
          ? <Empty description="没有可依据的资料（可能全部因权限被过滤）" />
          : result.hits.map((hit) => (
            <div key={hit.document_id} style={{ borderBottom: '1px solid #f0f2f6', padding: '12px 0' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <strong>{hit.title}</strong>
                <Space>{hit.matched_by.map((m) => <Tag color="blue" key={m}>{m}</Tag>)}</Space>
              </div>
              <Collapse ghost size="small" items={[{
                key: 'citations',
                label: <span className="muted">展开 {hit.citations.length} 条引文（文档名 / 页码 / 原文片段）</span>,
                children: hit.citations.map((c) => (
                  <div className="citation-box" key={c.chunk_id}>
                    <div className="citation-meta">{c.document_title} · 第 {c.page_no || '-'} 页 · {c.section_path || '正文'} · {c.source}</div>
                    {c.snippet}
                  </div>
                )),
              }]} />
            </div>
          ))}
      </Card>
    </div>
  );
}
