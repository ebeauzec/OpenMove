/* Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved. Proprietary software; see LICENSE. */
(function () {
  'use strict';
  var TOKEN = (document.querySelector('meta[name="openmove-token"]') || {}).content || '';
  var inventory = null;
  var MAX_ROWS = 500;

  function $(id) { return document.getElementById(id); }
  // Cloud data is shown with textContent only; nothing from the cloud is ever parsed as HTML.
  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') e.textContent = attrs[k]; else if (k === 'class') e.className = attrs[k]; else e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function api(method, path, body) {
    var opts = { method: method, headers: { 'X-OpenMove-Token': TOKEN } };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch('/api/' + path, opts).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.error || ('request failed (' + r.status + ')'));
        return d;
      });
    });
  }

  function banner(kind, text) {
    var b = $('banner');
    if (!text) { b.className = 'banner hidden'; return; }
    b.className = 'banner ' + kind; b.textContent = text;
  }
  function busy(btn, on) { btn.disabled = on; }
  function guard(btn, fn) {
    busy(btn, true); banner('', '');
    return fn().catch(function (e) { banner('bad', e.message); }).then(function () { busy(btn, false); });
  }

  // ---- tabs ----
  function showTab(name) {
    document.querySelectorAll('#tabs button').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === name); });
    document.querySelectorAll('.panel').forEach(function (p) { p.classList.toggle('hidden', p.id !== 'tab-' + name); });
    if (name === 'journal') loadJournal();
  }
  document.querySelectorAll('#tabs button').forEach(function (b) {
    b.addEventListener('click', function () { showTab(b.dataset.tab); });
  });

  // ---- status ----
  function refreshStatus() {
    return api('GET', 'status').then(function (s) {
      $('ver').textContent = 'v' + s.version;
      var m = $('mode');
      if (!s.connected) { m.className = 'chip'; m.textContent = 'not connected'; }
      else if (s.platform && s.read_only) { m.className = 'chip warn'; m.textContent = 'read only'; }
      else if (s.platform) { m.className = 'chip ok'; m.textContent = 'validated release'; }
      else { m.className = 'chip'; m.textContent = 'connected'; }
      return s;
    }).catch(function () {});
  }

  // ---- connect ----
  $('connect-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var f = ev.target, d = {};
    ['auth_url', 'username', 'password', 'project_name', 'user_domain', 'project_domain', 'region', 'interface', 'ca_file']
      .forEach(function (n) { d[n] = f.elements[n].value; });
    d.verify = f.elements.verify.checked;
    guard(f.querySelector('button.primary'), function () {
      return api('POST', 'connect', d).then(function (r) {
        f.elements.password.value = '';
        var out = $('connect-result'); clear(out);
        out.appendChild(el('div', { class: 'banner ok', text: 'Connected. Roles: ' + (r.roles || []).join(', ') }));
        if ((r.roles || []).indexOf('admin') < 0) out.appendChild(el('div', { class: 'banner warn',
          text: 'This account has no admin role. Back-end, host and all-project data will not be available.' }));
        return refreshStatus().then(function () { showTab('platform'); });
      });
    });
  });
  $('disconnect').addEventListener('click', function () {
    api('POST', 'disconnect', {}).then(function () { clear($('connect-result')); clear($('platform-view')); refreshStatus(); });
  });

  // ---- platform ----
  function kv(rows) {
    var t = el('table'), tb = el('tbody');
    rows.forEach(function (r) { tb.appendChild(el('tr', {}, [el('th', { text: r[0] }), el('td', { text: String(r[1] == null ? '-' : r[1]) })])); });
    t.appendChild(tb); return t;
  }
  function table(headers, rows) {
    var t = el('table'), th = el('tr');
    headers.forEach(function (h) { th.appendChild(el('th', { text: h })); });
    t.appendChild(el('thead', {}, [th]));
    var tb = el('tbody');
    rows.forEach(function (r) { tb.appendChild(el('tr', {}, r.map(function (c) { return el('td', { text: String(c == null ? '-' : c) }); }))); });
    t.appendChild(tb); return t;
  }
  function renderPlatform(p) {
    var v = $('platform-view'); clear(v);
    var prof = p.profile || {}, rel = p.release || {};
    var kind = prof.status === 'validated' ? 'ok' : 'warn';
    v.appendChild(el('div', { class: 'banner ' + kind, text: prof.status === 'validated'
      ? 'Release ' + prof.release + ' is validated. Changes to the cloud are allowed.'
      : 'Read only. ' + prof.reason + '. Inventory, checks and reports work; every step that changes the cloud is refused.' }));
    var cards = el('div', { class: 'cards' });
    [['Release', (rel.candidates || []).join(' / ') || 'unknown'], ['Confidence', rel.confidence],
     ['Block storage API', p.block_storage.max], ['Compute API', p.compute.max]].forEach(function (c) {
      cards.appendChild(el('div', { class: 'card' }, [el('div', { class: 'k', text: c[0] }), el('div', { class: 'v', text: String(c[1] == null ? '-' : c[1]) })]));
    });
    v.appendChild(cards);
    (rel.notes || []).forEach(function (n) { v.appendChild(el('p', { class: 'muted', text: n })); });
    v.appendChild(el('h3', { text: 'Endpoints and versions' }));
    v.appendChild(kv([['Identity', (p.identity.version_id || '-') + '  ' + (p.identity.endpoint || '')],
      ['Block storage', (p.block_storage.version_id || '-') + ' (' + (p.block_storage.min || '?') + ' to ' + (p.block_storage.max || '?') + ')  ' + (p.block_storage.endpoint || '')],
      ['Compute', (p.compute.version_id || '-') + ' (' + (p.compute.min || '?') + ' to ' + (p.compute.max || '?') + ')  ' + (p.compute.endpoint || '')],
      ['TLS verification', p.tls_verification ? 'on' : 'OFF']]));
    v.appendChild(el('h3', { text: 'Storage back-ends (' + p.backends.length + ')' }));
    if (p.backends.length) v.appendChild(table(['Pool', 'Back-end', 'Vendor', 'Driver version', 'Protocol', 'Total GiB', 'Free GiB'],
      p.backends.map(function (b) { return [b.pool, b.backend, b.vendor, b.driver_version, b.protocol, b.total_gb, b.free_gb]; })));
    else v.appendChild(el('p', { class: 'muted', text: 'None reported (needs an administrator account).' }));
    v.appendChild(el('h3', { text: 'Block storage services (' + p.cinder_services.length + ')' }));
    if (p.cinder_services.length) v.appendChild(table(['Binary', 'Host', 'Zone', 'Status', 'State'],
      p.cinder_services.map(function (s) { return [s.binary, s.host, s.zone, s.status, s.state]; })));
    v.appendChild(el('h3', { text: 'Compute hosts (' + p.compute_hosts.length + ')' }));
    if (p.compute_hosts.length) v.appendChild(table(['Host', 'Type', 'Version (as reported)', 'State', 'Status', 'Running VMs'],
      p.compute_hosts.map(function (h) { return [h.host, h.type, h.version, h.state, h.status, h.running_vms]; })));
    if (p.warnings.length) {
      v.appendChild(el('h3', { text: 'Notes' }));
      var ul = el('ul', { class: 'plain' }); p.warnings.forEach(function (w) { ul.appendChild(el('li', { text: w })); }); v.appendChild(ul);
    }
    var sel = $('inv-backend'), keep = sel.value; clear(sel);
    sel.appendChild(el('option', { value: '', text: 'all back-ends' }));
    var seen = {};
    p.backends.forEach(function (b) { if (b.backend && !seen[b.backend]) { seen[b.backend] = 1; sel.appendChild(el('option', { value: b.backend, text: b.backend })); } });
    sel.value = keep;
  }
  $('discover').addEventListener('click', function () {
    guard($('discover'), function () {
      return api('POST', 'discover', {}).then(function (p) { renderPlatform(p); return refreshStatus(); });
    });
  });

  // ---- inventory ----
  function renderInventory(inv) {
    inventory = inv;
    var s = $('inv-summary'); clear(s);
    (inv.warnings || []).forEach(function (w) { s.appendChild(el('div', { class: 'banner warn', text: w })); });
    var cards = el('div', { class: 'cards' });
    cards.appendChild(el('div', { class: 'card' }, [el('div', { class: 'k', text: 'Volumes' }), el('div', { class: 'v', text: String(inv.total) })]));
    cards.appendChild(el('div', { class: 'card' }, [el('div', { class: 'k', text: 'Total GiB' }), el('div', { class: 'v', text: String(inv.total_gib) })]));
    ['A', 'B', 'C', 'D', 'E'].forEach(function (c) {
      cards.appendChild(el('div', { class: 'card' }, [el('div', { class: 'k', text: 'Class ' + c }), el('div', { class: 'v', text: String(inv.counts[c] || 0) })]));
    });
    s.appendChild(cards);
    var keys = Object.keys(inv.assumptions || {});
    if (keys.length) {
      s.appendChild(el('h3', { text: 'Not checked' }));
      var ul = el('ul', { class: 'plain' });
      keys.forEach(function (k) { ul.appendChild(el('li', { text: k + ' (' + inv.assumptions[k] + ' volumes)' })); });
      s.appendChild(ul);
    }
    var cs = $('inv-class'); clear(cs); cs.appendChild(el('option', { value: '', text: 'all' }));
    ['A', 'B', 'C', 'D', 'E'].forEach(function (c) { cs.appendChild(el('option', { value: c, text: c })); });
    $('inv-filter').hidden = false; $('inv-table').hidden = false;
    drawRows();
  }
  function drawRows() {
    if (!inventory) return;
    var cls = $('inv-class').value, q = $('inv-search').value.toLowerCase();
    var rows = inventory.volumes.filter(function (v) {
      if (cls && v['class'] !== cls) return false;
      if (!q) return true;
      return [v.id, v.name, v.project_id, v.host].join(' ').toLowerCase().indexOf(q) >= 0;
    });
    var tb = $('inv-table').querySelector('tbody'); clear(tb);
    rows.slice(0, MAX_ROWS).forEach(function (v) {
      var att = (v.attachments || []).map(function (a) { return a.server_id; }).filter(Boolean).join(', ');
      tb.appendChild(el('tr', {}, [
        el('td', {}, [el('span', { class: 'mono', text: v.id }), el('div', { class: 'muted', text: v.name || '' })]),
        el('td', {}, [el('span', { class: 'cls cls-' + v['class'], text: v['class'] })]),
        el('td', { text: v.reason }),
        el('td', { class: 'num', text: String(v.size) }),
        el('td', { class: 'num', text: String(v.snapshots) }),
        el('td', { text: v.status || '' }),
        el('td', { text: v.backend || v.host || '' }),
        el('td', { class: 'mono', text: att })]));
    });
    $('inv-more').textContent = rows.length > MAX_ROWS
      ? 'Showing the first ' + MAX_ROWS + ' of ' + rows.length + ' matching volumes. Narrow the filter to see the rest.'
      : rows.length + ' matching volumes.';
  }
  $('inv-class').addEventListener('change', drawRows);
  $('inv-search').addEventListener('input', drawRows);
  $('inv-run').addEventListener('click', function () {
    guard($('inv-run'), function () {
      return api('POST', 'inventory/run', { backend: $('inv-backend').value, window_days: Number($('inv-window').value) || 14 })
        .then(renderInventory);
    });
  });
  $('inv-import').addEventListener('click', function () {
    guard($('inv-import'), function () {
      return api('POST', 'inventory/import', {
        a1: $('imp-a1').value, a2: $('imp-a2').value, a4: $('imp-a4').value, inst: $('imp-inst').value,
        backend: $('inv-backend').value, window_days: Number($('inv-window').value) || 14 }).then(renderInventory);
    });
  });
  document.querySelectorAll('input[type=file][data-for]').forEach(function (inp) {
    inp.addEventListener('change', function () {
      var f = inp.files[0]; if (!f) return;
      var r = new FileReader();
      r.onload = function () { $(inp.dataset['for']).value = String(r.result); };
      r.readAsText(f);
    });
  });

  // ---- journal ----
  function loadJournal() {
    api('GET', 'journal?limit=100').then(function (j) {
      var v = $('journal-view'); clear(v);
      if (j.unresolved_steps.length) v.appendChild(el('div', { class: 'banner bad',
        text: j.unresolved_steps.length + ' step(s) have an intent and no result. They must be settled before their volumes can be touched.' }));
      v.appendChild(table(['Time (UTC)', 'Kind', 'Subject', 'Data'], j.events.map(function (e) {
        return [e.ts, e.kind, e.subject, JSON.stringify(e.data)];
      })));
    }).catch(function (e) { banner('bad', e.message); });
  }
  $('journal-refresh').addEventListener('click', loadJournal);

  refreshStatus();
})();
