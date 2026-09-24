/* =========================================================
   Phase 8.1 — AI Remove Studio
   Persistent multi-object selection + contextual detection UI.
   The important invariant: selecting another object NEVER replaces
   the previous selection. One confirmed operation sends the union mask.
   ========================================================= */
(() => {
  "use strict";

  const { state, canvas, setStatus, draw, roomFit, hitItem } = window.App;
  const API = window.AI_API;
  const eraserBtn = document.getElementById("eraserBtn");
  const undoBtn = document.getElementById("undoAiBtn");

  let selections = [];
  let busy = false;
  let history = [];
  let autoGroup = true;

  const overlay = document.createElement("canvas");
  overlay.id = "aiMaskOverlay";
  overlay.style.cssText = "position:absolute;inset:0;width:100%;height:100%;z-index:7;pointer-events:none;display:none";
  canvas.parentElement.appendChild(overlay);
  const octx = overlay.getContext("2d");

  function ensureStudio() {
    let panel = document.getElementById("aiSelectionStudio");
    if (panel) return panel;
    panel = document.createElement("aside");
    panel.id = "aiSelectionStudio";
    panel.className = "ai-selection-studio";
    panel.innerHTML = `
      <div class="ai-studio__head">
        <div>
          <span class="ai-studio__eyebrow">RETOUCHE IA</span>
          <h3>Objets détectés <b id="aiSelectionCount">0</b></h3>
        </div>
        <button id="aiStudioClose" class="ai-studio__icon" type="button" aria-label="Fermer">×</button>
      </div>
      <div class="ai-studio__auto">
        <div><strong>Détection automatique</strong><small>L’IA récupère aussi les parties fines manquantes (pieds, accoudoirs, bords).</small></div>
        <button id="aiAutoToggle" class="ai-switch is-on" type="button" aria-pressed="true"><i></i></button>
      </div>
      <div id="aiDetectedList" class="ai-detected-list"></div>
      <div class="ai-studio__preview">
        <div class="ai-studio__preview-head"><strong>Prévisualisation</strong><span id="aiPreviewMeta">Aucun objet</span></div>
        <div class="ai-preview-box"><canvas id="aiPreviewCanvas"></canvas><span>Zone à reconstruire</span></div>
      </div>
      <div class="ai-studio__tip"><b>Détection renforcée</b><span>Après chaque clic, l’IA vérifie les contours et tente de récupérer les parties fines du même meuble. Les autres meubles restent séparés.</span></div>
      <div class="ai-studio__actions">
        <button id="aiStudioReset" class="btn btn--quiet" type="button">Réinitialiser</button>
        <button id="aiStudioConfirm" class="btn btn--ai" type="button" disabled>✨ Supprimer <span id="aiConfirmCount">0</span></button>
      </div>`;
    canvas.parentElement.appendChild(panel);

    panel.querySelector("#aiStudioClose").onclick = () => cancelSelection();
    panel.querySelector("#aiStudioReset").onclick = () => clearSelection(true);
    panel.querySelector("#aiStudioConfirm").onclick = () => confirmRemoval();
    panel.querySelector("#aiAutoToggle").onclick = () => {
      autoGroup = !autoGroup;
      const b = panel.querySelector("#aiAutoToggle");
      b.classList.toggle("is-on", autoGroup);
      b.setAttribute("aria-pressed", String(autoGroup));
      renderStudio();
    };
    return panel;
  }

  function syncOverlaySize() {
    const r = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    overlay.width = Math.max(1, Math.round(r.width * dpr));
    overlay.height = Math.max(1, Math.round(r.height * dpr));
    octx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawOverlay();
  }

  function imageFromDataUrl(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = src;
    });
  }

  function maskToCanvas(img) {
    const w = img.naturalWidth || img.width, h = img.naturalHeight || img.height;
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    c.getContext("2d").drawImage(img, 0, 0);
    return c;
  }

  function unionMaskCanvas() {
    if (!selections.length || !state.roomImage) return null;
    const W = state.roomImage.naturalWidth, H = state.roomImage.naturalHeight;
    const out = document.createElement("canvas"); out.width = W; out.height = H;
    const ctx = out.getContext("2d");
    ctx.fillStyle = "black"; ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = "lighter";
    for (const item of selections) {
      if (!item.enabled) continue;
      const c = maskToCanvas(item.mask);
      ctx.drawImage(c, 0, 0, W, H);
    }
    // Convert additive white mask back to a binary luminance mask.
    const data = ctx.getImageData(0, 0, W, H);
    for (let i=0;i<data.data.length;i+=4) {
      const v = data.data[i] > 0 ? 255 : 0;
      data.data[i]=data.data[i+1]=data.data[i+2]=v; data.data[i+3]=255;
    }
    ctx.putImageData(data,0,0);
    return out;
  }

  function selectionKey(label, bbox) {
    return `${label}|${bbox?.x}|${bbox?.y}|${bbox?.width}|${bbox?.height}`;
  }

  function addSelection(maskImg, meta, source="SAM") {
    const key = selectionKey(meta.label, meta.bbox);
    if (selections.some(s => s.key === key)) return false;
    selections.push({
      key, mask: maskImg, label: meta.label || "meuble", bbox: meta.bbox || null,
      confidence: Number(meta.confidence || meta.score || 0.8), source,
      enabled: true,
    });
    return true;
  }

  async function addRelated(related) {
    // Related components are suggestions only. They are never activated by
    // the AI: the user must explicitly opt each one into the union mask.
    if (!autoGroup || !Array.isArray(related)) return;
    for (const r of related.slice(0, 6)) {
      try {
        const img = await imageFromDataUrl(r.mask);
        addSelection(img, { label:r.label, bbox:r.bbox, score:r.score }, "SegFormer");
        const key = selectionKey(r.label, r.bbox);
        const suggestion = selections.find(s => s.key === key);
        if (suggestion) suggestion.enabled = false;
      } catch (_) {}
    }
  }

  function drawOverlay() {
    const w = canvas.parentElement.clientWidth, h = canvas.parentElement.clientHeight;
    octx.clearRect(0,0,w,h);
    if (!selections.length || !state.roomImage) { overlay.style.display="none"; return; }
    const fit=roomFit(); if(!fit) return;
    overlay.style.display="block";
    const W=state.roomImage.naturalWidth,H=state.roomImage.naturalHeight;
    const colors=["#6B4DF6","#8B5CF6","#A78BFA","#7C3AED"];
    selections.forEach((sel, idx)=>{
      if(!sel.enabled) return;
      const c=maskToCanvas(sel.mask); const mc=c.getContext("2d",{willReadFrequently:true});
      const md=mc.getImageData(0,0,c.width,c.height).data;
      const fill=document.createElement("canvas"); fill.width=c.width; fill.height=c.height;
      const fc=fill.getContext("2d"); const fd=fc.createImageData(c.width,c.height);
      const col=colors[idx%colors.length];
      const rgb=col.match(/\w\w/g).map(x=>parseInt(x,16));
      for(let i=0;i<md.length;i+=4){
        const v=md[i]; fd.data[i]=rgb[0];fd.data[i+1]=rgb[1];fd.data[i+2]=rgb[2];fd.data[i+3]=v>127?78:0;
      }
      fc.putImageData(fd,0,0); octx.drawImage(fill,fit.x,fit.y,fit.w,fit.h);
      // crisp contour at image scale, sampled sparsely for performance
      const sx=Math.max(1,Math.round(c.width/700));
      octx.save(); octx.strokeStyle=col; octx.lineWidth=2; octx.setLineDash([5,4]); octx.beginPath();
      for(let y=sx;y<c.height-sx;y+=sx){for(let x=sx;x<c.width-sx;x+=sx){
        const i=(y*c.width+x)*4;if(md[i]<127)continue;
        if(md[((y-sx)*c.width+x)*4]<127||md[((y+sx)*c.width+x)*4]<127||md[(y*c.width+x-sx)*4]<127||md[(y*c.width+x+sx)*4]<127){
          octx.rect(fit.x+x/c.width*fit.w,fit.y+y/c.height*fit.h,Math.max(1,sx/c.width*fit.w),Math.max(1,sx/c.height*fit.h));
        }
      }} octx.stroke();octx.restore();
    });
  }

  function renderPreview() {
    const p=document.getElementById("aiPreviewCanvas"); if(!p||!state.roomImage)return;
    const maxW=320,maxH=180, scale=Math.min(maxW/state.roomImage.naturalWidth,maxH/state.roomImage.naturalHeight);
    p.width=Math.max(1,Math.round(state.roomImage.naturalWidth*scale)); p.height=Math.max(1,Math.round(state.roomImage.naturalHeight*scale));
    const ctx=p.getContext("2d", {willReadFrequently:true});ctx.drawImage(state.roomImage,0,0,p.width,p.height);
    const u=unionMaskCanvas(); if(!u)return;
    ctx.fillStyle="rgba(107,77,246,.34)"; const d=u.getContext("2d").getImageData(0,0,u.width,u.height).data;
    const ov=ctx.getImageData(0,0,p.width,p.height); const sx=u.width/p.width,sy=u.height/p.height;
    for(let y=0;y<p.height;y++)for(let x=0;x<p.width;x++){const mx=Math.min(u.width-1,Math.floor(x*sx)),my=Math.min(u.height-1,Math.floor(y*sy));if(d[(my*u.width+mx)*4]>127){const i=(y*p.width+x)*4;ov.data[i]=Math.min(255,ov.data[i]*.45+120);ov.data[i+1]=Math.min(255,ov.data[i+1]*.45+90);ov.data[i+2]=Math.min(255,ov.data[i+2]*.45+230);}}
    ctx.putImageData(ov,0,0);
  }

  function renderStudio() {
    const panel=ensureStudio(); const active=selections.filter(s=>s.enabled).length;
    panel.querySelector("#aiSelectionCount").textContent=active;
    panel.querySelector("#aiConfirmCount").textContent=active;
    panel.querySelector("#aiStudioConfirm").disabled=active===0||busy;
    panel.querySelector("#aiPreviewMeta").textContent=active?`${active} zone${active>1?'s':''} sélectionnée${active>1?'s':''}`:"Aucun objet";
    const list=panel.querySelector("#aiDetectedList"); list.innerHTML="";
    selections.forEach((s,i)=>{
      const row=document.createElement("div");row.className="ai-detected-item"+(s.enabled?" is-selected":"");
      row.innerHTML=`<button class="ai-check" type="button" aria-pressed="${s.enabled}">${s.enabled?'✓':''}</button><span class="ai-detected-name">${escapeHtml(s.label)}</span><small>${Math.round(Math.max(0,Math.min(1,s.confidence))*100)}%</small><button class="ai-remove-one" type="button" aria-label="Retirer ${escapeHtml(s.label)}">×</button>`;
      row.querySelector(".ai-check").onclick=()=>{s.enabled=!s.enabled;renderStudio();drawOverlay();};
      row.querySelector(".ai-remove-one").onclick=()=>{selections.splice(i,1);renderStudio();drawOverlay();};
      list.appendChild(row);
    });
    renderPreview(); drawOverlay();
    document.body.classList.toggle("is-erasing", selections.length>0 || state.eraserOn);
  }

  function escapeHtml(v){return String(v||"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[m]));}

  function pushHistory() {
    if(!state.roomImage)return;
    const c=document.createElement("canvas");c.width=state.roomImage.naturalWidth;c.height=state.roomImage.naturalHeight;c.getContext("2d").drawImage(state.roomImage,0,0);
    history.push(c.toDataURL("image/jpeg",.94)); if(history.length>12)history.shift(); undoBtn.disabled=false;
  }

  function loadRoom(src,statusText,expectedW=null,expectedH=null){return new Promise(resolve=>{
    const img=new Image();img.onload=()=>{
      if(expectedW&&expectedH&&(img.naturalWidth!==expectedW||img.naturalHeight!==expectedH)){setStatus(`Résultat IA rejeté : ${img.naturalWidth}×${img.naturalHeight} au lieu de ${expectedW}×${expectedH}.`);resolve(false);return;}
      if(state.roomObjectUrl)URL.revokeObjectURL(state.roomObjectUrl);state.roomObjectUrl=null;state.roomImage=img;state.roomFileName="room-edited.jpg";
      state.analysis=null;state.floorMaskImg=null;state.floorMaskPx=null;state.depthImg=null;selectedMaskCleanup();draw();window.Phase3?.sync?.();setStatus(statusText);resolve(true);
    };img.onerror=()=>{setStatus("Impossible de restaurer l'image");resolve(false)};img.src=src;
  });}

  function selectedMaskCleanup(){state.aiMaskImg=null;}

  function clearSelection(exitMode=false){
    selections=[]; renderStudio();
    if(exitMode||state.eraserOn){state.eraserOn=false;canvas.style.cursor="default";threePointer(true);}
    eraserBtn.innerHTML="✨ Supprimer IA";eraserBtn.classList.remove("active");
    const p=document.getElementById("aiSelectionStudio");if(p)p.classList.remove("is-visible");
    drawOverlay();
  }
  function cancelSelection(){clearSelection(true);setStatus("Sélection IA annulée");}
  function threePointer(on){const s=document.getElementById("threeStage");if(s)s.style.pointerEvents=on?"auto":"none";}

  async function confirmRemoval(){
    if(busy)return; const active=selections.filter(s=>s.enabled);if(!active.length)return;
    busy=true;eraserBtn.disabled=true;undoBtn.disabled=true;
    try{
      pushHistory();
      setStatus(`Reconstruction IA de ${active.length} objet${active.length>1?'s':''}…`);
      window.CigogneUI?.showAI?.(`L’IA supprime ${active.length} objet${active.length>1?'s':''}…`);
      const imageBlob=await roomBlob();const union=unionMaskCanvas();
      const maskBlob=await new Promise((resolve,reject)=>union.toBlob(b=>b?resolve(b):reject(new Error("Masque invalide")),"image/png"));
      const fd=new FormData();fd.append("file",imageBlob,"room.jpg");fd.append("mask",maskBlob,"mask.png");
      const selectionKeys=active.map(s=>s.key);
      console.debug("[Phase9.7] submitted selection keys", selectionKeys);
      fd.append("selection_keys", JSON.stringify(selectionKeys));
      const resp=await fetch(`${API}/inpaint`,{method:"POST",body:fd});
      if(!resp.ok){
        const e=await resp.json().catch(()=>({}));
        const err=new Error(
          resp.status===422
            ? (e.unconfirmed
                ? "Résultat IA non confirmé — aucune reconstruction suffisamment fiable n’a été obtenue."
                : "Aucun candidat n’a satisfait le contrôle qualité. Essayez un autre objet ou une photo plus nette.")
            : (e.detail||`HTTP ${resp.status}`)
        );
        err.status=resp.status; err.payload=e;
        throw err;
      }
      const data=await resp.json();
      const ok=await loadRoom(data.image,`Suppression réussie ✔ — ${active.length} objet${active.length>1?'s':''}`,state.roomImage.naturalWidth,state.roomImage.naturalHeight);
      if(!ok)throw new Error("Résultat IA rejeté");
      clearSelection(true);window.CigogneUI?.hideAI?.();
    }catch(err){
      console.error("AI inpainting failed", err);
      if(history.length)history.pop();
      if(err?.status===422){
        setStatus(err?.payload?.unconfirmed
          ? "Résultat IA non confirmé — la reconstruction n’est pas assez fiable pour être appliquée."
          : "Aucun candidat n’a satisfait le contrôle qualité. Essayez un autre objet ou une photo plus nette.");
      }else{
        setStatus(`Échec de l'inpainting : ${err.message}`);
      }
      window.CigogneUI?.hideAI?.();
    }
    finally{busy=false;eraserBtn.disabled=false;undoBtn.disabled=history.length===0;}
  }

  async function roomBlob(){
    if(!state.roomImage)throw new Error("Aucune pièce chargée");const tmp=document.createElement("canvas");tmp.width=state.roomImage.naturalWidth;tmp.height=state.roomImage.naturalHeight;tmp.getContext("2d").drawImage(state.roomImage,0,0);
    return new Promise((resolve,reject)=>tmp.toBlob(b=>b?resolve(b):reject(new Error("Conversion image impossible")),"image/jpeg",.94));
  }

  eraserBtn.addEventListener("click",()=>{
    if(busy)return;
    if(selections.some(s=>s.enabled)){confirmRemoval();return;}
    state.eraserOn=!state.eraserOn;canvas.style.cursor=state.eraserOn?"crosshair":"default";threePointer(!state.eraserOn);
    eraserBtn.innerHTML=state.eraserOn?"<span style='color:#ef4444'>Ciblez les objets à effacer</span>":"✨ Supprimer IA";eraserBtn.classList.toggle("active",state.eraserOn);
    const panel=ensureStudio();panel.classList.toggle("is-visible",state.eraserOn);renderStudio();
    setStatus(state.eraserOn?"Sélection IA — cliquez sur un ou plusieurs objets réels de la photo":"Édition IA désactivée");
  });

  canvas.addEventListener("click",async e=>{
    if(!state.eraserOn||busy||!state.roomImage)return;
    if(hitItem({x:e.clientX-canvas.getBoundingClientRect().left,y:e.clientY-canvas.getBoundingClientRect().top}))return;
    const f=roomFit();if(!f)return;const r=canvas.getBoundingClientRect();const ix=Math.round(((e.clientX-r.left-f.x)/f.w)*(state.roomImage.naturalWidth-1));const iy=Math.round(((e.clientY-r.top-f.y)/f.h)*(state.roomImage.naturalHeight-1));
    if(ix<0||iy<0||ix>=state.roomImage.naturalWidth||iy>=state.roomImage.naturalHeight)return;
    busy=true;eraserBtn.disabled=true;setStatus("Segmentation IA en cours…");window.CigogneUI?.showAI?.("L’IA identifie l’objet…");
    try{
      const blob=await roomBlob();const fd=new FormData();fd.append("file",blob,"room.jpg");fd.append("x",String(ix));fd.append("y",String(iy));
      const resp=await fetch(`${API}/select-mask`,{method:"POST",body:fd});if(!resp.ok){const e=await resp.json().catch(()=>({}));throw new Error(e.detail||`HTTP ${resp.status}`)}
      const data=await resp.json();const img=await imageFromDataUrl(data.mask);
      addSelection(img,{label:data.label,bbox:data.bbox,confidence:data.confidence},data.method||"SAM");
      await addRelated(data.related);
      ensureStudio().classList.add("is-visible");renderStudio();
      eraserBtn.innerHTML=`✨ Confirmer suppression (${selections.filter(s=>s.enabled).length})`;
      const methodHint = data.method ? ` · ${data.method}` : "";
      setStatus(`Sélection IA ✔ — ${selections.filter(s=>s.enabled).length} objet${selections.filter(s=>s.enabled).length>1?'s':''} prêt${selections.filter(s=>s.enabled).length>1?'s':''}${methodHint}. Cliquez sur d’autres objets ou confirmez.`);
    }catch(err){console.error(err);setStatus(`Sélection IA impossible : ${err.message}`);window.CigogneUI?.hideAI?.();}
    finally{busy=false;eraserBtn.disabled=false;window.CigogneUI?.hideAI?.();renderStudio();}
  });

  undoBtn.addEventListener("click",()=>{if(!history.length||busy)return;const src=history.pop();loadRoom(src,"Dernière modification annulée ✔");undoBtn.disabled=history.length===0;});
  window.addEventListener("keydown",e=>{if(e.key==="Escape"&&(state.eraserOn||selections.length)){e.preventDefault();cancelSelection();}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="z"&&history.length){e.preventDefault();undoBtn.click();}});
  window.addEventListener("resize",syncOverlaySize);
  ensureStudio();syncOverlaySize();renderStudio();
})();
