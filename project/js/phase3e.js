/* =========================================================
   Phase 3E — Photorealistic compositing / final render
   ========================================================= */
(() => {
  'use strict';
  const App = window.App;
  const P3 = window.Phase3;
  const P12 = window.RealisticRendering;
  if (!App) return;

  const stageWrap = document.querySelector('.stage-wrap');
  const inspectorBody = document.getElementById('inspectorBody');
  const state = {
    enabled: false,
    quality: '2k',
    exposure: 1.00,
    saturation: 0.96,
    contrast: 1.02,
    warmth: 0.00,
    edge: 0.65,
    shadow: 0.34,
    grain: 0.025,
    vignette: 0.06,
    preview: false,
    estimated: false
  };

  function setStatus(msg) { App.setStatus?.(msg); }
  function room() { return App.state.roomImage; }
  function threeCanvas() { return document.getElementById('threeStage'); }
  function roomFit() { return App.roomFit?.(); }
  function naturalSize() {
    const img = room();
    return img && img.naturalWidth ? {w: img.naturalWidth, h: img.naturalHeight} : App.getCanvasSize();
  }

  function estimatePhoto() {
    const img = room();
    if (!img) return;
    const w = Math.min(480, img.naturalWidth || 480);
    const h = Math.max(1, Math.round((img.naturalHeight || 320) * w / (img.naturalWidth || w)));
    const c = document.createElement('canvas'); c.width=w; c.height=h;
    const x=c.getContext('2d',{willReadFrequently:true}); x.drawImage(img,0,0,w,h);
    const d=x.getImageData(0,0,w,h).data;
    let r=0,g=0,b=0,n=0,lum=0;
    for(let i=0;i<d.length;i+=16){r+=d[i];g+=d[i+1];b+=d[i+2];lum+=.2126*d[i]+.7152*d[i+1]+.0722*d[i+2];n++;}
    if(!n)return;
    const L=lum/n/255, rb=(r/n)/(Math.max(1,b/n));
    state.exposure=Math.max(.84,Math.min(1.18,.96+(0.52-L)*.42));
    state.saturation=Math.max(.90,Math.min(1.08,.96+(rb-1)*.12));
    state.warmth=Math.max(-.12,Math.min(.12,(rb-1)*.22));
    state.estimated=true;
  }

  function drawRoom(ctx,w,h) {
    const img=room();
    if(img) ctx.drawImage(img,0,0,w,h);
    else {ctx.fillStyle='#090a0f';ctx.fillRect(0,0,w,h);}
  }

  function getForegroundLayer(w,h) {
    // Prefer the real 3D renderer when it is active.
    const hires=window.Phase3?.renderAtSize?.(w,h);
    if(hires)return hires;
    const src=threeCanvas();
    if(src && src.width && src.height){
      const c=document.createElement('canvas'); c.width=w; c.height=h;
      const x=c.getContext('2d'); x.imageSmoothingQuality='high'; x.drawImage(src,0,0,w,h); return c;
    }
    // Photo mode has no transparent 3D canvas. Build a true transparent
    // foreground from the application's catalogue sprites instead of
    // exporting only the room/background.
    const img=room(), fit=roomFit();
    const items=App?.state?.items || [];
    if(!img || !fit || !items.length) return null;
    const c=document.createElement('canvas'); c.width=w; c.height=h;
    const x=c.getContext('2d'); x.imageSmoothingQuality='high';
    const factor=w/Math.max(1,fit.w);
    for(const item of [...items].sort((a,b)=>a.z-b.z)){
      const sprite=App.spriteOf?.(item); if(!sprite || !sprite.complete || !sprite.naturalWidth) continue;
      const size=App.itemSize(item); const px=(item.x-fit.x)*factor, py=(item.y-fit.y)*factor;
      x.save(); x.translate(px,py); x.rotate(item.rot||0);
      x.drawImage(sprite,-size.w*factor/2,-size.h*factor/2,size.w*factor,size.h*factor); x.restore();
    }
    return c;
  }

  /* ------------------------------------------------------------------
     Phase 6 — accord colorimétrique.
     On mesure la dominante de la photo dans la zone où les meubles se
     posent (la moitié basse), puis on pousse le calque 3D vers cette
     dominante. Le gain est borné : au-delà, on ne corrige plus une
     lumière, on repeint le meuble.
     ------------------------------------------------------------------ */
  const MATCH_LIMIT=0.14;

  function regionStats(ctx,x0,y0,w,h,step=4){
    let r=0,g=0,b=0,n=0;
    const data=ctx.getImageData(x0,y0,Math.max(1,w),Math.max(1,h)).data;
    for(let i=0;i<data.length;i+=4*step){
      if(data[i+3]<24)continue;
      r+=data[i];g+=data[i+1];b+=data[i+2];n++;
    }
    if(!n)return null;
    return {r:r/n,g:g/n,b:b/n,n};
  }

  function matchForegroundToRoom(layer,roomCtx,w,h){
    try{
      const lower=Math.round(h*0.45);
      const roomStats=regionStats(roomCtx,0,lower,w,h-lower,6);
      const layerCtx=layer.getContext('2d',{willReadFrequently:true});
      const fgStats=regionStats(layerCtx,0,0,w,h,6);
      if(!roomStats||!fgStats||fgStats.n<64)return null;

      const clamp=v=>Math.max(1-MATCH_LIMIT,Math.min(1+MATCH_LIMIT,v));
      // Accord de teinte seulement : on normalise par la luminance pour
      // ne pas éclaircir ou assombrir les meubles au passage.
      const roomL=(roomStats.r+roomStats.g+roomStats.b)/3||1;
      const fgL=(fgStats.r+fgStats.g+fgStats.b)/3||1;
      const gain={
        r:clamp((roomStats.r/roomL)/Math.max(0.001,fgStats.r/fgL)),
        g:clamp((roomStats.g/roomL)/Math.max(0.001,fgStats.g/fgL)),
        b:clamp((roomStats.b/roomL)/Math.max(0.001,fgStats.b/fgL)),
      };
      const img=layerCtx.getImageData(0,0,w,h),d=img.data;
      for(let i=0;i<d.length;i+=4){
        if(d[i+3]===0)continue;
        d[i]=Math.max(0,Math.min(255,d[i]*gain.r));
        d[i+1]=Math.max(0,Math.min(255,d[i+1]*gain.g));
        d[i+2]=Math.max(0,Math.min(255,d[i+2]*gain.b));
      }
      layerCtx.putImageData(img,0,0);
      return gain;
    }catch(err){
      console.warn('[rendu] accord colorimétrique impossible',err);
      return null;
    }
  }

  /* Ombres de contact par meuble : une ellipse au point d'appui, à
     l'échelle de l'empreinte et de la perspective, au lieu d'un unique
     dégradé centré qui ne correspondait à rien de précis. */
  function addContactShadows(ctx,w,h){
    const App2=window.App;
    const fit=roomFit();
    if(!App2||!fit||!App2.state.items.length)return 0;
    const factor=w/Math.max(1,fit.w);
    let drawn=0;
    ctx.save();
    ctx.globalCompositeOperation='multiply';
    for(const item of App2.state.items){
      if(item.catId==='rug')continue;
      const size=App2.itemSize(item);
      const support=App2.getSupportPoint(item);
      const cx=(support.x-fit.x)*factor;
      const cy=(support.y-fit.y)*factor;
      const rx=Math.max(4,size.w*factor*0.52);
      const ry=Math.max(2,size.h*factor*0.17);
      const grad=ctx.createRadialGradient(cx,cy,Math.max(1,rx*0.05),cx,cy,rx);
      const strength=Math.max(0,Math.min(0.5,state.shadow??0.3));
      grad.addColorStop(0,`rgba(12,10,8,${0.42*strength*2})`);
      grad.addColorStop(0.55,`rgba(12,10,8,${0.16*strength*2})`);
      grad.addColorStop(1,'rgba(12,10,8,0)');
      ctx.save();
      ctx.translate(cx,cy);
      ctx.scale(1,Math.max(0.12,ry/rx));
      ctx.translate(-cx,-cy);
      ctx.fillStyle=grad;
      ctx.beginPath();
      ctx.arc(cx,cy,rx,0,Math.PI*2);
      ctx.fill();
      ctx.restore();
      drawn++;
    }
    ctx.restore();
    return drawn;
  }

  function featherAlpha(canvas, amount) {
    if(amount<=0) return canvas;
    const w=canvas.width,h=canvas.height;
    const src=canvas.getContext('2d').getImageData(0,0,w,h);
    const a=new Uint8ClampedArray(w*h);
    for(let i=0,p=0;i<src.data.length;i+=4,p++)a[p]=src.data[i+3];
    const radius=Math.max(1,Math.round(amount*2.5));
    const out=new Uint8ClampedArray(a);
    for(let y=0;y<h;y++){
      for(let x=0;x<w;x++){
        let sum=0,cnt=0;
        for(let yy=Math.max(0,y-radius);yy<=Math.min(h-1,y+radius);yy+=Math.max(1,Math.floor(radius/2))){
          for(let xx=Math.max(0,x-radius);xx<=Math.min(w-1,x+radius);xx+=Math.max(1,Math.floor(radius/2))){
            sum+=a[yy*w+xx];cnt++;
          }
        }
        out[y*w+x]=sum/Math.max(1,cnt);
      }
    }
    const d=src.data;
    for(let p=0,i=0;p<out.length;p++,i+=4)d[i+3]=Math.max(0,Math.min(255,out[p]));
    canvas.getContext('2d').putImageData(src,0,0);
    return canvas;
  }

  function applyForegroundGrade(layer, exposure, sat, contrast, warmth) {
    const ctx=layer.getContext('2d');
    const w=layer.width,h=layer.height;
    const img=ctx.getImageData(0,0,w,h), d=img.data;
    const c=contrast, e=exposure;
    for(let i=0;i<d.length;i+=4){
      if(d[i+3]<2)continue;
      let r=d[i]/255,g=d[i+1]/255,b=d[i+2]/255;
      r=Math.max(0,Math.min(1,((r-.5)*c+.5)*e));
      g=Math.max(0,Math.min(1,((g-.5)*c+.5)*e));
      b=Math.max(0,Math.min(1,((b-.5)*c+.5)*e));
      const y=.2126*r+.7152*g+.0722*b;
      r=y+(r-y)*sat;g=y+(g-y)*sat;b=y+(b-y)*sat;
      if(warmth>0){r+=warmth*.06;b-=warmth*.035;}else{b+=(-warmth)*.045;r-=(-warmth)*.025;}
      d[i]=Math.max(0,Math.min(255,r*255));d[i+1]=Math.max(0,Math.min(255,g*255));d[i+2]=Math.max(0,Math.min(255,b*255));
    }
    ctx.putImageData(img,0,0);
  }

  function addSoftContact(ctx,w,h) {
    const fit=roomFit();
    if(!fit)return;
    const f=fit;
    const g=ctx.createRadialGradient(w*.5,h*.88,1,w*.5,h*.88,Math.max(w,h)*.30);
    g.addColorStop(0,'rgba(0,0,0,0.035)');g.addColorStop(1,'rgba(0,0,0,0)');
    ctx.save();ctx.globalCompositeOperation='multiply';ctx.fillStyle=g;ctx.fillRect(f.x,f.y,f.w,f.h);ctx.restore();
  }

  function gradeRoom(ctx,w,h) {
    if(Math.abs(state.exposure-1)<.001 && Math.abs(state.saturation-1)<.001 && Math.abs(state.contrast-1)<.001 && Math.abs(state.warmth)<.001)return;
    const img=ctx.getImageData(0,0,w,h),d=img.data;
    for(let i=0;i<d.length;i+=4){
      let r=d[i]/255,g=d[i+1]/255,b=d[i+2]/255;
      r=Math.max(0,Math.min(1,((r-.5)*state.contrast+.5)*state.exposure));
      g=Math.max(0,Math.min(1,((g-.5)*state.contrast+.5)*state.exposure));
      b=Math.max(0,Math.min(1,((b-.5)*state.contrast+.5)*state.exposure));
      const y=.2126*r+.7152*g+.0722*b;
      r=y+(r-y)*state.saturation;g=y+(g-y)*state.saturation;b=y+(b-y)*state.saturation;
      if(state.warmth>0){r+=state.warmth*.025;b-=state.warmth*.015;}else{b+=(-state.warmth)*.02;r-=(-state.warmth)*.012;}
      d[i]=Math.max(0,Math.min(255,r*255));d[i+1]=Math.max(0,Math.min(255,g*255));d[i+2]=Math.max(0,Math.min(255,b*255));
    }
    ctx.putImageData(img,0,0);
  }

  function finish(ctx,w,h) {
    if(state.grain>0){
      const c=document.createElement('canvas');c.width=Math.min(900,w);c.height=Math.min(900,h);
      const x=c.getContext('2d'),im=x.createImageData(c.width,c.height),d=im.data;
      for(let i=0;i<d.length;i+=4){const n=(Math.random()-.5)*255*state.grain;d[i]=128+n;d[i+1]=128+n;d[i+2]=128+n;d[i+3]=255;}
      x.putImageData(im,0,0);ctx.save();ctx.globalAlpha=.045;ctx.globalCompositeOperation='soft-light';ctx.drawImage(c,0,0,w,h);ctx.restore();
    }
    if(state.vignette>0){
      const g=ctx.createRadialGradient(w*.5,h*.5,Math.min(w,h)*.28,w*.5,h*.5,Math.max(w,h)*.72);
      g.addColorStop(.55,'rgba(0,0,0,0)');g.addColorStop(1,`rgba(0,0,0,${state.vignette})`);
      ctx.save();ctx.fillStyle=g;ctx.fillRect(0,0,w,h);ctx.restore();
    }
  }

  function renderFinal(showStatus=true) {
    if(!room()){setStatus('Importez une photo de pièce avant le rendu final');return null;}
    const native=naturalSize();
    const max=state.quality==='4k'?4096:state.quality==='native'?Math.max(native.w,native.h):2560;
    const scale=Math.min(1,max/Math.max(native.w,native.h));
    const w=Math.max(1,Math.round(native.w*scale)),h=Math.max(1,Math.round(native.h*scale));
    const out=document.createElement('canvas');out.width=w;out.height=h;
    const ctx=out.getContext('2d');
    drawRoom(ctx,w,h);
    gradeRoom(ctx,w,h);
    let fg=getForegroundLayer(w,h);
    if(fg){
      // Phase 12: lighting-aware integration. The legacy contact shadow
      // remains as a safe fallback when the new renderer is unavailable.
      if(state.shadow>0){
        const fit=roomFit();
        const light=P12?.estimateLight?.(room());
        if(P12?.drawIntegrationShadows && fit) P12.drawIntegrationShadows(ctx,App,fit,w,h,{light,quality:state.quality});
        else if(!addContactShadows(ctx,w,h)) addSoftContact(ctx,w,h);
      }
      matchForegroundToRoom(fg,ctx,w,h);
      if(P12?.integrationGrade) P12.integrationGrade(fg,ctx,w,h,P12.estimateLight?.(room()));
      featherAlpha(fg,state.edge);
      applyForegroundGrade(fg,state.exposure,state.saturation,state.contrast,state.warmth);
      ctx.save();
      ctx.globalAlpha=1;
      ctx.drawImage(fg,0,0,w,h);
      ctx.restore();
    }else if(state.shadow>0){
      addSoftContact(ctx,w,h);
    }
    finish(ctx,w,h);
    if(showStatus)setStatus(`Rendu HD prêt ✔`);
    return out;
  }

  function preview() {
    const out=renderFinal(false); if(!out)return;
    const existing=document.getElementById('p3ePreviewOverlay');
    if(existing)existing.remove();
    
    const overlay=document.createElement('div');
    overlay.id='p3ePreviewOverlay';
    overlay.style.cssText='position:fixed;inset:0;background:rgba(9, 10, 15, 0.95);backdrop-filter:blur(10px);z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;gap:20px;';
    
    const img=document.createElement('img');
    img.src=out.toDataURL('image/jpeg',.94);
    img.style.cssText='max-width:90vw;max-height:80vh;object-fit:contain;box-shadow:0 24px 48px rgba(0,0,0,0.5);border:1px solid rgba(255,255,255,0.1);border-radius:12px;';
    
    const closeBtn=document.createElement('button');
    closeBtn.textContent="Fermer l'aperçu";
    closeBtn.style.cssText="padding:10px 24px;border-radius:8px;border:none;background:#e1ad4f;color:#000;font-weight:600;cursor:pointer;";
    
    overlay.appendChild(img);
    overlay.appendChild(closeBtn);
    overlay.onclick=(e)=>{if(e.target === overlay || e.target === closeBtn) overlay.remove()};
    document.body.appendChild(overlay);
  }

  function download() {
    const out=renderFinal(true);if(!out)return;
    const a=document.createElement('a');a.download=`Cigogne-Design-${state.quality}.png`;a.href=out.toDataURL('image/png');a.click();
  }

  function renderInspector() {
    const old=document.querySelector('.phase3e-card');if(old)old.remove();
    const html=`
    <div class="control-section phase3e-card" style="margin-top:20px; border-top:1px solid var(--border-light); padding-top:16px;">
      <div class="section-label" style="color:var(--success); display:flex; justify-content:space-between;">
        <span>📸 Rendu Final</span>
        <span style="color:var(--text-muted); font-weight:normal;">Post-production</span>
      </div>
      
      <div style="display:flex; gap:8px; margin-bottom:16px;">
        <button id="p3ePreview" class="btn outline" style="flex:1;">👁 Aperçu Rapide</button>
        <button id="p3eExport" class="btn primary" style="flex:1;">⬇ Exporter Image</button>
      </div>

      <div class="field" style="margin-bottom:16px;">
        <label>Qualité du Rendu Final</label>
        <select id="p3eQuality" style="width:100%; height:36px; background:var(--surface-2); color:var(--text-main); border:1px solid var(--border-light); border-radius:var(--radius-sm); padding:0 12px; outline:none; font-family:inherit;">
          <option value="2k" ${state.quality==='2k'?'selected':''}>Standard (2K)</option>
          <option value="native" ${state.quality==='native'?'selected':''}>Native (Photo originale)</option>
          <option value="4k" ${state.quality==='4k'?'selected':''}>Ultra Haute Définition (4K)</option>
        </select>
      </div>

      <div class="phase12-integration" style="margin:0 0 14px;padding:12px;border:1px solid var(--border-light);border-radius:12px;background:var(--surface-2);">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
          <span style="font-weight:700;color:var(--text-main);">✨ Intégration réaliste</span>
          <span style="font-size:11px;color:var(--success);font-weight:700;">Lumière auto</span>
        </div>
        <div style="font-size:11px;line-height:1.45;color:var(--text-muted);">Ombres directionnelles, contact au sol et adaptation lumineuse selon la photo.</div>
      </div>

      <details class="advanced-details">
        <summary>Retouche Colorimétrique</summary>
        <div class="advanced-copy">
          <div style="font-weight:600; color:var(--text-main); margin:12px 0 6px;">Exposition Globale</div>
          <div class="field"><label>Exposition <span id="p3eExpVal">${state.exposure.toFixed(2)}</span></label><input class="big-range" id="p3eExp" type="range" min=".82" max="1.20" step=".01" value="${state.exposure}"></div>
          <div class="field"><label>Saturation <span id="p3eSatVal">${state.saturation.toFixed(2)}</span></label><input class="big-range" id="p3eSat" type="range" min=".85" max="1.12" step=".01" value="${state.saturation}"></div>
          <div class="field"><label>Contraste <span id="p3eConVal">${state.contrast.toFixed(2)}</span></label><input class="big-range" id="p3eCon" type="range" min=".88" max="1.16" step=".01" value="${state.contrast}"></div>
          
          <div style="font-weight:600; color:var(--text-main); margin:16px 0 6px;">Effets Cinématiques</div>
          <div class="field"><label>Bords doux <span id="p3eEdgeVal">${Math.round(state.edge*100)}%</span></label><input class="big-range" id="p3eEdge" type="range" min="0" max="1" step=".05" value="${state.edge}"></div>
          <div class="field"><label>Grain de film <span id="p3eGrainVal">${Math.round(state.grain*100)}%</span></label><input class="big-range" id="p3eGrain" type="range" min="0" max=".08" step=".005" value="${state.grain}"></div>
          <div class="field"><label>Vignettage <span id="p3eVigVal">${Math.round(state.vignette*100)}%</span></label><input class="big-range" id="p3eVig" type="range" min="0" max=".16" step=".01" value="${state.vignette}"></div>
        </div>
      </details>
    </div>`;
    
    inspectorBody.insertAdjacentHTML('beforeend',html);
    const q=id=>document.getElementById(id);
    
    const bind=(id,key,fmt)=>q(id).oninput=e=>{state[key]=+e.target.value;q(id+'Val').textContent=fmt(state[key]);};
    bind('p3eExp','exposure',v=>v.toFixed(2));
    bind('p3eSat','saturation',v=>v.toFixed(2));
    bind('p3eCon','contrast',v=>v.toFixed(2));
    bind('p3eEdge','edge',v=>Math.round(v*100)+'%');
    bind('p3eGrain','grain',v=>Math.round(v*100)+'%');
    bind('p3eVig','vignette',v=>Math.round(v*100)+'%');
    
    q('p3eQuality').onchange=e=>state.quality=e.target.value;
    q('p3ePreview').onclick=preview;
    q('p3eExport').onclick=download;
  }

  function init() {
    if(room())estimatePhoto();
    const observer=new MutationObserver(()=>{if(room() && !document.querySelector('.phase3e-card'))renderInspector();});
    observer.observe(inspectorBody,{childList:true});
    if(room())renderInspector();
    window.Phase3E={state,render:renderFinal,preview,export:download,refresh:renderInspector,matchForegroundToRoom,addContactShadows,regionStats};
  }
  init();
})();