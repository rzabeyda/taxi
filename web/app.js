const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : { initData: '', ready() {}, expand() {} };
tg.ready(); tg.expand();
try { tg.setHeaderColor('#000000'); tg.setBackgroundColor('#000000'); tg.disableVerticalSwipes && tg.disableVerticalSwipes(); } catch (e) {}

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ls = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) {} return null; };
const carLine = (x) => [x.car, x.color, x.year].filter(Boolean).join(', ');
const tgUser = () => (tg.initDataUnsafe && tg.initDataUnsafe.user) || {};
function avatar(url, name) {
  const u = url || tgUser().photo_url;
  const ini = esc((name || '?').trim().charAt(0).toUpperCase());
  return u ? `<img class="avatar" src="${esc(u)}" onerror="this.outerHTML='<div class=\'avatar\'>${ini}</div>'">` : `<div class="avatar">${ini}</div>`;
}
const eur = (v) => (Math.round(Number(v || 0) * 100) / 100).toString().replace('.', ',') + ' €';
const dt = (ts) => new Date(ts * 1000).toLocaleString('ru', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

const IC = {
  car: '<svg viewBox="0 0 24 24"><path d="M5 16l1.6-5.4A2 2 0 0 1 8.5 9h7a2 2 0 0 1 1.9 1.6L19 16"/><rect x="3" y="16" width="18" height="4" rx="1.5"/><path d="M7 18h.01M17 18h.01"/></svg>',
  list: '<svg viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/></svg>',
  user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/></svg>',
  wallet: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="14" rx="3"/><path d="M3 10h18M16 15h2"/></svg>',
};
const TABS = {
  client: [['Заказ', IC.car], ['Поездки', IC.list], ['Профиль', IC.user]],
  driver: [['Заказы', IC.car], ['Поездки', IC.list], ['Профиль', IC.user]],
};

let S = { role: ls('role') || 'client', tab: 0, st: null, key: '', off: 0, eta: 10 };

async function api(path, body) {
  const r = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Init-Data': tg.initData },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Ошибка');
  return j;
}
function toast(m) {
  const t = $('#toast'); t.textContent = m; t.classList.remove('hidden');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add('hidden'), 2600);
}
async function act(fn) { try { await fn(); } catch (e) { toast(e.message); } await refresh(true); }
function ask(text) {
  return new Promise((res) => {
    if (tg.showConfirm) tg.showConfirm(text, res); else res(confirm(text));
  });
}
function readImg(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onerror = rej;
    r.onload = () => {
      const img = new Image();
      img.onerror = rej;
      img.onload = () => {
        const k = Math.min(1, 800 / Math.max(img.width, img.height));
        const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        res(c.toDataURL('image/jpeg', 0.8));
      };
      img.src = r.result;
    };
    r.readAsDataURL(file);
  });
}
function tgLink(u) { return u && u.username ? 'https://t.me/' + u.username : u ? 'tg://user?id=' + u.id : '#'; }

/* ---------------------------------------------------------------- chrome */
function drawChrome() {
  document.querySelectorAll('#roleSwitch button').forEach((b) => b.classList.toggle('on', b.dataset.role === S.role));
  $('#title').textContent = TABS[S.role][S.tab][0];
  $('#nav').innerHTML = TABS[S.role].map((t, i) => `<button data-i="${i}" class="${i === S.tab ? 'on' : ''}">${t[1]}<span>${t[0]}</span></button>`).join('');
}
$('#roleSwitch').onclick = (e) => {
  const r = e.target.dataset.role; if (!r || r === S.role) return;
  S.role = r; S.tab = 0; ls('role', r); S.key = ''; drawChrome(); refresh(true);
};
$('#nav').onclick = (e) => {
  const b = e.target.closest('button'); if (!b) return;
  S.tab = +b.dataset.i; S.key = ''; drawChrome(); render(true);
};

/* ---------------------------------------------------------------- data */
async function refresh(force) {
  try {
    const st = await api('state?role=' + S.role);
    S.off = st.now - Math.floor(Date.now() / 1000);
    S.st = st;
    render(force);
    syncGeo();
  } catch (e) {
    if (!S.st) $('#view').innerHTML = `<div class="card center muted">${esc(e.message)}</div>`;
  }
}
const nowTs = () => Math.floor(Date.now() / 1000) + S.off;

