/* =========================================================
   La Cigogne D'Ailleurs — UX guidance + mobile runtime bridge
   - Forces a real mobile UI on touch devices even when a browser
     exposes a desktop-like CSS viewport.
   - Adds compact French + Algerian Darija explanations.
   - Does not touch backend, AI, placement or 3D logic.
   ========================================================= */
(() => {
  "use strict";

  const root = document.documentElement;
  const body = document.body;
  const $ = (sel, scope = document) => scope.querySelector(sel);
  const $$ = (sel, scope = document) => [...scope.querySelectorAll(sel)];

  /* ---------------------------------------------------------------
     Mobile runtime detection
     Some Android browsers / "Desktop site" modes expose a wide CSS
     viewport even though the device is touch-first. The CSS class lets
     the responsive layout activate in that case too.
     --------------------------------------------------------------- */
  function isTouchDevice() {
    return navigator.maxTouchPoints > 0 || "ontouchstart" in window;
  }

  function syncMobileMode() {
    const cssNarrow = window.matchMedia?.("(max-width: 1100px)")?.matches;
    const mobileUA = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "");
    const compactTouch = isTouchDevice() && Math.min(screen.width || 9999, screen.height || 9999) <= 1100;
    const mobile = Boolean(cssNarrow || mobileUA || compactTouch);
    root.classList.toggle("is-mobile-ui", mobile);
    root.dataset.viewportWidth = String(Math.round(window.visualViewport?.width || window.innerWidth || 0));
  }

  function syncViewportHeight() {
    const vv = window.visualViewport;
    const h = vv?.height || window.innerHeight || document.documentElement.clientHeight;
    root.style.setProperty("--app-vh", `${Math.round(h)}px`);
  }

  syncMobileMode();
  syncViewportHeight();
  window.addEventListener("resize", () => { syncMobileMode(); syncViewportHeight(); }, { passive: true });
  window.addEventListener("orientationchange", () => setTimeout(() => {
    syncMobileMode(); syncViewportHeight(); window.App?.resize?.();
  }, 80), { passive: true });
  window.visualViewport?.addEventListener("resize", () => {
    syncViewportHeight();
    window.App?.resize?.();
  }, { passive: true });
  window.visualViewport?.addEventListener("scroll", syncViewportHeight, { passive: true });
  window.addEventListener("pageshow", () => { syncMobileMode(); syncViewportHeight(); window.App?.resize?.(); });

  /* ---------------------------------------------------------------
     Reusable bilingual explainer component
     --------------------------------------------------------------- */
  const helpIcon = `<span class="ui-explainer__icon" aria-hidden="true">i</span>`;

  function makeExplainer({ title, fr, dz, tone = "info", example = "" }) {
    const el = document.createElement("div");
    el.className = `ui-explainer ui-explainer--${tone}`;
    el.setAttribute("role", "note");
    el.innerHTML = `
      <div class="ui-explainer__top">
        ${helpIcon}
        <strong>${title}</strong>
      </div>
      <div class="ui-explainer__fr">${fr}</div>
      <div class="ui-explainer__dz" dir="rtl">${dz}</div>
      ${example ? `<div class="ui-explainer__example">${example}</div>` : ""}
    `;
    return el;
  }

  function insertAfter(target, data, key) {
    if (!target || target.parentElement?.querySelector(`:scope > [data-ui-help="${key}"]`)) return;
    const el = makeExplainer(data);
    el.dataset.uiHelp = key;
    target.insertAdjacentElement("afterend", el);
    return el;
  }

  /* ---------------------------------------------------------------
     Navigation help: hover/focus on desktop, tap on mobile.
     --------------------------------------------------------------- */
  const navHelp = {
    catalog: { title: "Meubles", fr: "Choisissez et placez vos meubles dans la pièce.", dz: "اختار وحطّ الموبليات تاعك فالغرفة.", tone: "ai" },
    room: { title: "Pièce", fr: "Importez la photo et analysez l'espace.", dz: "دخل تصويرة الغرفة وخلي الـIA تحلل المساحة تاعها.", tone: "info" },
    ai: { title: "Retouche", fr: "Supprimez les meubles existants avec la gomme IA.", dz: "هنا تقدر تنحي الموبليات لي كاينين باستعمال الـIA.", tone: "ai" },
    summary: { title: "Liste", fr: "Retrouvez les meubles ajoutés et leur prix.", dz: "هنا تلقى الموبليات لي زدتهم وتشوف السومة تاعهم.", tone: "success" },
    projects: { title: "Projets", fr: "Enregistrez et retrouvez vos compositions.", dz: "هنا تحفظ المشاريع تاعك وترجعلهم من بعد.", tone: "info" },
    account: { title: "Compte", fr: "Gérez votre compte et vos informations.", dz: "من هنا تسير الحساب والمعلومات تاعك.", tone: "info" },
    help: { title: "Aide", fr: "Retrouvez les explications et les conseils.", dz: "ما فهمتش حاجة؟ هنا تلقى الشرح والمساعدة.", tone: "orange" },
  };

  let navPopover = null;
  let navPopoverTimer = 0;

  function hideNavHelp() {
    clearTimeout(navPopoverTimer);
    navPopover?.remove();
    navPopover = null;
  }

  function showNavHelp(btn) {
    const data = navHelp[btn?.dataset.panel];
    if (!data) return;
    hideNavHelp();
    navPopover = makeExplainer(data);
    navPopover.classList.add("ui-explainer--nav");
    document.body.appendChild(navPopover);
    const r = btn.getBoundingClientRect();
    const mobile = root.classList.contains("is-mobile-ui");
    const width = Math.min(310, window.innerWidth - 20);
    navPopover.style.width = `${width}px`;
    if (mobile) {
      navPopover.style.left = `${Math.max(10, Math.min(window.innerWidth - width - 10, r.left + r.width / 2 - width / 2))}px`;
      navPopover.style.bottom = `${Math.max(74, window.innerHeight - r.top + 10)}px`;
    } else {
      navPopover.style.left = `${Math.min(window.innerWidth - width - 14, r.right + 12)}px`;
      navPopover.style.top = `${Math.max(10, Math.min(window.innerHeight - 160, r.top))}px`;
    }
    navPopover.addEventListener("click", e => e.stopPropagation());
    navPopoverTimer = window.setTimeout(hideNavHelp, mobile ? 4200 : 3600);
  }

  function wireNavigation() {
    $$(".rail__btn[data-panel]").forEach(btn => {
      if (btn.dataset.uiHelpWired) return;
      btn.dataset.uiHelpWired = "1";
      btn.addEventListener("mouseenter", () => {
        if (!root.classList.contains("is-mobile-ui")) showNavHelp(btn);
      });
      btn.addEventListener("focus", () => showNavHelp(btn));
      btn.addEventListener("touchstart", () => showNavHelp(btn), { passive: true });
      btn.setAttribute("title", navHelp[btn.dataset.panel]?.fr || "");
    });
  }

  /* ---------------------------------------------------------------
     Panel explanations
     --------------------------------------------------------------- */
  function installPanelExplainers() {
    const panel = $("#panel");
    if (!panel) return;

    insertAfter($("[data-view=\"catalog\"] .panel__head", panel), {
      title: "Meubles",
      fr: "Choisissez un meuble dans le catalogue puis placez-le dans votre pièce.",
      dz: "اختار الموبلية من الكاتالوغ ومن بعد حطّها فالغرفة تاعك.",
      tone: "ai"
    }, "catalog-head");

    insertAfter($("[data-view=\"room\"] .panel__head", panel), {
      title: "Commencer ici",
      fr: "Importez une photo claire de votre pièce pour commencer.",
      dz: "دخل تصويرة واضحة للغرفة باش نبداو.",
      tone: "info"
    }, "room-head");

    insertAfter($("[data-view=\"ai\"] .panel__head", panel), {
      title: "Gomme intelligente",
      fr: "Sélectionnez un meuble réel : l'IA crée le masque puis reconstruit la surface.",
      dz: "اختار موبلية حقيقية، والـIA تدير الماسك وتعاود تبني البلاصة.",
      tone: "ai"
    }, "ai-head");

    insertAfter($("[data-view=\"summary\"] .panel__head", panel), {
      title: "Votre liste",
      fr: "Vérifiez les meubles ajoutés et le total estimé.",
      dz: "شوف الموبليات لي زدتهم والسومة الإجمالية التقريبية.",
      tone: "success"
    }, "summary-head");

    insertAfter($("[data-view=\"projects\"] .panel__head", panel), {
      title: "Projets",
      fr: "Sauvegardez vos compositions pour les retrouver plus tard.",
      dz: "احفظ الكومبوزيسيون تاعك باش ترجع لها من بعد.",
      tone: "info"
    }, "projects-head");

    insertAfter($("[data-view=\"account\"] .panel__head", panel), {
      title: "Compte",
      fr: "Gérez vos informations si vous utilisez les fonctions de compte.",
      dz: "من هنا تسير معلومات الحساب تاعك إذا حبيت تستعملو.",
      tone: "info"
    }, "account-head");

    insertAfter($("[data-view=\"help\"] .panel__head", panel), {
      title: "Besoin d'aide ?",
      fr: "Retrouvez ici les gestes, raccourcis et explications principales.",
      dz: "هنا تلقى الحركات، الاختصارات والشرح المهم.",
      tone: "orange"
    }, "help-head");

    insertAfter($("#catalogSearch"), {
      title: "Recherche",
      fr: "Trouvez rapidement le meuble que vous cherchez.",
      dz: "قلب بسرعة على الموبلية لي راك حابها.",
      tone: "info"
    }, "catalog-search");

    insertAfter($("#catalogFilters"), {
      title: "Catégories",
      fr: "Filtrez les meubles par type : assises, tables, chambre, déco…",
      dz: "صنّف الموبليات حسب النوع: قعدات، طاولات، غرفة، ديكو…",
      tone: "info"
    }, "catalog-filters");

    insertAfter($(".catalog-smart-tools"), {
      title: "Affiner la recherche",
      fr: "Style, matière et tri vous aident à trouver le bon produit.",
      dz: "الستايل، الماتريال والترتيب يعاونوك تلقى المنتج المناسب.",
      tone: "info"
    }, "catalog-smart-tools");

    insertAfter($("#catalogAssistantBtn"), {
      title: "Recherche avec l'IA",
      fr: "Décrivez simplement ce que vous voulez : l'IA cherche dans le catalogue.",
      dz: "قول للـIA واش راك حاب وهي تقلب فالكَاتالوغ على الموبلية المناسبة.",
      tone: "ai",
      example: "Exemple : « Je cherche un canapé moderne beige » · « نحب كانابي مودرن بالبيج »"
    }, "catalog-ai");

    insertAfter($("[data-view=\"room\"] [data-import-room]", panel), {
      title: "Importer une photo",
      fr: "Ajoutez une photo de votre pièce pour commencer.",
      dz: "دخل تصويرة الغرفة باش نبداو.",
      tone: "info"
    }, "room-import");

    insertAfter($("#analyzeAction"), {
      title: "Analyse de la pièce",
      fr: "L'IA analyse le sol, la profondeur et les objets pour améliorer le placement.",
      dz: "الـIA تحلل السول، العمق والموبليات باش تحط الجديد فالبلاصة الصح.",
      tone: "ai"
    }, "room-analyze");

    insertAfter($("#spatialOptimizeBtn"), {
      title: "Placement intelligent",
      fr: "L'IA utilise le sol et la profondeur détectés pour aider à respecter l'échelle.",
      dz: "الـIA تستعمل السول والعمق باش تعاونك تحط الموبلية بالقياس الصحيح.",
      tone: "ai"
    }, "room-spatial");

    insertAfter($("#threeModeGroup"), {
      title: "Vue 3D",
      fr: "Visualisez les meubles en 3D et vérifiez leur placement.",
      dz: "شوف الموبليات بالـ3D وتأكد بلي البلاصة تاعهم مليحة.",
      tone: "ai"
    }, "room-3d");

    insertAfter($("#glbAction"), {
      title: "Importer un modèle 3D",
      fr: "Ajoutez votre propre modèle 3D (.glb) pour l'utiliser dans la scène.",
      dz: "دخل الموديل 3D تاعك (.glb) إذا عندك واحد واستعملو فالغرفة.",
      tone: "info"
    }, "room-glb");

    insertAfter($("#eraseAction"), {
      title: "Effacer un objet",
      fr: "Sélectionnez un meuble existant : l'IA le retire et reconstruit la surface.",
      dz: "اختار الموبلية لي حاب تنحيها والـIA تنحيهالك وتعاود تبني البلاصة.",
      tone: "ai"
    }, "ai-erase");

    const quickButtons = {
      "#viewPhoto": ["Photo", "Revenir à la photo et continuer le placement.", "ارجع للتصويرة وكمل ترتيب الموبليات."],
      "#viewThree": ["Vue 3D", "Passer à la scène 3D pour vérifier le volume et le placement.", "روح للـ3D باش تشوف الحجم والبلاصة مليح."],
      "#previewBtn": ["Aperçu", "Prévisualiser la composition finale.", "شوف المعاينة النهائية للكومبوزيسيون."],
      "#exportBtn": ["Exporter", "Exporter le résultat de votre composition.", "صدّر النتيجة تاع الكومبوزيسيون."],
      "#summaryBtn": ["Récapitulatif", "Voir les meubles ajoutés et le total estimé.", "شوف الموبليات لي زدتهم والسومة الإجمالية."],
      "#newDesignBtn": ["Nouvelle pièce", "Vider la composition et recommencer.", "فرّغ الكومبوزيسيون وعاود من جديد."],
      "#panelCollapse": ["Réduire", "Masquer le panneau pour voir davantage la pièce.", "خبّي البانيل باش تشوف مساحة أكبر من الغرفة."],
      "#aiAssistantSend": ["Envoyer", "Envoyer votre demande à l'Assistant IA.", "ابعث الطلب تاعك للـAssistant IA."]
    };
    Object.entries(quickButtons).forEach(([selector, [title, fr, dz]]) => {
      const el = $(selector);
      if (!el || el.dataset.uiQuickHelp) return;
      el.dataset.uiQuickHelp = "1";
      el.setAttribute("title", `${title} — ${fr}\n${dz}`);
      el.setAttribute("aria-description", `${fr} ${dz}`);
    });
  }

  /* ---------------------------------------------------------------
     AI assistant guidance
     --------------------------------------------------------------- */
  function installAssistantExplainer() {
    const dialog = $("#aiAssistant .ai-assistant__dialog");
    if (!dialog || dialog.querySelector("[data-ui-help=assistant-intro]")) return;
    const intro = makeExplainer({
      title: "✨ Assistant IA",
      fr: "Décrivez simplement ce que vous voulez changer dans votre pièce.",
      dz: "قول للـIA واش حاب تبدل فالغرفة، وهي تعاونك تديرها.",
      tone: "ai",
      example: "« Ajoute un canapé moderne beige » · « زيد كانابي مودرن بالبيج »"
    });
    intro.dataset.uiHelp = "assistant-intro";
    dialog.querySelector(".ai-assistant__head > div")?.appendChild(intro);

    const input = $("#aiAssistantInput");
    if (input) input.setAttribute("title", "Décrivez l'action à réaliser dans la pièce");
  }

  /* ---------------------------------------------------------------
     Furniture card help. The catalogue is rebuilt dynamically, so use
     a MutationObserver and add a tiny info badge to every card.
     --------------------------------------------------------------- */
  function decorateFurnitureCards() {
    $$("#catalog .product-card").forEach(card => {
      if (card.querySelector(".product-card__info")) return;
      const info = document.createElement("span");
      info.className = "product-card__info";
      info.setAttribute("role", "button");
      info.setAttribute("tabindex", "0");
      info.setAttribute("aria-label", "Voir l'explication du produit");
      info.textContent = "i";
      info.title = "Dimensions, prix et disponibilité";
      card.appendChild(info);

      const toggle = e => {
        e.preventDefault();
        e.stopPropagation();
        const old = document.querySelector(".product-help-popover");
        if (old) old.remove();
        const entry = window.catalogEntry?.(card.dataset.entry);
        if (!entry) return;
        const pop = makeExplainer({
          title: entry.name || "Meuble",
          fr: "Les dimensions affichées correspondent au produit du catalogue. Le badge 3D indique qu'un modèle 3D est disponible.",
          dz: "الديمانسيونات لي باينين هما تاع المنتج. وإذا شفت 3D راه كاين موديل 3D متوفر.",
          tone: "info",
          example: `${Number(entry.w || 0).toFixed(2)} × ${Number(entry.d || 0).toFixed(2)} m · ${window.formatPrice?.(entry.price) || "—"}`
        });
        pop.classList.add("product-help-popover");
        document.body.appendChild(pop);
        const r = card.getBoundingClientRect();
        const width = Math.min(320, window.innerWidth - 20);
        pop.style.width = `${width}px`;
        pop.style.left = `${Math.max(10, Math.min(window.innerWidth - width - 10, r.left))}px`;
        pop.style.top = `${Math.max(10, Math.min(window.innerHeight - 190, r.bottom + 8))}px`;
        setTimeout(() => pop.remove(), 4500);
      };
      info.addEventListener("click", toggle);
      info.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") toggle(e); });
    });
  }

  /* ---------------------------------------------------------------
     Compact success guidance without changing existing toast logic.
     --------------------------------------------------------------- */
  function installSuccessHints() {
    if (window.CigogneUI?.toast && !window.CigogneUI._guidanceToast) {
      const originalToast = window.CigogneUI.toast;
      // Keep the original API intact; only add bilingual context for the
      // most important successful action.
      window.CigogneUI.toast = function (message, tone = "info") {
        if (tone === "success" || /ajouté|ajoutée/i.test(message || "")) {
          originalToast(`${message} · ${"زدنا الموبلية، دابا تقدر تبدل البلاصة تاعها."}`, tone);
        } else {
          originalToast(message, tone);
        }
      };
      window.CigogneUI._guidanceToast = true;
    }
  }

  /* ---------------------------------------------------------------
     First-visit walkthrough. It is deliberately short and skippable.
     --------------------------------------------------------------- */
  const ONBOARDING_KEY = "cigogne-onboarding-v1";
  function installOnboarding() {
    if (localStorage.getItem(ONBOARDING_KEY) || $("#cigogneOnboarding")) return;
    const overlay = document.createElement("div");
    overlay.id = "cigogneOnboarding";
    overlay.className = "cigogne-onboarding";
    overlay.innerHTML = `
      <div class="cigogne-onboarding__card" role="dialog" aria-modal="true" aria-labelledby="onboardingTitle">
        <div class="cigogne-onboarding__head">
          <span class="cigogne-onboarding__eyebrow">PREMIÈRE VISITE</span>
          <button type="button" class="cigogne-onboarding__close" data-onboarding-skip aria-label="Fermer">×</button>
        </div>
        <h2 id="onboardingTitle">Découvrez La Cigogne D'Ailleurs</h2>
        <p class="cigogne-onboarding__intro">5 étapes pour comprendre l'essentiel.</p>
        <div class="cigogne-onboarding__steps">
          <div><b>1</b><span><strong>Meubles</strong><small>اختار الموبليات وحطّهم فالغرفة.</small></span></div>
          <div><b>2</b><span><strong>Pièce</strong><small>دخل تصويرة الغرفة.</small></span></div>
          <div><b>3</b><span><strong>Retouche IA</strong><small>نحي الموبليات القديمة بالـIA.</small></span></div>
          <div><b>4</b><span><strong>Assistant IA</strong><small>قول للـIA واش حاب تدير.</small></span></div>
          <div><b>5</b><span><strong>Vue 3D</strong><small>شوف النتيجة بالـ3D.</small></span></div>
        </div>
        <div class="cigogne-onboarding__actions">
          <button type="button" class="btn btn--quiet" data-onboarding-skip>Passer</button>
          <button type="button" class="btn btn--primary" data-onboarding-done>J'ai compris</button>
        </div>
        <label class="cigogne-onboarding__remember"><input type="checkbox" id="onboardingRemember"> Ne plus afficher · ما تبانش ثاني</label>
      </div>`;
    document.body.appendChild(overlay);

    const close = () => {
      if ($("#onboardingRemember")?.checked) localStorage.setItem(ONBOARDING_KEY, "1");
      overlay.remove();
    };
    overlay.querySelectorAll("[data-onboarding-skip], [data-onboarding-done]").forEach(btn => btn.addEventListener("click", close));
  }

  /* ---------------------------------------------------------------
     Observe dynamic catalogue and late-loaded modules.
     --------------------------------------------------------------- */
  const observer = new MutationObserver(() => {
    wireNavigation();
    installAssistantExplainer();
    decorateFurnitureCards();
  });

  function bootGuidance() {
    wireNavigation();
    installPanelExplainers();
    installAssistantExplainer();
    decorateFurnitureCards();
    installSuccessHints();
    observer.observe(document.body, { childList: true, subtree: true });
    // Give the real app a moment to finish its initial catalog render.
    window.setTimeout(() => {
      wireNavigation();
      installPanelExplainers();
      installAssistantExplainer();
      decorateFurnitureCards();
      installOnboarding();
      window.App?.resize?.();
    }, 120);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bootGuidance, { once: true });
  else bootGuidance();
})();
