/* ============================================================
   WC26 Veikkauskilpailu — app.js
   Loads data.json (static predictions) + results.json (live scores)
   ============================================================ */

'use strict';

// ── State ────────────────────────────────────────────────────
let DATA = null;          // from data.json (static predictions)
let RESULTS = null;       // from results.json (live Bzzoiro data)
let TEAM_IDS = {};        // team name → Bzzoiro team id (for logos)
let PRED_MAP = {};        // "home|away" → prediction object
let ACTIVE_TAB = 'standings';
const PANELS = {};        // which tabs have been built
let REFRESH_TIMER = null;

// ── Constants ────────────────────────────────────────────────
const LOGO_BASE = 'https://sports.bzzoiro.com/img/team/';
const AVATAR_COLORS = [
  '#E8445A','#F4A259','#F4D03F','#58D68D','#45B7D1','#9B59B6',
  '#3498DB','#E74C3C','#1ABC9C','#E67E22','#BDC3C7','#2ECC71','#EC407A'
];
const GROUP_COLORS = {
  A:'#FF6B5B',B:'#FF9F43',C:'#FFC845',D:'#A3CB38',E:'#46D9C0',F:'#45AAF2',
  G:'#9B59B6',H:'#E91E63',I:'#F06292',J:'#00BCD4',K:'#8D6E63',L:'#78909C'
};

// ── Helpers ──────────────────────────────────────────────────
const $  = id => document.getElementById(id);
const qs = sel => document.querySelector(sel);
function toast(msg, ms=2600) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), ms);
}
function initials(name) { return name.slice(0, 2).toUpperCase(); }
function fmtDate(iso) {
  return new Date(iso + 'T12:00:00').toLocaleDateString('fi-FI', {
    weekday: 'short', day: 'numeric', month: 'short'
  });
}
function today() { return new Date().toISOString().slice(0, 10); }
function logoUrl(teamId) { return teamId ? `${LOGO_BASE}${teamId}/` : null; }
function logoImg(id, flag, cls='logo') {
  if (!id) return `<span>${flag}</span>`;
  return `<img class="${cls}" src="${logoUrl(id)}" alt="" onerror="this.outerHTML='<span>${flag}</span>'">`;
}

// ── Rarity bonus (matches original scoring) ──────────────────
function rarityBonus(nCorrect) {
  if (nCorrect <= 0) return 0;
  if (nCorrect === 1) return 4;
  if (nCorrect === 2) return 3;
  if (nCorrect <= 5)  return 1;
  return 0;
}

// ── Normalise Bzzoiro team names → data.json names ───────────
const ALIASES = {
  'Bosnia & Herzegovina': 'Bosnia and Herzegovina',
  'Cabo Verde':           'Cape Verde',
  "Côte d'Ivoire":        'Ivory Coast',
  'DR Congo':             'Congo DR',
  'Czechia':              'Czech Republic',
  'USA':                  'United States',
  'Türkiye':              'Turkey',
  'South Korea':          'Korea Republic',
};
function normTeam(t) { return t ? (ALIASES[t] || t) : ''; }

// ── Match lookup ─────────────────────────────────────────────
function findMatch(home, away) {
  const h = normTeam(home), a = normTeam(away);
  return DATA.matches.find(m =>
    (m.home === h && m.away === a) || (m.home === a && m.away === h)
  );
}

