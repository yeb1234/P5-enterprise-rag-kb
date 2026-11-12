import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Form, Input, message, Alert } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useAuth } from '../store/AuthContext';
import { authApi } from '../api/endpoints';

export default function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [booting, setBooting] = useState(false);
  const [notice, setNotice] = useState('');

  if (user) { navigate('/', { replace: true }); return null; }

  const onFinish = async (values: any) => {
    setLoading(true);
    try {
      await login(values.email, values.password);
      message.success('登录成功');
      navigate('/', { replace: true });
    } catch (e: any) {
      message.error(e.message || '登录失败');
    } finally { setLoading(false); }
  };

  const bootstrap = async () => {
    setBooting(true);
    try {
      const res = await authApi.bootstrap();
      setNotice('初始化完成：' + res.data.accounts.map((a: any) => a.email).join(' / '));
      message.success('演示数据已创建');
    } catch (e: any) {
      message.info(e.message || '可能已初始化过了');
    } finally { setBooting(false); }
  };

  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1>企业级知识库系统</h1>
        <div className="sub">React · FastAPI · PostgreSQL · Milvus · Redis/Celery · DeepSeek</div>
        {notice && <Alert type="success" message={notice} style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={onFinish} initialValues={{ email: 'admin@demo.local', password: 'Admin@12345' }}>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: 'email' }]}>
            <Input prefix={<UserOutlined />} size="large" placeholder="admin@demo.local" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password prefix={<LockOutlined />} size="large" placeholder="请输入密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={loading}>登录</Button>
        </Form>
        <div className="demo-account">
          <div>首次使用请先「初始化演示数据」，然后用以下账号登录：</div>
          <div>管理员：admin@demo.local / Admin@12345</div>
          <div>研发：dev@demo.local / Dev@12345　访客：viewer@demo.local / View@12345</div>
        </div>
        <Button block style={{ marginTop: 12 }} onClick={bootstrap} loading={booting}>初始化演示数据</Button>
      </div>
    </div>
  );
}
