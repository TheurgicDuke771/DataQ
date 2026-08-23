import {
  ApiOutlined,
  BarChartOutlined,
  ContainerOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DownOutlined,
  LogoutOutlined,
  MenuOutlined,
  ReadOutlined,
  SafetyOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Avatar, Button, Drawer, Dropdown, Flex, Layout, Menu, Spin, Tag, Typography } from 'antd';
import type { MenuProps } from 'antd';
import { lazy, Suspense, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { AuthGate } from './auth/AuthGate';
import { authMode } from './auth/config';
import { RequireRole } from './auth/RequireRole';
import { useCurrentUser } from './auth/useCurrentUser';
import { useIsWorkspaceAdmin } from './auth/useMe';
import { logout } from './auth/authClient';
import { useOtpSession } from './auth/otpSessionContext';
import { BrandMark } from './components/BrandMark';
import { ProfileCompletionPrompt } from './components/profile/ProfileCompletionPrompt';
import { SHELL } from './theme';

// Route components are code-split so the initial bundle doesn't ship every page
// (and antd-heavy pages only load on navigation). Named exports → map to default.
const Dashboard = lazy(() => import('./pages/Dashboard').then((m) => ({ default: m.Dashboard })));
const Connections = lazy(() =>
  import('./pages/Connections').then((m) => ({ default: m.Connections })),
);
const ConnectionNew = lazy(() =>
  import('./pages/ConnectionNew').then((m) => ({ default: m.ConnectionNew })),
);
const ConnectionEdit = lazy(() =>
  import('./pages/ConnectionEdit').then((m) => ({ default: m.ConnectionEdit })),
);
const Suites = lazy(() => import('./pages/Suites').then((m) => ({ default: m.Suites })));
const SuiteNew = lazy(() => import('./pages/SuiteNew').then((m) => ({ default: m.SuiteNew })));
const SuiteEdit = lazy(() => import('./pages/SuiteEdit').then((m) => ({ default: m.SuiteEdit })));
const CheckNew = lazy(() => import('./pages/CheckNew').then((m) => ({ default: m.CheckNew })));
const CheckEdit = lazy(() => import('./pages/CheckEdit').then((m) => ({ default: m.CheckEdit })));
const Assets = lazy(() => import('./pages/Assets').then((m) => ({ default: m.Assets })));
const AssetDetail = lazy(() =>
  import('./pages/AssetDetail').then((m) => ({ default: m.AssetDetail })),
);
const Results = lazy(() => import('./pages/Results').then((m) => ({ default: m.Results })));
const RunDetail = lazy(() => import('./pages/RunDetail').then((m) => ({ default: m.RunDetail })));
const Profile = lazy(() => import('./pages/Profile').then((m) => ({ default: m.Profile })));
const Admin = lazy(() => import('./pages/Admin').then((m) => ({ default: m.Admin })));
const Settings = lazy(() => import('./pages/Settings').then((m) => ({ default: m.Settings })));
const NotFound = lazy(() => import('./pages/NotFound').then((m) => ({ default: m.NotFound })));

const { Header, Sider, Content } = Layout;

// Primary nav (top of the sider).
const NAV_ITEMS = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: <Link to="/dashboard">Dashboard</Link> },
  { key: '/assets', icon: <DatabaseOutlined />, label: <Link to="/assets">Assets</Link> },
  { key: '/connections', icon: <ApiOutlined />, label: <Link to="/connections">Connections</Link> },
  { key: '/suites', icon: <ContainerOutlined />, label: <Link to="/suites">Suites</Link> },
  { key: '/results', icon: <BarChartOutlined />, label: <Link to="/results">Results</Link> },
  { key: '/profile', icon: <UserOutlined />, label: <Link to="/profile">Profile</Link> },
];
// Footer nav (pinned to the bottom).
const ADMIN_FOOTER_ITEMS = [
  { key: '/admin', icon: <SafetyOutlined />, label: <Link to="/admin">Admin</Link> },
  { key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">Settings</Link> },
];
// Published docs site (MkDocs Material → GitHub Pages). External link, opens in
// a new tab; never a "selected" nav key since it leaves the app.
const DOCS_URL = 'https://theurgicduke771.github.io/DataQ/docs/';
const DOC_ITEM = {
  key: 'documentation',
  icon: <ReadOutlined />,
  label: (
    <a href={DOCS_URL} target="_blank" rel="noreferrer">
      Documentation
    </a>
  ),
};
// Keys that can be "selected" (the disabled Documentation placeholder can't).
const SELECTABLE_KEYS = [...NAV_ITEMS, ...ADMIN_FOOTER_ITEMS].map((i) => i.key);

export function App() {
  const location = useLocation();
  const isAdmin = useIsWorkspaceAdmin();
  // Narrow-viewport nav (#617, #801): below the `lg` breakpoint the Sider collapses to zero width
  // and the nav moves into an overlay Drawer that floats *above* the content (a scrim behind it)
  const [narrow, setNarrow] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const footerItems = isAdmin ? [...ADMIN_FOOTER_ITEMS, DOC_ITEM] : [DOC_ITEM];
  // Highlight the nav item whose path matches the current location — exact, or a sub-path at a
  // segment boundary (so `/suites` matches `/suites/123` but not a sibling like `/suites-archive`).
  const selectedKeys = SELECTABLE_KEYS.filter(
    (k) => location.pathname === k || location.pathname.startsWith(`${k}/`),
  );

  // Auth gates the whole shell: an unauthenticated user gets the full-screen
  // LoginPage with no header/sider chrome; the Layout only renders once signed in.
  return (
    <AuthGate>
      {/* Otp-mode, first-login only (#1139) — a no-op render (returns a closed
          Modal) in every other mode/state, so it's cheap to mount unconditionally
          here rather than threading a prop through every route. */}
      <ProfileCompletionPrompt />
      {/* Fixed app shell: the Layout is exactly the viewport height and doesn't
          scroll — the header and sider stay put, and only <Content> scrolls. */}
      <Layout style={{ height: '100vh', overflow: 'hidden' }}>
        <Header
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            borderBottom: `1px solid var(--dq-border)`,
          }}
        >
          {narrow && (
            <Button
              type="text"
              icon={<MenuOutlined />}
              aria-label="Toggle navigation"
              // Only claim to control the drawer while it exists: AntD doesn't mount the panel
              // (`#app-nav-drawer`) until first open.
              aria-controls={drawerOpen ? 'app-nav-drawer' : undefined}
              aria-expanded={drawerOpen}
              onClick={() => setDrawerOpen((o) => !o)}
              style={{ marginInlineStart: -8 }}
            />
          )}
          <Link to="/" aria-label="DataQ home" style={{ flex: 1 }}>
            <Flex align="center" gap={10}>
              <BrandMark />
              {/* nowrap: squeezed between the hamburger and the user chip on a
                  narrow header, the brand otherwise wraps vertically (#692). */}
              <Typography.Text
                strong
                style={{ fontSize: 17, color: 'var(--dq-ink)', whiteSpace: 'nowrap' }}
              >
                DataQ
              </Typography.Text>
            </Flex>
          </Link>
          <UserMenu />
        </Header>
        <Layout>
          <Sider
            width={SHELL.siderWidth}
            theme="light"
            breakpoint="lg"
            collapsedWidth={0}
            trigger={null}
            collapsed={narrow}
            onBreakpoint={(broken) => {
              setNarrow(broken);
              // Reset the overlay on any breakpoint crossing so a drawer left open
              // on mobile doesn't re-appear the next time we drop below `lg`.
              setDrawerOpen(false);
            }}
            style={{ borderInlineEnd: `1px solid var(--dq-border)`, height: '100%' }}
          >
            {/* Desktop: the Sider *is* the nav. Narrow: it collapses to zero width
                and the nav lives in the overlay Drawer below, so it never squeezes
                the content (#801). Rendering the nav only when !narrow also keeps a
                single copy of each nav item in the DOM. */}
            {!narrow && (
              <SideNav selectedKeys={selectedKeys} footerItems={footerItems} onNavigate={noop} />
            )}
          </Sider>
          {/* Narrow-viewport overlay nav: a left Drawer with a scrim (AntD's default
              mask). It floats above the content — the page keeps its full width
              whether the nav is open or shut. Only mounted below `lg`; on desktop
              the Sider handles everything. */}
          {narrow && (
            <Drawer
              id="app-nav-drawer"
              placement="left"
              open={drawerOpen}
              onClose={() => setDrawerOpen(false)}
              size={SHELL.siderWidth}
              styles={{
                body: { padding: 0 },
                header: { borderBottom: `1px solid var(--dq-border)` },
              }}
              title={
                <Flex align="center" gap={8}>
                  <BrandMark size={20} />
                  <Typography.Text strong style={{ color: 'var(--dq-ink)' }}>
                    DataQ
                  </Typography.Text>
                </Flex>
              }
            >
              <SideNav
                selectedKeys={selectedKeys}
                footerItems={footerItems}
                onNavigate={() => setDrawerOpen(false)}
              />
            </Drawer>
          )}
          {/* The only scroll container: header + sider stay fixed, this scrolls. */}
          <Content style={{ padding: 24, position: 'relative', overflowY: 'auto' }}>
            <BrandWatermark />
            <div style={{ position: 'relative' }}>
              <Suspense fallback={<Spin size="large" style={{ marginTop: 80 }} />}>
                <Routes>
                  <Route path="/" element={<Navigate to="/dashboard" replace />} />
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/connections" element={<Connections />} />
                  {/* Deep-linkable routes need their own gate, not just a hidden
                      button (ADR 0033, #743): a bookmark, a Back after a
                      demotion, or a shared link reaches these directly, and a
                      Member would otherwise fill an entire credential form to
                      learn at submit that it 403s. */}
                  <Route
                    path="/connections/new"
                    element={
                      <RequireRole
                        minimum="admin"
                        message="Connections are managed by workspace admins."
                      >
                        <ConnectionNew />
                      </RequireRole>
                    }
                  />
                  <Route
                    path="/connections/:connectionId/edit"
                    element={
                      <RequireRole
                        minimum="admin"
                        message="Connections are managed by workspace admins."
                      >
                        <ConnectionEdit />
                      </RequireRole>
                    }
                  />
                  <Route path="/suites" element={<Suites />} />
                  <Route
                    path="/suites/new"
                    element={
                      <RequireRole
                        minimum="member"
                        message="Creating suites requires member access — you have read-only access to this workspace."
                      >
                        <SuiteNew />
                      </RequireRole>
                    }
                  />
                  <Route path="/suites/:suiteId" element={<Suites />} />
                  <Route path="/suites/:suiteId/edit" element={<SuiteEdit />} />
                  <Route path="/suites/:suiteId/checks/new" element={<CheckNew />} />
                  <Route path="/suites/:suiteId/checks/:checkId/edit" element={<CheckEdit />} />
                  <Route path="/assets" element={<Assets />} />
                  <Route path="/assets/:assetId" element={<AssetDetail />} />
                  <Route path="/results" element={<Results />} />
                  <Route path="/results/:runId" element={<RunDetail />} />
                  <Route path="/profile" element={<Profile />} />
                  <Route path="/admin" element={<Admin />} />
                  <Route path="/settings" element={<Settings />} />
                  {/* Unknown route → in-brand 404 (not a silent redirect). */}
                  <Route path="*" element={<NotFound />} />
                </Routes>
              </Suspense>
            </div>
          </Content>
        </Layout>
      </Layout>
    </AuthGate>
  );
}