// ── Apply live results + recalculate points ──────────────────
function applyResults() {
  if (!RESULTS || !DATA) return;

  // Build team ID map and prediction map from results.json
  TEAM_IDS = {};
  PRED_MAP = {};
  for (const g of RESULTS.games) {
    if (g.homeId) TEAM_IDS[normTeam(g.home)] = g.homeId;
    if (g.awayId) TEAM_IDS[normTeam(g.away)] = g.awayId;
    const key = `${normTeam(g.home)}|${normTeam(g.away)}`;
    PRED_MAP[key] = g;
  }

  // Build AI predictions map
  const aiPreds = {};
  for (const p of (RESULTS.predictions || [])) {
    const key = `${normTeam(p.home)}|${normTeam(p.away)}`;
    aiPreds[key] = p;
  }

  for (const m of DATA.matches) {
    const g = PRED_MAP[`${m.home}|${m.away}`] ||
              PRED_MAP[`${m.away}|${m.home}`];

    // Attach AI prediction if available
    m.ai = aiPreds[`${m.home}|${m.away}`] || aiPreds[`${m.away}|${m.home}`] || null;

    // Attach weather
    m.weather   = g?.weather   || null;
    m.htHome    = g?.htHome    ?? null;
    m.htAway    = g?.htAway    ?? null;
    m.minute    = g?.minute    ?? null;
    m.period    = g?.period    || '';
    m.attendance= g?.attendance ?? null;
    m.liveStatus= g?.status    || null;
    m.poly     = g?.poly     || null;   // ← ADD
    // Real xG from stats; fall back to AI-predicted xG when stats endpoint returns null
    m.xgHome   = g?.xgHome   ?? g?.ai?.xgHome  ?? null;
    m.xgAway   = g?.xgAway   ?? g?.ai?.xgAway  ?? null;
    m.xgotHome = g?.xgotHome ?? null;
    m.xgotAway = g?.xgotAway ?? null;

    if (!g) continue;

    const flipped = m.home !== normTeam(g.home);
    const a = flipped ? g.awayScore : g.homeScore;
    const b = flipped ? g.homeScore : g.awayScore;

    if (g.homeScore === null && g.awayScore === null) {
      // Not yet played - preserve existing data
      if (!m.played) {
        m.played = false;
        m.actualA = null;
        m.actualB = null;
      }
      continue;
    }

    m.played  = true;
    m.actualA = a;
    m.actualB = b;
    m.liveStatus = g.status;

    // Recalculate predictions
    let nCorrect = 0;
    for (const player of DATA.players) {
      const pr = m.preds[player];
      if (!pr) continue;
      const actualResult = a > b ? 'home' : a < b ? 'away' : 'draw';
      const exact = pr.a === a && pr.b === b;
      const correct = exact ||
        (actualResult === 'home' && pr.a > pr.b) ||
        (actualResult === 'away' && pr.a < pr.b) ||
        (actualResult === 'draw' && pr.a === pr.b);
      pr.pts   = exact ? 4 : correct ? 2 : 0;
      pr.exact = exact;
      if (correct) nCorrect++;
    }
    // Apply rarity bonus
    const bonus = rarityBonus(nCorrect);
    m.oikein = nCorrect;
    m.rarity = bonus;
    if (bonus > 0) {
      for (const player of DATA.players) {
        const pr = m.preds[player];
        if (pr && pr.pts > 0) pr.pts += bonus;
      }
    }
  }
}

// ── Player standings ─────────────────────────────────────────
function buildStandings() {
  const played = DATA.matches.filter(m => m.played);
  return DATA.players.map((p, i) => {
    const pts     = played.reduce((s, m) => s + (m.preds[p]?.pts || 0), 0);
    const exacts  = played.filter(m => m.preds[p]?.exact).length;
    const correct = played.filter(m => (m.preds[p]?.pts || 0) > 0).length;
    const pct     = played.length ? Math.round(correct / played.length * 100) : 0;
    return { name: p, idx: i, pts, exacts, correct, played: played.length, pct };
  }).sort((a, b) => b.pts - a.pts || b.exacts - a.exacts);
}

// ── Cumulative timeline (for race chart) ─────────────────────
function buildTimeline() {
  const cum = Object.fromEntries(DATA.players.map(p => [p, 0]));
  const tl = [{ match: 0, home: '', away: '', cum: { ...cum } }];
  for (const m of DATA.matches.filter(m => m.played).sort((a, b) => a.id - b.id)) {
    for (const p of DATA.players) cum[p] += (m.preds[p]?.pts || 0);
    tl.push({ match: m.id, home: m.home, away: m.away, cum: { ...cum } });
  }
  return tl;
}

// ── Group standings (computed from match results) ─────────────
function buildGroupStandings() {
  const groups = {};
  const teams  = {};

  for (const m of DATA.matches) {
    const g = m.group;
    if (!g) continue;
    if (!groups[g]) groups[g] = new Set();

    const init = n => teams[n] || (teams[n] = { name: n, gp: g, mp:0, w:0, d:0, l:0, gf:0, ga:0 });
    const h = init(m.home), a = init(m.away);
    groups[g].add(m.home); groups[g].add(m.away);

    if (!m.played) continue;
    h.mp++; a.mp++;
    h.gf += m.actualA; h.ga += m.actualB;
    a.gf += m.actualB; a.ga += m.actualA;
    if (m.actualA > m.actualB)      { h.w++; a.l++; }
    else if (m.actualA < m.actualB) { a.w++; h.l++; }
    else                             { h.d++; a.d++; }
  }

  const result = {};
  for (const [g, teamSet] of Object.entries(groups)) {
    result[g] = [...teamSet].map(n => {
      const t = teams[n] || { name: n, mp:0, w:0, d:0, l:0, gf:0, ga:0 };
      return { ...t, pts: t.w * 3 + t.d, gd: t.gf - t.ga };
    }).sort((a, b) =>
      b.pts - a.pts || b.gd - a.gd || b.gf - a.gf || a.name.localeCompare(b.name)
    );
  }
  return result;
}