function viewKey() {
  const st = S.st, k = { r: S.role, t: S.tab };
  if (S.role === 'client') {
    if (S.tab === 0) k.o = st.client_order; else k.o = 0;
  } else {
    k.d = st.me.is_driver;
    if (S.tab === 0) { k.o = st.driver_order; k.on = st.me.online; k.b = st.me.blocked; k.l = (st.open_orders || []).map((o) => o.id + ':' + o.price); }
    else k.debt = st.me.debt;
  }
  return JSON.stringify(k);
}
async function render(force) {
  if (!S.st) return;
  const key = viewKey();
  if (!force && key === S.key) return;
  S.key = key;
  drawChrome();
  const v = $('#view');
  try {
    let html;
    if (S.role === 'client') html = await [clientOrder, clientHistory, clientCabinet][S.tab]();
    else if (!S.st.me.is_driver) html = S.tab === 0 ? driverRegister() : '<div class="card center muted">Сначала зарегистрируйтесь на вкладке «Заказы»</div>';
    else html = await [driverOrders, driverTrips, driverCabinet][S.tab]();
    if (key !== S.key) return; // superseded
    v.innerHTML = html;
    bind();
    tick();
  } catch (e) { v.innerHTML = `<div class="card center muted">${esc(e.message)}</div>`; }
}

/* ---------------------------------------------------------------- shared pieces */
const routeHtml = (o) => `<div class="route"><div><span class="dot"></span><span>${esc(o.from_text)}</span></div><div><span class="dot b"></span><span>${esc(o.to_text)}</span></div></div>`;
const kmTag = (o) => (o.km ? `<span class="tag">${o.km} км</span>` : '');
function etaText(o) {
  if (!o.accepted_at || !o.eta_min) return '';
  if (o.picked_at) return 'Едем к месту назначения';
  if (o.arrived_at) return 'Водитель на месте';
  const left = Math.ceil((o.accepted_at + o.eta_min * 60 - nowTs()) / 60);
  return left > 0 ? `Будет через ${left} мин` : 'Уже подъезжает';
}
function tick() { document.querySelectorAll('[data-eta]').forEach((el) => { const o = JSON.parse(el.dataset.eta); el.textContent = etaText(o); }); }
setInterval(tick, 5000);

