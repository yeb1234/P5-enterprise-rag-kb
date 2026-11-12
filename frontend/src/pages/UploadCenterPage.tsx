import { useMemo, useState } from 'react';
import {
  Alert, Button, Card, Input, Progress, Select, Space, Table, Tag, Upload, message,
} from 'antd';
import { InboxOutlined, CloudUploadOutlined, ReloadOutlined, LinkOutlined } from '@ant-design/icons';
import { kbApi, uploadApi } from '../api/endpoints';
import { useProgressStream } from '../hooks/useProgressStream';
import { useEffect } from 'react';
import type { BatchProgress, FileProgress, Kb } from '../types';

const { Dragger } = Upload;
const CLIENT_HASH_LIMIT = 64 * 1024 * 1024;   // 超过 64MB 不在浏览器整文件哈希（避免内存爆掉）
const FILE_CONCURRENCY = 3;
const CHUNK_CONCURRENCY = 4;

function guessMime(file: File): string {
  if (file.type) return file.type;
  const ext = file.name.split('.').pop()?.toLowerCase();
  return ({
    pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    txt: 'text/plain', md: 'text/markdown', csv: 'text/csv', json: 'application/json',
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
  } as Record<string, string>)[ext || ''] || 'application/octet-stream';
}

async function sha256(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** 有限并发池：批内 3 个文件 × 单文件 4 个分片 = 最多 12 个在途请求 */
async function pool<T>(items: T[], limit: number, fn: (item: T) => Promise<void>) {
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (cursor < items.length) {
      const idx = cursor++;
      await fn(items[idx]);
    }
  });
  await Promise.all(workers);
}

const statusColor: Record<string, string> = {
  succeeded: 'green', indexed: 'green', completed: 'green', instant: 'cyan',
  failed: 'red', cancelled: 'default', uploading: 'processing', uploading_ingest: 'processing',
  queued: 'blue', running: 'processing', parsing: 'processing',
};

