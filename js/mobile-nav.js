/* =========================================================
   Mobile nav glue — "more actions" overflow menu
   Purely additive: forwards to the existing buttons (undo/redo/
   Aperçu/Exporter), which stay in the DOM and keep working exactly
   as before. No app/state logic is touched here.
   ========================================================= */
(() => {
  'use strict';
  const btn = document.getElementById('moreActionsBtn');
  const menu = document.getElementById('moreActionsMenu');
  if (!btn || !menu) return;

  function isOpen() { return !menu.hidden; }

  function syncDisabledState() {
    menu.querySelectorAll('[data-forward]').forEach(item => {
      const target = document.getElementById(item.dataset.forward);
      item.disabled = !!(target && target.disabled);
    });
  }

  function open() {
    syncDisabledState();
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    document.addEventListener('click', onOutsideClick, true);
    document.addEventListener('keydown', onKeydown, true);
  }

  function close() {
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', onOutsideClick, true);
    document.removeEventListener('keydown', onKeydown, true);
  }

  function onOutsideClick(e) {
    if (menu.contains(e.target) || btn.contains(e.target)) return;
    close();
  }
  function onKeydown(e) { if (e.key === 'Escape') close(); }

  btn.addEventListener('click', () => (isOpen() ? close() : open()));

  menu.querySelectorAll('[data-forward]').forEach(item => {
    item.addEventListener('click', () => {
      const target = document.getElementById(item.dataset.forward);
      close();
      target?.click();
    });
  });
})();
