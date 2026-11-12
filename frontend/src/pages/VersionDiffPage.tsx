import { useEffect, useState } from 'react';
import { Button, Card, Empty, Select, Space, Spin, Tag, message } from 'antd';
import { useSearchParams } from 'react-router-dom';
import { docApi } from '../api/endpoints';
import type { DiffResult, DocItem } from '../types';

export default function VersionDiffPage() {
  const [params] = useSearchParams();
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [docId, setDocId] = useState<string | undefined>(params.get('doc') || undefined);
  const [versions, setVersions] = useState<any[]>([]);
  const [fromV, setFromV] = useState<number | undefined>();
  const [toV, setToV] = useState<number | undefined>();
  const [diff, setDiff] = useState<DiffResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    docApi.list({ page_size: 100 }).then((r) => setDocs(r.data.items)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!docId) return;
    docApi.versions(docId).then((r) => {
      setVersions(r.data);
      if (r.data.length >= 2) { setFromV(r.data[1].version_no); setToV(r.data[0].version_no); }
      else if (r.data.length === 1) { setFromV(1); setToV(1); }
      setDiff(null);
    }).catch((e) => message.error(e.message));
  }, [docId]);

  const compare = async () => {
    if (!docId || !fromV || !toV) return;
    if (fromV >= toV) { message.warning('目标版本必须大于起始版本'); return; }
    setLoading(true);
    try { const r = await docApi.compare(docId, fromV, toV); setDiff(r.data); }
    catch (e: any) { message.error(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div>
      <div className="page-header"><h2>版本管理与历史对比</h2>
        <p>每次编辑生成不可变快照；diff 基于行级 SequenceMatcher；回滚会追加新版本而不是改历史</p>
      </div>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select showSearch placeholder="选择文档" style={{ width: 320 }} value={docId} onChange={setDocId}
            filterOption={(input, opt: any) => (opt?.label || '').includes(input)}
            options={docs.map((d) => ({ value: d.id, label: d.title }))} />
          <Select placeholder="起始版本" style={{ width: 140 }} value={fromV} onChange={setFromV}
            options={versions.map((v) => ({ value: v.version_no, label: `v${v.version_no}` }))} />
          <Select placeholder="目标版本" style={{ width: 140 }} value={toV} onChange={setToV}
            options={versions.map((v) => ({ value: v.version_no, label: `v${v.version_no}` }))} />
          <Button type="primary" onClick={compare} loading={loading} disabled={!docId || !fromV || !toV}>对比</Button>
        </Space>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16 }}>
        <Card title="版本历史">
          {versions.length === 0 ? <Empty description="暂无版本" /> : versions.map((v) => (
            <div key={v.id} style={{ padding: '8px 0', borderBottom: '1px solid #f0f2f6' }}>
              <Space direction="vertical" size={2}>
                <Space><Tag color="blue">v{v.version_no}</Tag><span>{v.change_summary || '无说明'}</span></Space>
                <span className="muted">{v.created_at?.replace('T', ' ').slice(0, 19)} · +{v.diff_stats?.added || 0} / -{v.diff_stats?.removed || 0}</span>
                <Button size="small" type="link" style={{ paddingLeft: 0 }} onClick={async () => {
                  await docApi.restoreVersion(docId!, v.version_no);
                  message.success(`已从 v${v.version_no} 回滚并追加新版本`);
                  docApi.versions(docId!).then((r) => setVersions(r.data));
                }}>回滚到此版本</Button>
              </Space>
            </div>
          ))}
        </Card>

        <Card title={diff ? `v${diff.from_version} → v${diff.to_version} · 新增 ${diff.added} 行 / 删除 ${diff.removed} 行` : '差异明细'}>
          {loading ? <Spin /> : !diff ? <Empty description="选择两个版本后点击对比" /> : (
            <div style={{ maxHeight: 560, overflow: 'auto', border: '1px solid #eef1f6', borderRadius: 8 }}>
              {diff.lines.map((line, i) => (
                <div key={i} className={`diff-line diff-${line.type === 'added' ? 'added' : line.type === 'removed' ? 'removed' : 'equal'}`}>
                  <span className="n">{line.old_no || ''}</span>
                  <span className="n">{line.new_no || ''}</span>
                  <span style={{ width: 14 }}>{line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '}</span>
                  <span>{line.text}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