export default function UploadCenterPage() {
  const [kbs, setKbs] = useState<Kb[]>([]);
  const [kbId, setKbId] = useState<string>('');
  const [files, setFiles] = useState<File[]>([]);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [localRows, setLocalRows] = useState<Record<string, FileProgress>>({});
  const { snapshot, connected, degraded } = useProgressStream(batchId);

  useEffect(() => {
    kbApi.list().then((r) => {
      setKbs(r.data.items);
      if (r.data.items.length) setKbId(r.data.items[0].id);
    }).catch((e) => message.error(e.message));
  }, []);

  const setLocal = (filename: string, patch: Partial<FileProgress>) => {
    setLocalRows((prev) => ({
      ...prev,
      [filename]: {
        filename, size_bytes: 0, status: 'uploading', phase: 'uploading', stage: 'upload',
        progress: 0, stage_progress: 0, done_units: 0, total_units: 0, message: '',
        retry_count: 0, error: null, updated_at: null,
        ...(prev[filename] || {}), ...patch,
      },
    }));
  };

  const uploadOne = async (file: File, batch: string) => {
    // 大文件把哈希交给服务端流式计算：不能在浏览器 arrayBuffer() 一个 2GB 文件
    const checksum = file.size <= CLIENT_HASH_LIMIT
      ? await sha256(file)
      : `skip:${file.size}:${file.lastModified}:${file.name}`.slice(0, 60).padEnd(16, '0');
    const init = await uploadApi.init({
      batch_id: batch, filename: file.name, size_bytes: file.size, checksum,
      mime_type: guessMime(file), kb_id: kbId,
    });
    const data = init.data;
    if (data.instant) {
      setLocal(file.name, { status: 'succeeded', phase: 'done', stage: 'done', progress: 100,
        message: '秒传命中，复用已有解析结果' });
      return;
    }
    const done = new Set(data.received_chunks);
    const pending = Array.from({ length: data.total_chunks }, (_, i) => i).filter((i) => !done.has(i));
    setLocal(file.name, {
      size_bytes: file.size, status: 'uploading', stage: 'upload',
      total_units: data.total_chunks, done_units: done.size,
      progress: (done.size / data.total_chunks) * 20,
      message: '分片传输中',
    });
    await pool(pending, CHUNK_CONCURRENCY, async (idx) => {
      const blob = file.slice(idx * data.chunk_size, Math.min((idx + 1) * data.chunk_size, file.size));
      await uploadApi.putChunk(data.upload_id, idx, blob);
      setLocal(file.name, {
        done_units: done.size + pending.indexOf(idx) + 1,
        progress: Math.min(19, ((done.size + pending.indexOf(idx) + 1) / data.total_chunks) * 20),
        message: `分片 ${done.size + pending.indexOf(idx) + 1}/${data.total_chunks}`,
      });
    });
    setLocal(file.name, { status: 'queued', phase: 'ingesting', stage: 'queued',
      progress: 20, message: '分片已合并，等待异步解析' });
    await uploadApi.complete(data.upload_id);
  };

  const start = async () => {
    if (!kbId) { message.warning('请先选择知识库'); return; }
    if (!files.length) { message.warning('请先选择文件'); return; }
    setBusy(true);
    setNote('');
    setLocalRows({});
    try {
      const totalBytes = files.reduce((s, f) => s + f.size, 0);
      const batch = await uploadApi.initBatch(kbId, files.length, totalBytes);
      const id = batch.data.batch_id;
      setBatchId(id);
      const queue = [...files];
      await pool(queue, FILE_CONCURRENCY, async (file) => {
        try { await uploadOne(file, id); }
        catch (e: any) {
          setLocal(file.name, { status: 'failed', phase: 'failed', error: e.message });
          message.error(`${file.name} 失败：${e.message}`);
        }
      });
      message.success('文件已全部提交，后台解析进度见下方');
    } catch (e: any) {
      setNote(e.message);
      message.error(e.message);
    } finally { setBusy(false); }
  };

  const rows: FileProgress[] = useMemo(() => {
    if (snapshot?.files?.length) return snapshot.files;
    return Object.values(localRows);
  }, [snapshot, localRows]);

  const snap: BatchProgress | null = snapshot;
  const overall = snap?.progress ?? (
    rows.length ? rows.reduce((s, r) => s + r.progress, 0) / rows.length : 0
  );

  return (
    <div>
      <div className="page-header">
        <h2>大文件与批量上传</h2>
        <p>分片断点续传 · 秒传去重 · 服务端合并 · Celery 异步解析 · SSE 实时进度</p>
      </div>

      <Alert
        type="info" showIcon style={{ marginBottom: 16 }}
        message="进度链路说明"
        description="每个文件按 8MiB 分片并发上传并支持续传；合并发生在对象存储服务端，API 不整文件读进内存。
        超过 100MB 或 200 页的文件走 heavy 队列并按页并行解析。进度真值在 PostgreSQL，Redis 只做实时推送；
        刷新页面或 SSE 断线都会从快照恢复。"
      />

      <Card style={{ marginBottom: 16 }}>
        <Space wrap style={{ marginBottom: 14 }}>
          <span>目标知识库：</span>
          <Select style={{ width: 260 }} value={kbId} onChange={setKbId}
            options={kbs.map((k) => ({ value: k.id, label: k.name }))} placeholder="选择知识库" />
          <Button type="primary" icon={<CloudUploadOutlined />} onClick={start} loading={busy}
            disabled={!files.length}>
            开始上传（{files.length} 个文件）
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => { setFiles([]); setBatchId(null); setLocalRows({}); }}>
            清空
          </Button>
          <Input style={{ width: 260 }} disabled value={batchId || '尚未创建批次'} prefix={<LinkOutlined />} />
        </Space>

        <Dragger
          multiple
          beforeUpload={(file, list) => {
            setFiles(list.map((f: any) => f.originFileObj || f));
            return false;
          }}
          onRemove={(file) => setFiles((prev) => prev.filter((f) => f.name !== file.name))}
          fileList={files.map((f) => ({ uid: f.name, name: f.name, size: f.size, status: 'done' } as any))}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽多个文件到此处</p>
          <p className="ant-upload-hint">单文件上限 2GB；PDF / Word / Excel / PPT / Markdown / 文本；可执行与宏文件会被拒绝</p>
        </Dragger>
      </Card>

      <Card title="批次进度">
        <Space style={{ marginBottom: 12 }}>
          <Tag color={connected ? 'green' : degraded ? 'orange' : 'default'}>
            {connected ? 'SSE 实时连接' : degraded ? '降级轮询 PG' : '等待连接'}
          </Tag>
          {snap && <Tag color="blue">状态：{snap.status}</Tag>}
          {snap && <Tag>成功 {snap.succeeded} / 失败 {snap.failed} / 进行中 {snap.running}</Tag>}
        </Space>
        <Progress percent={Math.round(overall)} status={snap?.status === 'partial' ? 'exception' : undefined} />
        <Table
          rowKey={(r) => r.upload_id || r.filename}
          size="small" dataSource={rows} pagination={false}
          columns={[
            { title: '文件名', dataIndex: 'filename', ellipsis: true, width: '28%' },
            { title: '大小', dataIndex: 'size_bytes', width: 100,
              render: (v: number) => `${(v / 1024 / 1024).toFixed(1)} MB` },
            { title: '阶段', dataIndex: 'stage', width: 100, render: (s: string) => <Tag>{s}</Tag> },
            { title: '进度', dataIndex: 'progress', width: 200,
              render: (p: number, r) => <Progress percent={Math.round(p)} size="small"
                status={r.status === 'failed' ? 'exception' : undefined} /> },
            { title: '说明', dataIndex: 'message', ellipsis: true,
              render: (m: string, r) => r.error ? <span style={{ color: '#dc2626' }}>{r.error}</span> : m },
            { title: '状态', dataIndex: 'status', width: 110,
              render: (s: string) => <Tag color={statusColor[s] || 'default'}>{s}</Tag> },
          ]}
          locale={{ emptyText: '选择文件并点击「开始上传」后，这里会实时显示每个文件的进度' }}
        />
      </Card>

      {note && <Alert type="error" message={note} style={{ marginTop: 12 }} />}
    </div>
  );
}