/** Shared no-op — the desktop Sider has nothing to close after a nav click. */
const noop = () => {};

/**
 * The sider's nav content — the primary nav up top and the footer group (Admin · Settings ·
 * Documentation) pinned to the bottom by the flex layout, separated by a hairline.
 */
function SideNav({
  selectedKeys,
  footerItems,
  onNavigate,
}: {
  selectedKeys: string[];
  footerItems: MenuProps['items'];
  onNavigate: () => void;
}) {
  return (
    <Flex vertical style={{ height: '100%' }}>
      <Menu
        mode="inline"
        selectedKeys={selectedKeys}
        items={NAV_ITEMS}
        onClick={onNavigate}
        style={{ borderInlineEnd: 0, paddingTop: 8, flex: 1, minHeight: 0, overflowY: 'auto' }}
      />
      <Menu
        mode="inline"
        selectedKeys={selectedKeys}
        items={footerItems}
        onClick={onNavigate}
        style={{ borderInlineEnd: 0, borderTop: `1px solid var(--dq-border)`, paddingBlock: 8 }}
      />
    </Flex>
  );
}

/** Up-to-two-letter initials for the avatar (e.g. "Dev Bypass User" → "DB"). */
function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}

/**
 * A very subtle brand watermark behind every page: the yin-yang mark bled off the content area's
 * bottom-right corner at low opacity.
 */
