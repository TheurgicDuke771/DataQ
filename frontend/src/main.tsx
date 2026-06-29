import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App as AntApp, ConfigProvider } from 'antd';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import { CurrentUserProvider } from './auth/CurrentUserProvider';
import { MeProvider } from './auth/MeProvider';
import { MsalProvider } from './auth/MsalProvider';
import { getMsalInstance } from './auth/msalInstance';
import { ErrorBoundary } from './components/ErrorBoundary';
import { appTheme } from './theme';
// Self-hosted fonts (visual-fidelity pass, ADR 0022) — Inter for UI text,
// JetBrains Mono for code/SQL/identifiers. Bundled via @fontsource (no external
// CDN fetch, clears pnpm audit), only the weights the design uses. Imported
// before styles.css so the @font-face rules register ahead of our overrides.
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import './styles.css';

const maybeRoot = document.getElementById('root');
if (!maybeRoot) {
  throw new Error('Root element #root not found in index.html');
}
const rootEl: HTMLElement = maybeRoot;

// MSAL lifecycle (issue #62):
//   1. .initialize() must complete before any MSAL API call (v5 requirement).
//   2. .handleRedirectPromise() must resolve before React renders so the
//      first paint reflects post-login state.
//   3. Errors surface to the console + a static page — fail loud during dev.
async function bootstrap() {
  const instance = getMsalInstance();
  if (instance) {
    await instance.initialize();
    await instance.handleRedirectPromise();
  }
  createRoot(rootEl).render(
    <StrictMode>
      <ConfigProvider theme={appTheme}>
        <AntApp>
          <ErrorBoundary>
            <MsalProvider>
              <CurrentUserProvider>
                <MeProvider>
                  <BrowserRouter>
                    <App />
                  </BrowserRouter>
                </MeProvider>
              </CurrentUserProvider>
            </MsalProvider>
          </ErrorBoundary>
        </AntApp>
      </ConfigProvider>
    </StrictMode>,
  );
}

bootstrap().catch((err) => {
  console.error('MSAL bootstrap failed', err);
  rootEl.innerHTML =
    '<pre style="padding:24px;color:#a00">Authentication bootstrap failed. See console.</pre>';
});
