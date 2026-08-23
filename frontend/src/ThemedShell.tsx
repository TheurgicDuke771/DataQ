import { useMemo, type ReactNode } from 'react';
import { App as AntApp, ConfigProvider } from 'antd';

import { getAppTheme } from './theme';
import { useThemeMode } from './themeMode/useThemeMode';

/** Wraps antd's ConfigProvider/App with the theme resolved from ThemeModeProvider. */
export function ThemedShell({ children }: { children: ReactNode }) {
  const { resolvedMode } = useThemeMode();
  const theme = useMemo(() => getAppTheme(resolvedMode), [resolvedMode]);
  return (
    <ConfigProvider theme={theme}>
      <AntApp>{children}</AntApp>
    </ConfigProvider>
  );
}
