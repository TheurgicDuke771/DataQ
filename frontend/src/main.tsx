import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App as AntApp, ConfigProvider } from 'antd';

import { App } from './App';
import { CurrentUserProvider } from './auth/CurrentUserProvider';
import { MsalProvider } from './auth/MsalProvider';
import { getMsalInstance } from './auth/msalInstance';
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
      <ConfigProvider>
        <AntApp>
          <MsalProvider>
            <CurrentUserProvider>
              <App />
            </CurrentUserProvider>
          </MsalProvider>
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
