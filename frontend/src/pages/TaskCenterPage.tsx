import { useEffect, useState } from 'react';
import { Button, Card, Progress, Select, Space, Table, Tag, message } from 'antd';
import { taskApi } from '../api/endpoints';
import type { Job } from '../types';

const stateColor: Record<string, string> = {
  succeeded: 'green', failed: 'red', running: 'processing', queued: 'blue',
  retrying: 'orange', cancelled: 'default',
};

export default function TaskCenterPage() {
  const [rows, setRows] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [state, setState] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const params: any = { page, page_size: 20 };
      if (state) params.state = state;
      const r = await taskApi.list(params);
      setRows(r.data.items); setTotal(r.data.total);
    } catch (e: any) { message.error(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [page, state]);
  useEffect(() => {
    const timer = setInterval(load, 5000);   // 运行中任务自动刷新，学习期够用；生产换 SSE
    return () => clearInterval(timer);
  }, [page, state]);

  return (
    <div>
      <div className="page-header"><h2>任务中心</h2>
        <p>入库任务台账以 PostgreSQL 为真值，Celery result 只作辅助；失败可重试，心跳超时由 beat 自动捞回</p>
      </div>
      <Card>
        <Space style={{ marginBottom: 14 }}>
          <Select allowClear placeholder="按状态筛选" style={{ width: 180 }} value={state} onChange={setState}
            options={['queued', 'running', 'retrying', 'succeeded', 'failed', 'cancelled']
              .map((s) => ({ value: s, label: s }))} />
          <Button onClick={load}>刷新</Button>
          <Tag>5 秒自动刷新</Tag>
        </Space>
        <Table rowKey="id" size="small" dataSource={rows} loading={loading}
          pagination={{ current: page, total, pageSize: 20, onChange: setPage }}
          columns={[
            { title: '文档 ID', dataIndex: 'document_id', width: 200, ellipsis: true },
            { title: '队列', dataIndex: 'queue', width: 110,
              render: (q: string, r) => <Space><Tag color={r.is_large ? 'purple' : 'default'}>{q}</Tag>{r.is_large && <Tag color="purple">大文件</Tag>}</Space> },
            { title: '阶段', dataIndex: 'stage', width: 100, render: (s: string) => <Tag>{s}</Tag> },
            { title: '进度', dataIndex: 'progress', width: 180,
              render: (p: number) => <Progress percent={Math.round(p)} size="small" /> },
            { title: '说明', dataIndex: 'message', ellipsis: true,
              render: (m: string, r) => r.error ? <span style={{ color: '#dc2626' }}>{r.error}</span> : m },
            { title: '重试', dataIndex: 'retry_count', width: 70 },
            { title: '状态', dataIndex: 'state', width: 110,
              render: (s: string) => <Tag color={stateColor[s]}>{s}</Tag> },
            { title: '操作', width: 170, render: (_: any, r: Job) => (
              <Space size={2}>
                {r.state === 'failed' && <Button size="small" type="link" onClick={async () => {
                  await taskApi.retry(r.id); message.success('已重新入队'); load();
                }}>重试</Button>}
                {['queued', 'running'].includes(r.state) && <Button size="small" type="link" danger onClick={async () => {
                  await taskApi.cancel(r.id); message.success('已取消'); load();
                }}>取消</Button>}
              </Space>
            ) },
          ]}
        />
      </Card>
    </div>
  );
}
