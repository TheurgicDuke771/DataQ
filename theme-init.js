// Sets data-theme before first paint (avoids a light-theme flash). Same-origin
// file, not inline — CSP script-src has no 'unsafe-inline'. Keep this logic in
// sync with ThemeModeProvider's own resolution.
(function () {
  try {
    var stored = localStorage.getItem('dq-theme-mode');
    var mode = stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system';
    var resolved =
      mode === 'system'
        ? window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light'
        : mode;
    document.documentElement.setAttribute('data-theme', resolved);
  } catch (e) {
    // localStorage/matchMedia unavailable — ThemeModeProvider's own effect will
    // set it once React mounts.
  }
})();
