/** Self-contained so the same theme logic can run before React hydrates. */
export function applyTheme() {
  let preference = 'system';
  try {
    preference = localStorage.getItem('reqlica:theme') || 'system';
  } catch {
    // System appearance remains available when browser storage is blocked.
  }
  const dark =
    preference === 'dark' ||
    (preference !== 'light' &&
      preference !== 'dark' &&
      matchMedia('(prefers-color-scheme: dark)').matches);
  const color = dark ? '#111b16' : '#f7faf8';
  document.documentElement.classList.toggle('dark', dark);
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', color);
  return color;
}

// Keep this function free of module-scope references: it runs in the initial HTML.
export const themeBootstrap = `(${applyTheme.toString()})();`;