/* ---------------------------------------------------------------- client */
async function clientOrder() {
  const o = S.st.client_order;
  if (o && o.status === 'done' && ls('cd' + o.id)) return clientForm();
  if (!o) return clientForm();
  if (o.status === 'open') {
    return `<div class="card glow">
      <div class="row"><span class="muted pulse">Ищем водителя…</span>${kmTag(o)}</div>
      ${routeHtml(o)}
      <div class="big">${eur(o.price)}</div>
      <div class="muted small-t">Повысьте цену, чтобы заказ приняли быстрее</div>
    </div>
    <div class="grid2"><button class="btn" data-raise="1">+1 €</button><button class="btn" data-raise="2">+2 €</button></div>
    <div style="height:10px"></div>
    <button class="btn danger" data-cancel="${o.id}">Отменить заказ</button>`;
  }
  if (o.status === 'accepted') {
    const d = o.driver;
    return `<div class="card glow">
      <div class="muted small-t">Водитель принял заказ</div>
      <div class="big" data-eta='${esc(JSON.stringify({ accepted_at: o.accepted_at, eta_min: o.eta_min, arrived_at: o.arrived_at, picked_at: o.picked_at }))}' style="font-size:26px">${etaText(o)}</div>
      <div class="row" style="margin-top:12px;justify-content:flex-start;gap:12px">${d.driver_photo ? `<img class="ph round" src="${esc(d.driver_photo)}">` : ''}<div class="name">${esc(d.name)}</div></div>
      ${d.car_photo ? `<img class="ph" src="${esc(d.car_photo)}">` : ''}
      <div class="muted">${esc(carLine(d))}${d.username ? ' · @' + esc(d.username) : ''}</div>
      <div class="big" style="font-size:28px;letter-spacing:2px">${esc(d.plate)}</div>
    </div>
    <div class="card">${routeHtml(o)}<div class="row"><b>${eur(o.price)}</b>${kmTag(o)}</div></div>
    <a class="btn" href="${tgLink(d)}" target="_blank">Написать водителю</a>
    ${d.phone ? `<div style="height:10px"></div><a class="btn" href="tel:${esc(d.phone)}">Позвонить</a>` : ''}
    ${o.picked_at ? '' : `<div style="height:10px"></div><button class="btn danger" data-cancel="${o.id}">Отменить заказ</button>`}`;
  }
  // done
  return `<div class="card glow center"><div class="muted">Поездка завершена</div><div class="big">${eur(o.price)}</div>
    <div class="muted">${esc(o.driver.name)} · ${esc(carLine(o.driver))} ${esc(o.driver.plate)}</div></div>
    <button class="btn gold" data-fav="${o.driver.id}" data-on="${o.fav ? 0 : 1}">${o.fav ? '★ Убрать из избранного' : '☆ Добавить водителя в избранное'}</button>
    <div style="height:10px"></div><button class="btn" data-dismiss="${o.id}">Готово</button>`;
}
async function clientForm() {
  const favs = await api('favorites').catch(() => []);
  const last = JSON.parse(ls('last') || '{}');
  return `<div class="card">
    <label>Откуда</label><input id="f_from" placeholder="Linnamäe tee 24, Tallinn" value="${esc(last.from || '')}" autocomplete="off">
    <div style="height:10px"></div>
    <label>Куда</label><input id="f_to" placeholder="Raadiku 19, Tallinn" value="${esc(last.to || '')}" autocomplete="off">
    <div style="height:14px"></div>
    <label>Ваша цена</label>
    <div class="stepper"><button class="btn" id="p_minus">−</button><input id="f_price" type="number" inputmode="decimal" min="1" step="0.5" value="${last.price || 5}"><button class="btn" id="p_plus">+</button></div>
    ${favs.length ? `<div style="height:14px"></div><label>Кому отправить</label><select id="f_target"><option value="">Любому водителю</option>${favs.map((f) => `<option value="${f.id}">★ ${esc(f.name)} · ${esc(f.car)}${f.online ? ' · на линии' : ''}</option>`).join('')}</select>` : ''}
  </div>
  <button class="btn" id="f_ok" style="font-size:18px;padding:16px">OK</button>`;
}

async function clientHistory() {
  const rows = await api('history?role=client');
  if (!rows.length) return '<div class="card center muted">Поездок пока нет</div>';
  return rows.map((o) => `<div class="card"><div class="row"><span class="muted small-t">${dt(o.done_at)}</span><b>${eur(o.price)}</b></div>
    ${routeHtml(o)}<div class="row"><span class="muted small-t">${esc(o.driver.name)} · ${esc(carLine(o.driver))} ${esc(o.driver.plate)}</span>
    <button class="btn sm gold" style="width:auto" data-fav="${o.driver.id}" data-on="${o.fav ? 0 : 1}">${o.fav ? '★' : '☆'}</button></div></div>`).join('');
}
async function clientCabinet() {
  const [rows, favs] = await Promise.all([api('history?role=client'), api('favorites')]);
  const spent = rows.reduce((a, o) => a + o.price, 0);
  return `<div class="card glow"><div class="row" style="justify-content:flex-start;gap:14px">${avatar(null, S.st.me.name)}<div><div class="name" style="font-size:20px">${esc(S.st.me.name)}</div>${S.st.me.username ? '<div class="muted">@' + esc(S.st.me.username) + '</div>' : ''}</div></div></div>
    <div class="stats"><div class="stat"><span class="muted small-t">Поездок</span><b>${rows.length}</b></div>
    <div class="stat"><span class="muted small-t">Потрачено</span><b>${eur(spent)}</b></div></div>
    <div class="muted small-t" style="margin:16px 4px 0">Избранные водители</div>
    ${favs.length ? favs.map((f) => `<div class="card"><div class="row"><div><div class="name">${esc(f.name)}</div><div class="muted small-t">${esc(carLine(f))} · ${esc(f.plate)}</div></div>
    <div class="col" style="align-items:flex-end"><span class="tag ${f.online ? 'green' : ''}">${f.online ? 'на линии' : 'не на линии'}</span>
    <button class="btn sm danger" style="width:auto" data-fav="${f.id}" data-on="0">Убрать</button></div></div></div>`).join('')
    : '<div class="card center muted">Добавьте водителя в избранное после поездки</div>'}`;
}

