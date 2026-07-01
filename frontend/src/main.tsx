import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App as AntApp, ConfigProvider } from 'antd';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import { AuthProvider } from './auth/AuthProvider';
import { CurrentUserProvider } from './auth/CurrentUserProvider';
import { MeProvider } from './auth/MeProvider';
import { completeSigninIfCallback } from './auth/authClient';
import { ErrorBoundary } from './components/ErrorBoundary';
import { appTheme } from './theme';
// Self-hosted fonts (visual-fidelity pass, ADR 0022) — Inter for UI text,
// JetBrains Mono for code/SQL/identifiers. Bundled via @fontsource (no external
// CDN fetch, clears pnpm audit). Only the weights the design uses, and only the
// **latin** subset — the UI is English; shipping cyrillic/greek/vietnamese
// subsets would balloon the SWA asset count for glyphs we never render (a stray
// non-latin data value just falls back to the system font). Imported before
// styles.css so the @font-face rules register ahead of our overrides.
import '@fontsource/inter/latin-400.css';
import '@fontsource/inter/latin-500.css';
import '@fontsource/inter/latin-600.css';
import '@fontsource/inter/latin-700.css';
import '@fontsource/jetbrains-mono/latin-400.css';
import '@fontsource/jetbrains-mono/latin-500.css';
import './styles.css';

const maybeRoot = document.getElementById('root');
if (!maybeRoot) {
  throw new Error('Root element #root not found in index.html');
}
const rootEl: HTMLElement = maybeRoot;

// Auth lifecycle (issue #62, generic OIDC per ADR 0028): if this load is the IdP
// redirect back, complete the code exchange BEFORE React renders so the first
// paint reflects post-login state. Errors surface to the console + a static page.
async function bootstrap() {
  await completeSigninIfCallback();
  createRoot(rootEl).render(
    <StrictMode>
      <ConfigProvider theme={appTheme}>
        <AntApp>
          <ErrorBoundary>
            <AuthProvider>
              <CurrentUserProvider>
                <MeProvider>
                  <BrowserRouter>
                    <App />
                  </BrowserRouter>
                </MeProvider>
              </CurrentUserProvider>
            </AuthProvider>
          </ErrorBoundary>
        </AntApp>
      </ConfigProvider>
    </StrictMode>,
  );
}

bootstrap().catch((err) => {
  console.error('Auth bootstrap failed', err);
  rootEl.innerHTML =
    '<pre style="padding:24px;color:#a00">Authentication bootstrap failed. See console.</pre>';
});
