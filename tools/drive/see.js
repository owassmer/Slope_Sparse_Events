
(limit) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return (r.width > 0 || r.height > 0) && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const sel = 'a[href], button, input, select, textarea, summary, [role=button], [role=tab], [role=link],'
            + ' [role=checkbox], [role=switch], [onclick], [tabindex]:not([tabindex="-1"])';
  const picked = new Set(Array.from(document.querySelectorAll(sel)).filter(visible));
  // anything else a pointer cursor marks as clickable, keeping only the outermost such element
  for (const el of document.querySelectorAll('body *')) {
    if (picked.has(el) || !visible(el)) continue;
    if (getComputedStyle(el).cursor !== 'pointer') continue;
    let p = el.parentElement, inside = false;
    while (p) { if (picked.has(p) || (p !== document.body && getComputedStyle(p).cursor === 'pointer')) { inside = true; break; } p = p.parentElement; }
    if (!inside) picked.add(el);
  }
  document.querySelectorAll('[data-drive-ref]').forEach(e => e.removeAttribute('data-drive-ref'));
  const label = (el) => {
    const lab = el.labels && el.labels.length ? el.labels[0].innerText : '';
    const t = (el.getAttribute('aria-label') || lab || el.innerText || el.textContent || el.getAttribute('title')
               || el.getAttribute('placeholder') || el.getAttribute('name') || el.id || '').replace(/\s+/g, ' ').trim();
    return t.length > 120 ? t.slice(0, 117) + '...' : t;
  };
  const controls = [];
  let n = 0;
  const ordered = Array.from(picked).sort((a, b) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
  for (const el of ordered) {
    n += 1;
    el.setAttribute('data-drive-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const kind = tag === 'input' ? `input:${el.type}` : (el.getAttribute('role') || tag);
    const c = {ref: n, kind, label: label(el)};
    if ('value' in el && tag !== 'button' && tag !== 'li') c.value = String(el.value);
    if (tag === 'select') c.value = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '';
    if (el.min) c.min = el.min; if (el.max) c.max = el.max;
    if (el.type === 'checkbox' || el.type === 'radio') c.checked = el.checked;
    if (el.disabled) c.disabled = true;
    if (tag === 'a') c.href = el.getAttribute('href');
    controls.push(c);
  }
  let text = (document.body ? document.body.innerText : '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim();
  const total = text.length;
  if (limit > 0 && text.length > limit) text = text.slice(0, limit) + `\n... (${total - limit} more characters; use --all)`;
  return {title: document.title, url: location.href, text, controls};
}
