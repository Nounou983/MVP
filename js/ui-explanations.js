/* =========================================================
   La Cigogne D'Ailleurs — Système d'aide contextuelle bilingue
   Français + Arabe algérien (Darija).

   Self-contained: injects its own <style>, reads the DOM by stable
   selectors/IDs, and never modifies application state directly
   (it only calls the same public buttons/AppActions the user could
   click themselves). Safe to remove this one file without breaking
   any core feature.
   ========================================================= */
(() => {
  'use strict';

  const SEEN_KEY = 'cigogne_ui_guide_v3_seen';
  const ADD_HINT_KEY = 'cigogne_add_hint_count';
  const ADD_HINT_MAX = 3; // show the full "meuble ajouté" explainer only the first few times

  const ready = fn => document.readyState === 'loading'
    ? document.addEventListener('DOMContentLoaded', fn)
    : fn();

  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] || c));

  /* ---------------- Contenu bilingue ---------------- */

  // Navigation principale (rail gauche / barre du bas)
  const NAV_HELP = [
    { sel: '[data-panel="catalog"]', icon: '🛋️', title: 'Meubles', accent: 'info',
      fr: 'Choisissez des meubles dans votre catalogue et placez-les directement dans votre pièce.',
      dz: 'من هنا تختار الموبليات من الكاتالوغ وتحطّهم مباشرة فالغرفة تاعك.' },
    { sel: '[data-panel="room"]', icon: '🏠', title: 'Pièce', accent: 'info',
      fr: 'Importez la photo de votre pièce et analysez son espace.',
      dz: 'من هنا دخل تصويرة الغرفة وخلي الـIA تحلل المساحة تاعها.' },
    { sel: '[data-panel="ai"]', icon: '✨', title: 'Retouche', accent: 'ai',
      fr: 'Supprimez les meubles existants grâce à la gomme intelligente.',
      dz: 'هنا تقدر تنحي الموبليات لي كاينين باستعمال الـIA.' },
    { sel: '[data-panel="summary"]', icon: '📋', title: 'Liste', accent: 'info',
      fr: 'Retrouvez les meubles ajoutés et consultez leur prix.',
      dz: 'هنا تلقى كامل الموبليات لي زدتهم وتشوف السومة تاعهم.' },
    { sel: '[data-panel="projects"]', icon: '📁', title: 'Projets', accent: 'info',
      fr: 'Enregistrez et retrouvez vos compositions.',
      dz: 'هنا تحفظ المشاريع تاعك وترجعلهم من بعد.' },
    { sel: '[data-panel="account"]', icon: '👤', title: 'Compte', accent: 'info',
      fr: 'Gérez votre compte et vos informations.',
      dz: 'من هنا تسير الحساب والمعلومات تاعك.' },
    { sel: '[data-panel="help"]', icon: '❓', title: 'Aide', accent: 'info',
      fr: "Besoin d'aide ? Retrouvez les explications et les conseils.",
      dz: 'ما فهمتش حاجة؟ هنا تلقى الشرح والمساعدة.' },
  ];

  // Badges "?" sur des contrôles qui n'ont pas de place pour un texte inline
  const BADGE_HELP = [
    { sel: '#catalogSearch', anchor: 'parent', title: 'Rechercher', accent: 'info',
      fr: 'Trouvez rapidement le meuble que vous cherchez.',
      dz: 'قلب بسرعة على الموبلية لي راك حابها.' },
    { sel: '#catalogFilters', anchor: 'before', label: 'Catégories', title: 'Catégories', accent: 'info',
      fr: 'Filtrez les meubles par catégorie.',
      dz: 'صنّف الموبليات حسب النوع.' },
    { sel: '.catalog-smart-tools', anchor: 'before', label: 'Filtres', title: 'Filtres', accent: 'info',
      fr: 'Style, matière et tri : affinez la liste selon vos envies.',
      dz: 'الستايل، الماتريال والترتيب: صنّف الموبليات كيما تحب.' },
    { sel: '[data-3dmode="photo"]', anchor: 'self', title: 'Caler sur la photo', accent: 'info',
      fr: 'Alignez le modèle 3D avec votre photo.',
      dz: 'طابق الموديل 3D مع التصويرة تاع الغرفة.' },
    { sel: '[data-3dmode="orbit"]', anchor: 'self', title: 'Tourner autour', accent: 'info',
      fr: 'Observez le meuble sous différents angles.',
      dz: 'دور وشوف الموبلية من زوايا مختلفة.' },
    { sel: '#glbAction', anchor: 'self', title: 'Modèle 3D', accent: 'info',
      fr: 'Ajoutez votre propre modèle 3D.',
      dz: 'دخل الموديل 3D تاعك إذا عندك واحد.' },
    { sel: '#viewThree', anchor: 'self', title: 'Vue 3D', accent: 'ai',
      fr: 'Visualisez vos meubles en 3D et vérifiez leur placement.',
      dz: 'شوف الموبليات تاعك بالـ3D وتأكد بلي البلاصة تاعهم مليحة.' },
  ];

  // Légendes en darija accolées à un texte français déjà présent dans la page :
  // voir setupPairedCaptions() ci-dessous, qui cible chaque paragraphe directement.

  // Étapes du guide de première visite
  const ONBOARDING = [
    { target: '[data-panel="catalog"]', icon: '🛋️', title: 'Meubles',
      fr: 'Choisissez des meubles dans votre catalogue et placez-les directement dans votre pièce.',
      dz: 'اختار الموبليات' },
    { target: '[data-panel="room"]', icon: '🏠', title: 'Pièce',
      fr: 'Importez la photo de votre pièce et analysez son espace.',
      dz: 'دخل تصويرة الغرفة' },
    { target: '[data-panel="ai"]', icon: '✨', title: 'Retouche',
      fr: "Supprimez les meubles existants grâce à la gomme intelligente.",
      dz: 'نحي الموبليات القديمة بالـIA' },
    { target: '#catalogAssistantBtn', icon: '✨', title: 'Assistant IA',
      fr: 'Décrivez simplement ce que vous voulez changer dans votre pièce.',
      dz: 'قول للـIA واش حاب تدير' },
    { target: '#viewThree', icon: '🧊', title: 'Vue 3D',
      fr: 'Visualisez vos meubles en 3D et vérifiez leur placement.',
      dz: 'شوف النتيجة بالـ3D' },
  ];

  /* ---------------- Styles ---------------- */

  function injectStyles() {
    if (document.getElementById('ui-guide-styles')) return;
    const st = document.createElement('style');
    st.id = 'ui-guide-styles';
    st.textContent = `
      .ui-x-accent-ai{--x-fg:#5636E0;--x-bg:#F1EDFF;--x-line:#E4DBFF}
      .ui-x-accent-success{--x-fg:#0F7A57;--x-bg:#E7F6EF;--x-line:#CFEEDF}
      .ui-x-accent-info{--x-fg:#2F6FE0;--x-bg:#EAF1FD;--x-line:#D6E4FB}
      .ui-x-accent-attention{--x-fg:#B5590A;--x-bg:#FDF1E4;--x-line:#F6DFC1}

      .ui-explainer{display:flex;gap:9px;align-items:flex-start;padding:10px 12px;margin:8px 0;
        border-radius:14px;border:1px solid var(--x-line,#E7E4DE);background:var(--x-bg,#FAF8F5);font-size:12px;line-height:1.42}
      .ui-explainer__icon{flex:none;font-size:15px;line-height:1;margin-top:1px}
      .ui-explainer__body{min-width:0;display:grid;gap:3px}
      .ui-explainer__title{margin:0 0 1px;font-weight:700;font-size:11.5px;color:var(--x-fg,#5636E0)}
      .ui-explainer__fr{margin:0;color:#3A3B42}
      .ui-explainer__dz{margin:0;color:#5C5D66;direction:rtl;text-align:right;font-size:12.5px}
      .ui-explainer__example{margin:5px 0 0;padding-top:5px;border-top:1px dashed var(--x-line,#E7E4DE);font-size:11px;color:#7C818C;font-style:italic}
      .ui-explainer__example .dz{display:block;direction:rtl;text-align:right;margin-top:2px}

      .ui-dz-caption{margin:3px 0 0;font-size:11.5px;line-height:1.5;color:#8A8E97;direction:rtl;text-align:right}

      .ui-hint-dot{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:999px;
        background:#EAF1FD;color:#2F6FE0;border:1px solid #D6E4FB;font-size:10px;font-weight:700;
        margin-left:5px;cursor:pointer;vertical-align:middle;flex:none}
      .ui-hint-dot.ai{background:#F1EDFF;color:#5636E0;border-color:#E4DBFF}
      .ui-hint-wrap{display:inline-flex;align-items:center}
      .ui-mini-label{display:flex;align-items:center;gap:5px;font-size:10.5px;font-weight:600;
        letter-spacing:.02em;color:#9A9DA6;text-transform:uppercase;margin:0 0 -2px}

      .ui-hint-pop{position:fixed;z-index:9998;width:min(300px,calc(100vw - 24px));
        background:#191A20;color:#fff;border-radius:14px;box-shadow:0 14px 40px rgba(0,0,0,.3);
        padding:12px 13px;display:none}
      .ui-hint-pop strong{display:flex;align-items:center;gap:6px;font-size:12.5px;margin-bottom:5px}
      .ui-hint-pop p{margin:0 0 5px;color:#E7E6F0;font-size:12px;line-height:1.45}
      .ui-hint-pop .dz{margin:0;color:#B9B8D6;direction:rtl;text-align:right;font-size:12.5px}
      .ui-hint-pop .example{margin-top:7px;padding-top:7px;border-top:1px solid rgba(255,255,255,.14);font-size:11px;color:#9E9CB5;font-style:italic}
      .ui-hint-pop .example .dz{display:block;margin-top:2px}

      .ui-help-dot{position:absolute;right:4px;top:4px;width:17px;height:17px;border-radius:50%;
        background:#6B4DF6;color:#fff;font-size:10.5px;font-weight:700;display:grid;place-items:center;z-index:4;cursor:pointer}

      .product-card__dims,.product-card__model{cursor:pointer}
      .product-card__dims::after{content:" ⓘ";opacity:.5;font-size:9px}
      .product-card__model::after{content:" ⓘ";opacity:.7;font-size:8px}

      .ai-assistant__intro{margin:0 0 10px}
      .ai-assistant__examples{display:grid;gap:6px;margin-top:8px}
      .ai-assistant__example{display:flex;flex-direction:column;align-items:flex-start;gap:1px;
        padding:7px 10px;border-radius:10px;border:1px solid #E4DBFF;background:#fff;cursor:pointer;width:100%;text-align:left}
      .ai-assistant__example:hover{background:#F1EDFF}
      .ai-assistant__example .fr{font-size:12px;color:#14151A;font-weight:500}
      .ai-assistant__example .dz{font-size:12px;color:#5636E0;direction:rtl;text-align:right;width:100%}

      .toast--success{background:#E7F6EF;color:#0F7A57}
      .toast--ai{background:#F1EDFF;color:#5636E0}
      .toast .dz{display:block;direction:rtl;text-align:right;margin-top:3px;opacity:.85;font-size:12px}

      .ui-guide-overlay{position:fixed;inset:0;z-index:10000;background:rgba(15,16,20,.62);backdrop-filter:blur(3px);display:grid;place-items:center;padding:20px}
      .ui-guide-card{width:min(92vw,440px);background:#fff;border:1px solid #E7E4DE;border-radius:22px;box-shadow:0 28px 80px rgba(0,0,0,.35);padding:22px;color:#14151A}
      .ui-guide-kicker{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:999px;background:#F1EDFF;color:#5636E0;font-size:11px;font-weight:700}
      .ui-guide-card h2{font-size:21px;margin:12px 0 7px}
      .ui-guide-card p{color:#4A4E58;font-size:13px;line-height:1.55;margin:0 0 10px}
      .ui-guide-dz{padding:10px 12px;background:#FFF7D9;border-right:4px solid #FFC42E;border-radius:10px;
        font-size:13.5px;line-height:1.55;margin:10px 0 16px;direction:rtl;text-align:right}
      .ui-guide-progress{display:flex;gap:5px;margin:0 0 15px}
      .ui-guide-dot{width:7px;height:7px;border-radius:50%;background:#D9D5CD}
      .ui-guide-dot.on{background:#6B4DF6;width:20px}
      .ui-guide-actions{display:flex;gap:8px;justify-content:space-between;align-items:center}
      .ui-guide-actions button{min-height:42px;border-radius:10px;padding:0 14px;border:1px solid #E7E4DE;background:#FAF8F5;color:#14151A;font-weight:600;cursor:pointer}
      .ui-guide-actions .primary{background:#6B4DF6;border-color:#6B4DF6;color:#fff}
      .ui-guide-skip-row{display:flex;align-items:center;justify-content:space-between;margin-top:14px;font-size:11.5px;color:#7C818C}
      .ui-guide-skip-row label{display:flex;align-items:center;gap:6px;cursor:pointer}
      .ui-guide-highlight{position:relative!important;z-index:10001!important;
        box-shadow:0 0 0 4px rgba(107,77,246,.45),0 0 35px rgba(107,77,246,.45)!important;border-radius:12px!important}

      @media (max-width: 760px) {
        .ui-hint-pop{width:min(280px,calc(100vw - 20px))}
        .ui-guide-card{padding:18px}
      }
    `;
    document.head.appendChild(st);
  }

  /* ---------------- Popover générique (tap + survol desktop) ---------------- */

  let openPop = null;
  let openAnchor = null;
  const fineHover = window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  function closePop() {
    if (openPop) { openPop.style.display = 'none'; openPop = null; openAnchor = null; }
  }

  function showPop(anchorEl, data) {
    let pop = document.querySelector('.ui-hint-pop');
    if (!pop) { pop = document.createElement('div'); pop.className = 'ui-hint-pop'; document.body.appendChild(pop); }
    const icon = data.accent === 'ai' ? '✨' : (data.accent === 'success' ? '✓' : 'ⓘ');
    pop.innerHTML = `
      <strong>${icon} ${esc(data.title || '')}</strong>
      <p>${esc(data.fr || '')}</p>
      <p class="dz" dir="rtl" lang="ar">${esc(data.dz || '')}</p>
      ${data.example ? `<div class="example">${esc(data.example)}${data.exampleDz ? `<span class="dz" dir="rtl" lang="ar">${esc(data.exampleDz)}</span>` : ''}</div>` : ''}
    `;
    const r = anchorEl.getBoundingClientRect();
    const w = Math.min(300, window.innerWidth - 24);
    let left = Math.max(12, Math.min(r.left, window.innerWidth - w - 12));
    let top = r.bottom + 8;
    pop.style.display = 'block';
    const h = pop.offsetHeight || 140;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 8);
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
    openPop = pop;
    openAnchor = anchorEl;
  }

  function attachHint(el, data, dotClass) {
    if (!el || el.dataset.cigogneHint === '1') return;
    el.dataset.cigogneHint = '1';
    el.addEventListener('click', e => {
      e.stopPropagation();
      if (openAnchor === el) closePop(); else showPop(el, data);
    });
    if (fineHover) {
      el.addEventListener('mouseenter', () => showPop(el, data));
      el.addEventListener('mouseleave', () => setTimeout(() => { if (openPop && !openPop.matches(':hover')) closePop(); }, 120));
    }
    if (dotClass !== false) el.classList.add(dotClass || 'ui-hint-dot');
  }

  document.addEventListener('click', e => {
    if (openPop && !openPop.contains(e.target)) closePop();
  });
  window.addEventListener('resize', closePop, { passive: true });
  window.addEventListener('scroll', closePop, { passive: true, capture: true });

  /* ---------------- Badges "?" sur les contrôles ---------------- */

  function setupBadgeHelp() {
    BADGE_HELP.forEach(item => {
      const target = document.querySelector(item.sel);
      if (!target) return;

      if (item.anchor === 'before') {
        if (target.dataset.cigogneBefore === '1') return;
        target.dataset.cigogneBefore = '1';
        const row = document.createElement('div');
        row.className = 'ui-mini-label';
        const dot = document.createElement('span');
        dot.className = 'ui-hint-dot' + (item.accent === 'ai' ? ' ai' : '');
        dot.textContent = '?';
        dot.setAttribute('role', 'button');
        dot.setAttribute('aria-label', item.title || 'Aide');
        row.textContent = item.label || item.title || '';
        row.appendChild(dot);
        target.parentElement?.insertBefore(row, target);
        attachHint(dot, item, false);
        return;
      }

      let host = target;
      if (item.anchor === 'parent') host = target.closest('label, .field') || target.parentElement;
      if (!host || host.dataset.cigogneBefore === '1') return;
      host.dataset.cigogneBefore = '1';
      const dot = document.createElement('span');
      dot.className = 'ui-hint-dot' + (item.accent === 'ai' ? ' ai' : '');
      dot.textContent = '?';
      dot.setAttribute('role', 'button');
      dot.setAttribute('aria-label', item.title || 'Aide');
      host.appendChild(dot);
      attachHint(dot, item, false);
    });
  }

  /* ---------------- Rail : petit indicateur "?" sur chaque section ---------------- */

  function setupNavHelp() {
    NAV_HELP.forEach(item => {
      const el = document.querySelector(item.sel);
      if (!el || el.dataset.cigogneHint === '1') return;
      el.dataset.cigogneHint = '1';
      if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
      const dot = document.createElement('i');
      dot.className = 'ui-help-dot';
      dot.textContent = '?';
      dot.setAttribute('role', 'button');
      dot.setAttribute('aria-label', `Aide — ${item.title}`);
      el.appendChild(dot);
      attachHint(dot, item, false);
    });
  }

  /* ---------------- Légendes darija sous un texte FR déjà présent ---------------- */

  function pairCaption(el, dz) {
    if (!el || el.dataset.cigogneCaption === '1') return;
    el.dataset.cigogneCaption = '1';
    const cap = document.createElement('p');
    cap.className = 'ui-dz-caption';
    cap.dir = 'rtl'; cap.lang = 'ar';
    cap.textContent = dz;
    el.insertAdjacentElement('afterend', cap);
  }

  function setupPairedCaptions() {
    document.querySelectorAll('[data-view="room"] .panel__head p').forEach(el => pairCaption(el, 'تصويرة واضحة ومن قدّام تعطي نتيجة خير.'));
    document.querySelectorAll('[data-view="ai"] .panel__head p').forEach(el => pairCaption(el, 'اختار الموبلية لي حاب تنحيها والـIA تنحيهالك أوتوماتيك.'));
    document.querySelectorAll('[data-view="projects"] .panel__head p').forEach(el => pairCaption(el, 'هنا تحفظ المشاريع تاعك وترجعلهم من بعد.'));
    document.querySelectorAll('[data-view="account"] .panel__head p').forEach(el => pairCaption(el, 'من هنا تسير الحساب والمعلومات تاعك.'));
    document.querySelectorAll('[data-view="help"] .panel__head p').forEach(el => pairCaption(el, 'ما فهمتش حاجة؟ هنا تلقى الشرح والمساعدة.'));
    document.querySelectorAll('[data-view="summary"] .panel__head p').forEach(el => pairCaption(el, 'هنا تلقى كامل الموبليات لي زدتهم وتشوف السومة تاعهم.'));
    const analyzeHint = document.querySelector('#analyzeAction')?.previousElementSibling;
    if (analyzeHint && analyzeHint.classList.contains('card__hint')) {
      pairCaption(analyzeHint, 'الـIA تحلل السول، العمق والموبليات لي كاينين باش تحط الموبلية الجديدة فالبلاصة الصح.');
    }
    const spatialHint = document.querySelector('#spatialCard .card__hint');
    if (spatialHint) pairCaption(spatialHint, 'الـIA تستعمل العمق والسول لي اكتاشفتهم باش تعاونك تحط الموبلية بالقياس الصحيح.');
    const vue3dCard = document.querySelector('#glbAction')?.closest('.card');
    const vue3dHint = vue3dCard?.querySelector('.card__hint');
    if (vue3dHint) pairCaption(vue3dHint, 'شوف الموبليات تاعك بالـ3D وتأكد بلي البلاصة تاعهم مليحة.');
  }

  /* ---------------- Import photo : petit helper sous le bouton ---------------- */

  function setupRoomImportHelper() {
    const card = document.getElementById('roomCard');
    if (!card || card.dataset.cigogneHelper === '1') return;
    card.dataset.cigogneHelper = '1';
    const btn = card.querySelector('[data-import-room]');
    if (!btn) return;
    const el = document.createElement('div');
    el.className = 'ui-explainer ui-x-accent-info';
    el.innerHTML = `
      <span class="ui-explainer__icon">📷</span>
      <span class="ui-explainer__body">
        <p class="ui-explainer__fr">Ajoutez une photo de votre pièce pour commencer.</p>
        <p class="ui-explainer__dz" dir="rtl" lang="ar">دخل تصويرة الغرفة باش نبداو.</p>
      </span>`;
    btn.insertAdjacentElement('afterend', el);
  }

  /* ---------------- AI Catalogue : helper sous le bouton assistant ---------------- */

  function setupCatalogAIHelper() {
    const row = document.querySelector('.catalog-ai-row');
    if (!row || row.dataset.cigogneHelper === '1') return;
    row.dataset.cigogneHelper = '1';
    const el = document.createElement('div');
    el.className = 'ui-explainer ui-x-accent-ai';
    el.innerHTML = `
      <span class="ui-explainer__icon">✨</span>
      <span class="ui-explainer__body">
        <p class="ui-explainer__fr">Décrivez simplement ce que vous voulez. L'IA peut rechercher le meuble adapté.</p>
        <p class="ui-explainer__dz" dir="rtl" lang="ar">قول للـIA واش راك حاب، وهي تعاونك تلقى الموبلية المناسبة.</p>
        <p class="ui-explainer__example">Exemple : « Je cherche un canapé moderne beige »
          <span class="dz" dir="rtl" lang="ar">مثال: « نحب كانابي مودرن بالبيج »</span>
        </p>
      </span>`;
    row.insertAdjacentElement('afterend', el);
  }

  /* ---------------- Retouche IA : ce qui se passe ---------------- */

  function setupRetoucheProcessCard() {
    const steps = document.querySelector('[data-view="ai"] .steps');
    if (!steps || steps.dataset.cigogneProcess === '1') return;
    steps.dataset.cigogneProcess = '1';
    const el = document.createElement('div');
    el.className = 'ui-explainer ui-x-accent-ai';
    el.innerHTML = `
      <span class="ui-explainer__icon">✨</span>
      <span class="ui-explainer__body">
        <p class="ui-explainer__title">Ce qui se passe</p>
        <p class="ui-explainer__fr">L'IA analyse les contours de l'objet…</p>
        <p class="ui-explainer__dz" dir="rtl" lang="ar">الـIA راهي تحلل حدود الموبلية...</p>
        <p class="ui-explainer__fr" style="margin-top:5px">Objet supprimé et surface reconstruite.</p>
        <p class="ui-explainer__dz" dir="rtl" lang="ar">تنحات الموبلية والـIA عاودت بنات البلاصة.</p>
      </span>`;
    steps.insertAdjacentElement('afterend', el);
  }

  /* ---------------- Fiche produit : dimensions + badge 3D ---------------- */

  function setupProductCardInfo() {
    const grid = document.getElementById('catalog');
    if (!grid || grid.dataset.cigogneInfo === '1') return;
    grid.dataset.cigogneInfo = '1';
    // Empêche le tap sur l'indicateur d'info de déclencher l'ajout du meuble
    // (le glisser-poser du catalogue s'accroche en pointerdown sur toute la carte).
    grid.addEventListener('pointerdown', e => {
      if (e.target.closest('.product-card__dims, .product-card__model')) e.stopPropagation();
    }, true);
    grid.addEventListener('click', e => {
      const dims = e.target.closest('.product-card__dims');
      const model = e.target.closest('.product-card__model');
      if (dims) {
        e.stopPropagation();
        showPop(dims, { title: 'Dimensions', accent: 'info',
          fr: 'Ce sont les dimensions réelles du produit.',
          dz: 'هاذو هما الديمانسيونات الحقيقية تاع الموبلية.' });
      } else if (model) {
        e.stopPropagation();
        showPop(model, { title: 'Modèle 3D', accent: 'ai',
          fr: 'Ce meuble possède un modèle 3D.',
          dz: 'هاد الموبلية عندها موديل 3D.' });
      }
    }, true);
  }

  /* ---------------- Toast bilingue à l'ajout d'un meuble ---------------- */

  function bilingualToast(fr, dz, tone) {
    const host = document.getElementById('toasts');
    if (!host) return;
    const el = document.createElement('div');
    el.className = `toast toast--${tone}`;
    el.innerHTML = `${esc(fr)}<span class="dz" dir="rtl" lang="ar">${esc(dz)}</span>`;
    host.appendChild(el);
    requestAnimationFrame(() => el.classList.add('is-in'));
    setTimeout(() => { el.classList.remove('is-in'); setTimeout(() => el.remove(), 260); }, 3800);
    while (host.children.length > 3) host.firstChild.remove();
  }

  function setupAddedItemToast() {
    if (!window.App || typeof window.App.on !== 'function') return;
    let prevCount = (window.App.state?.items || []).length;
    window.App.on('items', items => {
      const count = (items || []).length;
      if (count > prevCount) {
        let shown = Number(localStorage.getItem(ADD_HINT_KEY) || 0);
        if (shown < ADD_HINT_MAX) {
          bilingualToast(
            'Meuble ajouté. Vous pouvez maintenant ajuster sa position dans la pièce.',
            'زدنا الموبلية. دابا تقدر تبدل البلاصة تاعها فالغرفة.',
            'success'
          );
          localStorage.setItem(ADD_HINT_KEY, String(shown + 1));
        }
      }
      prevCount = count;
    });
  }

  /* ---------------- Assistant IA : carte d'intro + exemples ---------------- */

  function setupAssistantIntro() {
    const dialog = document.querySelector('.ai-assistant__dialog');
    const quick = document.getElementById('aiAssistantQuick');
    if (!dialog || !quick || dialog.dataset.cigogneIntro === '1') return;
    dialog.dataset.cigogneIntro = '1';

    const intro = document.createElement('div');
    intro.className = 'ui-explainer ui-x-accent-ai ai-assistant__intro';
    intro.innerHTML = `
      <span class="ui-explainer__icon">✨</span>
      <span class="ui-explainer__body">
        <p class="ui-explainer__title">Assistant IA</p>
        <p class="ui-explainer__fr">Décrivez simplement ce que vous voulez changer dans votre pièce.</p>
        <p class="ui-explainer__dz" dir="rtl" lang="ar">قول للـIA واش حاب تبدل فالغرفة، وهي تعاونك تديرها.</p>
      </span>`;
    quick.insertAdjacentElement('beforebegin', intro);

    const examples = [
      { fr: 'Ajoute un canapé moderne beige.', dz: 'زيد كانابي مودرن بالبيج.' },
      { fr: 'Place-le devant le mur.', dz: 'حطّو قدّام الحيط.' },
      { fr: 'Rends-le 20 % plus petit.', dz: 'صغّرو بـ20٪.' },
      { fr: 'Supprime le fauteuil.', dz: 'نحي الفوطاي.' },
    ];
    const wrap = document.createElement('div');
    wrap.className = 'ai-assistant__examples';
    wrap.innerHTML = examples.map(ex => `
      <button type="button" class="ai-assistant__example">
        <span class="fr">${esc(ex.fr)}</span>
        <span class="dz" dir="rtl" lang="ar">${esc(ex.dz)}</span>
      </button>`).join('');
    quick.insertAdjacentElement('beforebegin', wrap);
    wrap.querySelectorAll('.ai-assistant__example').forEach((btn, i) => {
      btn.addEventListener('click', () => {
        const input = document.getElementById('aiAssistantInput');
        if (input) { input.value = examples[i].fr; document.getElementById('aiAssistantSend')?.click(); input.value = ''; }
      });
    });
  }

  /* ---------------- Guide de première visite ---------------- */

  function showGuide(force) {
    if (!force && localStorage.getItem(SEEN_KEY) === '1') return;
    if (document.querySelector('.ui-guide-overlay')) return;
    let step = 0;
    const overlay = document.createElement('div');
    overlay.className = 'ui-guide-overlay';
    const card = document.createElement('section');
    card.className = 'ui-guide-card';

    function highlight() {
      document.querySelectorAll('.ui-guide-highlight').forEach(e => e.classList.remove('ui-guide-highlight'));
      const el = document.querySelector(ONBOARDING[step].target);
      if (el) { el.classList.add('ui-guide-highlight'); try { el.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } catch (_) {} }
    }

    function finish(remember) {
      if (remember) localStorage.setItem(SEEN_KEY, '1');
      document.querySelectorAll('.ui-guide-highlight').forEach(e => e.classList.remove('ui-guide-highlight'));
      overlay.remove();
    }

    function render() {
      const x = ONBOARDING[step];
      card.innerHTML = `
        <span class="ui-guide-kicker">${x.icon} GUIDE RAPIDE · ${step + 1} / ${ONBOARDING.length}</span>
        <h2>${esc(x.title)}</h2>
        <p>${esc(x.fr)}</p>
        <div class="ui-guide-dz" dir="rtl" lang="ar">${esc(x.dz)}</div>
        <div class="ui-guide-progress">${ONBOARDING.map((_, i) => `<i class="ui-guide-dot ${i === step ? 'on' : ''}"></i>`).join('')}</div>
        <div class="ui-guide-actions">
          <button id="uiGuideSkip" type="button">Passer</button>
          <button class="primary" id="uiGuideNext" type="button">${step === ONBOARDING.length - 1 ? 'Commencer' : 'Suivant'}</button>
        </div>
        <div class="ui-guide-skip-row">
          <label><input type="checkbox" id="uiGuideRemember" checked> Ne plus afficher</label>
        </div>`;
      card.querySelector('#uiGuideSkip').onclick = () => finish(card.querySelector('#uiGuideRemember').checked);
      card.querySelector('#uiGuideNext').onclick = () => {
        if (step < ONBOARDING.length - 1) { step++; render(); }
        else finish(card.querySelector('#uiGuideRemember').checked);
      };
      highlight();
    }

    overlay.appendChild(card);
    document.body.appendChild(overlay);
    render();
  }

  function addGuideLauncher() {
    const help = document.querySelector('[data-view="help"] .panel__scroll');
    if (!help || help.dataset.cigogneLauncher === '1') return;
    help.dataset.cigogneLauncher = '1';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn--ai btn--block';
    btn.style.margin = '0 0 12px';
    btn.textContent = '✨ Revoir le guide';
    btn.onclick = () => showGuide(true);
    help.insertBefore(btn, help.firstChild);
  }

  /* ---------------- Démarrage ---------------- */

  ready(() => {
    injectStyles();
    setupNavHelp();
    setTimeout(() => {
      setupBadgeHelp();
      setupPairedCaptions();
      setupRoomImportHelper();
      setupCatalogAIHelper();
      setupRetoucheProcessCard();
      setupProductCardInfo();
      setupAddedItemToast();
      addGuideLauncher();
      const forceGuide = /[?&]guide=1(?:&|$)/.test(location.search);
      showGuide(forceGuide);
    }, 350);

    // L'assistant IA est ouvert à la demande : on ajoute la carte d'intro
    // au premier affichage plutôt qu'au chargement de la page.
    document.getElementById('catalogAssistantBtn')?.addEventListener('click', () => setTimeout(setupAssistantIntro, 30));
  });

  // Re-scanner les filtres/catégories du catalogue si le panneau catalogue
  // est réactivé plus tard (badges idempotents grâce à data-cigogne-hint).
  document.addEventListener('click', e => {
    if (e.target.closest('[data-panel="catalog"]')) setTimeout(setupBadgeHelp, 60);
  });

  window.CigogneUIGuide = {
    show: showGuide,
    reset: () => { localStorage.removeItem(SEEN_KEY); showGuide(true); },
  };
})();