// ── Interim "if this score stands" standings for a live match ──
function buildInterimPts(liveMatch) {
  if (!liveMatch.played) return [];
  const current = buildStandings();
  // Simulate with current live score applied
  const intPts = {};
  for (const st of current) intPts[st.name] = st.pts;
  return DATA.players
    .filter(p => (liveMatch.preds[p]?.pts || 0) > 0)
    .map(p => ({ name: p, gain: liveMatch.preds[p].pts }))
    .sort((a, b) => b.gain - a.gain);
}

// ── Live match detection ─────────────────────────────────────
function getLiveMatches() {
  return DATA.matches.filter(m =>
    m.liveStatus === 'LIVE' || m.liveStatus === 'HT'
  );
}

// ── Init & load ──────────────────────────────────────────────
async function init() {
  try {
    const [dataRes, resultsRes] = await Promise.all([
      fetch('./data.json'),
      fetch('./results.json?_=' + Date.now()),
    ]);
    DATA    = await dataRes.json();
    RESULTS = resultsRes.ok ? await resultsRes.json() : null;
  } catch (e) {
    console.error('Load failed:', e);
    toast('⚠️ Datan lataus epäonnistui');
    return;
  }

  applyResults();
  updateHero();
  updateTopbar();
  checkLiveMode();
  buildPanel('standings');
}

async function refreshResults() {
  try {
    const r = await fetch('./results.json?_=' + Date.now());
    if (!r.ok) return;
    RESULTS = await r.json();
    applyResults();

    // Invalidate built panels so they rebuild
    Object.keys(PANELS).forEach(k => delete PANELS[k]);
    document.querySelectorAll('.tabpanel').forEach(el => { el.innerHTML = ''; });

    updateHero();
    updateTopbar();
    checkLiveMode();
    buildPanel(ACTIVE_TAB);
  } catch (e) { /* silent */ }
}

function checkLiveMode() {
  const live = getLiveMatches();
  const liveChip   = $('liveChip');
  const liveBanner = $('liveBanner');

  if (live.length > 0) {
    liveChip.hidden = false;
    liveBanner.hidden = false;
    const names = live.map(m => `${m.home} – ${m.away}`).join(', ');
    $('liveBannerText').textContent = `${live.length} ottelu käynnissä`;
    $('liveBannerSub').textContent  = names;
    // Auto-refresh every 60s while live
    if (!REFRESH_TIMER) {
      REFRESH_TIMER = setInterval(refreshResults, 60000);
    }
  } else {
    liveChip.hidden   = true;
    liveBanner.hidden = true;
    clearInterval(REFRESH_TIMER);
    REFRESH_TIMER = null;
  }
}

function updateHero() {
  const played  = DATA.matches.filter(m => m.played).length;
  const total   = DATA.matches.length;
  const pct     = Math.round(played / total * 100);
  $('progressFill').style.width = pct + '%';
  $('progressText').textContent = `${played} / ${total} ottelua pelattu · ${pct}% ryhmävaiheesta`;

  const st = buildStandings();
  if (st.length) {
    $('leaderName').textContent = st[0].name.toUpperCase();
    $('leaderPts').textContent  = st[0].pts;
  }
}

function updateTopbar() {
  if (!RESULTS?.updated) return;
  const ts = new Date(RESULTS.updated).toLocaleTimeString('fi-FI', { hour: '2-digit', minute: '2-digit' });
  $('updatedPill').textContent = `päivitetty ${ts}`;
}

// ── Tab routing ──────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const t = btn.dataset.tab;
    if (t === ACTIVE_TAB) return;
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tabpanel').forEach(p => { p.hidden = p.id !== 'tab-' + t; });
    ACTIVE_TAB = t;
    if (!PANELS[t]) buildPanel(t);
  });
});

function buildPanel(t) {
  PANELS[t] = true;
  const el = $('tab-' + t);
  const fns = { standings: renderStandings, race: renderRace, matches: renderMatches, groups: renderGroups, awards: renderAwards };
  if (fns[t]) fns[t](el);
}