/* ---------------------------------------------------------------- driver */
function driverRegister() {
  const un = S.st.me.username;
  return `<div class="card"><div class="name">Стать водителем</div>
    <div class="${un ? 'muted' : 'red'} small-t" style="margin:6px 0">${un ? 'Ваш Telegram: @' + esc(un) + ' (клиент сможет вам написать)' : 'У вас нет @username. Задайте его в настройках Telegram и откройте приложение заново.'}</div><div class="muted small-t" style="margin:6px 0 12px">Данные увидит клиент после принятия заказа.</div>
    <label>Имя</label><input id="r_first" placeholder="Иван" autocomplete="given-name">
    <div style="height:10px"></div><label>Фамилия</label><input id="r_last" placeholder="Иванов" autocomplete="family-name">
    <div style="height:10px"></div><label>Марка и модель</label><input id="r_car" placeholder="Toyota Prius">
    <div class="grid2" style="margin-top:10px"><div><label>Цвет</label><input id="r_color" placeholder="белый"></div>
    <div><label>Год</label><input id="r_year" type="number" inputmode="numeric" placeholder="2015"></div></div>
    <div style="height:10px"></div><label>Номер авто</label><input id="r_plate" placeholder="123 ABC" style="text-transform:uppercase">
    <div style="height:10px"></div><label>Телефон (по желанию)</label><input id="r_phone" type="tel" placeholder="+372 …">
    <div class="grid2" style="margin-top:14px">
      <label class="btn sm ghost" style="margin:0">Фото машины<br><span class="small-t" id="n_car">по желанию</span><input type="file" accept="image/*" id="r_carph" hidden></label>
      <label class="btn sm ghost" style="margin:0">Ваше фото<br><span class="small-t" id="n_drv">по желанию</span><input type="file" accept="image/*" id="r_drvph" hidden></label></div></div>
  <button class="btn" id="r_ok">Зарегистрироваться</button>`;
}

