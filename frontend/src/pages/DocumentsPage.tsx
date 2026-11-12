import { useEffect, useState } from 'react';
import {
  Button, Card, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, message, Popconfirm,
} from 'antd';
import { PlusOutlined, SearchOutlined, DownloadOutlined, BranchesOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { docApi, kbApi, categoryApi } from '../api/endpoints';
import type { DocItem, Kb, Category, Tag as TagType } from '../types';

const statusColor: Record<string, string> = {
  indexed: 'green', pending: 'blue', parsing: 'processing', chunking: 'processing',
  embedding: 'processing', indexing: 'processing', failed: 'red', draft: 'default',
};

function flatten(cats: Category[], depth = 0): { value: string; label: string }[] {
  return cats.flatMap((c) => [
    { value: c.id, label: '　'.repeat(depth) + c.name },
    ...flatten(c.children || [], depth + 1),
  ]);
}

export default function DocumentsPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<DocItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [kbs, setKbs] = useState<Kb[]>([]);
  const [cats, setCats] = useState<Category[]>([]);
  const [tags, setTags] = useState<TagType[]>([]);
  const [filters, setFilters] = useState<any>({ q: '', kb_id: undefined, category_id: undefined,
    tag_id: undefined, status: undefined });
  const [editing, setEditing] = useState<DocItem | 'new' | null>(null);
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const params: any = { page, page_size: 20, ...filters };
      Object.keys(params).forEach((k) => (params[k] === undefined || params[k] === '') && delete params[k]);
      const res = await docApi.list(params);
      setRows(res.data.items);
      setTotal(res.data.total);
    } catch (e: any) { message.error(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    kbApi.list().then((r) => setKbs(r.data.items)).catch(() => {});
    categoryApi.tree().then((r) => setCats(r.data)).catch(() => {});
    categoryApi.tags().then((r) => setTags(r.data)).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [page, filters]);

  const openEditor = (doc: DocItem | 'new') => {
    setEditing(doc);
    if (doc === 'new') form.resetFields();
    else {
      docApi.get(doc.id).then((r) => form.setFieldsValue({
        title: r.data.title, kb_id: r.data.kb_id, category_id: r.data.category_id,
        sensitivity: r.data.sensitivity, attributes: JSON.stringify(r.data.attributes),
        content_text: r.data.content_text || '', change_summary: '',
      })).catch(() => form.setFieldsValue({ title: doc.title }));
    }
  };

  const submit = async () => {
    const values = await form.validateFields();
    let attributes = {};
    try { attributes = values.attributes ? JSON.parse(values.attributes) : {}; }
    catch { message.error('自定义属性必须是合法 JSON'); return; }
    const payload = { ...values, attributes, tag_ids: values.tag_ids || [] };
    delete payload.attributesRaw;
    try {
      if (editing === 'new') await docApi.create(payload);
      else await docApi.patch((editing as DocItem).id, payload);
      message.success('已保存并触发重新索引');
      setEditing(null);
      load();
    } catch (e: any) { message.error(e.message); }
  };

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between' }}>
        <div><h2>文档管理</h2><p>多级分类 · 标签/属性筛选 · 版本快照 · 软删除三方联动</p></div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor('new')}>新建文档</Button>
      </div>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search allowClear placeholder="标题 / 正文全文检索" style={{ width: 250 }}
            value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })}
            onSearch={() => { setPage(1); load(); }} enterButton={<SearchOutlined />} />
          <Select allowClear placeholder="知识库" style={{ width: 180 }} value={filters.kb_id}
            onChange={(v) => setFilters({ ...filters, kb_id: v })}
            options={kbs.map((k) => ({ value: k.id, label: k.name }))} />
          <Select allowClear placeholder="分类（含子分类）" style={{ width: 200 }} value={filters.category_id}
            onChange={(v) => setFilters({ ...filters, category_id: v })} options={flatten(cats)} />
          <Select allowClear placeholder="标签" style={{ width: 150 }} value={filters.tag_id}
            onChange={(v) => setFilters({ ...filters, tag_id: v })}
            options={tags.map((t) => ({ value: t.id, label: t.name }))} />
          <Select allowClear placeholder="状态" style={{ width: 140 }} value={filters.status}
            onChange={(v) => setFilters({ ...filters, status: v })}
            options={['indexed', 'pending', 'failed', 'draft'].map((s) => ({ value: s, label: s }))} />
          <Button onClick={() => { setPage(1); load(); }}>刷新</Button>
        </Space>
      </Card>

      <Table rowKey="id" dataSource={rows} loading={loading} size="middle"
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 篇` }}
        columns={[
          { title: '标题', dataIndex: 'title', ellipsis: true, width: '28%' },
          { title: '状态', dataIndex: 'status', width: 110,
            render: (s: string, r) => <Tag color={statusColor[s]}>{r.error_message || s}</Tag> },
          { title: '密级', dataIndex: 'sensitivity', width: 100 },
          { title: '版本', dataIndex: 'current_version', width: 70, render: (v) => `v${v}` },
          { title: '切片', dataIndex: 'chunk_count', width: 80 },
          { title: '大小', dataIndex: 'size_bytes', width: 100,
            render: (v: number) => `${(v / 1024).toFixed(1)} KB` },
          { title: '标签', dataIndex: 'tags', width: 150,
            render: (ts: TagType[]) => <div className="tag-row">{ts?.map((t) => <Tag key={t.id} color={t.color}>{t.name}</Tag>)}</div> },
          { title: '更新时间', dataIndex: 'updated_at', width: 170,
            render: (v: string) => v?.replace('T', ' ').slice(0, 19) },
          { title: '操作', width: 250, render: (_: any, r: DocItem) => (
            <Space size={4}>
              <Button size="small" type="link" onClick={() => openEditor(r)}>编辑</Button>
              <Button size="small" type="link" icon={<BranchesOutlined />}
                onClick={() => navigate(`/versions?doc=${r.id}`)}>版本</Button>
              <Button size="small" type="link" icon={<DownloadOutlined />}
                onClick={async () => {
                  try { const d = await docApi.download(r.id); window.open(d.data.url, '_blank'); }
                  catch (e: any) { message.info(e.message); }
                }}>下载</Button>
              <Popconfirm title="确认删除？向量与索引会异步清理" onConfirm={async () => {
                await docApi.remove(r.id); message.success('已删除'); load();
              }}>
                <Button size="small" type="link" danger>删除</Button>
              </Popconfirm>
            </Space>
          ) },
        ]}
      />

      <Modal open={!!editing} title={editing === 'new' ? '新建文档' : '编辑文档（自动生成新版本）'}
        onCancel={() => setEditing(null)} onOk={submit} width={640} destroyOnClose>
        <Form form={form} layout="vertical" initialValues={{ sensitivity: 'internal', kb_id: kbs[0]?.id }}>
          <Form.Item name="title" label="标题" rules={[{ required: true }]}><Input /></Form.Item>
          <Space style={{ width: '100%' }} size={12}>
            <Form.Item name="kb_id" label="知识库" rules={[{ required: true }]} style={{ width: 240 }}>
              <Select options={kbs.map((k) => ({ value: k.id, label: k.name }))} />
            </Form.Item>
            <Form.Item name="category_id" label="分类" style={{ width: 240 }}>
              <Select allowClear options={flatten(cats)} />
            </Form.Item>
          </Space>
          <Space style={{ width: '100%' }} size={12}>
            <Form.Item name="sensitivity" label="密级" style={{ width: 240 }}>
              <Select options={['public', 'internal', 'dept', 'confidential'].map((s) => ({ value: s, label: s }))} />
            </Form.Item>
            <Form.Item name="tag_ids" label="标签" style={{ width: 240 }}>
              <Select mode="multiple" options={tags.map((t) => ({ value: t.id, label: t.name }))} />
            </Form.Item>
          </Space>
          <Form.Item name="content_text" label="正文（Markdown / 纯文本）">
            <Input.TextArea rows={6} placeholder="在线编辑内容会生成版本快照并触发重建索引" />
          </Form.Item>
          <Form.Item name="attributes" label="自定义属性（JSON，支持 GIN 索引筛选）">
            <Input.TextArea rows={2} placeholder='{"年度":"2026","产品":"G502"}' />
          </Form.Item>
          <Form.Item name="change_summary" label="变更说明"><Input placeholder="本次改了什么" /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
