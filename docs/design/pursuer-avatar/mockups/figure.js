const Fig = (() => {
  const BG0 = '#0b0f15';
  const RANKS = ['Newbie','Recruit','Seeker','Hunter','Ranger','Warden','Marshal','Vanquisher','Paragon','Luminary','Ascendant'];
  const TIER_OF = [0,0,1,1,2,2,2,3,3,4,4];
  const TIERS = [
    { name: 'Matte',    light: '#8b94a2', mid: '#5f6876', dark: '#373d48', edge: '#4b525e' },
    { name: 'Steel',    light: '#e2e8f0', mid: '#a3aebd', dark: '#5a6371', edge: '#8e98a7' },
    { name: 'Violet',   light: '#e2d9ff', mid: '#a08cf5', dark: '#4c3c9a', edge: '#a492f6' },
    { name: 'Platinum', light: '#ecfbff', mid: '#8fd2ea', dark: '#2f7690', edge: '#8fd2ea' },
    { name: 'Radiant',  light: '#ffffff', mid: '#b4f0ff', dark: '#3f93b4', edge: '#cdf6ff' },
  ];
  const DISC = {
    combat:      { name: 'Combat',      c: '#e5544b' },
    exploration: { name: 'Exploration', c: '#55cf8e' },
    mind:        { name: 'Mind',        c: '#9b8cf2' },
    heart:       { name: 'Heart',       c: '#ee6a9e' },
    finesse:     { name: 'Finesse',     c: '#f0bb52' },
  };

  /* ---------- helpers ---------- */
  const hex2rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const mix = (a, b, t) => {
    const A = hex2rgb(a), B = hex2rgb(b);
    return '#' + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, '0')).join('');
  };
  const f1 = n => n.toFixed(1);
  const starPath = (cx, cy, R, r, n) => {
    let d = '';
    for (let i = 0; i < n * 2; i++) {
      const a = -Math.PI / 2 + i * Math.PI / n, rad = i % 2 ? r : R;
      d += (i ? 'L' : 'M') + f1(cx + rad * Math.cos(a)) + ',' + f1(cy + rad * Math.sin(a));
    }
    return d + 'Z';
  };
  // Rounded rectangle as a path of x,y pairs only, so mirrorD can flip it.
  const rr = (x, y, w, h, r) =>
    `M${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} L${x + r},${y + h} Q${x},${y + h} ${x},${y + h - r} L${x},${y + r} Q${x},${y} ${x + r},${y} Z`;
  // Mirror a path across x = 100. Paths must be written as absolute "x,y" pairs.
  const mirrorD = d => d.replace(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g, (_, x, y) => `${f1(200 - Number(x))},${y}`);

  /* ---------- shading ---------- */
  // Every surface is lit from the top left: a shadow crescent on the lower right with a faint bounce
  // rim, a soft vinyl gloss on the upper left, and a dark edge so it reads on a dark page.
  function makeShade(c) {
    let n = 0;
    return (d, base, o = {}) => {
      const id = `sc${c.uid}-${n++}`, dx = o.dx ?? 4, dy = o.dy ?? 3;
      const isUrl = base.startsWith('url');
      const shadow = o.shadow ?? (isUrl ? c.tier.dark : mix(base, '#000000', 0.42));
      const rim = o.rim ?? (isUrl ? c.tier.mid : mix(base, '#ffffff', 0.2));
      const line = o.line ?? mix(isUrl ? c.tier.dark : shadow, '#000000', 0.45);
      const gloss = o.gloss === false ? '' : `<path d="${d}" fill="url(#gl${c.uid})"/>`;
      return `<clipPath id="${id}"><path d="${d}"/></clipPath>` +
        `<g clip-path="url(#${id})"><path d="${d}" fill="${shadow}"/><path d="${d}" fill="none" stroke="${rim}" stroke-opacity=".5" stroke-width="3"/>` +
        `<path d="${d}" fill="${base}" transform="translate(${-dx},${-dy})"/>${o.inner || ''}${gloss}</g>` +
        `<path d="${d}" fill="none" stroke="${line}" stroke-width="${o.lw ?? 1.6}" stroke-linejoin="round"/>`;
    };
  }

  function defs(c) {
    const u = c.uid, t = c.tier, A = c.accent;
    return `<defs>
      <linearGradient id="m${u}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${t.light}"/><stop offset=".55" stop-color="${t.mid}"/><stop offset="1" stop-color="${t.dark}"/></linearGradient>
      <linearGradient id="mh${u}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="${t.dark}"/><stop offset=".35" stop-color="${t.light}"/><stop offset=".65" stop-color="${t.mid}"/><stop offset="1" stop-color="${t.dark}"/></linearGradient>
      <linearGradient id="pt${u}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f2fcff"/><stop offset=".55" stop-color="#8fd2ea"/><stop offset="1" stop-color="#2f7690"/></linearGradient>
      <linearGradient id="gl${u}" x1="0" y1="0" x2=".85" y2=".95"><stop offset="0" stop-color="#ffffff" stop-opacity=".24"/><stop offset=".38" stop-color="#ffffff" stop-opacity=".05"/><stop offset=".5" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
      <radialGradient id="lg${u}"><stop offset="0" stop-color="${mix(A, '#ffffff', 0.5)}" stop-opacity=".5"/><stop offset="1" stop-color="${A}" stop-opacity="0"/></radialGradient>
      <radialGradient id="pg${u}"><stop offset="0" stop-color="#bfefff" stop-opacity=".55"/><stop offset="1" stop-color="#8fd2ea" stop-opacity="0"/></radialGradient>
      <radialGradient id="scr${u}" cx=".5" cy=".38" r=".75"><stop offset="0" stop-color="${mix(A, BG0, 0.82)}"/><stop offset="1" stop-color="#06090d"/></radialGradient>
      <radialGradient id="bg${u}" cx=".5" cy=".38" r=".8"><stop offset="0" stop-color="${mix(A, BG0, 0.62)}"/><stop offset="1" stop-color="${BG0}"/></radialGradient>
      <linearGradient id="sh${u}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ffffff" stop-opacity="0"/><stop offset=".5" stop-color="#ffffff" stop-opacity=".55"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
      <clipPath id="c${u}"><rect x="6" y="6" width="188" height="188" rx="14"/></clipPath>
    </defs>`;
  }

  function trophyGlyph(x, y, r, fill, dk) {
    return `<path d="M${x - r * .8},${y - r} H${x + r * .8} V${y - r * .3} Q${x + r * .8},${y + r * .35} ${x},${y + r * .45} Q${x - r * .8},${y + r * .35} ${x - r * .8},${y - r * .3} Z" fill="${fill}" stroke="${dk}" stroke-width="1.2"/>` +
      `<path d="M${x - r * .8},${y - r * .75} H${x - r * 1.15} Q${x - r * 1.2},${y - r * .05} ${x - r * .55},${y + r * .1}" fill="none" stroke="${fill}" stroke-width="${r * .22}" stroke-linecap="round"/>` +
      `<path d="M${x + r * .8},${y - r * .75} H${x + r * 1.15} Q${x + r * 1.2},${y - r * .05} ${x + r * .55},${y + r * .1}" fill="none" stroke="${fill}" stroke-width="${r * .22}" stroke-linecap="round"/>` +
      `<rect x="${x - r * .14}" y="${y + r * .42}" width="${r * .28}" height="${r * .36}" fill="${fill}"/>` +
      `<rect x="${x - r * .55}" y="${y + r * .74}" width="${r * 1.1}" height="${r * .32}" rx="${r * .1}" fill="${fill}" stroke="${dk}" stroke-width="1"/>`;
  }

  function eyesSVG(kind, A) {
    const core = mix(A, '#ffffff', 0.4);
    let s = '';
    [86, 114].forEach((x, i) => {
      const flip = i ? -1 : 1;
      switch (kind) {
        case 'Bright': s += `<rect x="${x - 7}" y="70" width="14" height="24" rx="7" fill="${A}" fill-opacity=".28"/><rect x="${x - 5}" y="72" width="10" height="20" rx="5" fill="${core}"/>`; break;
        case 'Happy': s += `<path d="M${x - 7},87 Q${x},75 ${x + 7},87" fill="none" stroke="${A}" stroke-opacity=".3" stroke-width="9" stroke-linecap="round"/><path d="M${x - 7},87 Q${x},75 ${x + 7},87" fill="none" stroke="${core}" stroke-width="4.5" stroke-linecap="round"/>`; break;
        case 'Calm': s += `<path d="M${x - 7},84 Q${x},88 ${x + 7},84" fill="none" stroke="${A}" stroke-opacity=".3" stroke-width="9" stroke-linecap="round"/><path d="M${x - 7},84 Q${x},88 ${x + 7},84" fill="none" stroke="${core}" stroke-width="4.5" stroke-linecap="round"/>`; break;
        case 'Determined': {
          const d = `M${x - 6 * flip},74 L${x + 6 * flip},79 L${x + 6 * flip},88 Q${x},93 ${x - 6 * flip},88 Z`;
          s += `<path d="${d}" fill="${A}" fill-opacity=".3" stroke="${A}" stroke-opacity=".3" stroke-width="5" stroke-linejoin="round"/><path d="${d}" fill="${core}"/>`;
          break;
        }
        case 'Star': s += `<path d="${starPath(x, 82, 12, 5.5, 5)}" fill="${A}" fill-opacity=".3"/><path d="${starPath(x, 82, 9, 4, 5)}" fill="${core}"/>`; break;
      }
    });
    return s;
  }

  /* ---------- head (unchanged from mockup 2) ---------- */
  function headSVG(p, c) {
    const u = c.uid, A = c.accent, M = `url(#m${u})`, MH = `url(#mh${u})`;
    const dk = mix(c.tier.dark, '#000000', 0.35);
    let s = `<g class="head">`;
    if (p.helmet === 'Crested') s += `<path d="M88,40 C88,14 102,0 116,2 C110,14 112,28 112,40 Z" fill="${mix(A, BG0, 0.12)}" stroke="${dk}" stroke-width="2" stroke-linejoin="round"/>`;
    switch (p.helmet) {
      case 'Dome':
      case 'Crested': s += `<circle cx="100" cy="80" r="50" fill="${M}" stroke="${dk}" stroke-width="2"/>`; break;
      case 'Scout': s += `<rect x="40" y="66" width="14" height="30" rx="6" fill="${MH}" stroke="${dk}" stroke-width="2"/><rect x="146" y="66" width="14" height="30" rx="6" fill="${MH}" stroke="${dk}" stroke-width="2"/><rect x="50" y="32" width="100" height="96" rx="40" fill="${M}" stroke="${dk}" stroke-width="2"/>`; break;
      case 'Faceted': s += `<path d="M100,28 L146,48 L154,92 L130,128 L70,128 L46,92 L54,48 Z" fill="${M}" stroke="${dk}" stroke-width="2" stroke-linejoin="round"/><path d="M100,28 L54,48 L62,60 L100,44 Z" fill="#ffffff" fill-opacity=".18"/><path d="M100,28 L146,48 L138,60 L100,44 Z" fill="#ffffff" fill-opacity=".07"/>`; break;
    }
    if (p.helmet !== 'Faceted') s += `<path d="M66,50 Q100,24 134,50" fill="none" stroke="#ffffff" stroke-opacity=".32" stroke-width="3" stroke-linecap="round"/>`;
    s += `<rect x="62" y="58" width="76" height="48" rx="22" fill="url(#scr${u})" stroke="${dk}" stroke-width="3"/>`;
    s += `<g class="eyes">${eyesSVG(p.eyes, A)}</g>`;
    s += `<path d="M72,66 Q80,62 92,62" fill="none" stroke="#ffffff" stroke-opacity=".3" stroke-width="3" stroke-linecap="round"/>`;
    switch (p.topper) {
      case 'Antenna': s += `<path d="M128,38 L142,12" stroke="${M}" stroke-width="3" stroke-linecap="round"/><circle cx="143" cy="10" r="9" fill="${A}" fill-opacity=".25"/><circle cx="143" cy="10" r="5" fill="${A}"/>`; break;
      case 'Lamp': s += `<rect x="54" y="42" width="20" height="12" rx="4" fill="${MH}" stroke="${dk}" stroke-width="1.5"/><circle cx="56" cy="48" r="10" fill="${mix(A, '#ffffff', 0.5)}" fill-opacity=".3"/><circle cx="56" cy="48" r="3.5" fill="#ffffff"/>`; break;
      case 'Halo': s += `<ellipse cx="100" cy="17" rx="30" ry="7" fill="none" stroke="${mix(A, '#ffffff', 0.6)}" stroke-opacity=".35" stroke-width="9"/><ellipse cx="100" cy="17" rx="30" ry="7" fill="none" stroke="#ffffff" stroke-width="2.5"/>`; break;
    }
    return s + `</g>`;
  }

  /* ---------- body ---------- */
  const HX = 126, HY = 168; // right hand
  const D = {
    torso: 'M79,128 C79,121 121,121 121,128 L124,162 C125,174 114,180 100,180 C86,180 75,174 76,162 Z',
    leg: rr(85, 168, 12, 36, 6),
    foot: 'M75,215 C75,205 81,201 88,201 C95,201 98,206 98,212 L98,217 C98,220.5 95.5,222 92,222 L79,222 C76.5,222 75,220 75,217 Z',
    armL: rr(68, 130, 12, 36, 6),
    // ranger
    cape: 'M80,124 C70,138 60,168 54,200 L66,195 L74,208 L86,199 L100,210 L114,199 L126,208 L134,195 L146,200 C140,168 130,138 120,124 Z',
    skirt: 'M75,164 C74,174 72,182 71,188 Q100,195 129,188 C128,182 126,174 125,164 Z',
    hood: 'M60,110 C58,148 142,148 140,110 C128,130 72,130 60,110 Z',
    mantle: 'M72,122 C64,130 61,140 61,150 L71,146 L79,155 L89,149 L100,157 L111,149 L121,155 L129,146 L139,150 C139,140 136,130 128,122 Z',
    strap: 'M112,127 L120,131 L90,170 L82,166 Z',
    quiver: 'M146.4,95.8 L157.6,104.2 L109.6,168.2 L98.4,159.8 Z',
  };

  function bodySVG(p, c) {
    const u = c.uid, A = c.accent, M = `url(#m${u})`, MH = `url(#mh${u})`, PT = `url(#pt${u})`;
    const sh = makeShade(c), dk = mix(c.tier.dark, '#000000', 0.35);
    const ranger = p.suit === 'Ranger';
    const suit = ranger ? '#4d5246' : '#465063';
    const glove = ranger ? '#3b2a20' : '#262c36';
    const leather = '#8a5a38', leatherDk = '#4a2e1c';
    const cloak = mix(A, '#000000', 0.45);
    const flame = mix(A, '#ffffff', 0.35);
    const both = (d, fn) => fn(d) + fn(mirrorD(d));
    let s = '';

    const folds = `<path d="M66,160 Q62,178 61,196 M134,160 Q138,178 139,196 M76,170 Q74,186 74,204 M124,170 Q126,186 126,204" fill="none" stroke="${mix(cloak, '#000000', 0.4)}" stroke-opacity=".55" stroke-width="1.6" stroke-linecap="round"/>`;
    if (ranger) s += `<g class="cape">${sh(D.cape, cloak, { dx: 6, dy: 2, inner: folds })}</g>`;
    if (ranger) {
      s += sh(D.quiver, leather, { shadow: leatherDk });
      s += `<path d="M146.4,95.8 L157.6,104.2" stroke="${M}" stroke-width="4" stroke-linecap="round"/>`;
      [[149, 98], [152.5, 100.5], [156, 103]].forEach(([x, y], i) => {
        const len = 15 + i * 2, ex = x + 0.6 * len, ey = y - 0.8 * len;
        s += `<path d="M${x},${y} L${f1(ex)},${f1(ey)}" stroke="#d9c9a8" stroke-width="1.6"/>` +
          `<ellipse cx="${f1(ex - 1.5)}" cy="${f1(ey + 2)}" rx="5.5" ry="2.4" transform="rotate(-53 ${f1(ex - 1.5)} ${f1(ey + 2)})" fill="${mix(A, '#ffffff', 0.25)}" stroke="${mix(A, '#000000', 0.4)}" stroke-width=".8"/>`;
      });
    }
    if (p.pack === 'Backpack') {
      s += sh(rr(68, 122, 64, 48, 14), mix(A, BG0, 0.5));
      s += sh(rr(72, 113, 56, 13, 6.5), mix(suit, '#ffffff', 0.25));
    }
    if (p.pack === 'Jetpack') {
      [70, 130].forEach(x => { s += `<path class="jet" opacity="0" d="M${x - 7},166 Q${x},200 ${x + 7},166 Z" fill="${flame}"/><path class="jet" opacity="0" d="M${x - 3.5},166 Q${x},184 ${x + 3.5},166 Z" fill="#ffffff"/>`; });
      s += both(rr(62, 118, 16, 48, 8), d => sh(d, M));
      s += `<rect x="64" y="163" width="12" height="6" rx="2" fill="${dk}"/><rect x="124" y="163" width="12" height="6" rx="2" fill="${dk}"/>`;
    }

    // legs and boots
    s += both(D.leg, d => sh(d, mix(suit, '#000000', 0.25), { dx: 3, dy: 0 }));
    switch (p.boots) {
      case 'Trail boots':
        s += both(D.foot, d => sh(d, leather, { shadow: leatherDk }));
        s += both(rr(82, 195, 16, 8, 3.5), d => sh(d, mix(leather, '#ffffff', 0.12), { dx: 2, dy: 2, shadow: leatherDk }));
        break;
      case 'Heavy boots':
        s += both(rr(74, 194, 28, 28, 10), d => sh(d, M));
        s += `<path d="M77,206 H99 M101,206 H123" stroke="${A}" stroke-width="2.5"/>`;
        break;
      case 'Jet boots':
        [88.5, 111.5].forEach(x => { s += `<path class="jet" opacity="0" d="M${x - 7},221 Q${x},252 ${x + 7},221 Z" fill="${flame}"/><path class="jet" opacity="0" d="M${x - 3.5},221 Q${x},238 ${x + 3.5},221 Z" fill="#ffffff"/>`; });
        s += both(D.foot, d => sh(d, M));
        s += `<rect x="82.5" y="219" width="12" height="5" rx="2" fill="${dk}"/><rect x="105.5" y="219" width="12" height="5" rx="2" fill="${dk}"/>`;
        break;
    }

    // tunic skirt sits behind the torso
    if (ranger) s += sh(D.skirt, mix(suit, '#000000', 0.12), { inner: `<path d="M100,180 V194" stroke="${mix(suit, '#000000', 0.5)}" stroke-width="1.4"/>` });

    // torso
    s += sh(D.torso, suit, { dx: 5, dy: 3 });
    if (ranger) {
      s += sh(D.strap, leather, { shadow: leatherDk, dx: 1.5, dy: 1.5, gloss: false });
      [[110, 136], [103, 145], [96, 154], [89, 163]].forEach(([x, y]) => { s += `<circle cx="${x}" cy="${y}" r="1.6" fill="${M}"/>`; });
      s += sh(rr(76, 160, 48, 8, 3), leather, { shadow: leatherDk, dx: 1.5, dy: 2, gloss: false });
      s += sh(rr(78, 165, 13, 12, 3.5), mix(leather, '#ffffff', 0.08), { shadow: leatherDk, dx: 2, dy: 2 });
      s += `<path d="M78.5,169 H90.5" stroke="${leatherDk}" stroke-width="1.2"/>`;
      s += sh(rr(95, 158.5, 10, 11, 2), M, { dx: 1.5, dy: 1.5, lw: 1.2 });
    } else {
      s += `<path d="M84,130 Q100,140 116,130" fill="none" stroke="${M}" stroke-width="4" stroke-linecap="round"/>`;
      if (p.pack !== 'None') s += `<path d="M86,132 L88,160 M114,132 L112,160" stroke="${mix(suit, '#000000', 0.4)}" stroke-width="4.5" stroke-linecap="round"/>`;
      s += `<path d="M100,140 L106,148 L100,156 L94,148 Z" fill="${M}" stroke="${dk}" stroke-width="1.2"/><path d="M100,144 L103,148 L100,152 L97,148 Z" fill="${A}"/>`;
      s += sh(rr(77, 160, 46, 7, 3), mix(suit, '#000000', 0.35), { dx: 1, dy: 1.5, gloss: false });
      s += sh(rr(95, 158.5, 10, 10, 2), M, { dx: 1.5, dy: 1.5, lw: 1.2 });
    }

    // arms sit in front of the torso
    const armParts = x => {
      const d = x === 74 ? D.armL : mirrorD(D.armL);
      let a = sh(d, suit, { dx: 3, dy: 2 });
      if (ranger) a += sh(rr(x - 6.5, 152, 13, 8, 3), leather, { shadow: leatherDk, dx: 1.5, dy: 1.5, gloss: false });
      return a;
    };
    const hand = (x, y) => `<circle cx="${x}" cy="${y}" r="6.5" fill="${glove}" stroke="${mix(glove, '#000000', 0.5)}" stroke-width="1.5"/><circle cx="${x - 2.4}" cy="${y - 2.6}" r="2.6" fill="#ffffff" fill-opacity=".14"/>`;
    s += `<g class="arm arm-l">${armParts(74)}${hand(74, HY)}</g>`;
    let held = '';
    switch (p.held) {
      case 'Lantern': held = `<circle cx="${HX}" cy="${HY + 16}" r="14" fill="url(#lg${u})"/>` +
        `<path d="M${HX - 5},${HY + 6} Q${HX},${HY - 3} ${HX + 5},${HY + 6}" fill="none" stroke="${M}" stroke-width="1.8"/>` +
        sh(rr(HX - 7, HY + 10, 14, 17, 5), mix(A, '#ffffff', 0.55), { dx: 0, dy: 0, shadow: mix(A, '#ffffff', 0.55), rim: '#ffffff', line: dk, lw: 1.2 }) +
        `<path d="M${HX},${HY + 11} V${HY + 26}" stroke="${dk}" stroke-opacity=".5" stroke-width="1.2"/>` +
        sh(rr(HX - 6, HY + 6, 12, 5, 2.5), M, { dx: 1, dy: 1, lw: 1.1 }) + sh(rr(HX - 8, HY + 25, 16, 5, 2.5), M, { dx: 1, dy: 1, lw: 1.1 }); break;
      case 'Map': held = sh(rr(HX - 5, HY - 12, 10, 32, 5), '#e6dcc4', { dx: 2, dy: 1, shadow: '#a89a7a' }) + `<path d="M${HX - 5},${HY + 6} H${HX + 5}" stroke="${A}" stroke-width="2.5"/>`; break;
      case 'Trophy': held = `<circle cx="${HX}" cy="${HY - 16}" r="14" fill="url(#pg${u})"/>` + trophyGlyph(HX, HY - 15, 10, PT, '#1f4f63'); break;
    }
    // invisible anchors centre each bounding box on the hand: the arm pivots at the shoulder, the item turns in the hand
    s += `<g class="arm arm-r"><circle cx="${HX}" cy="${HY}" r="30" fill="none"/>${armParts(126)}<g class="item"><circle cx="${HX}" cy="${HY}" r="30" fill="none"/>${held}</g>${hand(HX, HY)}</g>`;

    if (ranger) {
      s += sh(D.hood, mix(cloak, '#ffffff', 0.06), { dx: 3, dy: 4 });
      s += sh(D.mantle, cloak, { dx: 5, dy: 3, inner: `<path d="M63.5,145.5 L71,141.5 L79,150.5 L89,144.5 L100,152.5 L111,144.5 L121,150.5 L129,141.5 L136.5,145.5" fill="none" stroke="${mix(cloak, '#ffffff', 0.4)}" stroke-opacity=".7" stroke-width="1" stroke-dasharray="2 2.2"/>` });
      s += `<path d="M75,141 Q78,146 79,152 M121,141 Q122,146 121,152" fill="none" stroke="${mix(cloak, '#000000', 0.35)}" stroke-width="1.4" stroke-linecap="round"/>`;
      s += sh('M100,134 C104,134 107,137 107,141 C107,145 104,148 100,148 C96,148 93,145 93,141 C93,137 96,134 100,134 Z', M, { dx: 1.5, dy: 1.5, lw: 1.2 });
      s += `<path d="M100,137.5 L103,141 L100,144.5 L97,141 Z" fill="${A}"/>`;
    }
    return s;
  }

  function charSVG(p, c) {
    return `<g class="fig-g"><g class="idle">${bodySVG(p, c)}${headSVG(p, c)}</g></g>`;
  }

  function plinth(c) {
    const t = c.tier, ti = c.ti, M = `url(#m${c.uid})`, MH = `url(#mh${c.uid})`;
    let s = '';
    if (ti === 4) s += `<ellipse cx="100" cy="228" rx="74" ry="18" fill="${t.mid}" fill-opacity=".18"/>`;
    s += `<path d="M44,223 V233 A56,11 0 0 0 156,233 V223 Z" fill="${MH}"/>`;
    s += `<ellipse cx="100" cy="223" rx="56" ry="11" fill="${M}"/>`;
    s += `<ellipse cx="100" cy="223" rx="47" ry="8" fill="${mix(t.dark, BG0, 0.5)}"/>`;
    if (ti >= 1) s += `<path d="M48,226 A52,10 0 0 0 152,226" fill="none" stroke="#ffffff" stroke-opacity="${ti >= 3 ? 0.5 : 0.25}" stroke-width="1.2"/>`;
    if (ti >= 2) {
      const sz = ti === 4 ? 4.5 : 3.6, fill = ti === 4 ? '#ffffff' : ti === 3 ? t.light : t.mid;
      [[72, 238], [100, 239.5], [128, 238]].forEach(([x, y]) => {
        if (ti === 4) s += `<path d="M${x},${y - 9} L${x + 9},${y} L${x},${y + 9} L${x - 9},${y} Z" fill="${t.mid}" fill-opacity=".35"/>`;
        s += `<path d="M${x},${y - sz} L${x + sz},${y} L${x},${y + sz} L${x - sz},${y} Z" fill="${fill}" stroke="${t.dark}" stroke-width=".8"/>`;
      });
    }
    if (ti === 4) s += `<ellipse cx="100" cy="223" rx="62" ry="13" fill="none" stroke="#ffffff" stroke-opacity=".45" stroke-width="1.2"/>`;
    return s;
  }

  function fx(c) {
    let s = `<g class="fx-trophy" opacity="0"><circle cx="100" cy="-16" r="30" fill="url(#pg${c.uid})"/>${trophyGlyph(100, -18, 15, `url(#pt${c.uid})`, '#1f4f63')}</g>`;
    for (let i = 0; i < 6; i++) {
      const a = (-60 + i * 60) * Math.PI / 180, x = 100 + 34 * Math.cos(a), y = -16 + 30 * Math.sin(a);
      s += `<path class="spark" opacity="0" d="${starPath(x, y, 6, 1.6, 4)}" fill="#ffffff"/>`;
    }
    return s;
  }

  let UID = 0;
  const ctx = (ti, accent) => ({ uid: ++UID, tier: TIERS[ti], ti, accent });

  function renderFull(p, ti, accent, withFx) {
    const c = ctx(ti, accent);
    return `<svg viewBox="0 0 200 250" role="img" aria-label="Full-body Pursuer, ${c.tier.name} finish">${defs(c)}${plinth(c)}<ellipse class="shadow" cx="100" cy="222" rx="30" ry="5" fill="#000000" fill-opacity=".45"/>${charSVG(p, c)}${withFx ? fx(c) : ''}</svg>`;
  }

  // Same recipe, cropped to head and shoulders for small sizes.
  function renderCrop(p, ti, accent) {
    const c = ctx(ti, accent), t = c.tier, u = c.uid;
    const sheen = ti === 4 ? `<g transform="skewX(-18)"><rect class="av-sheen" x="-120" y="-10" width="70" height="220" fill="url(#sh${u})"/></g>` : '';
    const topLight = ti >= 1 ? `<path d="M22,7.6 H178" stroke="#ffffff" stroke-opacity="${ti >= 3 ? 0.55 : 0.3}" stroke-width="1.5" stroke-linecap="round"/>` : '';
    let notches = '';
    if (ti >= 2) {
      const sz = ti === 4 ? 6.5 : 5.2, fill = ti === 4 ? '#ffffff' : ti === 3 ? t.light : t.mid;
      [[10.1, 10.1], [189.9, 10.1], [10.1, 189.9], [189.9, 189.9]].forEach(([x, y]) => {
        if (ti === 4) notches += `<path d="M${x},${y - 12} L${x + 12},${y} L${x},${y + 12} L${x - 12},${y} Z" fill="${t.mid}" fill-opacity=".35"/>`;
        notches += `<path d="M${x},${y - sz} L${x + sz},${y} L${x},${y + sz} L${x - sz},${y} Z" fill="${fill}" stroke="${t.dark}" stroke-width="1"/>`;
      });
    }
    return `<svg viewBox="0 0 200 200" role="img" aria-label="Pursuer portrait, ${t.name} finish">${defs(c)}<g clip-path="url(#c${u})"><rect width="200" height="200" fill="url(#bg${u})"/><g transform="matrix(1.4242 0 0 1.4242 -42.4 -13.9)">${charSVG(p, c)}</g>${sheen}</g><rect x="6" y="6" width="188" height="188" rx="14" fill="none" stroke="${ti >= 2 ? `url(#m${u})` : t.edge}" stroke-width="${ti >= 2 ? 5 : 4}"/>${topLight}${notches}</svg>`;
  }

  return { BG0, RANKS, TIER_OF, TIERS, DISC, mix, eyesSVG, renderFull, renderCrop };
})();
