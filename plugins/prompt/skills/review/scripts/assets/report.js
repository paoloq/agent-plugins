(function () {
  const root = document.documentElement;
  const toggle = document.getElementById('theme-toggle');
  const live = document.getElementById('live-region');

  function announce(msg) {
    if (live) { live.textContent = ''; setTimeout(() => { live.textContent = msg; }, 30); }
  }
  function syncToggleLabel() {
    if (!toggle) return;
    const cur = root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    const next = cur === 'light' ? 'dark' : 'light';
    toggle.setAttribute('aria-label', 'Switch to ' + next + ' theme');
    toggle.setAttribute('title', 'Switch to ' + next + ' theme');
    toggle.setAttribute('aria-pressed', cur === 'light' ? 'true' : 'false');
  }
  syncToggleLabel();

  if (toggle) {
    toggle.addEventListener('click', () => {
      const cur = root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
      const next = cur === 'light' ? 'dark' : 'light';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('prompt-review-theme', next); } catch (_) {}
      syncToggleLabel();
      announce(next === 'light' ? 'Light theme on' : 'Dark theme on');
    });
  }

  // Follow OS preference live as long as the user hasn't picked one.
  try {
    const mq = matchMedia('(prefers-color-scheme: light)');
    mq.addEventListener('change', (ev) => {
      if (localStorage.getItem('prompt-review-theme')) return;
      root.setAttribute('data-theme', ev.matches ? 'light' : 'dark');
      syncToggleLabel();
    });
  } catch (_) {}

  const buttons = document.querySelectorAll('.dist .legend button[data-sev]');
  const items = document.querySelectorAll('.iv');
  const clear = document.querySelector('.dist .legend .clear');
  const active = new Set();


  function apply() {
    let visible = 0;
    items.forEach(el => {
      const sev = el.dataset.sev;
      const show = active.size === 0 || active.has(sev);
      el.classList.toggle('hidden', !show);
      if (show) visible++;
    });
    buttons.forEach(b => {
      const on = active.has(b.dataset.sev);
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    if (clear) clear.style.display = active.size === 0 ? 'none' : '';
    const label = active.size === 0
      ? `Showing all ${visible} interventions`
      : `Filtered to ${[...active].join(', ')} — ${visible} visible`;
    announce(label);
  }

  buttons.forEach(b => {
    if (b.disabled) return;
    b.addEventListener('click', () => {
      const sev = b.dataset.sev;
      if (active.has(sev)) active.delete(sev); else active.add(sev);
      apply();
    });
  });
  if (clear) clear.addEventListener('click', () => { active.clear(); apply(); });

  document.querySelectorAll('.iv .copy').forEach(btn => {
    btn.addEventListener('click', async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const text = btn.parentElement.querySelector('.suggest-text').textContent;
      try {
        await navigator.clipboard.writeText(text);
        btn.classList.add('copied');
        btn.setAttribute('aria-label', 'Copied to clipboard');
        btn.setAttribute('title', 'Copied');
        announce('Suggested change copied to clipboard');
        setTimeout(() => {
          btn.classList.remove('copied');
          btn.setAttribute('aria-label', 'Copy to clipboard');
          btn.setAttribute('title', 'Copy to clipboard');
        }, 1500);
      } catch (_) {
        announce('Copy failed — clipboard unavailable');
      }
    });
  });
})();