async function driverOrders() {
  const st = S.st, me = st.me, o = st.driver_order;
  let h = '';
  if (o && o.status === 'done') {
    if (!ls('dd' + o.id)) {
      return `<div class="card glow center"><div class="muted">Поездка завершена</div><div class="big">${eur(o.price)}</div>
        <div class="muted">Комиссия ${eur(o.commission)} · вам ${eur(o.net)}</div></div>
        <button class="btn" data-ddismiss="${o.id}">Готово</button>`;
    }
  }
  if (o && o.status === 'accepted') return driverActive(o);
  h += `<div class="card"><div class="row"><div><div class="name">${me.online ? 'На линии' : 'Не на линии'}</div>
    <div class="muted small-t">${me.online ? 'Вам приходят новые заказы' : 'Включите, чтобы получать заказы'}</div></div>
    <div class="switch ${me.online ? 'on' : ''}" id="sw"></div></div></div>`;
  if (me.blocked) return h + `<div class="card glow"><div class="name red">Приём заказов заблокирован</div><div class="muted" style="margin-top:6px">Долг ${eur(me.debt)}. Оплатите комиссию во вкладке «Баланс».</div></div>`;
  if (!me.online) return h;
  const list = st.open_orders || [];
  if (!list.length) return h + '<div class="card center muted pulse">Ждём новые заказы…</div>';
  return h + list.map((x) => `<div class="card glow" data-open="${x.id}" style="cursor:pointer">
    <div class="row"><b style="font-size:24px">${eur(x.price)}</b>${kmTag(x)}</div>${routeHtml(x)}
    <div class="muted small-t">${dt(x.created)}${x.client ? ' · ' + esc(x.client.name) : ''}</div></div>`).join('');
}
function driverActive(o) {
  const c = o.client;
  const net = Math.round((o.price - Math.max(0.5, o.price * 0.1)) * 100) / 100;
  return `<div class="card glow">
    <div class="row"><span class="tag">Активный заказ</span>${kmTag(o)}</div>
    <div class="big" style="font-size:26px;margin-top:6px" data-eta='${esc(JSON.stringify({ accepted_at: o.accepted_at, eta_min: o.eta_min, arrived_at: o.arrived_at, picked_at: o.picked_at }))}'>${etaText(o)}</div>
    ${routeHtml(o)}
    <div class="row"><b style="font-size:24px">${eur(o.price)}</b><span class="muted small-t">вам ≈ ${eur(net)}</span></div>
    <div class="muted small-t" style="margin-top:6px">Клиент: ${esc(c.name)}${c.username ? ' · @' + esc(c.username) : ''}</div>
  </div>
  <div class="grid2">
    <a class="btn sm" href="${o.nav.gm_full}" target="_blank">Google: весь маршрут</a>
    <a class="btn sm" href="${o.nav.gm_pickup}" target="_blank">Google: к клиенту</a>
    <a class="btn sm" href="${o.nav.waze_pickup}" target="_blank">Waze: к клиенту</a>
    <a class="btn sm" href="${o.nav.waze_dest}" target="_blank">Waze: до места</a>
  </div>
  <div style="height:10px"></div><a class="btn sm" href="${tgLink(c)}" target="_blank">Написать клиенту</a>
  <div style="height:10px"></div>
  ${o.picked_at ? '' : `<button class="btn" data-picked="${o.id}">Клиент в машине</button><div style="height:10px"></div>`}
  <button class="btn gold" data-complete="${o.id}">Завершить поездку</button>
  ${o.picked_at ? '' : `<div style="height:10px"></div><button class="btn danger" data-release="${o.id}">Отказаться от заказа</button>`}
  <div class="muted small-t center" style="padding:14px 0">Поездка завершится сама, когда вы приедете на место (держите приложение открытым).</div>`;
}

async function driverTrips() {
  const hist = await api('history?role=driver');
  return hist.length ? hist.map((o) => `<div class="card"><div class="row"><span class="muted small-t">${dt(o.done_at)}</span><b>${eur(o.price)}</b></div>${routeHtml(o)}
      <div class="muted small-t">Комиссия ${eur(o.commission)} · вам ${eur(o.net)}</div></div>`).join('') : '<div class="card center muted">Поездок пока нет</div>';
}
async function driverCabinet() {
  const s = await api('driver/stats'), me = S.st.me;
  return `<div class="card glow"><div class="row" style="justify-content:flex-start;gap:14px">${avatar(me.driver_photo, me.name)}<div><div class="name" style="font-size:20px">${esc(me.name)}</div>
    ${me.username ? '<div class="muted">@' + esc(me.username) + '</div>' : ''}<div class="muted small-t">${esc(carLine(me))} · ${esc(me.plate)}</div></div></div></div>
    <div class="stats">
    <div class="stat"><span class="muted small-t">Заработано</span><b class="green">${eur(s.earned)}</b></div>
    <div class="stat"><span class="muted small-t">Комиссия сервису</span><b class="gold">${eur(s.commission)}</b></div>
    <div class="stat"><span class="muted small-t">Оборот</span><b>${eur(s.gross)}</b></div>
    <div class="stat"><span class="muted small-t">Поездок</span><b>${s.count}</b></div>
    <div class="stat"><span class="muted small-t">Проехано</span><b>${s.km} км</b></div>
    <div class="stat"><span class="muted small-t">Дней работает</span><b>${s.work_days}</b><span class="muted small-t">с нами ${s.since_days} дн.</span></div></div>
    <div class="muted small-t" style="margin:16px 4px 0">Баланс</div>
    ${await driverBalance()}`;
}
async function driverBalance() {
  const s = await api('driver/stats');
  const pct = Math.min(100, Math.round((s.debt / s.limit) * 100));
  return `<div class="card glow"><div class="muted small-t">Долг по комиссии</div><div class="big ${s.debt >= s.limit ? 'red' : ''}">${eur(s.debt)}</div>
    <div class="bar ${pct >= 80 ? 'warn' : ''}" style="margin:10px 0"><i style="width:${pct}%"></i></div>
    <div class="muted small-t">Лимит ${eur(s.limit)}: при достижении приём заказов блокируется до оплаты.<br>Комиссия: ${s.pct}% с поездки, минимум ${eur(s.min)}.</div></div>
    <div class="card"><div class="name">Как оплатить</div><div class="muted" style="margin-top:6px;white-space:pre-line">${esc(s.pay_info)}</div></div>
    ${s.pending ? `<div class="card center muted">Заявка на ${eur(s.pending)} отправлена, ждите подтверждения</div>` : ''}
    <button class="btn" id="pay_ok" ${s.debt <= 0 || s.pending ? 'disabled' : ''}>Я оплатил ${s.debt > 0 ? eur(s.debt) : ''}</button>
    ${s.payments.length ? `<div class="muted small-t" style="margin:16px 4px 0">Оплаты</div>` + s.payments.map((p) => `<div class="card row"><span class="muted">${dt(p.created)}</span><b class="green">${eur(p.amount)}</b></div>`).join('') : ''}`;
}

