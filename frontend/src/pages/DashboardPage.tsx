import { useEffect, useState } from 'react';
import { Button, Spin, Empty, Tag, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, PieChart, Pie, Cell, Legend,
} from 'recharts';
import { statsApi } from '../api/endpoints';
import { getAccessToken } from '../api/client';
import type { Report } from '../types';

const COLORS = ['#2563eb', '#16a34a', '#f59e0b', '#dc2626', '#7c3aed', '#0891b2'];

function fmtBytes(n: number) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  return `${(n / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

export default function DashboardPage() {
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    statsApi.report(14).then((r) => setReport(r.data)).catch((e) => message.error(e.message))
      .finally(() => setLoading(false));
  }, []);

  const exportCsv = async () => {
    const res = await fetch(statsApi.exportCsv(), { headers: { Authorization: `Bearer ${getAccessToken()}` } });
    if (!res.ok) { message.error('导出失败'); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'kb-report.csv'; a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) return <Spin />;
  if (!report) return <Empty description="暂无数据，请稍后刷新" />;

  const o = report.overview;
  const cards = [
    { label: '文档总数', value: o.total_documents, hint: `已索引 ${o.indexed_documents} · 失败 ${o.failed_documents}` },
    { label: '知识切片', value: o.total_chunks.toLocaleString(), hint: 'Milvus 语义检索单元' },
    { label: '存储占用', value: fmtBytes(o.total_storage_bytes), hint: '原始文件 + 版本快照' },
    { label: '今日问答', value: o.queries_today, hint: `活跃用户 ${o.active_users_today}` },
    { label: '入库成功率', value: `${o.ingest_success_rate.toFixed(1)}%`, hint: `待处理任务 ${o.pending_jobs}` },
    { label: 'P95 查询延迟', value: `${o.p95_query_latency_ms} ms`, hint: '来自每日预聚合' },
  ];

  const pieData = Object.entries(o.status_distribution).map(([name, value]) => ({ name, value }));

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div><h2>知识库数据看板</h2><p>口径统一来自 PG 预聚合日报；明细埋点在 metric_events，可供回溯</p></div>
        <Button icon={<DownloadOutlined />} onClick={exportCsv}>导出报表 CSV</Button>
      </div>

      <div className="card-grid" style={{ marginBottom: 16 }}>
        {cards.map((c) => (
          <div className="stat-card" key={c.label}>
            <div className="label">{c.label}</div>
            <div className="value">{c.value}</div>
            <div className="hint">{c.hint}</div>
          </div>
        ))}
      </div>

      <div className="panel">
        <h3>14 天趋势 · 文档入库与检索量</h3>
        {report.trend.length ? (
          <ResponsiveContainer width="100%" height={270}>
            <AreaChart data={report.trend}>
              <defs>
                <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#2563eb" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#2563eb" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#eef1f6" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Area type="monotone" dataKey="docs_indexed" name="成功入库" stroke="#2563eb" fill="url(#g1)" />
              <Area type="monotone" dataKey="queries" name="检索次数" stroke="#16a34a" fill="#16a34a" fillOpacity={0.08} />
            </AreaChart>
          </ResponsiveContainer>
        ) : <Empty description="尚无日报数据，Celery beat 每天 01:00 预聚合" />}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
        <div className="panel">
          <h3>部门文档分布</h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={report.by_department}>
              <CartesianGrid strokeDasharray="3 3" stroke="#eef1f6" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="value" name="文档数" fill="#2563eb" radius={[5, 5, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="panel">
          <h3>热门标签</h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={report.by_tag.slice(0, 8)} layout="vertical">
              <XAxis type="number" tick={{ fontSize: 11 }} />
              <YAxis dataKey="name" type="category" width={78} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="value" name="文档数" fill="#16a34a" radius={[0, 5, 5, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="panel">
          <h3>文档状态构成</h3>
          {pieData.length ? (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie data={pieData} dataKey="value" nameKey="name" outerRadius={78} label>
                  {pieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                </Pie>
                <Tooltip /><Legend />
              </PieChart>
            </ResponsiveContainer>
          ) : <Empty description="暂无文档" />}
        </div>
      </div>

      <div className="panel" style={{ marginTop: 16 }}>
        <h3>入库任务概览</h3>
        <div className="tag-row">
          <Tag>总任务 {report.ingest.total_jobs}</Tag>
          <Tag color="green">成功 {report.ingest.succeeded}</Tag>
          <Tag color="red">失败 {report.ingest.failed}</Tag>
          <Tag color="blue">进行中 {report.ingest.running}</Tag>
          <Tag>P95 耗时 {report.ingest.p95_duration_ms} ms</Tag>
        </div>
        <p className="muted" style={{ marginTop: 12 }}>
          看板读预聚合表 daily_stats，不扫明细；审计日志只追加，导出报表会写入审计。
        </p>
      </div>
    </div>
  );
}
