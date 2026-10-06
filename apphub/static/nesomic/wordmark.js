/*
 * NESOMIC animated wordmark (from NESOMIC.html).
 *
 * Plain script, no build step: index.html loads it for the pre-bootstrap splash screen and the
 * Angular <app-nesomic-wordmark> component reuses the same global, so there is a single copy of
 * the drawing code.
 *
 *   const handle = NesomicWordmark.mount(container, {intro: true, mono: false, speed: 1});
 *   handle.replay();    // restart the intro
 *   handle.destroy();   // stop the animation loop and remove the SVG
 *
 * Options
 *   intro  draw the letters and wind the helix in (true), or show it finished at once (false)
 *   mono   paint everything with currentColor (e.g. white on the header) instead of logo colours
 *   speed  multiplier for the idle motion (helix twist and globe spin); 1 = as in NESOMIC.html
 *   fps    cap for the frame rate once the intro is over (the idle motion is slow, so a low cap
 *          is invisible and saves CPU on pages that keep it running all the time)
 */
(function (global) {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const D = Math.PI / 180;

  const CSS = `
  .nsw { --nsw-ink: #006e5a; --nsw-blue: #0078b8; --nsw-red: #e30613; --nsw-black: #000000;
         width: 100%; height: 100%; overflow: visible; display: block; }
  .nsw.nsw-mono { --nsw-ink: currentColor; --nsw-blue: currentColor; --nsw-red: currentColor; --nsw-black: currentColor; }
  .nsw .letter { fill: none; stroke: var(--nsw-ink); stroke-width: 11; stroke-linecap: round;
                 stroke-linejoin: round; stroke-dasharray: 1; stroke-dashoffset: 0; }
  .nsw.nsw-intro .letter { stroke-dashoffset: 1; }
  .nsw.nsw-play .letter { animation: nsw-draw .75s cubic-bezier(.65,0,.35,1) forwards; animation-delay: var(--d); }
  @keyframes nsw-draw { from { stroke-dashoffset: 1; } to { stroke-dashoffset: 0; } }
  .nsw .strand { fill: none; stroke-width: 6.5; stroke-linecap: round; stroke-linejoin: round; }
  .nsw .strand.a { stroke: var(--nsw-blue); }
  .nsw .strand.b { stroke: var(--nsw-ink); }
  .nsw .strand.back { opacity: .45; }
  .nsw.nsw-mono .strand.b { opacity: .8; }
  .nsw.nsw-mono .strand.back { opacity: .35; }
  .nsw .rung { stroke-width: 5; stroke-linecap: round; }
  .nsw .ocean { fill: var(--nsw-blue); fill-opacity: .16; }
  .nsw .land path { fill: var(--nsw-ink); }
  .nsw.nsw-mono .land path { fill-opacity: .85; }
  .nsw .globe { transform-box: fill-box; transform-origin: center; }
  .nsw.nsw-intro .globe { opacity: 0; transform: scale(.55); }
  .nsw.nsw-play .globe { animation: nsw-globe-in 1s cubic-bezier(.2,.8,.2,1) 1.3s forwards; }
  @keyframes nsw-globe-in { from { opacity: 0; transform: scale(.55); } to { opacity: 1; transform: scale(1); } }
  @media (prefers-reduced-motion: reduce) {
    .nsw .letter { stroke-dashoffset: 0 !important; animation: none !important; }
    .nsw .globe { opacity: 1 !important; transform: none !important; animation: none !important; }
  }`;

  const MARKUP = `
    <defs><clipPath id="CLIP"><circle cx="302" cy="50" r="29.5"/></clipPath></defs>
    <polyline class="letter" pathLength="1" style="--d:0s" points="5.5,94.5 5.5,5.5 64.5,94.5 64.5,5.5"/>
    <path class="letter" pathLength="1" style="--d:.14s" d="M138.5,5.5 H97.5 V94.5 H138.5 M97.5,50 H132"/>
    <path class="letter" pathLength="1" style="--d:.28s" d="M218.52,16.63 A26,22.25 0 1 0 196,50 A26,22.25 0 1 1 173.48,83.38"/>
    <g class="globe" aria-hidden="true">
      <g clip-path="url(#CLIP)">
        <circle class="ocean" cx="302" cy="50" r="29.5"/>
        <g class="land"></g>
      </g>
    </g>
    <g class="helix" aria-hidden="true"><g class="back"></g><g class="rungs"></g><g class="front"></g></g>
    <g transform="translate(14 0)">
      <polyline class="letter" pathLength="1" style="--d:.56s" points="371.5,94.5 371.5,5.5 407,72 442.5,5.5 442.5,94.5"/>
      <line class="letter" pathLength="1" style="--d:.70s" x1="475.5" y1="5.5" x2="475.5" y2="94.5"/>
      <path class="letter" pathLength="1" style="--d:.84s" d="M579.5,18.5 A44.5,44.5 0 1 0 579.5,81.5"/>
    </g>`;

  // low-detail continents (lon, lat), densified so the rim clipping stays smooth
  const LAND = [
    [[-165,65],[-140,70],[-95,72],[-80,63],[-60,55],[-55,48],[-75,40],[-81,30],[-82,25],[-97,26],[-97,18],[-88,15],[-80,8],[-85,12],[-105,22],[-117,32],[-124,40],[-125,50],[-135,58],[-160,58]],
    [[-50,60],[-42,60],[-20,70],[-25,82],[-60,82],[-70,76],[-55,68]],
    [[-80,8],[-60,10],[-50,0],[-35,-7],[-40,-22],[-55,-35],[-65,-42],[-68,-55],[-75,-50],[-72,-30],[-71,-18],[-80,-5]],
    [[-10,36],[-9,43],[-2,47],[-5,48],[5,53],[8,57],[5,62],[15,69],[28,71],[40,67],[60,70],[80,73],[110,77],[140,72],[170,70],[180,66],[160,60],[142,52],[140,40],[122,30],[120,22],[108,18],[105,10],[100,13],[98,8],[92,22],[80,15],[77,8],[72,20],[65,25],[57,25],[50,30],[35,36],[28,36],[26,40],[20,40],[15,38],[12,44],[3,43],[-5,36]],
    [[-17,21],[-10,30],[-5,35],[10,37],[20,32],[32,31],[43,12],[51,12],[40,-5],[40,-15],[35,-25],[20,-35],[17,-30],[12,-17],[13,-5],[8,4],[-8,4],[-17,14]],
    [[35,30],[48,30],[56,25],[60,22],[52,16],[43,12],[38,20]],
    [[114,-22],[122,-18],[130,-12],[137,-12],[142,-11],[146,-19],[153,-26],[150,-37],[140,-38],[131,-31],[115,-34]],
    [[44,-13],[50,-15],[47,-25],[44,-22]],
    [[-6,50],[2,51],[-2,58],[-6,57]],
    [[130,31],[141,35],[142,43],[139,38]]
  ].map(poly => {
    const out = [];
    poly.forEach((a, i) => {
      const b = poly[(i + 1) % poly.length];
      for (let k = 0; k < 4; k++) out.push([a[0] + (b[0] - a[0]) * k / 4, a[1] + (b[1] - a[1]) * k / 4]);
    });
    return out;
  });

  // helix around the O centreline, a circle like the circular plastome
  const CX = 302, CY = 50, R = 44;
  const AMP = 7.5, TURNS = 4, SAMPLES = 320, RUNGS = 24;
  const RUNG_COLOURS = ["var(--nsw-red)", "var(--nsw-blue)", "var(--nsw-ink)", "var(--nsw-black)"];
  // globe
  const GX = 302, GY = 50, GR = 29.5, TILT = 18 * D, SPIN = 0.35;  // rad/s
  // intro timing
  const S_DELAY = 420, S_DUR = 1700, TWIST = 0.55;                  // ms, ms, rad/s

  let instances = 0;

  function injectStyle() {
    if (document.getElementById("nesomic-wordmark-style")) return;
    const style = document.createElement("style");
    style.id = "nesomic-wordmark-style";
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function centre(t) {
    const pt = u => { const th = (-90 + 360 * u) * D; return [CX + R * Math.cos(th), CY + R * Math.sin(th)]; };
    const p = pt(t), a = pt(t - 0.002), b = pt(t + 0.002);
    const tx = b[0] - a[0], ty = b[1] - a[1], L = Math.hypot(tx, ty) || 1;
    return { x: p[0], y: p[1], nx: -ty / L, ny: tx / L };
  }
  const CENTRES = Array.from({ length: SAMPLES + 1 }, (_, i) => centre(i / SAMPLES));
  const RUNG_CENTRES = Array.from({ length: RUNGS }, (_, k) => centre((k + 0.5) / RUNGS));

  const ease = x => x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;

  function mount(container, options) {
    const opts = Object.assign({ intro: true, mono: false, speed: 1, fps: 60, label: "NESOMIC" }, options || {});
    injectStyle();
    const id = ++instances;
    const reduce = global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "-12 -16 631 132");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", opts.label);
    svg.setAttribute("class", "nsw" + (opts.mono ? " nsw-mono" : ""));
    svg.innerHTML = MARKUP.replace("CLIP", "nsw-clip-" + id).replace("url(#CLIP)", "url(#nsw-clip-" + id + ")");
    container.appendChild(svg);

    const q = sel => svg.querySelector(sel);
    const mk = (tag, cls, parent) => { const e = document.createElementNS(NS, tag); e.setAttribute("class", cls); parent.appendChild(e); return e; };
    const backA = mk("path", "strand a back", q(".back")), backB = mk("path", "strand b back", q(".back"));
    const frontA = mk("path", "strand a", q(".front")), frontB = mk("path", "strand b", q(".front"));
    const rungEls = RUNG_CENTRES.map((_, k) => {
      const l = mk("line", "rung", q(".rungs"));
      l.style.stroke = RUNG_COLOURS[k % 4];
      return l;
    });
    const landG = q(".land");
    const landEls = LAND.map(() => { const e = document.createElementNS(NS, "path"); landG.appendChild(e); return e; });

    function renderHelix(progress, phase) {
      // strands, split into front / back segments by depth
      const seg = { aF: "", aB: "", bF: "", bB: "" };
      let prevA = null, prevB = null;
      for (let i = 0; i <= SAMPLES; i++) {
        const t = i / SAMPLES;
        if (t > progress) break;
        const c = CENTRES[i];
        const ang = 2 * Math.PI * TURNS * t + phase;
        const s = Math.sin(ang), z = Math.cos(ang);
        const ax = c.x + c.nx * AMP * s, ay = c.y + c.ny * AMP * s;
        const bx = c.x - c.nx * AMP * s, by = c.y - c.ny * AMP * s;
        const aKey = z >= 0 ? "aF" : "aB", bKey = z >= 0 ? "bB" : "bF";
        if (prevA) {
          seg[aKey] += `M${prevA[0].toFixed(2)},${prevA[1].toFixed(2)}L${ax.toFixed(2)},${ay.toFixed(2)}`;
          seg[bKey] += `M${prevB[0].toFixed(2)},${prevB[1].toFixed(2)}L${bx.toFixed(2)},${by.toFixed(2)}`;
        }
        prevA = [ax, ay]; prevB = [bx, by];
      }
      frontA.setAttribute("d", seg.aF); backA.setAttribute("d", seg.aB);
      frontB.setAttribute("d", seg.bF); backB.setAttribute("d", seg.bB);

      // base-pair rungs
      rungEls.forEach((el, k) => {
        const t = (k + 0.5) / RUNGS;
        const c = RUNG_CENTRES[k];
        const s = Math.sin(2 * Math.PI * TURNS * t + phase);
        const half = Math.max(0, AMP * Math.abs(s) - 3.4) * Math.sign(s);
        el.setAttribute("x1", c.x + c.nx * half); el.setAttribute("y1", c.y + c.ny * half);
        el.setAttribute("x2", c.x - c.nx * half); el.setAttribute("y2", c.y - c.ny * half);
        const visible = Math.min(1, Math.max(0, (Math.abs(s) - 0.55) / 0.25));
        const born = Math.min(1, Math.max(0, (progress - t) / 0.06));
        el.style.opacity = visible * born;
      });
    }

    function renderGlobe(rot) {
      // orthographic projection, hidden points pinned to the rim
      const ct = Math.cos(TILT), st = Math.sin(TILT);
      LAND.forEach((poly, i) => {
        let d = "", anyVisible = false;
        poly.forEach(([lon, lat], j) => {
          const l = lon * D + rot, p = lat * D;
          const x = Math.cos(p) * Math.sin(l);
          const y0 = Math.sin(p), z0 = Math.cos(p) * Math.cos(l);
          const y = y0 * ct - z0 * st, z = y0 * st + z0 * ct;
          let px = x, py = y;
          if (z < 0) { const m = Math.hypot(x, y) || 1; px = x / m; py = y / m; }
          else anyVisible = true;
          d += (j ? "L" : "M") + (GX + px * GR).toFixed(2) + "," + (GY - py * GR).toFixed(2);
        });
        landEls[i].setAttribute("d", anyVisible ? d + "Z" : "");
      });
    }

    let start = 0, raf = 0, last = 0, intro = opts.intro;
    const minGap = 1000 / Math.max(1, opts.fps);

    function frame(now) {
      raf = requestAnimationFrame(frame);
      if (!svg.isConnected) { destroy(); return; }  // e.g. the splash replaced by the app
      const el = now - start;
      const building = intro && el < S_DELAY + S_DUR;
      if (!building && now - last < minGap) return;
      last = now;
      const p = intro ? ease(Math.min(1, Math.max(0, (el - S_DELAY) / S_DUR))) : 1;
      const idle = intro ? Math.max(0, el - S_DELAY - S_DUR) : el;
      // the helix winds in as it builds, then keeps a slow twist
      const phase = -2.2 * (1 - p) + idle / 1000 * TWIST * opts.speed;
      const spin = (intro ? Math.min(el, S_DELAY + S_DUR) : 0) + idle * opts.speed;
      render(p, phase, spin / 1000 * SPIN);
    }

    function render(p, phase, globeRot) {
      renderHelix(p, phase);
      renderGlobe(-0.4 + globeRot);
    }

    function play() {
      cancelAnimationFrame(raf);
      if (reduce) { svg.classList.remove("nsw-intro", "nsw-play"); render(1, 0, 0.7); return; }
      svg.classList.remove("nsw-play");
      svg.classList.toggle("nsw-intro", intro);
      if (intro) {
        void svg.getBoundingClientRect();   // restart CSS letter animations
        svg.classList.add("nsw-play");
      }
      start = performance.now();
      last = 0;
      raf = requestAnimationFrame(frame);
    }

    function destroy() {
      cancelAnimationFrame(raf);
      raf = 0;
      if (svg.parentNode) svg.parentNode.removeChild(svg);
    }

    play();
    return {
      replay() { intro = true; play(); },
      destroy,
      // ms the intro takes until the helix is complete
      introDuration: S_DELAY + S_DUR,
    };
  }

  global.NesomicWordmark = { mount };
})(window);