/* ---------------------------------------------------------------- order sheet (driver) */
async function openOrder(id) {
  let o;
  try { o = await api('order/' + id); } catch (e) { return toast(e.message); }
  if (o.status !== 'open') return toast('Заказ уже занят');
  S.eta = 10;
  const m = $('#modal');
  m.innerHTML = `<div class="sheet">
    <div class="row"><b style="font-size:28px">${eur(o.price)}</b>${kmTag(o)}</div>${routeHtml(o)}
    <div class="muted small-t">Вам останется ≈ ${eur(o.price - Math.max(0.5, o.price * 0.1))}</div>
    <div class="grid2" style="margin:12px 0"><a class="btn sm" href="${o.nav.gm_route}" target="_blank">Маршрут Google</a><a class="btn sm" href="${o.nav.waze_pickup}" target="_blank">Waze: к клиенту</a></div>
    <label>Через сколько минут будете у клиента?</label>
    <div class="chips" id="etas">${[3, 5, 10, 15, 20].map((n) => `<button class="chip ${n === 10 ? 'on' : ''}" data-n="${n}">${n}</button>`).join('')}<input id="eta_c" type="number" inputmode="numeric" placeholder="свои" style="width:90px;padding:10px"></div>
    <div style="height:14px"></div>
    <button class="btn" id="acc">Принять заказ</button>
    <div style="height:10px"></div><button class="btn ghost" id="cl">Закрыть</button></div>`;
  m.classList.remove('hidden');
  m.onclick = (e) => { if (e.target === m) m.classList.add('hidden'); };
  $('#cl').onclick = () => m.classList.add('hidden');
  $('#etas').onclick = (e) => {
    const b = e.target.closest('.chip'); if (!b) return;
    document.querySelectorAll('#etas .chip').forEach((c) => c.classList.remove('on')); b.classList.add('on');
    S.eta = +b.dataset.n; $('#eta_c').value = '';
  };
  $('#eta_c').oninput = (e) => { document.querySelectorAll('#etas .chip').forEach((c) => c.classList.remove('on')); S.eta = +e.target.value; };
  $('#acc').onclick = async () => {
    try { await api('order/' + id + '/accept', { eta: S.eta }); m.classList.add('hidden'); toast('Заказ ваш'); }
    catch (e) { toast(e.message); m.classList.add('hidden'); }
    refresh(true);
  };
}

