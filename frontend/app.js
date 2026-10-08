import { h, render } from "preact";
import { useState, useEffect, useCallback, useRef } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);
const API = "/api/v1";

const STATUS = {
  draft: "مسودة", ai_generated: "بدون تصميم", design_pending: "جارٍ التصميم", design_ready: "التصميم جاهز",
  pending_approval: "بانتظار الموافقة", approved: "معتمد", rejected: "مرفوض", archived: "مؤرشف",
};
const PLATFORMS = { instagram: "إنستغرام", facebook: "فيسبوك", tiktok: "تيك توك", linkedin: "لينكدإن", x: "إكس" };
const OBJECTIVES = { increase_orders: "زيادة الطلبات", brand_awareness: "الوعي بالعلامة", new_product: "إطلاق منتج جديد", engagement: "رفع التفاعل" };

let toastSetter = () => {};
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    toastSetter(detail);
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function useFetch(path, deps = []) {
  const [data, setData] = useState(null);
  const [tick, setTick] = useState(0);
  useEffect(() => { let live = true; api(path).then((d) => live && setData(d)).catch(() => {}); return () => { live = false; }; }, [path, tick, ...deps]);
  return [data, () => setTick((t) => t + 1), setData];
}

function useHash() {
  const [hash, setHash] = useState(location.hash || "#/");
  useEffect(() => { const f = () => setHash(location.hash || "#/"); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f); }, []);
  return hash.slice(1).split("/").filter(Boolean);
}

const LOC = "ar-SA-u-ca-gregory-nu-latn";
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (d, n) => new Date(d.getTime() + n * 864e5);
const arDate = (s) => new Date(s + "T00:00:00Z").toLocaleDateString(LOC, { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });

function Badge({ status }) { return html`<span class=${"badge s-" + status}>${STATUS[status] || status}</span>`; }
function Busy({ text }) { return html`<span class="muted"><span class="spin"></span>${text}</span>`; }

/* ---------- Projects ---------- */
function Projects() {
  const [list, reload] = useFetch("/projects");
  const [name, setName] = useState("");
  const create = async (e) => {
    e.preventDefault();
    const p = await api("/projects", { method: "POST", body: { name } });
    location.hash = `#/p/${p.id}/brand`;
  };
  return html`<h1>المشاريع</h1>
    <form class="card row" onSubmit=${create}>
      <div class="field"><label>اسم مشروع جديد</label><input required value=${name} onInput=${(e) => setName(e.target.value)} placeholder="مثال: مطاعم المذاق" /></div>
      <button class="primary">إنشاء مشروع</button>
    </form>
    ${list === null ? html`<${Busy} text="تحميل..." />` : list.length === 0 ? html`<p class="muted">لا توجد مشاريع بعد.</p>` : html`
      <div class="grid">${list.map((p) => html`<a class="card" style="text-decoration:none;color:inherit" href=${"#/p/" + p.id}><h2>${p.name}</h2><span class="muted">${p.timezone}</span></a>`)}</div>`}`;
}

/* ---------- Project shell ---------- */
function Project({ id, tab }) {
  const [project] = useFetch(`/projects/${id}`);
  const tabs = [["campaigns", "الحملات"], ["calendar", "التقويم"], ["brand", "هوية العلامة"]];
  const cur = tab || "campaigns";
  return html`<h1>${project ? project.name : "..."}</h1>
    <div class="tabs">${tabs.map(([k, l]) => html`<button class=${cur === k ? "on" : ""} onClick=${() => (location.hash = `#/p/${id}/${k}`)}>${l}</button>`)}</div>
    ${cur === "campaigns" && html`<${Campaigns} pid=${id} />`}
    ${cur === "calendar" && html`<${Calendar} pid=${id} />`}
    ${cur === "brand" && html`<${BrandBrain} pid=${id} />`}`;
}

