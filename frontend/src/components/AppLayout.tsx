import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import { Layout, Menu, Dropdown, Avatar, Tag, Button, Space } from 'antd';
import {
  DashboardOutlined, FileTextOutlined, CloudUploadOutlined, SearchOutlined,
  BranchesOutlined, ThunderboltOutlined, LogoutOutlined, UserOutlined,
} from '@ant-design/icons';
import { useAuth } from '../store/AuthContext';

const { Sider, Header, Content } = Layout;

const items = [
  { key: '/', icon: <DashboardOutlined />, label: '数据看板' },
  { key: '/documents', icon: <FileTextOutlined />, label: '文档管理' },
  { key: '/upload', icon: <CloudUploadOutlined />, label: '批量上传' },
  { key: '/search', icon: <SearchOutlined />, label: '知识检索' },
  { key: '/versions', icon: <BranchesOutlined />, label: '版本对比' },
  { key: '/tasks', icon: <ThunderboltOutlined />, label: '任务中心' },
];

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="dark" width={216} breakpoint="lg" collapsedWidth="0">
        <div className="app-logo"><span className="mark">K</span> Enterprise KB</div>
        <Menu
          theme="dark" mode="inline" selectedKeys={[location.pathname]}
          items={items} onClick={({ key }) => navigate(key)}
          style={{ borderRight: 0 }}
        />
        <div style={{ position: 'absolute', bottom: 18, left: 20, right: 20, color: '#7c8ba5', fontSize: 11 }}>
          开发骨架 v0.1 · 逐步加固中
        </div>
      </Sider>
      <Layout>
        <Header className="app-header">
          <div style={{ fontWeight: 600, color: '#374151' }}>
            企业级知识库系统
            <Tag color="blue" style={{ marginLeft: 10 }}>内部使用</Tag>
          </div>
          <Space size={14}>
            <span className="muted">{user?.tenant_name} / {user?.department_name || '未分配部门'}</span>
            <Dropdown menu={{
              items: [{ key: 'logout', icon: <LogoutOutlined />, label: '退出登录' }],
              onClick: async ({ key }) => { if (key === 'logout') { await logout(); navigate('/login'); } },
            }}>
              <Button type="text" icon={<Avatar size="small" icon={<UserOutlined />} />}>
                {user?.email}
              </Button>
            </Dropdown>
          </Space>
        </Header>
        <Content className="app-content"><Outlet /></Content>
      </Layout>
    </Layout>
  );
}
