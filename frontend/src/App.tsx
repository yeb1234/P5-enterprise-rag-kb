import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Spin } from 'antd';
import { useAuth } from './store/AuthContext';
import AppLayout from './components/AppLayout';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import DocumentsPage from './pages/DocumentsPage';
import UploadCenterPage from './pages/UploadCenterPage';
import SearchPage from './pages/SearchPage';
import VersionDiffPage from './pages/VersionDiffPage';
import TaskCenterPage from './pages/TaskCenterPage';

function Protected() {
  const { user, loading } = useAuth();
  if (loading) return <div className="center-spin"><Spin size="large" tip="恢复会话中..." /></div>;
  if (!user) return <Navigate to="/login" replace />;
  return <AppLayout />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<Protected />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/upload" element={<UploadCenterPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/versions" element={<VersionDiffPage />} />
          <Route path="/tasks" element={<TaskCenterPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