/* ---------------------------------------------------------------- bindings */
function bind() {
  const v = $('#view');
  const on = (sel, fn) => v.querySelectorAll(sel).forEach((el) => (el.onclick = () => fn(el)));
  const o = S.st.client_order;
  on('[data-raise]', (el) => act(() => api('order/' + o.id + '/raise', { delta: +el.dataset.raise })));
  on('[data-cancel]', async (el) => { if (await ask('Отменить заказ?')) act(() => api('order/' + el.dataset.cancel + '/cancel', {})); });
  on('[data-dismiss]', (el) => { ls('cd' + el.dataset.dismiss, '1'); S.key = ''; render(true); });
  on('[data-ddismiss]', (el) => { ls('dd' + el.dataset.ddismiss, '1'); S.key = ''; render(true); });
  on('[data-fav]', (el) => act(() => api('favorites', { driver_id: +el.dataset.fav, on: el.dataset.on === '1' })));
  on('[data-open]', (el) => openOrder(+el.dataset.open));
  on('[data-picked]', (el) => act(() => api('order/' + el.dataset.picked + '/picked', {})));
  on('[data-complete]', async (el) => { if (await ask('Завершить поездку?')) act(() => api('order/' + el.dataset.complete + '/complete', {})); });
  on('[data-release]', async (el) => { if (await ask('Отказаться от заказа? Он вернётся в общий список.')) act(() => api('order/' + el.dataset.release + '/release', {})); });
  const sw = $('#sw'); if (sw) sw.onclick = () => act(() => api('driver/online', { online: !S.st.me.online }));
  const ok = $('#f_ok');
  if (ok) {
    const price = $('#f_price');
    $('#p_minus').onclick = () => (price.value = Math.max(1, (+price.value || 5) - 1));
    $('#p_plus').onclick = () => (price.value = (+price.value || 5) + 1);
    ok.onclick = () => act(async () => {
      const body = { from: $('#f_from').value, to: $('#f_to').value, price: +price.value, target_driver_id: $('#f_target') ? $('#f_target').value || null : null };
      await api('order', body);
      ls('last', JSON.stringify({ from: body.from, to: body.to, price: body.price }));
    });
  }
  const rk = $('#r_ok');
  if (rk) {
    const ph = {};
    [['r_carph', 'n_car', 'car_photo'], ['r_drvph', 'n_drv', 'driver_photo']].forEach(([i, n, k]) => {
      $('#' + i).onchange = async (e) => { const f = e.target.files[0]; if (!f) return; try { ph[k] = await readImg(f); $('#' + n).textContent = 'выбрано ✓'; } catch (x) { toast('Не удалось прочитать фото'); } };
    });
    rk.onclick = () => act(() => api('driver/register', { name: $('#r_first').value.trim() + ' ' + $('#r_last').value.trim(), car: $('#r_car').value, color: $('#r_color').value, year: $('#r_year').value, plate: $('#r_plate').value, phone: $('#r_phone').value, car_photo: ph.car_photo, driver_photo: ph.driver_photo }));
  }
  const pk = $('#pay_ok');
  if (pk) pk.onclick = () => act(async () => { await api('driver/pay', {}); toast('Заявка отправлена'); });
}

/* ---------------------------------------------------------------- geolocation (driver) */
let geoWatch = null, lastSent = 0;
function syncGeo() {
  const o = S.st && S.st.driver_order;
  const need = o && o.status === 'accepted';
  if (need && geoWatch === null && navigator.geolocation) {
    geoWatch = navigator.geolocation.watchPosition((p) => {
      const t = Date.now();
      if (t - lastSent < 8000) return;
      lastSent = t;
      api('location', { lat: p.coords.latitude, lng: p.coords.longitude }).catch(() => {});
    }, () => {}, { enableHighAccuracy: true, maximumAge: 5000 });
  } else if (!need && geoWatch !== null) {
    navigator.geolocation.clearWatch(geoWatch); geoWatch = null;
  }
}

/* ---------------------------------------------------------------- boot */
(async function init() {
  drawChrome();
  await refresh(true);
  const q = new URLSearchParams(location.search).get('o') || (tg.initDataUnsafe && tg.initDataUnsafe.start_param);
  if (q && S.st) {
    try {
      const o = await api('order/' + parseInt(q, 10));
      if (!o.mine_client && S.st.me.is_driver && o.status === 'open') {
        S.role = 'driver'; S.tab = 0; ls('role', 'driver'); S.key = ''; drawChrome(); await refresh(true); openOrder(o.id);
      } else if (!o.mine_client && !S.st.me.is_driver && o.status === 'open') {
        S.role = 'driver'; S.tab = 0; S.key = ''; drawChrome(); render(true);
      } else if (o.driver && o.driver.id === S.st.me.id) {
        S.role = 'driver'; S.tab = 0; S.key = ''; drawChrome(); render(true);
      }
    } catch (e) {}
  }
  setInterval(() => refresh(false), 3000);
})();