// ── PANEL: Sarjataulukko ─────────────────────────────────────
function renderStandings(el) {
  const st = buildStandings();
  const medals = ['🥇','🥈','🥉'];
  el.innerHTML = `
    <div class="panel-hd">
      <div class="panel-title">Sarjataulukko</div>
      <div class="panel-sub">${DATA.matches.filter(m=>m.played).length} ottelua pelattu · 2p oikea tulos · 4p tarkka · bonus harvinaisuudesta</div>
    </div>
    <div class="tbl-scroll">
    <table class="lb">
      <thead><tr>
        <th>#</th><th>Pelaaja</th>
        <th class="r">Pisteet</th><th class="r">Tarkat</th>
        <th class="r">Oikein</th><th>Osuvuus</th>
      </tr></thead>
      <tbody>
      ${st.map((s, i) => `
        <tr class="${i===0?'gold':i===st.length-1?'last':''}" data-player="${s.name}" style="cursor:pointer">
          <td><span class="rank-n">${i<3?medals[i]:(i+1)}</span></td>
          <td>
            <div style="display:flex;align-items:center;gap:10px">
              <div class="av" style="background:${AVATAR_COLORS[s.idx]}">${initials(s.name)}</div>
              <span class="pname">${s.name}</span>
            </div>
          </td>
          <td class="r"><span class="pts-chip">${s.pts}</span></td>
          <td class="r"><span class="exacts">✦ ${s.exacts}</span></td>
          <td class="r" style="font-family:var(--font-m);font-size:12px">${s.correct}/${s.played}</td>
          <td>
            <div class="bar-wrap">
              <div class="pct-lbl">${s.pct}%</div>
              <div class="bar-bg"><div class="bar-fg" style="width:${s.pct}%"></div></div>
            </div>
          </td>
        </tr>`).join('')}
      </tbody>
    </table>
    </div>`;

  el.querySelectorAll('tr[data-player]').forEach(row => {
    row.addEventListener('click', () => {
      showPlayerModal(row.dataset.player);
    });
  });
}

