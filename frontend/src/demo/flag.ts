/** True only in the static demo build (`pnpm demo:build`, #2419). A build-time constant. */
export const IS_DEMO = import.meta.env.VITE_DEMO === 'true';

export const INSTALL_GUIDE_URL =
  'https://theurgicduke771.github.io/DataQ/docs/latest/get-started/install/';
