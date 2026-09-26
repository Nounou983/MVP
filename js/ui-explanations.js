/* La Cigogne D'Ailleurs — UI explanations + first-use guide
   French + Algerian Darija. No dependency on the application internals. */
(function(){
  'use strict';
  var KEY='cigogne_ui_guide_v2_seen';
  var root=document.documentElement;
  var guideSteps=[
    {target:'[data-panel="catalog"]',title:'Meubles',fr:'Choisissez un meuble dans le catalogue et ajoutez-le dans votre pièce.',dz:'اختار الموبليا من الكاتالوغ وزيدها للبياسة تاعك.'},
    {target:'[data-panel="room"]',title:'Pièce',fr:'Importez votre photo et laissez l’IA analyser le sol, la profondeur et l’espace.',dz:'دخل تصويرة تاع البياسة وخلي الـIA تحلل السول، العمق والمساحة.'},
    {target:'[data-panel="ai"]',title:'Retouche IA',fr:'Sélectionnez un ancien meuble : l’IA crée un masque et reconstruit la zone.',dz:'حدد الموبليا القديمة، والـIA تدير الماسك وتمحيها وتعاود تبني البلاصة.'},
    {target:'#catalogAssistantBtn',title:'Assistant IA',fr:'Décrivez ce que vous voulez : ajouter, retirer, remplacer ou déplacer un meuble.',dz:'قول للـAI واش حاب: زيد، نحي، بدل ولا حرك موبليا وبالمكان والحجم المناسب.'},
    {target:'#viewThree',title:'Vue 3D',fr:'Passez en 3D pour visualiser les volumes et déplacer les meubles dans la scène.',dz:'روح للـ3D باش تشوف الحجم والموضع وتقدر تحرك الموبليا في المشهد.'}
  ];

  function ready(fn){ if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',fn); else fn(); }
  function esc(s){return String(s).replace(/[&<>\"]/g,function(c){return ({'&':'&amp;','<':'&lt;','>':'&gt;','\\':'&#92;','"':'&quot;'})[c]||c;});}

  function injectStyles(){
    if(document.getElementById('ui-guide-styles')) return;
    var st=document.createElement('style'); st.id='ui-guide-styles';
    st.textContent=`
      .ui-guide-overlay{position:fixed;inset:0;z-index:10000;background:rgba(15,16,20,.60);backdrop-filter:blur(3px);display:grid;place-items:center;padding:20px}
      .ui-guide-card{width:min(92vw,460px);background:#fff;border:1px solid #E7E4DE;border-radius:22px;box-shadow:0 28px 80px rgba(0,0,0,.35);padding:24px;color:#14151A}
      .ui-guide-kicker{display:inline-flex;padding:5px 9px;border-radius:999px;background:#F1EDFF;color:#5636E0;font-size:11px;font-weight:700;letter-spacing:.02em}
      .ui-guide-card h2{font-size:22px;margin:12px 0 7px}.ui-guide-card p{color:#4A4E58;font-size:13px;line-height:1.55;margin:0 0 10px}
      .ui-guide-dz{padding:10px 12px;background:#FFF7D9;border-left:4px solid #FFC42E;border-radius:10px;font-size:13px;line-height:1.5;margin:10px 0 18px}
      .ui-guide-actions{display:flex;gap:8px;justify-content:space-between;align-items:center}.ui-guide-actions button{min-height:42px;border-radius:10px;padding:0 14px;border:1px solid #E7E4DE;background:#FAF8F5;color:#14151A;font-weight:600}.ui-guide-actions .primary{background:#6B4DF6;border-color:#6B4DF6;color:#fff}.ui-guide-progress{display:flex;gap:5px;margin:0 0 15px}.ui-guide-dot{width:7px;height:7px;border-radius:50%;background:#D9D5CD}.ui-guide-dot.on{background:#6B4DF6;width:20px}
      .ui-guide-popover{position:fixed;z-index:9998;width:min(330px,calc(100vw - 24px));background:#fff;border:1px solid #E7E4DE;border-radius:14px;box-shadow:0 14px 40px rgba(0,0,0,.24);padding:13px;color:#14151A;display:none}
      .ui-guide-popover strong{display:block;font-size:13px;margin-bottom:4px}.ui-guide-popover p{margin:0;color:#5C5D66;font-size:11.5px;line-height:1.45}.ui-guide-popover .dz{margin-top:7px;color:#5636E0;font-weight:600}
      .ui-guide-highlight{position:relative!important;z-index:10001!important;box-shadow:0 0 0 4px rgba(107,77,246,.45),0 0 35px rgba(107,77,246,.45)!important;border-radius:12px!important}
      .ui-help-dot{position:absolute;right:4px;top:4px;width:18px;height:18px;border-radius:50%;background:#6B4DF6;color:#fff;font-size:11px;font-weight:700;display:grid;place-items:center;z-index:4;pointer-events:none}
    `;
    document.head.appendChild(st);
  }

  function addPersistentHints(){
    var items=[
      ['[data-panel="catalog"]','Meubles','اختار وزيد الموبليا'],
      ['[data-panel="room"]','Pièce','دخل وحلل تصويرة البياسة'],
      ['[data-panel="ai"]','Retouche IA','نحي الموبليا القديمة بالـAI'],
      ['[data-panel="summary"]','Liste','شوف الموبليا والأسعار'],
      ['[data-panel="projects"]','Projets','سجل وخزن المشاريع'],
      ['[data-panel="help"]','Aide','شوف كيفاش تخدم كل خاصية'],
      ['#catalogAssistantBtn','Assistant IA','قول للـAI واش حاب يدير'],
      ['#glbAction','Modèle 3D','دخل موديل GLB ثلاثي الأبعاد'],
      ['#analyzeAction','Analyse','خلي الـAI يفهم البياسة'],
      ['#eraseAction','Gomme IA','حدد الموبليا القديمة وامحيها'],
      ['#spatialOptimizeBtn','Placement intelligent','خلي الـAI يقترح بلاصة وحجم مناسب'],
      ['#viewThree','Vue 3D','شوف الموبليا في 3D']
    ];
    items.forEach(function(it){
      var el=document.querySelector(it[0]); if(!el || el.dataset.explained==='1') return;
      el.dataset.explained='1'; el.setAttribute('data-explain-fr',it[1]); el.setAttribute('data-explain-dz',it[2]);
      var wrap=el.parentElement;
      if(wrap && getComputedStyle(wrap).position==='static') wrap.style.position='relative';
      if(el.classList.contains('rail__btn')){
        var dot=document.createElement('i'); dot.className='ui-help-dot'; dot.textContent='?'; el.appendChild(dot);
      }
      el.addEventListener('click',function(){ showHint(el); });
    });
  }

  function showHint(el){
    var old=document.querySelector('.ui-guide-popover'); if(old) old.remove();
    var pop=document.createElement('div'); pop.className='ui-guide-popover';
    pop.innerHTML='<strong>'+esc(el.dataset.explainFr||'Fonction')+'</strong><p>'+esc(explainFR(el))+'</p><p class="dz">🇩🇿 '+esc(el.dataset.explainDz||'هاذ الخاصية تعاونك باش تستعمل الموقع.')+'</p>';
    document.body.appendChild(pop);
    var r=el.getBoundingClientRect(), w=Math.min(330,window.innerWidth-24), left=Math.max(12,Math.min(r.left,window.innerWidth-w-12));
    var top=r.bottom+10; if(top+150>window.innerHeight) top=Math.max(12,r.top-160);
    pop.style.left=left+'px'; pop.style.top=top+'px'; pop.style.display='block';
    setTimeout(function(){document.addEventListener('click',function close(e){if(!pop.contains(e.target)&&e.target!==el){pop.remove();document.removeEventListener('click',close)}},{once:true})},0);
  }
  function explainFR(el){
    var map={
      'catalogAssistantBtn':'Décrivez un besoin en langage naturel et l’assistant cherche dans votre catalogue.',
      'glbAction':'Importez votre propre modèle 3D au format GLB ou glTF.',
      'analyzeAction':'Analysez automatiquement le sol, la profondeur et les objets visibles.',
      'eraseAction':'Activez la gomme IA puis sélectionnez le meuble réel à retirer.',
      'spatialOptimizeBtn':'Optimisez le placement selon la profondeur et les relations entre meubles.',
      'viewThree':'Basculez entre la photo et la scène 3D.'
    };
    return map[el.id]||el.dataset.explainFr||'Ouvre cette fonction de l’application.';
  }

  function showGuide(force){
    if(!force && localStorage.getItem(KEY)==='1') return;
    if(document.querySelector('.ui-guide-overlay')) return;
    injectStyles(); addPersistentHints();
    var overlay=document.createElement('div'); overlay.className='ui-guide-overlay';
    var card=document.createElement('section'); card.className='ui-guide-card';
    var step=0;
    function render(){
      var x=guideSteps[step];
      card.innerHTML='<span class="ui-guide-kicker">GUIDE RAPIDE · '+(step+1)+' / '+guideSteps.length+'</span><h2>'+esc(x.title)+'</h2><p>'+esc(x.fr)+'</p><div class="ui-guide-dz">🇩🇿 <b>بالدارجة:</b> '+esc(x.dz)+'</div><div class="ui-guide-progress">'+guideSteps.map(function(_,i){return '<i class="ui-guide-dot '+(i===step?'on':'')+'"></i>'}).join('')+'</div><div class="ui-guide-actions"><button id="uiGuideSkip">Passer</button><button class="primary" id="uiGuideNext">'+(step===guideSteps.length-1?'Commencer':'Suivant')+'</button></div>';
      card.querySelector('#uiGuideSkip').onclick=finish;
      card.querySelector('#uiGuideNext').onclick=function(){ if(step<guideSteps.length-1){step++;render();highlight()} else finish(); };
      highlight();
    }
    function highlight(){
      document.querySelectorAll('.ui-guide-highlight').forEach(function(e){e.classList.remove('ui-guide-highlight')});
      var el=document.querySelector(guideSteps[step].target); if(el){el.classList.add('ui-guide-highlight'); try{el.scrollIntoView({block:'nearest',inline:'nearest'})}catch(e){}}
    }
    function finish(){
      localStorage.setItem(KEY,'1'); document.querySelectorAll('.ui-guide-highlight').forEach(function(e){e.classList.remove('ui-guide-highlight')}); overlay.remove();
    }
    overlay.appendChild(card); document.body.appendChild(overlay); render();
  }

  function addGuideLauncher(){
    var help=document.querySelector('[data-panel="help"]');
    if(!help || help.dataset.guideLauncher) return;
    help.dataset.guideLauncher='1';
    var btn=document.createElement('button'); btn.type='button'; btn.className='btn btn--ai btn--block'; btn.style.margin='10px 0'; btn.textContent='✨ Revoir le guide / عاود شوف الدليل';
    btn.onclick=function(){showGuide(true)};
    var view=document.querySelector('[data-view="help"] .panel__scroll'); if(view) view.insertBefore(btn,view.firstChild);
  }

  ready(function(){
    injectStyles();
    setTimeout(function(){
      addPersistentHints(); addGuideLauncher();
      // Always show the first-use guide once. If the previous broken build set a stale flag, ?guide=1 forces it.
      var force=/[?&]guide=1(?:&|$)/.test(location.search);
      showGuide(force);
    },350);
  });
  window.CigogneUIGuide={show:showGuide,reset:function(){localStorage.removeItem(KEY);showGuide(true)}};
})();