/* ---------- Campaigns ---------- */
function Campaigns({ pid }) {
  const [list, reload] = useFetch(`/projects/${pid}/campaigns`);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ objective: "increase_orders", duration_days: 7, post_count: 5, platforms: ["instagram"], tone: "", brief: "", start_date: iso(new Date()) });
  const set = (k, v) => setF((o) => ({ ...o, [k]: v }));
  const togglePlat = (p) => set("platforms", f.platforms.includes(p) ? f.platforms.filter((x) => x !== p) : [...f.platforms, p]);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const body = { ...f, duration_days: +f.duration_days, post_count: +f.post_count, tone: f.tone || null };
      const c = await api(`/projects/${pid}/campaigns`, { method: "POST", body });
      location.hash = `#/p/${pid}/c/${c.id}`;
    } catch {} finally { setBusy(false); }
  };
  return html`<form class="card" onSubmit=${submit}>
      <h2>إنشاء حملة</h2>
      <div class="row">
        <div class="field"><label>الهدف</label><select value=${f.objective} onChange=${(e) => set("objective", e.target.value)}>${Object.entries(OBJECTIVES).map(([k, v]) => html`<option value=${k}>${v}</option>`)}</select></div>
        <div class="field"><label>المدة (أيام)</label><input type="number" min="1" max="60" value=${f.duration_days} onInput=${(e) => set("duration_days", e.target.value)} /></div>
        <div class="field"><label>عدد المنشورات</label><input type="number" min="1" max="30" value=${f.post_count} onInput=${(e) => set("post_count", e.target.value)} /></div>
        <div class="field"><label>تاريخ البداية</label><input type="date" value=${f.start_date} onInput=${(e) => set("start_date", e.target.value)} /></div>
      </div>
      <p><label>المنصات</label><span class="row">${Object.entries(PLATFORMS).map(([k, v]) => html`<label style="display:inline-flex;gap:6px;color:var(--ink)"><input type="checkbox" style="width:auto" checked=${f.platforms.includes(k)} onChange=${() => togglePlat(k)} />${v}</label>`)}</span></p>
      <div class="row">
        <div class="field"><label>النبرة (اختياري)</label><input value=${f.tone} onInput=${(e) => set("tone", e.target.value)} placeholder="ودية" /></div>
      </div>
      <p><label>وصف الحملة</label><textarea value=${f.brief} onInput=${(e) => set("brief", e.target.value)} placeholder="أريد حملة بمناسبة نهاية الأسبوع تستهدف العائلات في الرياض"></textarea></p>
      <button class="primary" disabled=${busy || !f.platforms.length}>${busy ? "جارٍ إنشاء الحملة (قد تستغرق دقيقة)..." : "إنشاء الحملة"}</button>
    </form>
    <h2>الحملات السابقة</h2>
    ${list === null ? html`<${Busy} text="تحميل..." />` : list.length === 0 ? html`<p class="muted">لا توجد حملات.</p>` : html`<div class="grid">${list.map((c) => html`
      <a class="card" style="text-decoration:none;color:inherit" href=${`#/p/${pid}/c/${c.id}`}><h2>${c.name}</h2><span class="muted">${OBJECTIVES[c.objective] || c.objective} · ${c.start_date || ""}</span></a>`)}</div>`}`;
}

/* ---------- Campaign review ---------- */
function ItemCard({ it, reload, onEdit }) {
  const [busy, setBusy] = useState(false);
  const act = async (path, opts = { method: "POST" }) => { setBusy(true); try { await api(path, opts); reload(); } catch {} finally { setBusy(false); } };
  const hasImg = ["design_ready", "pending_approval", "approved", "rejected"].includes(it.status);
  return html`<div class="card item">
    ${hasImg ? html`<img src=${`${API}/content-items/${it.id}/design/image?s=${it.status}`} alt="" loading="lazy" />`
      : html`<div class="ph">${it.status === "design_pending" ? html`<${Busy} text="جارٍ التصميم..." />` : "لا يوجد تصميم بعد"}</div>`}
    <h3>${it.headline}</h3>
    <p>${it.caption}</p>
    <p class="muted" style="max-height:none">${PLATFORMS[it.platform] || it.platform} · ${it.planned_date ? arDate(it.planned_date) : ""}</p>
    <div class="actions">
      <${Badge} status=${it.status} />
      ${["ai_generated", "draft"].includes(it.status) && html`<button disabled=${busy} onClick=${() => act(`/content-items/${it.id}/design`)}>توليد التصميم</button>`}
      ${it.status === "design_ready" && html`<button class="primary" disabled=${busy} onClick=${() => act(`/content-items/${it.id}/submit`)}>إرسال للموافقة</button>
        <button disabled=${busy} onClick=${() => act(`/content-items/${it.id}/design`)}>إعادة التصميم</button>`}
      ${it.status === "pending_approval" && html`<button class="primary" disabled=${busy} onClick=${() => act(`/content-items/${it.id}/approve`)}>اعتماد</button>
        <button class="danger" disabled=${busy} onClick=${() => act(`/content-items/${it.id}/reject`, { method: "POST", body: { reason: "" } })}>رفض</button>`}
      ${it.status === "rejected" && html`<button disabled=${busy} onClick=${() => act(`/content-items/${it.id}/transition?to=draft`)}>إعادة لمسودة</button>`}
      ${!["approved", "archived"].includes(it.status) && html`<button disabled=${busy} onClick=${() => onEdit(it)}>تعديل</button>`}
    </div>
  </div>`;
}

function EditModal({ it, onClose, onSaved }) {
  const [f, setF] = useState({ headline: it.headline, caption: it.caption, cta: it.cta, planned_date: it.planned_date || "" });
  const save = async (e) => {
    e.preventDefault();
    const body = { ...f, planned_date: f.planned_date || null };
    await api(`/content-items/${it.id}`, { method: "PATCH", body });
    onSaved();
  };
  return html`<div class="modal" onClick=${(e) => e.target === e.currentTarget && onClose()}><form class="box" onSubmit=${save}>
    <h2>تعديل المنشور</h2>
    <p><label>العنوان (حتى 60 حرفًا)</label><input maxlength="60" required value=${f.headline} onInput=${(e) => setF({ ...f, headline: e.target.value })} /></p>
    <p><label>النص</label><textarea value=${f.caption} onInput=${(e) => setF({ ...f, caption: e.target.value })}></textarea></p>
    <p><label>زر الدعوة</label><input maxlength="100" value=${f.cta} onInput=${(e) => setF({ ...f, cta: e.target.value })} /></p>
    <p><label>تاريخ النشر</label><input type="date" value=${f.planned_date} onInput=${(e) => setF({ ...f, planned_date: e.target.value })} /></p>
    <p class="muted">بعد التعديل أعد توليد التصميم ليظهر النص الجديد عليه.</p>
    <div class="actions"><button class="primary">حفظ</button><button type="button" onClick=${onClose}>إلغاء</button></div>
  </form></div>`;
}

function CampaignPage({ pid, cid }) {
  const [c, reload] = useFetch(`/projects/${pid}/campaigns/${cid}`);
  const [edit, setEdit] = useState(null);
  const [busy, setBusy] = useState(false);
  const pending = c && c.items.some((i) => i.status === "design_pending");
  useEffect(() => { if (!pending) return; const t = setInterval(reload, 2500); return () => clearInterval(t); }, [pending]);
  if (!c) return html`<${Busy} text="تحميل..." />`;
  const counts = c.items.reduce((m, i) => ((m[i.status] = (m[i.status] || 0) + 1), m), {});
  const run = async (path) => { setBusy(true); try { await api(path, { method: "POST" }); reload(); } catch {} finally { setBusy(false); } };
  const needDesign = c.items.filter((i) => ["ai_generated", "draft"].includes(i.status)).length;
  const reviewable = c.items.filter((i) => ["design_ready", "pending_approval"].includes(i.status)).length;
  return html`<p><a href=${`#/p/${pid}/campaigns`}>← الحملات</a></p>
    <h1>${c.name}</h1>
    <div class="card"><p>${c.strategy}</p>
      <p class="muted">${Object.entries(counts).map(([k, v]) => `${STATUS[k]}: ${v}`).join(" · ")}</p>
      <div class="actions">
        <button disabled=${busy || !needDesign} onClick=${() => run(`/projects/${pid}/campaigns/${cid}/designs`)}>توليد كل التصاميم (${needDesign})</button>
        <button class="primary" disabled=${busy || !reviewable} onClick=${() => run(`/projects/${pid}/campaigns/${cid}/approve-all`)}>اعتماد الكل (${reviewable})</button>
        <button onClick=${() => (location.hash = `#/p/${pid}/calendar`)}>عرض في التقويم</button>
      </div></div>
    <div class="grid">${c.items.map((it) => html`<${ItemCard} key=${it.id} it=${it} reload=${reload} onEdit=${setEdit} />`)}</div>
    ${edit && html`<${EditModal} it=${edit} onClose=${() => setEdit(null)} onSaved=${() => { setEdit(null); reload(); }} />`}`;
}

/* ---------- Calendar ---------- */
function Calendar({ pid }) {
  const [mode, setMode] = useState("month");
  const [anchor, setAnchor] = useState(() => { const d = new Date(); return new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())); });
  // weeks start on Saturday (Gulf convention)
  const weekStart = (d) => addDays(d, -((d.getUTCDay() + 1) % 7));
  let first, last;
  if (mode === "month") {
    const m1 = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), 1));
    const m2 = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + 1, 0));
    first = weekStart(m1); last = addDays(weekStart(m2), 6);
  } else { first = weekStart(anchor); last = addDays(first, 6); }
  const [items, reload] = useFetch(`/projects/${pid}/calendar?start=${iso(first)}&end=${iso(last)}`);
  const [sel, setSel] = useState(null);
  const days = []; for (let d = first; d <= last; d = addDays(d, 1)) days.push(d);
  const byDay = {}; (items || []).forEach((i) => (byDay[i.planned_date] = [...(byDay[i.planned_date] || []), i]));
  const nav = (n) => setAnchor(mode === "month" ? new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + n, 1)) : addDays(anchor, 7 * n));
  const title = anchor.toLocaleDateString(LOC, { month: "long", year: "numeric", timeZone: "UTC" });
  const names = days.slice(0, 7).map((d) => d.toLocaleDateString(LOC, { weekday: "short", timeZone: "UTC" }));
  const today = iso(new Date());
  return html`<div class="card row" style="align-items:center">
      <button onClick=${() => nav(1)}>→</button><strong style="min-width:150px;text-align:center">${title}</strong><button onClick=${() => nav(-1)}>←</button>
      <span class="sp" style="flex:1"></span>
      <button class=${mode === "month" ? "primary" : ""} onClick=${() => setMode("month")}>شهري</button>
      <button class=${mode === "week" ? "primary" : ""} onClick=${() => setMode("week")}>أسبوعي</button>
    </div>
    <div class="cal">${names.map((n) => html`<div class="h">${n}</div>`)}
      ${days.map((d) => { const k = iso(d); const out = mode === "month" && d.getUTCMonth() !== anchor.getUTCMonth();
        return html`<div class=${"day" + (out ? " out" : "") + (k === today ? " today" : "")} style=${mode === "week" ? "min-height:220px" : ""}>
          <div class="n">${d.getUTCDate()}</div>
          ${(byDay[k] || []).map((i) => html`<button class=${"chip s-" + i.status} title=${i.campaign_name} onClick=${() => setSel(i)}>${i.headline}</button>`)}</div>`; })}
    </div>
    ${items && items.length === 0 && html`<p class="muted">لا يوجد محتوى مجدول في هذه الفترة.</p>`}
    ${sel && html`<${CalendarDetail} it=${sel} onClose=${() => setSel(null)} onChanged=${() => { setSel(null); reload(); }} />`}`;
}

function CalendarDetail({ it, onClose, onChanged }) {
  const [hist] = useFetch(`/content-items/${it.id}/history`);
  const hasImg = ["design_ready", "pending_approval", "approved", "rejected"].includes(it.status);
  return html`<div class="modal" onClick=${(e) => e.target === e.currentTarget && onClose()}><div class="box item">
    ${hasImg && html`<img src=${`${API}/content-items/${it.id}/design/image?s=${it.status}`} alt="" />`}
    <h3>${it.headline}</h3><p style="max-height:none">${it.caption}</p>
    <p class="muted" style="max-height:none">${it.campaign_name} · ${PLATFORMS[it.platform]} · ${arDate(it.planned_date)}</p>
    <${Badge} status=${it.status} />
    ${hist && html`<ul class="list muted">${hist.map((x) => html`<li><span>${STATUS[x.from]} ← ${STATUS[x.to]}</span><span>${new Date(x.at).toLocaleString(LOC)}</span></li>`)}</ul>`}
    <div class="actions" style="margin-top:12px">
      <button onClick=${onClose}>إغلاق</button></div>
  </div></div>`;
}

/* ---------- Brand Brain ---------- */
function BrandBrain({ pid }) {
  const [bb, reload] = useFetch(`/projects/${pid}/brand-brain`);
  const [brand, setBrand] = useState(null);
  const [aud, setAud] = useState(null);
  const [prod, setProd] = useState({ name: "", price: "", description: "", features: "" });
  const [rule, setRule] = useState({ kind: "forbidden_word", text: "" });
  useEffect(() => { if (bb) { setBrand(bb.brand || { name: "", description: "", mission: "", tone: "", language: "ar", dialect: "", primary_color: "#0F766E", secondary_color: "#F59E0B" }); setAud(bb.audience || { demographics: "", interests: [], pain_points: [], goals: [] }); } }, [bb && bb.products.length, bb && bb.rules.length, bb === null]);
  if (!bb || !brand || !aud) return html`<${Busy} text="تحميل..." />`;
  const split = (s) => s.split(/[،,\n]/).map((x) => x.trim()).filter(Boolean);
  const saveBrand = async (e) => { e.preventDefault(); await api(`/projects/${pid}/brand`, { method: "PUT", body: brand }); await api(`/projects/${pid}/audience`, { method: "PUT", body: aud }); reload(); };
  const addProd = async (e) => { e.preventDefault(); await api(`/projects/${pid}/products`, { method: "POST", body: { ...prod, features: split(prod.features) } }); setProd({ name: "", price: "", description: "", features: "" }); reload(); };
  const addRule = async (e) => { e.preventDefault(); await api(`/projects/${pid}/rules`, { method: "POST", body: rule }); setRule({ ...rule, text: "" }); reload(); };
  const B = (k, v) => setBrand({ ...brand, [k]: v });
  const A = (k, v) => setAud({ ...aud, [k]: v });
  return html`<form class="card" onSubmit=${saveBrand}>
      <h2>العلامة والجمهور</h2>
      <div class="row">
        <div class="field"><label>اسم العلامة</label><input required value=${brand.name} onInput=${(e) => B("name", e.target.value)} /></div>
        <div class="field"><label>النبرة</label><input value=${brand.tone} onInput=${(e) => B("tone", e.target.value)} placeholder="ودية، رسمية..." /></div>
        <div class="field"><label>اللهجة</label><input value=${brand.dialect} onInput=${(e) => B("dialect", e.target.value)} placeholder="خليجي، فصحى مبسطة" /></div>
        <div class="field" style="max-width:120px"><label>اللون الأساسي</label><input type="color" value=${brand.primary_color} onInput=${(e) => B("primary_color", e.target.value)} /></div>
        <div class="field" style="max-width:120px"><label>اللون الثانوي</label><input type="color" value=${brand.secondary_color} onInput=${(e) => B("secondary_color", e.target.value)} /></div>
      </div>
      <p><label>وصف النشاط</label><textarea value=${brand.description} onInput=${(e) => B("description", e.target.value)}></textarea></p>
      <p><label>الرسالة</label><input value=${brand.mission} onInput=${(e) => B("mission", e.target.value)} /></p>
      <p><label>الجمهور المستهدف</label><input value=${aud.demographics} onInput=${(e) => A("demographics", e.target.value)} placeholder="عائلات في الرياض" /></p>
      <div class="row">
        <div class="field"><label>الاهتمامات (افصل بفاصلة)</label><input value=${aud.interests.join("، ")} onInput=${(e) => A("interests", split(e.target.value))} /></div>
        <div class="field"><label>نقاط الألم</label><input value=${aud.pain_points.join("، ")} onInput=${(e) => A("pain_points", split(e.target.value))} /></div>
        <div class="field"><label>الأهداف</label><input value=${aud.goals.join("، ")} onInput=${(e) => A("goals", split(e.target.value))} /></div>
      </div>
      <p><button class="primary">حفظ</button></p>
    </form>
    <div class="card"><h2>المنتجات والخدمات</h2>
      <ul class="list">${bb.products.map((p) => html`<li><span>${p.name} <span class="muted">${p.price}</span></span><button class="danger" onClick=${async () => { await api(`/projects/${pid}/products/${p.id}`, { method: "DELETE" }); reload(); }}>حذف</button></li>`)}</ul>
      <form class="row" style="margin-top:12px" onSubmit=${addProd}>
        <div class="field"><label>الاسم</label><input required value=${prod.name} onInput=${(e) => setProd({ ...prod, name: e.target.value })} /></div>
        <div class="field"><label>السعر</label><input value=${prod.price} onInput=${(e) => setProd({ ...prod, price: e.target.value })} placeholder="49 ريال" /></div>
        <div class="field"><label>الوصف</label><input value=${prod.description} onInput=${(e) => setProd({ ...prod, description: e.target.value })} /></div>
        <button class="primary">إضافة</button>
      </form></div>
    <div class="card"><h2>قواعد العلامة</h2>
      <ul class="list">${bb.rules.map((r) => html`<li><span>${{ forbidden_word: "كلمة ممنوعة", must_do: "يجب", must_not: "يمنع" }[r.kind]}: ${r.text}</span><button class="danger" onClick=${async () => { await api(`/projects/${pid}/rules/${r.id}`, { method: "DELETE" }); reload(); }}>حذف</button></li>`)}</ul>
      <form class="row" style="margin-top:12px" onSubmit=${addRule}>
        <div class="field" style="max-width:180px"><label>النوع</label><select value=${rule.kind} onChange=${(e) => setRule({ ...rule, kind: e.target.value })}><option value="forbidden_word">كلمة ممنوعة</option><option value="must_do">يجب</option><option value="must_not">يمنع</option></select></div>
        <div class="field"><label>النص</label><input required value=${rule.text} onInput=${(e) => setRule({ ...rule, text: e.target.value })} /></div>
        <button class="primary">إضافة</button>
      </form></div>`;
}

/* ---------- App ---------- */
function App() {
  const parts = useHash();
  const [toast, setToast] = useState("");
  const timer = useRef();
  toastSetter = (m) => { setToast(m); clearTimeout(timer.current); timer.current = setTimeout(() => setToast(""), 6000); };
  let view;
  if (parts[0] === "p" && parts[1]) view = parts[2] === "c" && parts[3] ? html`<${CampaignPage} pid=${parts[1]} cid=${parts[3]} />` : html`<${Project} id=${parts[1]} tab=${parts[2]} key=${parts[1]} />`;
  else view = html`<${Projects} />`;
  return html`<header><a href="#/">Marketing OS</a><span class="sp"></span><a href="/docs" class="muted" style="font-weight:400">API</a></header>
    <main>${view}</main>${toast && html`<div class="toast" onClick=${() => setToast("")}>${toast}</div>`}`;
}

render(html`<${App} />`, document.getElementById("app"));