// ── PANEL: Race chart ────────────────────────────────────────
function renderRace(el) {
  const tl = buildTimeline();
  const hidden = new Set();
  const W=840, H=400, PL=48, PR=12, PT=24, PB=44;
  const innerW = W-PL-PR, innerH = H-PT-PB;
  const maxY = Math.max(...tl.flatMap(t => DATA.players.map(p => t.cum[p])), 1);

  function xOf(i) { return PL + (tl.length > 1 ? i * innerW/(tl.length-1) : innerW/2); }
  function yOf(v) { return PT + innerH - (v/maxY)*innerH; }

  function buildSVG() {
    const gridLines = Array.from({length:6},(_,i)=>{
      const v = Math.round(maxY*i/5), y = yOf(v);
      return `<line x1="${PL}" x2="${W-PR}" y1="${y}" y2="${y}" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>
              <text x="${PL-6}" y="${y+4}" text-anchor="end" fill="rgba(255,255,255,0.25)" font-size="9" font-family="Space Mono">${v}</text>`;
    }).join('');

    const xLabels = tl.slice(1).map((t,i) =>
      `<text x="${xOf(i+1).toFixed(1)}" y="${H-6}" text-anchor="middle" fill="rgba(255,255,255,0.2)" font-size="8" font-family="Space Mono">${t.match}</text>`
    ).join('');

    const lines = DATA.players.map((p, pi) => {
      if (hidden.has(p)) return '';
      const color = AVATAR_COLORS[pi];
      const d = tl.map((t,i) => (i?'L':'M') + xOf(i).toFixed(1) + ' ' + yOf(t.cum[p]).toFixed(1)).join(' ');
      const lx = xOf(tl.length-1), ly = yOf(tl[tl.length-1].cum[p]);
      return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/>
              <circle cx="${lx}" cy="${ly}" r="3.5" fill="${color}" stroke="var(--pitch)" stroke-width="1.5"/>`;
    }).join('');

    return `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      ${gridLines}
      <line x1="${PL}" x2="${PL}" y1="${PT}" y2="${H-PB}" stroke="rgba(255,255,255,0.1)"/>
      <line x1="${PL}" x2="${W-PR}" y1="${H-PB}" y2="${H-PB}" stroke="rgba(255,255,255,0.1)"/>
      ${xLabels}${lines}
    </svg>`;
  }

  function buildLegend() {
    return buildStandings().map(s => {
      const color = AVATAR_COLORS[s.idx];
      return `<span class="leg-item${hidden.has(s.name)?' off':''}" data-p="${s.name}">
        <span class="leg-swatch" style="background:${color}"></span>
        ${s.name} <span class="leg-pts">${s.pts}p</span>
      </span>`;
    }).join('');
  }

  el.innerHTML = `
    <div class="panel-hd">
      <div class="panel-title">Pistekisa</div>
      <div class="panel-sub">Kumulatiiviset pisteet ottelujärjestyksessä. Klikkaa nimeä piilottaaksesi.</div>
    </div>
    <div class="chart-card">
      <div id="raceChart">${buildSVG()}</div>
      <div class="race-legend" id="raceLeg">${buildLegend()}</div>
    </div>`;

  el.querySelector('#raceLeg').addEventListener('click', e => {
    const item = e.target.closest('.leg-item');
    if (!item) return;
    const p = item.dataset.p;
    if (hidden.has(p)) hidden.delete(p); else hidden.add(p);
    $('raceChart').innerHTML = buildSVG();
    $('raceLeg').innerHTML = buildLegend();
  });
}

// ── PANEL: Ottelut ───────────────────────────────────────────
function renderMatches(el) {
  let fGroup = 'all', fStatus = 'all', fSearch = '';

  function passes(m) {
    if (fGroup !== 'all' && m.group !== fGroup) return false;
    if (fStatus === 'played'   && !m.played) return false;
    if (fStatus === 'upcoming' && m.played)  return false;
    if (fStatus === 'live' && m.liveStatus !== 'LIVE' && m.liveStatus !== 'HT') return false;
    if (fSearch) {
      const q = fSearch.toLowerCase();
      if (!m.home.toLowerCase().includes(q) && !m.away.toLowerCase().includes(q)) return false;
    }
    return true;
  }

  function matchHtml(m) {
    const gc  = GROUP_COLORS[m.group] || '#aaa';
    const hId = TEAM_IDS[m.home], aId = TEAM_IDS[m.away];
    const isLive = m.liveStatus === 'LIVE' || m.liveStatus === 'HT';
    const isHT   = m.liveStatus === 'HT';

    // Score / status badge
    let scoreBadge;
    if (m.played && isLive) {
      scoreBadge = `<span class="score-badge live-score">${m.actualA}–${m.actualB}</span>`;
    } else if (m.played) {
      scoreBadge = `<span class="score-badge">${m.actualA}–${m.actualB}</span>`;
    } else {
      const dt = m.date ? m.date.slice(5).replace('-','/') : '?';
      scoreBadge = `<span class="score-badge upcoming">${dt}</span>`;
    }

    // Live minute badge
    const minBadge = isLive && !isHT && m.minute
      ? `<span class="min-badge">${m.minute}'</span>` : isHT
      ? `<span class="ht-badge">HT</span>` : '';

    // HT score badge (only when LIVE and HT known)
    const htBadge = isLive && m.htHome !== null && m.htAway !== null && !isHT
      ? `<span class="ht-badge">${m.htHome}–${m.htAway} HT</span>` : '';

    // Weather badge (upcoming only)
    let weatherBadge = '';
    if (!m.played && m.weather?.temp != null) {
      const w = m.weather;
      const icon = w.temp > 30 ? '🌡️' : w.temp < 10 ? '🥶' : '⛅';
      weatherBadge = `<span class="weather-badge">${icon} ${Math.round(w.temp)}°</span>`;
    }
     
   // Polymarket Odds Badge (upcoming only)
    let polyBadge = '';
    if (!m.played && m.poly) {
      const ph = m.poly.home ? `${m.poly.home}` : '-';
      const pd = m.poly.draw ? `${m.poly.draw}` : '-';
      const pa = m.poly.away ? `${m.poly.away}` : '-';
      polyBadge = `<span class="poly-badge" title="Polymarket Decimal Odds">📈 ${ph} / ${pd} / ${pa}</span>`;
    }

    // xG badge — real stats for finished, AI-predicted xG for upcoming/live
    let xgBadge = '';
    if (m.xgHome !== null || m.xgAway !== null) {
      const xgH = m.xgHome !== null ? Number(m.xgHome).toFixed(2) : '?';
      const xgA = m.xgAway !== null ? Number(m.xgAway).toFixed(2) : '?';
      // Label as prediction when sourced from AI (no real stats yet)
      const xgLabel = (!m.played || isLive) ? 'xG pred' : 'xG';
      const xgotStr = m.xgotHome !== null && m.xgotAway !== null
        ? ` · xGoT ${Number(m.xgotHome).toFixed(1)}–${Number(m.xgotAway).toFixed(1)}` : '';
      xgBadge = `<span class="xg-badge">${xgLabel} ${xgH}–${xgA}${xgotStr}</span>`;
    }

    // AI prediction — show for upcoming AND live matches, all three outcomes
    let aiBadge = '';
    if ((!m.played || isLive) && m.ai?.prob1 != null) {
      const p1 = normProb(m.ai.prob1);
      const px = normProb(m.ai.probX);
      const p2 = normProb(m.ai.prob2);
      aiBadge = `<span class="ai-badge" title="ML-ennuste: 1=${p1}% X=${px}% 2=${p2}%">
        <span class="ai-label">1:${p1}% X:${px}% 2:${p2}%</span>
        <div class="pred-bar"><div class="p1" style="width:${p1}%"></div><div class="px" style="width:${px}%"></div><div class="p2" style="width:${p2}%"></div></div>
      </span>`;
    }

    // Prediction grid (expanded)
    const preds = DATA.players.map(p => {
      const pr = m.preds[p];
      if (!pr) return '';
      let cls = m.played ? (pr.exact ? 'exact' : pr.pts > 0 ? 'right' : 'wrong') : '';
      const ptsTxt = m.played ? `<span class="pp">${pr.pts}p</span>` : '';
      return `<div class="pc ${cls}">
        <span class="pn">${p}</span>
        <span class="pv">${pr.a}–${pr.b}</span>${ptsTxt}
      </div>`;
    }).join('');

    // Interim "if score stands" for live matches
    let interimHtml = '';
    if (isLive && m.played) {
      const gaining = buildInterimPts(m);
      if (gaining.length) {
        const rows = gaining.slice(0, 5).map(x =>
          `<div class="interim-row gaining">
            <span>${x.name}</span><span>+${x.gain}p nyt</span>
          </div>`
        ).join('');
        interimHtml = `<div class="interim-pts">
          <div class="interim-title">🎯 Pisteitä nyt jos tulos jää</div>
          ${rows}
        </div>`;
      }
    }

    return `<details class="mc${isLive?' is-live':''}">
      <summary>
        <span class="grp-badge" style="color:${gc};background:${gc}22;border:1px solid ${gc}44">L${m.group}</span>
        <div class="teams">
          <div class="tm">${logoImg(hId, m.homeFlag)} <span>${m.home}</span></div>
          <span class="vs">vs</span>
          <div class="tm away">${logoImg(aId, m.awayFlag)} <span>${m.away}</span></div>
        </div>
       ${scoreBadge}${minBadge}${htBadge}${htBadge?'':weatherBadge}${aiBadge}${polyBadge}${xgBadge}
        <span class="chev">▾</span>
      </summary>
      ${interimHtml}
      <div class="pred-grid">${preds}</div>
    </details>`;
  }

  function renderList() {
    const filtered = DATA.matches.filter(passes);
    if (!filtered.length) return `<div class="empty-state">Ei otteluja valituilla suodattimilla ⚽</div>`;

    // Separate live matches to show at top
    const live    = filtered.filter(m => m.liveStatus === 'LIVE' || m.liveStatus === 'HT');
    const others  = filtered.filter(m => m.liveStatus !== 'LIVE' && m.liveStatus !== 'HT');

    const byDate = {};
    for (const m of others) { (byDate[m.date] = byDate[m.date] || []).push(m); }

    const liveSection = live.length ? `
      <div class="date-hd"><span class="live-dot-anim" style="margin-right:2px"></span>Nyt pelaa</div>
      ${live.map(matchHtml).join('')}
    ` : '';

    const datedSection = Object.entries(byDate).sort(([a],[b]) => a < b ? -1 : 1).map(([date, ms]) => `
      <div class="date-hd">${fmtDate(date)}${date===today()?'<span class="today-tag">Tänään</span>':''}</div>
      ${ms.map(matchHtml).join('')}
    `).join('');

    return liveSection + datedSection;
  }

  const groups = [...new Set(DATA.matches.map(m => m.group))].sort();
  el.innerHTML = `
    <div class="panel-hd">
      <div class="panel-title">Ottelut</div>
      <div class="panel-sub">Klikkaa ottelua nähdäksesi kaikkien veikkaukset.</div>
    </div>
    <div class="filter-bar">
      <select id="fGroup"><option value="all">Kaikki lohkot</option>${groups.map(g=>`<option value="${g}">Lohko ${g}</option>`).join('')}</select>
      <select id="fStatus">
        <option value="all">Kaikki ottelut</option>
        <option value="live">🔴 Live</option>
        <option value="played">Pelatut</option>
        <option value="upcoming">Tulevat</option>
      </select>
      <input type="text" id="fSearch" placeholder="Hae joukkue…" autocomplete="off">
    </div>
    <div id="matchList">${renderList()}</div>`;

  $('fGroup').addEventListener('change',  e => { fGroup=e.target.value;  $('matchList').innerHTML=renderList(); });
  $('fStatus').addEventListener('change', e => { fStatus=e.target.value; $('matchList').innerHTML=renderList(); });
  $('fSearch').addEventListener('input',  e => { fSearch=e.target.value.trim(); $('matchList').innerHTML=renderList(); });
}

// ── PANEL: Lohkot (group standings) ──────────────────────────
function renderGroups(el) {
  const groupData = buildGroupStandings();

  const cards = Object.entries(groupData).sort(([a],[b]) => a.localeCompare(b)).map(([g, teams]) => {
    const gc = GROUP_COLORS[g] || '#aaa';
    const totalMatches = DATA.matches.filter(m => m.group === g).length;
    const playedMatches = DATA.matches.filter(m => m.group === g && m.played).length;
    const rows = teams.map((t, i) => {
      const id = TEAM_IDS[t.name];
      const rowClass = i < 2 ? 'qualify' : playedMatches === totalMatches ? 'eliminated' : '';
      return `<tr class="${rowClass}">
        <td>${logoImg(id, '', 'logo')} <span style="font-weight:700">${t.name}</span></td>
        <td>${t.mp}</td>
        <td>${t.w}</td>
        <td>${t.d}</td>
        <td>${t.l}</td>
        <td style="font-family:var(--font-m);color:${t.gd>0?'var(--teal)':t.gd<0?'var(--coral)':'var(--dim)'}">${t.gd>0?'+':''}${t.gd}</td>
        <td class="pts-num">${t.pts}</td>
      </tr>`;
    }).join('');

    return `<div class="group-card">
      <div class="group-card-head" style="border-left:3px solid ${gc}">Lohko ${g} · ${playedMatches}/${totalMatches}</div>
      <table class="group-tbl">
        <thead><tr><th>Joukkue</th><th>P</th><th>V</th><th>T</th><th>H</th><th>MD</th><th>Pts</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }).join('');

  el.innerHTML = `
    <div class="panel-hd">
      <div class="panel-title">Lohkot</div>
      <div class="panel-sub">Lohkotaulukot pelattujen tulosten perusteella. Kaksi ylintä jatkaa.</div>
    </div>
    <div class="groups-grid">${cards}</div>`;
}

// ── PANEL: Palkinnot ─────────────────────────────────────────
function renderAwards(el) {
  const played = DATA.matches.filter(m => m.played);
  const st = buildStandings();
  const P = DATA.players;

  function best(arr, key, max=true) {
    const v = max ? Math.max(...arr.map(x=>x[key])) : Math.min(...arr.map(x=>x[key]));
    return { winners: arr.filter(x=>x[key]===v).map(x=>x.name).join(' & '), val: v };
  }

  const exactArr  = P.map(p => ({ name:p, v: played.filter(m=>m.preds[p]?.exact).length }));
  const zeroArr   = P.map(p => ({ name:p, v: played.filter(m=>(m.preds[p]?.pts||0)===0).length }));
  const rarityArr = P.map(p => ({ name:p, v: played.filter(m=>m.oikein<=4&&(m.preds[p]?.pts||0)>0).reduce((s,m)=>s+(m.preds[p]?.pts||0),0) }));
  const goalArr   = P.map(p => ({ name:p, v: DATA.matches.reduce((s,m)=>s+(m.preds[p]?.a||0)+(m.preds[p]?.b||0),0) }));
  const streakArr = P.map(p => {
    const res = played.sort((a,b)=>a.id-b.id).map(m=>(m.preds[p]?.pts||0)>0);
    let best=0, cur=0;
    for (const r of res) { if(r){cur++;best=Math.max(best,cur);}else cur=0; }
    return { name:p, v:best };
  });

  const awards = [
    { icon:'🏆', cat:'Johtaja', who:st[0].name, val:`${st[0].pts}p · ${st[0].exacts} tarkkaa`, desc:`${st[0].name} johtaa ${st[0].pts} pisteellä. Osuvuus ${st[0].pct}%.` },
    { icon:'🎯', cat:'Tarkka-ampuja', ...best(exactArr,'v'), desc:`Eniten täsmällisiä maalimääräveikkauksia pelatuissa otteluissa.` },
    { icon:'🔥', cat:'Striikki',     ...best(streakArr,'v'), desc:`Pisin peräkkäinen oikean veikkaussarjan putki.` },
    { icon:'💎', cat:'Ball knower', ...best(rarityArr,'v'), desc:`Eniten pisteitä otteluista joissa ≤4/13 sai oikein.` },
    { icon:'🤦', cat:'What is he cooking?', ...best(zeroArr,'v'), desc:`Eniten nollapistematsia — rohkea tai epäonninen.` },
    { icon:'⚽', cat:'Overikingi', ...best(goalArr,'v'), desc:`Ennakoi eniten maaleja kaikissa 72 ottelussa.` },
    { icon:'👴', cat:'Tasuripappa', ...best(goalArr,'v',false), desc:`Ennakoi vähiten maaleja — uskoo tiukkaan torjuntapeliin.` },
    { icon:'🪨', cat:'Häntäpää', who:st[st.length-1].name, val:`${st[st.length-1].pts}p`, desc:`Viimeistä sijaa pitää ${st[st.length-1].name}. Parannettavaa on.` },
  ];

  el.innerHTML = `
    <div class="panel-hd">
      <div class="panel-title">Palkinnot</div>
      <div class="panel-sub">Erityistunnustukset pelattujen otteluiden perusteella.</div>
    </div>
    <div class="awards-grid">
      ${awards.map(a => `
        <div class="award-card">
          <span class="aw-icon">${a.icon}</span>
          <div class="aw-cat">${a.cat}</div>
          <div class="aw-who">${a.winners || a.who}</div>
          <div class="aw-val">${a.val}</div>
          <div class="aw-desc">${a.desc}</div>
        </div>`).join('')}
    </div>`;
}

// ── Player modal ─────────────────────────────────────────────
function showPlayerModal(playerName) {
  const pi    = DATA.players.indexOf(playerName);
  const color = AVATAR_COLORS[pi];
  const played = DATA.matches.filter(m => m.played).sort((a,b)=>a.id-b.id);
  const pts   = played.reduce((s,m)=>s+(m.preds[playerName]?.pts||0),0);

  const rows = played.map(m => {
    const pr = m.preds[playerName];
    if (!pr) return '';
    const cls  = pr.exact ? 'exact' : pr.pts > 0 ? 'right' : 'wrong';
    const icon = pr.exact ? '✦' : pr.pts > 0 ? '✓' : '✗';
    return `<div class="pc ${cls}" style="min-width:0">
      <span class="pn">${m.homeFlag} ${m.home} – ${m.away} ${m.awayFlag}</span>
      <span class="pv">${icon} ${pr.a}–${pr.b}</span>
      <span class="pp">${pr.pts}p</span>
    </div>`;
  }).join('');

  const upcoming = DATA.matches.filter(m=>!m.played).slice(0,8).map(m => {
    const pr = m.preds[playerName];
    return pr ? `<div class="pc" style="min-width:0;opacity:.65">
      <span class="pn">${m.homeFlag} ${m.home} – ${m.away} ${m.awayFlag}</span>
      <span class="pv" style="color:var(--dim)">${pr.a}–${pr.b}</span>
    </div>` : '';
  }).join('');

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:200;display:flex;align-items:flex-start;justify-content:center;padding:32px 16px;overflow-y:auto;';
  overlay.innerHTML = `
    <div style="background:var(--surface);border:1px solid ${color};border-radius:var(--r);max-width:680px;width:100%;padding:26px;position:relative;animation:fadeIn .25s ease">
      <button id="mClose" style="position:absolute;top:14px;right:15px;background:none;border:none;color:var(--dim);font-size:18px;cursor:pointer;line-height:1">✕</button>
      <div style="display:flex;align-items:center;gap:13px;margin-bottom:18px">
        <div class="av" style="background:${color};width:50px;height:50px;font-size:17px">${initials(playerName)}</div>
        <div>
          <div style="font-family:var(--font-d);font-size:22px;text-transform:uppercase;letter-spacing:.03em">${playerName}</div>
          <div style="font-family:var(--font-m);font-size:11px;color:var(--dim)">${pts} pistettä · ${played.filter(m=>m.preds[playerName]?.exact).length} tarkkaa</div>
        </div>
      </div>
      <div style="font-family:var(--font-m);font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);margin:0 0 8px">Pelatut</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:5px;margin-bottom:18px">
        ${rows || '<p style="color:var(--faint)">Ei pelattuja otteluja vielä.</p>'}
      </div>
      <div style="font-family:var(--font-m);font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);margin:0 0 8px">Tulevat</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:5px">${upcoming}</div>
    </div>`;

  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  overlay.querySelector('#mClose').addEventListener('click', close);
  document.addEventListener('keydown', function esc(e) { if(e.key==='Escape'){close();document.removeEventListener('keydown',esc);} });
}

// ── Boot ─────────────────────────────────────────────────────
init();
