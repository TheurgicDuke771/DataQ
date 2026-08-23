import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import { ThemedShell } from './ThemedShell';
import { AuthProvider } from './auth/AuthProvider';
import { CurrentUserProvider } from './auth/CurrentUserProvider';
import { MeProvider } from './auth/MeProvider';
import { OtpSessionProvider } from './auth/OtpSessionProvider';
import { completeSigninIfCallback } from './auth/authClient';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ThemeModeProvider } from './themeMode/ThemeModeProvider';
// Self-hosted fonts (visual-fidelity pass, ADR 0022) — Inter for UI text, JetBrains Mono for
// code/SQL/identifiers.
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

// Auth lifecycle (issue #62, generic OIDC per ADR 0028): if this load is the IdP redirect back,
// complete the code exchange BEFORE React renders so the first paint reflects post-login state.
async function bootstrap() {
  await completeSigninIfCallback();
  createRoot(rootEl).render(
    <StrictMode>
      <ThemeModeProvider>
        <ThemedShell>
          <ErrorBoundary>
            <AuthProvider>
              {/* OTP session (ADR 0032) — above CurrentUserProvider, which derives
                  the signed-in user from it in `otp` mode. A passthrough in every
                  other mode, so no /me probe races the OIDC token acquisition. */}
              <OtpSessionProvider>
                <CurrentUserProvider>
                  <MeProvider>
                    <BrowserRouter>
                      <App />
                    </BrowserRouter>
                  </MeProvider>
                </CurrentUserProvider>
              </OtpSessionProvider>
            </AuthProvider>
          </ErrorBoundary>
        </ThemedShell>
      </ThemeModeProvider>
    </StrictMode>,
  );
}

bootstrap().catch((err) => {
  console.error('Auth bootstrap failed', err);
  rootEl.innerHTML =
    '<pre style="padding:24px;color:#a00">Authentication bootstrap failed. See console.</pre>';
});
