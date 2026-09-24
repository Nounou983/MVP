/* =========================================================
   La Cigogne D'Ailleurs — Phase 12
   Realistic furniture integration / lighting-aware rendering.

   Lightweight browser-side renderer. It does not replace the AI pipeline.
   It estimates the dominant light direction from the room photograph and
   uses the existing spatial geometry to render per-object contact shadows,
   directional cast shadows and subtle ambient occlusion.
   ========================================================= */
(() => {
  'use strict';

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function sampleImage(img, maxSide = 320) {
    if (!img || !img.naturalWidth || !img.naturalHeight) return null;
    const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
    const w = Math.max(1, Math.round(img.naturalWidth * scale));
    const h = Math.max(1, Math.round(img.naturalHeight * scale));
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;
    try {
      ctx.drawImage(img, 0, 0, w, h);
      return { data: ctx.getImageData(0, 0, w, h).data, w, h };
    } catch (_) { return null; }
  }

  function estimateLight(img) {
    const s = sampleImage(img);
    if (!s) return { angle: Math.PI * .75, strength: .5, warmth: 0, confidence: 0 };
    const { data, w, h } = s;
    const bins = Array.from({ length: 8 }, () => ({ l: 0, n: 0 }));
    let totalL = 0, totalWarm = 0, n = 0;
    // Compare broad spatial cells. Bright cells are treated as probable
    // light-source regions, while the lower 25% is down-weighted.
    for (let y = 0; y < h; y += 6) {
      for (let x = 0; x < w; x += 6) {
        const p = (y * w + x) * 4;
        const r = data[p], g = data[p + 1], b = data[p + 2];
        const l = (.2126*r + .7152*g + .0722*b) / 255;
        const warm = (r - b) / 255;
        const nx = x / Math.max(1, w - 1) - .5;
        const ny = y / Math.max(1, h - 1) - .5;
        const weight = .35 + .65 * (1 - Math.max(0, ny) * .55);
        const bin = Math.floor(((Math.atan2(ny, nx) + Math.PI) / (2*Math.PI)) * 8) % 8;
        bins[bin].l += l * weight;
        bins[bin].n += weight;
        totalL += l; totalWarm += warm; n++;
      }
    }
    const avg = totalL / Math.max(1, n);
    const ranked = bins.map((b, i) => ({ i, v: b.l / Math.max(.001, b.n) }))
      .sort((a,b) => b.v - a.v);
    const best = ranked[0];
    const second = ranked[1];
    const angle = ((best.i + .5) / 8) * 2*Math.PI - Math.PI;
    const separation = clamp((best.v - second.v) * 5, 0, 1);
    const strength = clamp(.34 + avg * .55, .28, .82);
    return {
      angle,
      // Direction of the light in image coordinates. Shadows travel roughly
      // opposite this vector.
      dx: Math.cos(angle),
      dy: Math.sin(angle),
      strength,
      warmth: clamp((totalWarm / Math.max(1,n)) * 2.5, -.15, .15),
      confidence: separation,
      averageLuminance: avg
    };
  }

  function itemBounds(item, App) {
    const s = App.itemSize(item);
    const support = App.getSupportPoint(item);
    return { s, support };
  }

  function drawShadowForItem(ctx, item, App, fit, factor, light, quality = 1) {
    if (!item || item.catId === 'rug') return;
    const { s, support } = itemBounds(item, App);
    const x = (support.x - fit.x) * factor;
    const y = (support.y - fit.y) * factor;
    const strength = clamp((.12 + light.strength * .26) * quality, .08, .36);
    const angle = Math.atan2(light.dy || .2, light.dx || .2);
    const away = Math.max(5, Math.min(34, Math.max(s.w, s.h) * factor * .10));
    const sx = x - Math.cos(angle) * away;
    const sy = y - Math.sin(angle) * away * .42;
    const rx = Math.max(8, s.w * factor * .48);
    const ry = Math.max(3, s.h * factor * .12);

    // Directional shadow: broad, soft and low-opacity.
    ctx.save();
    ctx.globalCompositeOperation = 'multiply';
    ctx.translate(sx, sy);
    ctx.rotate(item.rot || 0);
    ctx.scale(1, Math.max(.10, ry / Math.max(1, rx)));
    const g = ctx.createRadialGradient(0, 0, rx*.05, 0, 0, rx);
    g.addColorStop(0, `rgba(22,18,14,${strength})`);
    g.addColorStop(.38, `rgba(22,18,14,${strength*.62})`);
    g.addColorStop(.72, `rgba(22,18,14,${strength*.20})`);
    g.addColorStop(1, 'rgba(22,18,14,0)');
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(0,0,rx,0,Math.PI*2); ctx.fill();
    ctx.restore();

    // Tight contact occlusion keeps legs/edges visually grounded.
    ctx.save();
    ctx.globalCompositeOperation = 'multiply';
    ctx.translate(x, y);
    ctx.rotate(item.rot || 0);
    ctx.scale(1, .18);
    const crx = Math.max(5, s.w * factor * .34);
    const cg = ctx.createRadialGradient(0,0,1,0,0,crx);
    cg.addColorStop(0, `rgba(18,15,12,${strength*.90})`);
    cg.addColorStop(.55, `rgba(18,15,12,${strength*.34})`);
    cg.addColorStop(1, 'rgba(18,15,12,0)');
    ctx.fillStyle = cg;
    ctx.beginPath(); ctx.arc(0,0,crx,0,Math.PI*2); ctx.fill();
    ctx.restore();
  }

  function drawIntegrationShadows(ctx, App, fit, w, h, opts = {}) {
    if (!App?.state?.items?.length || !fit) return 0;
    const light = opts.light || estimateLight(App.state.roomImage);
    const factor = w / Math.max(1, fit.w);
    const quality = opts.quality === '4k' ? 1.12 : opts.quality === '2k' ? 1 : .92;
    let count = 0;
    for (const item of App.state.items) {
      drawShadowForItem(ctx, item, App, fit, factor, light, quality);
      count++;
    }
    return count;
  }

  function integrationGrade(layer, roomCtx, w, h, light) {
    if (!layer || !roomCtx) return;
    try {
      const lc = layer.getContext('2d', { willReadFrequently: true });
      const rc = roomCtx.getImageData(0, 0, w, h).data;
      const ld = lc.getImageData(0, 0, w, h);
      const d = ld.data;
      // Very restrained luminance adaptation: enough to avoid a cut-out look,
      // but intentionally not enough to recolor the catalog asset.
      const roomY = Math.round(h * .62), band = Math.max(8, Math.round(h*.16));
      let rr=0,gg=0,bb=0,n=0;
      for (let y=Math.max(0,roomY-band); y<Math.min(h,roomY+band); y+=5) {
        for (let x=0; x<w; x+=5) {
          const p=(y*w+x)*4;
          rr+=rc[p]; gg+=rc[p+1]; bb+=rc[p+2]; n++;
        }
      }
      const roomLum=(.2126*rr+.7152*gg+.0722*bb)/Math.max(1,n*255);
      const target=clamp(.86 + roomLum*.26, .88, 1.10);
      const lightBias=clamp(1 + (light?.warmth||0)*.18, .97, 1.03);
      for(let i=0;i<d.length;i+=4){
        if(d[i+3]<8)continue;
        const y=.2126*d[i]+.7152*d[i+1]+.0722*d[i+2];
        const g=clamp(target*(.94 + .06*(y/255))*lightBias,.88,1.10);
        d[i]=clamp(d[i]*g,0,255);
        d[i+1]=clamp(d[i+1]*g,0,255);
        d[i+2]=clamp(d[i+2]*g,0,255);
      }
      lc.putImageData(ld,0,0);
    } catch (err) { console.warn('[phase12] integration grade skipped', err); }
  }

  window.RealisticRendering = {
    version: 12,
    estimateLight,
    drawIntegrationShadows,
    integrationGrade
  };
})();
