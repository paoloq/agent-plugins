(function () {
  try {
    var saved = localStorage.getItem('prompt-review-theme');
    var theme = (saved === 'light' || saved === 'dark')
      ? saved
      : (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', theme);
  } catch (_) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