function BrandWatermark() {
  return (
    <div
      aria-hidden
      style={{
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
        pointerEvents: 'none',
      }}
    >
      <div style={{ position: 'absolute', right: -70, bottom: -70, opacity: 0.05, lineHeight: 0 }}>
        <BrandMark size={460} />
      </div>
    </div>
  );
}

/**
 * Header identity + account menu: an avatar/name button that opens a dropdown with the signed-in
 * identity and a Sign out action.
 */
function UserMenu() {
  const user = useCurrentUser();
  // Called before the early return — hooks are unconditional. Inert in every mode
  // but `otp`, where it is the only thing that can end the cookie session.
  const { signOut: signOutOtpSession } = useOtpSession();
  if (!user) return null;

  const onLogout = () => {
    // OTP sign-out is a POST that revokes the session server-side and clears the cookie — not a
    // redirect.
    if (authMode === 'otp') {
      signOutOtpSession();
      return;
    }
    if (authMode !== 'real') return;
    void logout();
  };

  const items: MenuProps['items'] = [
    {
      type: 'group',
      label: (
        <Flex vertical gap={2} style={{ padding: '4px 4px 8px' }}>
          <Typography.Text strong style={{ color: 'var(--dq-ink)' }}>
            {user.name}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {user.username}
          </Typography.Text>
          {user.isDev && (
            <Tag color="orange" style={{ marginTop: 4, width: 'fit-content' }}>
              DEV BYPASS
            </Tag>
          )}
        </Flex>
      ),
    },
    { type: 'divider' },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: user.isDev ? 'Sign out (dev bypass)' : 'Sign out',
      danger: !user.isDev,
      disabled: user.isDev,
      onClick: onLogout,
    },
  ];

  return (
    <Dropdown menu={{ items }} trigger={['click']} placement="bottomRight">
      <Flex align="center" gap={8} style={{ cursor: 'pointer' }}>
        <Avatar size="small" style={{ backgroundColor: 'var(--dq-primary)', flexShrink: 0 }}>
          {initialsOf(user.name)}
        </Avatar>
        <Typography.Text style={{ color: 'var(--dq-ink)' }}>{user.name}</Typography.Text>
        <DownOutlined style={{ fontSize: 10, color: 'var(--dq-muted)' }} />
      </Flex>
    </Dropdown>
  );
}
