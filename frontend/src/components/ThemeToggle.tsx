import { CheckOutlined, DesktopOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons';
import { Button, Dropdown, Tooltip } from 'antd';
import type { MenuProps } from 'antd';
import type { ReactNode } from 'react';

import { useThemeMode } from '../themeMode/useThemeMode';
import type { ThemeModePreference } from '../themeMode/themeModeContext';

const OPTIONS: { key: ThemeModePreference; label: string; icon: ReactNode }[] = [
  { key: 'light', label: 'Light', icon: <SunOutlined /> },
  { key: 'dark', label: 'Dark', icon: <MoonOutlined /> },
  { key: 'system', label: 'System', icon: <DesktopOutlined /> },
];

const TRIGGER_ICON: Record<ThemeModePreference, ReactNode> = {
  light: <SunOutlined />,
  dark: <MoonOutlined />,
  system: <DesktopOutlined />,
};

/** Header control for the light/dark/system preference (#1562). */
export function ThemeToggle() {
  const { mode, setMode } = useThemeMode();

  const items: MenuProps['items'] = OPTIONS.map((o) => ({
    key: o.key,
    icon: o.icon,
    label: o.label,
    extra: mode === o.key ? <CheckOutlined /> : undefined,
  }));

  return (
    <Dropdown
      menu={{ items, onClick: ({ key }) => setMode(key as ThemeModePreference) }}
      trigger={['click']}
      placement="bottomRight"
    >
      <Tooltip title="Theme">
        <Button type="text" aria-label="Change theme" icon={TRIGGER_ICON[mode]} />
      </Tooltip>
    </Dropdown>
  );
}
