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

  var currentTab = 'connect';
  // ---- tabs ----
  function showTab(name, force) {
    var w = wizardInfo(name);
    if (w && !w.open && !force) { banner('warn', 'Not yet. ' + w.needs); return; }
    document.querySelectorAll('#tabs button').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === name); });
    document.querySelectorAll('.panel').forEach(function (p) { p.classList.toggle('hidden', p.id !== 'tab-' + name); });
    if (name === 'journal') loadJournal();
    if (name === 'preflight') fillBackends();
    if (name === 'live') { pollLive(true); loadUnmigrated(true); }
    if (name === 'select') { loadSelection(); loadRisk(); }
    if (name === 'move') loadMove();
    currentTab = name;
    drawWizard();
  }
  // The wizard is gated: the server says which step is open (every step before it is done) and what is missing for the next one.
  function wizardInfo(tab) {
    var list = (lastStatus && lastStatus.wizard) || [];
    for (var k = 0; k < list.length; k++) { if (list[k].id === tab) return list[k]; }
    return null;
  }
  function wizardDone(tab) { var w = wizardInfo(tab); return !w || w.done; }
  // Back and Next walk through the numbered steps in order, so the tabs at the top are not the only way to move
  function wizardSteps() {
    return Array.prototype.filter.call(document.querySelectorAll('#tabs button'), function (b) { return /^\d+\./.test(b.textContent.trim()); });
  }
  function drawWizard() {
    var steps = wizardSteps(), i = -1;
    steps.forEach(function (b, k) { if (b.dataset.tab === currentTab) i = k; });
    var back = $('wiz-back'), next = $('wiz-next'), lab = $('wiz-step'), hint = $('wiz-hint');
    steps.forEach(function (b) {                   // a step that is not open is shown locked, with what it waits for
      var w = wizardInfo(b.dataset.tab), locked = !!(w && !w.open);
      b.classList.toggle('locked', locked); b.setAttribute('aria-disabled', locked ? 'true' : 'false');
      b.title = locked ? w.needs : (w && w.done ? 'Done' : '');
    });
    hint.textContent = '';
    if (i < 0) {                                   // the journal is not a step: Back returns to the last one
      back.disabled = false; back.textContent = 'Back to ' + steps[steps.length - 1].textContent.trim();
      next.disabled = true; next.textContent = 'Next'; lab.textContent = 'Journal';
      return;
    }
    back.disabled = i === 0;
    back.textContent = i === 0 ? 'Back' : 'Back: ' + steps[i - 1].textContent.trim();
    var nw = i < steps.length - 1 ? wizardInfo(steps[i + 1].dataset.tab) : null;
    next.disabled = i === steps.length - 1 || !!(nw && !nw.open);
    next.textContent = i === steps.length - 1 ? 'Next' : 'Next: ' + steps[i + 1].textContent.trim();
    if (nw && !nw.open) hint.textContent = 'To continue: ' + nw.needs;
    lab.textContent = 'Step ' + (i + 1) + ' of ' + steps.length + ': ' + steps[i].textContent.trim().replace(/^\d+\.\s*/, '');
  }
  function wizardGo(delta) {
    var steps = wizardSteps(), i = -1;
    steps.forEach(function (b, k) { if (b.dataset.tab === currentTab) i = k; });
    var to = i < 0 ? steps.length - 1 : i + delta;
    if (to < 0 || to >= steps.length) return;
    showTab(steps[to].dataset.tab);
    window.scrollTo(0, 0);
  }
  document.querySelectorAll('#tabs button').forEach(function (b) {
    b.addEventListener('click', function () { showTab(b.dataset.tab); });
  });
  $('wiz-back').addEventListener('click', function () { wizardGo(-1); });
  $('wiz-next').addEventListener('click', function () { wizardGo(1); });
  drawWizard();

  // ---- status ----
  var lastStatus = {}, restoring = {}, deepLink = (window.location.hash || '').replace('#', '');
  // A step that is done keeps its result on the server. After a reload (or a link to a step) the page draws it again, so a done step is never blank.
  function restoreViews(s) {
    [['platform', 'platform-view', 'platform', function (d) { renderPlatform(d); }],
     ['inventory', 'inv-summary', 'inventory', function (d) { renderInventory(d); }],
     ['audit', 'audit-view', 'audit', function (d) { renderAudit(d); }],
     ['checks', 'pf-view', 'checks', function (d) { renderChecks(d); }]].forEach(function (r) {
      if (!s[r[0]] || $(r[1]).firstChild || restoring[r[0]]) return;
      restoring[r[0]] = true;
      api('GET', r[2]).then(r[3]).catch(function () {}).then(function () { restoring[r[0]] = false; });
    });
  }
  function refreshStatus() {
    return api('GET', 'status').then(function (s) {
      lastStatus = s;
      $('ver').textContent = 'v' + s.version;
      $('demo-banner').classList.toggle('hidden', !s.demo);
      $('lab-banner').classList.toggle('hidden', !s.lab);
      $('pf-lab-approve').classList.toggle('hidden', !s.lab);
      $('pf-demo-validate').classList.toggle('hidden', !s.demo);
      $('pf-demo-approve').classList.toggle('hidden', !s.demo);
      $('inv-demo-export').classList.toggle('hidden', !s.demo);
      if (s.demo) drawScenarios(s.demo_scenario);
      drawHalt(s.halted);
      $('live-start').classList.toggle('hidden', !s.demo); 
      if (s.lab_form && !$('connect-form').dataset.labFilled) {          // lab mode: the form is filled in; the password is the lab's own
        var cf = $('connect-form');
        Object.keys(s.lab_form).forEach(function (k) { if (cf.elements[k]) cf.elements[k].value = s.lab_form[k]; });
        cf.elements.password.required = false;
        cf.elements.password.placeholder = 'filled in automatically (lab)';
        cf.dataset.labFilled = '1';
      }
      if (s.demo) {
        var out = $('connect-result');
        if (!out.firstChild) out.appendChild(el('div', { class: 'banner ok', text: 'Demonstration mode: connected to the built-in demonstration cloud. Go to 2. Platform.' }));
        $('connect-form').classList.add('hidden');
      }
      var m = $('mode');
      if (!s.connected) { m.className = 'chip'; m.textContent = 'not connected'; }
      else if (s.platform && s.read_only) { m.className = 'chip warn'; m.textContent = 'read only'; }
      else if (s.platform) { m.className = 'chip ok'; m.textContent = 'validated release'; }
      else { m.className = 'chip'; m.textContent = 'connected'; }
      var cur = wizardInfo(currentTab);
      if (cur && !cur.open) {                      // the step we are on closed (disconnected, a new pre-flight run, a changed selection): go back to the first open one that is not done
        var open = (s.wizard || []).filter(function (w) { return w.open && !w.done; })[0] || (s.wizard || [])[0];
        if (open) showTab(open.id, true);
      }
      drawWizard();
      drawMoveGate();
      restoreViews(s);
      if (deepLink) { var go = deepLink; deepLink = ''; if (wizardInfo(go) || go === 'journal') showTab(go); }     // a link such as  /#preflight  opens that step if it is open
      return s;
    }).catch(function () {});
  }

  // ---- halt ----
  function askWho(what) {
    var by = window.prompt(what + '\n\nYour name:');
    if (!by || !by.trim()) return null;
    var reason = window.prompt('Reason (at least 8 characters):');
    if (!reason || reason.trim().length < 8) { banner('bad', 'A reason of at least 8 characters is needed. Nothing was changed.'); return null; }
    return { by: by.trim(), reason: reason.trim() };
  }
  function drawHalt(h) {
    var b = $('halt-banner'); clear(b);
    b.classList.toggle('hidden', !h);
    $('halt-btn').classList.toggle('hidden', !!h);
    if (!h) return;
    b.appendChild(el('span', { text: 'ALL CHANGES ARE HALTED by ' + h.by + ' (' + h.at + '): ' + h.reason }));
    var r = el('button', { text: 'Resume' });
    r.addEventListener('click', function () {
      var w = askWho('Resume changes? Only do this once the reason for the halt is resolved.');
      if (w) api('POST', 'resume', w).then(refreshStatus).catch(function (e) { banner('bad', e.message); });
    });
    b.appendChild(r);
  }
  $('halt-btn').addEventListener('click', function () {
    var w = askWho('HALT all changes now? Nothing that changes the cloud will be sent until someone resumes.');
    if (w) api('POST', 'halt', w).then(refreshStatus).catch(function (e) { banner('bad', e.message); });
  });

  // ---- demonstration scenarios ----
  function drawScenarios(info) {
    if (!info) return;
    var sel = $('demo-scenario');
    if (sel.options.length !== info.scenarios.length) {
      clear(sel);
      info.scenarios.forEach(function (x) { sel.appendChild(el('option', { value: x.id, text: x.title })); });
    }
    sel.value = info.id;
    $('demo-desc').textContent = info.summary + ' Cloud: ' + info.distribution + '. Source ' + info.source + ', default target ' + info.target + '.';
  }
  function resetViews() {
    ['platform-view', 'inv-summary', 'audit-view', 'pf-view'].forEach(function (id) { clear($(id)); });
    clear($('inv-table').tBodies[0]); $('inv-table').hidden = true; $('inv-more').textContent = '';
    ['inv-backend', 'pf-source', 'pf-target'].forEach(function (id) { $(id).value = ''; });
    $('rep-status').textContent = ''; $('inv-filter').hidden = true;
    inventory = null; attesting = null;
  }
  $('demo-scenario').addEventListener('change', function () {
    var sel = $('demo-scenario');
    sel.disabled = true;
    api('POST', 'demo/scenario', { id: sel.value }).then(function () {
      resetViews(); banner('ok', 'Switched the demonstration. Nothing has been read yet: start with 2. Platform.');
      return refreshStatus();
    }).catch(function (e) { banner('bad', e.message); }).then(function () { sel.disabled = false; showTab('platform'); });
  });
  $('inv-demo-export').addEventListener('click', function () {
    guard($('inv-demo-export'), function () {
      return api('POST', 'demo/db-export', {}).then(function (inv) {
        renderInventory(inv); refreshStatus();
        banner('ok', 'Loaded the demonstration database export: provider ids and snapshot history are now included, so classes B and the provider_id checks appear.');
      });
    });
  });

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
    var rows = sortInventoryRows(inventory.volumes.filter(function (v) {
      if (cls && v['class'] !== cls) return false;
      if (!q) return true;
      return [v.id, v.name, v.project_id, v.host].join(' ').toLowerCase().indexOf(q) >= 0;
    }));
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
        .then(function (inv) { renderInventory(inv); return refreshStatus(); });
    });
  });
  $('inv-import').addEventListener('click', function () {
    guard($('inv-import'), function () {
      return api('POST', 'inventory/import', {
        a1: $('imp-a1').value, a2: $('imp-a2').value, a4: $('imp-a4').value, inst: $('imp-inst').value,
        backend: $('inv-backend').value, window_days: Number($('inv-window').value) || 14 })
        .then(function (inv) { renderInventory(inv); return refreshStatus(); });
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
        var d = JSON.stringify(e.data);
        return [e.ts, e.kind, e.subject, d.length > 400 ? d.slice(0, 400) + ' ...' : d];
      })));
    }).catch(function (e) { banner('bad', e.message); });
  }
  $('journal-refresh').addEventListener('click', loadJournal);

  // ---- shared helpers for checks and the audit ----
  var STATUS_TEXT = { pass: 'Pass', info: 'Info', warn: 'Warn', fail: 'Fail', not_checked: 'Not checked' };
  function chip(status) { return el('span', { class: 'st st-' + status, text: STATUS_TEXT[status] || status }); }
  function sevChip(sev) { return el('span', { class: 'st sev-' + sev, text: sev.charAt(0).toUpperCase() + sev.slice(1) }); }
  function card(k, v) { return el('div', { class: 'card' }, [el('div', { class: 'k', text: k }), el('div', { class: 'v', text: String(v) })]); }
  function fillBackends() {
    if (!lastStatus.platform) return;
    api('GET', 'platform').then(function (p) {
      ['pf-source', 'pf-target'].forEach(function (id) {
        var sel = $(id), keep = sel.value; clear(sel);
        sel.appendChild(el('option', { value: '', text: 'choose' }));
        var seen = {};
        p.backends.forEach(function (b) { if (b.backend && !seen[b.backend]) { seen[b.backend] = 1; sel.appendChild(el('option', { value: b.backend, text: b.backend })); } });
        sel.value = keep;
      });
      var d = lastStatus.demo_scenario;
      if (d && !$('pf-source').value && !$('pf-target').value) { $('pf-source').value = d.source; $('pf-target').value = d.target; $('pf-access').value = 'full'; }
    }).catch(function () {});
  }

  // ---- audit ----
  function renderAudit(a) {
    var v = $('audit-view'); clear(v);
    var fc = a.finding_counts || {};
    var cards = el('div', { class: 'cards' });
    [['Volumes', a.volumes.total], ['Volume GiB', a.volumes.gib], ['Snapshots', a.snapshots.total], ['Backups', a.backups.total], ['Instances', a.instances.total],
     ['High findings', fc.high || 0], ['Medium findings', fc.medium || 0], ['Low findings', fc.low || 0], ['Info', fc.info || 0]].forEach(function (c) { cards.appendChild(card(c[0], c[1])); });
    v.appendChild(cards);
    if (a.not_read.length) v.appendChild(el('div', { class: 'banner warn', text: 'Parts of the audit could not be read (' + a.not_read.length + '). See the last finding and the notes below.' }));
    v.appendChild(el('h3', { text: 'Findings (' + a.findings.length + ')' }));
    if (!a.findings.length) v.appendChild(el('p', { class: 'muted', text: 'No findings.' }));
    a.findings.forEach(function (f) {
      var ul = el('ul'); f.affected.slice(0, 25).forEach(function (x) { ul.appendChild(el('li', { class: 'mono', text: x })); });
      if (f.count > 25) ul.appendChild(el('li', { text: 'and ' + (f.count - 25) + ' more' }));
      v.appendChild(el('div', { class: 'finding' }, [
        el('div', {}, [sevChip(f.severity), document.createTextNode(' '), el('span', { class: 'title', text: f.id + '  ' + f.title }), el('span', { class: 'meta', text: '  (' + f.count + ')' })]),
        el('div', { text: f.detail }), el('div', { class: 'meta', text: 'Action: ' + f.action }),
        el('details', {}, [el('summary', { text: 'Affected (' + f.count + ')' }), ul])]));
    });
    function group(title, node) { var d = el('details', {}, [el('summary', { text: title }), node]); v.appendChild(d); }
    group('Block storage services (' + a.services.length + ')', table(['Service', 'Host', 'Zone', 'Status', 'State', 'Hours since report'],
      a.services.map(function (s) { return [s.binary, s.host, s.zone, s.status, s.state, s.age_h]; })));
    group('Back-ends and capacity (' + a.backends.length + ')', table(['Pool', 'Vendor', 'Driver', 'Protocol', 'Total GiB', 'Free GiB', 'Used %'],
      a.backends.map(function (b) { return [b.pool, b.vendor, b.driver_version, b.protocol, b.total_gb, b.free_gb, b.utilisation_pct]; })));
    group('Compute hosts (' + a.compute_hosts.length + ')', table(['Host', 'Type', 'Version', 'State', 'Status', 'Instances'],
      a.compute_hosts.map(function (h) { return [h.host, h.type, h.version, h.state, h.status, h.running_vms]; })));
    group('Volume types (' + a.volume_types.length + ')', table(['Type', 'Public', 'Back-end', 'QoS', 'Volumes'],
      a.volume_types.map(function (t) { return [t.name, t.is_public ? 'yes' : 'no', (t.extra_specs || {}).volume_backend_name, t.qos_specs_id, t.volumes]; })));
    var vg = function (rows) { return rows.map(function (g) { return [g.name, g.volumes, g.gib, g.snapshots]; }); };
    group('Volumes by back-end', table(['Back-end', 'Volumes', 'GiB', 'Snapshots'], vg(a.volumes.by_backend)));
    group('Volumes by project', table(['Project', 'Volumes', 'GiB', 'Snapshots'], vg(a.volumes.by_project)));
    group('Volumes by type', table(['Type', 'Volumes', 'GiB', 'Snapshots'], vg(a.volumes.by_type)));
    group('Volumes by state', table(['State', 'Volumes'], Object.keys(a.volumes.by_status).map(function (k) { return [k, a.volumes.by_status[k]]; })));
    group('Snapshots by age', table(['Age', 'Snapshots'], Object.keys(a.snapshots.by_age).map(function (k) { return [k, a.snapshots.by_age[k]]; })));
    group('Instances by state', table(['State', 'Instances'], Object.keys(a.instances.by_status).map(function (k) { return [k, a.instances.by_status[k]]; })));
    if (a.not_read.length) {
      var ul = el('ul', { class: 'plain' }); a.not_read.forEach(function (w) { ul.appendChild(el('li', { text: w })); });
      v.appendChild(el('h3', { text: 'What could not be read' })); v.appendChild(ul);
    }
  }
  $('audit-run').addEventListener('click', function () {
    guard($('audit-run'), function () { return api('POST', 'audit/run', {}).then(function (a) { renderAudit(a); return refreshStatus(); }); });
  });

  // ---- pre-flight and the gate ----
  var GATE_TEXT = { approved: 'GATE OPEN: a migration may start', blocked: 'GATE BLOCKED', open: 'GATE CLOSED: checks need attention',
                    awaiting_signoff: 'GATE CLOSED: waiting for sign-off', stale: 'GATE CLOSED: the pre-flight is out of date' };
  var attesting = null;

  function attestForm(c, check) {
    var name = el('input', { placeholder: 'your name' }), note = el('textarea', { rows: '2', placeholder: 'the evidence or reason, in a few words' });
    var go = el('button', { class: 'primary', text: 'Record attestation' });
    go.addEventListener('click', function () {
      guard(go, function () {
        return api('POST', 'checks/attest', { check_id: check.id, by: name.value, note: note.value }).then(function (r) { attesting = null; renderChecks(r); return refreshStatus(); });
      });
    });
    var cancel = el('button', { class: 'secondary', text: 'Cancel' });
    cancel.addEventListener('click', function () { attesting = null; renderChecks(c); });
    return el('div', { class: 'inline-form' }, [
      el('strong', { text: 'Attest ' + check.id + ': ' + check.title }),
      el('p', { class: 'muted', text: 'You are stating that this prerequisite is met or accepted. Your name and the evidence go into the journal and the report.' }),
      el('div', { class: 'grid' }, [el('label', {}, ['Your name', name]), el('label', {}, ['Evidence or reason', note])]),
      el('div', { class: 'actions' }, [go, cancel])]);
  }

  function signBox(c, role, label) {
    var so = (c.gate.signoffs || {})[role];
    if (so) return el('div', { class: 'card' }, [el('div', { class: 'k', text: label }), el('div', { class: 'v', text: so.by }), el('div', { class: 'muted', text: so.at })]);
    var name = el('input', { placeholder: 'name' }), go = el('button', { class: 'primary', text: 'Sign as ' + role });
    var ready = !c.gate.blockers.length && !c.gate.open_items.length;
    go.disabled = !ready;
    go.addEventListener('click', function () {
      guard(go, function () { return api('POST', 'checks/signoff', { role: role, by: name.value }).then(function (r) { renderChecks(r); return refreshStatus(); }); });
    });
    return el('div', { class: 'card' }, [el('div', { class: 'k', text: label }), name, el('div', { class: 'actions' }, [go]),
      ready ? el('span') : el('div', { class: 'muted', text: 'Signing opens when nothing blocks and nothing is open.' })]);
  }

  function renderChecks(c) {
    var v = $('pf-view'); clear(v);
    var g = c.gate, att = c.attestations || {};
    v.appendChild(el('div', { class: 'gate gate-' + g.status }, [el('div', { class: 'gate-title', text: GATE_TEXT[g.status] || g.status }), el('div', { text: g.message })]));
    var cards = el('div', { class: 'cards' });
    [['Pass', c.counts.pass], ['Info', c.counts.info], ['Warn', c.counts.warn], ['Fail', c.counts.fail], ['Not checked', c.counts.not_checked],
     ['Volumes admitted', c.admitted.length + ' of ' + c.volumes_total]].forEach(function (x) { cards.appendChild(card(x[0], x[1])); });
    v.appendChild(cards);
    v.appendChild(el('p', { class: 'muted', text: c.access === 'full'
      ? 'Access level: APIs, hosts and arrays. Class C volumes can be admitted.'
      : 'Access level: the OpenStack APIs only. Classes A and B are moved by Cinder (a retype). ' + (c.no_mover && c.no_mover.length ? c.no_mover.length + ' class C volume(s) are held back: they need a data mover host and array access.' : '') }));
    if (g.blockers.length) {
      v.appendChild(el('h3', { text: 'Blocks the gate (fix these)' }));
      var ul = el('ul', { class: 'plain' });
      g.blockers.forEach(function (b) { ul.appendChild(el('li', { text: b.id + '  ' + b.title + ': ' + b.evidence })); });
      v.appendChild(ul);
    }
    v.appendChild(el('h3', { text: 'Platform checks' }));
    var t = el('table'), head = el('tr');
    ['ID', 'Check', 'Status', 'Evidence', 'What it means', ''].forEach(function (h) { head.appendChild(el('th', { text: h })); });
    t.appendChild(el('thead', {}, [head]));
    var tb = el('tbody');
    c.platform.concat(c.volume.filter(function (x) { return x.id === 'V9' || x.id === 'V10'; })).forEach(function (k) {
      var a = att[k.id], action = el('td');
      if (k.status === 'warn' || k.status === 'not_checked') {
        var b = el('button', { class: 'secondary', text: a ? 'Withdraw' : 'Attest' });
        b.addEventListener('click', function () {
          if (a) { api('POST', 'checks/attest', { check_id: k.id, by: 'operator', withdraw: true }).then(function (r) { renderChecks(r); refreshStatus(); }); }
          else { attesting = k.id; renderChecks(c); }
        });
        action.appendChild(b);
      }
      var meaning = k.meaning + (a ? '  Attested by ' + a.by + ': ' + a.note : '');
      tb.appendChild(el('tr', {}, [el('td', { class: 'mono', text: k.id }), el('td', { text: k.title }), el('td', {}, [chip(k.status)]),
        el('td', { text: k.evidence }), el('td', { text: meaning }), action]));
      if (attesting === k.id) tb.appendChild(el('tr', {}, [el('td', { colspan: '6' }, [attestForm(c, k)])]));
    });
    t.appendChild(tb); v.appendChild(el('div', { class: 'table-wrap' }, [t]));

    v.appendChild(el('h3', { text: 'Volume checks (every volume)' }));
    var rows = c.volume.filter(function (x) { return x.counts; });
    v.appendChild(table(['ID', 'Check', 'Pass', 'Warn', 'Fail', 'Not checked', ''], rows.map(function (k) {
      return [k.id, k.title, k.counts.pass, k.counts.warn, k.counts.fail, k.counts.not_checked, att[k.id] ? 'attested by ' + att[k.id].by : ''];
    })));
    rows.filter(function (x) { return x.findings && x.findings.length; }).forEach(function (k) {
      var ulf = el('ul'); k.findings.slice(0, 100).forEach(function (f) { ulf.appendChild(el('li', {}, [chip(f.status), document.createTextNode(' '), el('span', { class: 'mono', text: f.volume }), document.createTextNode('  ' + f.evidence)])); });
      if (k.findings.length > 100) ulf.appendChild(el('li', { text: 'and ' + (k.findings.length - 100) + ' more (all are in the report)' }));
      var det = el('details', {}, [el('summary', { text: k.id + ': ' + k.findings.length + ' volume(s) to look at' }), ulf]);
      v.appendChild(det);
      if ((k.status === 'warn' || k.status === 'not_checked') && !att[k.id]) {
        var b2 = el('button', { class: 'secondary', text: 'Attest ' + k.id });
        b2.addEventListener('click', function () { attesting = k.id; renderChecks(c); });
        v.appendChild(b2);
        if (attesting === k.id) v.appendChild(attestForm(c, k));
      }
    });
    v.appendChild(el('h3', { text: 'Sign-off (four eyes)' }));
    v.appendChild(el('p', { class: 'muted', text: 'Two different people sign: one as operator, one as reviewer. A new pre-flight run needs new sign-offs.' }));
    v.appendChild(el('div', { class: 'signs' }, [signBox(c, 'operator', 'Operator'), signBox(c, 'reviewer', 'Reviewer')]));
    var later = el('details', {}, [el('summary', { text: 'Checks that run during and after the migration (' + c.later.length + ')' }),
      table(['ID', 'Check', 'Runs', 'Needs'], c.later.map(function (k) { return [k.id, k.title, k.phase, k.needs.join(', ')]; }))]);
    v.appendChild(later);
  }
  $('pf-run').addEventListener('click', function () {
    guard($('pf-run'), function () {
      attesting = null;
      return api('POST', 'checks/run', { source_backend: $('pf-source').value, target_backend: $('pf-target').value, access: $('pf-access').value })
        .then(function (c) { renderChecks(c); return refreshStatus(); });
    });
  });
  $('pf-demo-validate').addEventListener('click', function () {
    guard($('pf-demo-validate'), function () {
      return api('POST', 'demo/validate', {}).then(function () { banner('ok', 'Recorded a lab validation of the demonstration cloud. Run the pre-flight again.'); return refreshStatus(); });
    });
  });

  $('pf-lab-approve').addEventListener('click', function () {
    guard($('pf-lab-approve'), function () {
      return api('POST', 'lab/approve', {}).then(function (r) { renderChecks(r); banner('ok', 'Lab: the open items were attested and the gate signed by two lab users. This is not a customer sign-off.'); return refreshStatus(); });
    });
  });
  $('pf-demo-approve').addEventListener('click', function () {
    guard($('pf-demo-approve'), function () {
      return api('POST', 'demo/approve', {}).then(function (r) { renderChecks(r); banner('ok', 'Demonstration: the open items were attested and the gate signed by two made-up people.'); return refreshStatus(); });
    });
  });

  // ---- live view ----
  var PHASES = [['precheck', 'Pre-check'], ['validate', 'Validate'], ['copy', 'Copy'], ['sync', 'Keep in sync'], ['recheck', 'Recheck'],
                ['cutover', 'Cutover'], ['postcheck', 'Post-check'], ['soak', 'Soak'], ['done', 'Done'], ['aborted', 'Aborted']];
  var PHASE_HELP = {
    precheck: 'The pre-flight gate is approved for this run. Nothing has been touched.',
    validate: 'The plan for this volume is checked against the target: room, mapping, which host copies. Nothing has been touched.',
    copy: 'The full copy to a staging volume on the target array. The source is only read.',
    sync: 'The target is kept in step with the source until the cutover is possible. The source is only read.',
    recheck: 'Everything is checked again, and then checked again later. Two passes, at least 5 minutes apart, are needed before a cutover.',
    cutover: 'The one step that touches the production path: the instance is switched to the new volume.',
    postcheck: 'The volume is checked from its new path: attachment, instance health, snapshots, tenant-visible fields.',
    soak: 'The waiting period after the cutover. The original source volume is kept untouched so a rollback is still possible. It is released only after the period ends and a person signs off.',
    done: 'The soak period ended and the source volume was released.',
    aborted: 'Stopped before the cutover, with a reason. The source volume was only read and is exactly as it was.'};
  var liveTimer = null;
  function bar(pct, cls) { var b = el('div', { class: 'bar ' + (cls || '') }); var f = el('span'); f.style.width = Math.max(0, Math.min(100, pct)) + '%'; b.appendChild(f); return b; }
  var lastLive = null;
  function renderLive(l) {
    lastLive = l;
    var sim = l.simulation;
    $('live-note').className = 'banner ' + (l.mode === 'simulation' ? 'ok' : 'warn');
    $('live-note').textContent = lastStatus.demo
      ? 'DEMONSTRATION: the migration engine is not built. This view shows a simulated run: made-up evidence, a virtual clock, and the real order, gate and halt rules. Nothing is sent to any system.'
      : 'The migration engine is not built yet, so nothing can be running. This view fills in when migrations run: the order, the checks and every refusal appear here as they happen.';
    $('live-start').classList.toggle('hidden', !lastStatus.demo || (sim && sim.running));
    $('live-stop').classList.toggle('hidden', !(sim && sim.running));
    $('live-status').textContent = sim ? 'Simulation: ' + sim.status + '  |  virtual time ' + sim.virtual_time : '';
    $('live-fill').style.width = l.overall + '%'; $('live-pct').textContent = l.overall.toFixed(1) + '%  (' + l.started + ' of ' + l.total + ' volumes started)';
    var cards = $('live-cards'); clear(cards);
    var fin = (l.counts.soak || 0) + (l.counts.done || 0);
    [['Volumes admitted', l.total], ['In flight', l.active], ['Past cutover', fin + (l.counts.postcheck || 0) + (l.counts.cutover || 0)],
     ['Stopped (source untouched)', l.counts.aborted || 0], ['Rolled back', l.counts.rollback || 0], ['Copy rate (synthetic)', sim ? sim.throughput_mb_s + ' of ' + sim.rate_cap_mb_s + ' MB/s cap' : '-']]
      .forEach(function (c) { cards.appendChild(card(c[0], c[1])); });
    var pipe = $('live-pipe'); clear(pipe);
    PHASES.forEach(function (p) {
      var n = l.counts[p[0]] || 0;
      pipe.appendChild(el('div', { title: PHASE_HELP[p[0]], class: 'stage' + (n ? ' on' : '') + (p[0] === 'cutover' ? ' cut' : '') + (p[0] === 'aborted' ? ' abort' : '') },
        [el('div', { class: 'n', text: String(n) }), el('div', { text: p[1] })]));
    });
    var sf = $('live-safety'); clear(sf);
    var g = l.gate;
    sf.appendChild(el('div', { class: 'gate gate-' + (g ? g.status : 'blocked') }, [el('div', { class: 'gate-title', text: g ? (GATE_TEXT[g.status] || g.status) : 'No pre-flight has run' }), el('div', { text: g ? g.message : '' })]));
    sf.appendChild(l.halted ? el('div', { class: 'banner bad', text: 'HALTED by ' + l.halted.by + ': ' + l.halted.reason }) : el('div', { class: 'muted', text: 'Halt: not pressed.' }));
    sf.appendChild(el('div', { class: 'muted', text: 'Unresolved steps in the journal: ' + l.unresolved_steps }));
    var rules = $('live-rules'); clear(rules); l.cutover_rules.forEach(function (r) { rules.appendChild(el('li', { text: r })); });
    var feed = $('live-feed'); clear(feed);
    l.events.forEach(function (e) {
      feed.appendChild(el('li', { class: e.kind }, [el('span', { class: 't', text: e.ts.slice(11, 19) }), document.createTextNode(e.line)]));
    });
    var tb = $('live-table').tBodies[0]; clear(tb);
    l.volumes.forEach(function (v) {
      var pc = el('td'); pc.appendChild(bar(v.progress, (v.phase === 'aborted' || v.phase === 'rollback') ? 'bad' : (v.phase === 'recheck' ? 'warn' : ''))); pc.appendChild(el('span', { class: 'muted', text: Math.round(v.progress) + '%' }));
      tb.appendChild(el('tr', { 'data-id': v.id, class: 'clickrow' }, [el('td', { class: 'mono', text: v.id.slice(-12) }), el('td', { text: v.name || '-' }), el('td', { text: v['class'] || '-' }), el('td', { text: v.size == null ? '-' : String(v.size) }),
        el('td', {}, [el('span', { class: 'ph ph-' + v.phase, text: v.phase, title: PHASE_HELP[v.phase] || '' })]), pc, el('td', { text: v.note || '' })]));
    });
    $('live-more').textContent = l.volumes_more ? 'and ' + l.volumes_more + ' more' : '';
    if (currentTab === 'live') { loadUnmigrated(); if (vdId) loadVolume(vdId); }
    $('live-release').classList.toggle('hidden', !l.ready);
    $('sign-g23').classList.toggle('hidden', !l.need_g23);
    $('sign-g4').classList.toggle('hidden', !l.need_g4);
    $('sign-demo').classList.toggle('hidden', !(lastStatus && lastStatus.demo));
    $('sign-demo').textContent = l.auto_sign ? 'Demo: stop the made-up second person signing' : 'Demo: a made-up second person signs';
    $('sign-status').textContent = (l.need_g23 ? l.need_g23 + ' volume(s) wait for G2 and G3. ' : '') + (l.need_g4 ? l.need_g4 + ' volume(s) wait for G4. ' : '') +
      (l.need_g23 || l.need_g4 ? 'The signer must not be the operator of the run.' : '');
    $('live-ready').textContent = l.ready ? l.ready + ' volume(s) are in sync and checked, waiting for your release. The settings are on the Select tab.' : '';
    var gp = $('gprog');
    gp.classList.toggle('hidden', !l.started);
    $('gprog-fill').style.width = l.overall + '%';
    $('gprog-text').textContent = 'Migration ' + l.overall.toFixed(0) + '%  |  ' + l.active + ' in flight' + (l.halted ? '  |  HALTED' : '');
  }
  function pollLive(once) {
    api('GET', 'live').then(renderLive).catch(function () {}).then(function () {
      clearTimeout(liveTimer);
      liveTimer = setTimeout(function () { if (!document.hidden) pollLive(); else liveTimer = setTimeout(pollLive, 3000); }, currentTab === 'live' ? 1200 : 4000);
    });
  }

  // ---- volumes not migrated: cause, how, state left behind, proposed steps ----
  var umBusy = false, umLast = 0, umSig = '';
  function loadUnmigrated(force) {
    var now = Date.now();
    if (umBusy || (!force && now - umLast < 4000)) return;
    umBusy = true; umLast = now;
    api('GET', 'unmigrated').then(renderUnmigrated).catch(function (e) {
      clear($('um-cards')); clear($('um-causes')); clear($('um-table').tBodies[0]);
      $('um-status').textContent = 'Not available yet: ' + (e && e.message ? e.message : 'run the inventory and the pre-flight first.');
    }).then(function () { umBusy = false; });
  }
  function renderUnmigrated(d) {
    var sig = JSON.stringify([d.migrated, d.by_cause, d.by_outcome]);
    $('um-status').textContent = '';
    if (sig === umSig) return;                                   // nothing changed: leave the page as it is (keeps open sections open)
    umSig = sig;
    var cards = $('um-cards'); clear(cards);
    [['Migrated', d.migrated + ' of ' + d.total], ['Not migrated', d.total - d.migrated]].concat(
      ['stopped', 'rolled back', 'held back', 'not admitted', 'not started', 'in progress'].filter(function (k) { return d.by_outcome[k]; })
        .map(function (k) { return [k.charAt(0).toUpperCase() + k.slice(1), d.by_outcome[k]]; })).forEach(function (c) { cards.appendChild(card(c[0], c[1])); });
    var box = $('um-causes');
    var opened = {}; Array.prototype.forEach.call(box.querySelectorAll('details[open]'), function (x) { opened[x.getAttribute('data-code')] = true; });
    clear(box);
    d.causes.forEach(function (c) {
      var steps = el('ol', {}, c.steps.map(function (s) { return el('li', { text: s }); }));
      box.appendChild(el('details', Object.assign({ class: 'cause', 'data-code': c.code }, opened[c.code] ? { open: '' } : {}), [
        el('summary', {}, [el('b', { text: c.title }), document.createTextNode('  (' + c.count + ' volume' + (c.count === 1 ? '' : 's') + ')  '), el('span', { class: 'muted', text: 'who acts: ' + c.who })]),
        el('p', { text: c.why }),
        el('p', {}, [el('b', { text: 'Left behind: ' }), document.createTextNode('source: ' + c.source + '  Target: ' + c.target)]),
        el('b', { text: 'Proposed steps to resolve' }), steps]));
    });
    var tb = $('um-table').tBodies[0]; clear(tb);
    d.rows.forEach(function (r) {
      tb.appendChild(el('tr', { title: r.why }, [el('td', { class: 'mono', text: r.volume.slice(-12) }), el('td', { text: r.name || '-' }), el('td', { text: r['class'] || '-' }),
        el('td', { text: String(r.size) }), el('td', {}, [el('span', { class: 'ph ph-' + (r.outcome === 'stopped' ? 'aborted' : r.outcome === 'rolled back' ? 'rollback' : 'validate'), text: r.outcome })]),
        el('td', { text: r.title }), el('td', { text: r.how || '' })]));
    });
    $('um-more').textContent = d.rows_more ? 'and ' + d.rows_more + ' more (all are counted above and in the report)' : '';
  }
  $('um-report').addEventListener('click', function () { downloadReport('unmigrated', $('um-report')); });

  // ---- select: volumes one by one, by group, and the settings ----
  var selData = null, selKind = 'class', selTimer = null;
  function selBy() { return ($('sel-by').value || '').trim(); }
  $('sel-confirm').addEventListener('click', function () {
    var by = $('sel-by').value.trim() || window.prompt('Confirm this selection. Your name:');
    if (!by || by.trim().length < 3) return;
    guard($('sel-confirm'), function () {
      return api('POST', 'selection/confirm', { by: by.trim() }).then(function (r) {
        banner('ok', 'Selection confirmed: ' + r.volumes + ' volume(s). Next: 7. Move.');
        return refreshStatus();
      });
    });
  });
  function drawSelConfirm() {
    var ok = wizardDone('select'), b = $('sel-confirm');
    b.textContent = ok ? 'Selection confirmed' : 'Confirm this selection';
    b.disabled = ok;
    $('sel-confirm-note').textContent = ok ? 'Confirmed. Changing the selection, or running the pre-flight again, asks for it to be confirmed again.' :
      'Step 7 stays closed until you confirm the selection as it stands.';
  }
  function drawMoveGate() {
    var w = wizardInfo('move');
    if (w) { drawSelConfirm(); }
    var st = $('mv-start');
    if (st) {                                       // a real run needs a dry run read first; the server enforces it too
      st.disabled = !wizardDone('move');
      st.title = st.disabled ? 'Run the dry run first (button 1).' : '';
    }
  }
  function loadSelection() {
    api('GET', 'selection').then(renderSelection).catch(function (e) {
      $('sel-summary').className = 'banner warn'; $('sel-summary').textContent = 'Not available yet: ' + e.message + ' Run the inventory and the pre-flight first.';
    });
  }
  function postSelection(body) { body.by = selBy(); return api('POST', 'selection', body).then(function (d) { renderSelection(d); refreshStatus(); if (d.refused && d.refused.length) banner('warn', d.refused.length + ' volume(s) were not selected: ' + d.refused[0].reason + '.'); }); }
  function renderSelection(d) {
    selData = d;
    var sum = $('sel-summary');
    sum.className = 'banner ' + (d.selected ? 'ok' : 'warn');
    sum.textContent = d.selected + ' of ' + d.eligible + ' admitted volumes are selected (' + d.selected_gib + ' GiB); ' + (d.total - d.eligible) + ' of ' + d.total + ' are held back by the pre-flight.' +
      (d.default ? ' This is the default selection.' : ' The selection was changed.') + (d.running ? ' A run is in progress: stop it to change the selection.' : '');
    ['sel-all', 'sel-none', 'sel-reset'].forEach(function (id) { $(id).disabled = !!d.running; });
    var ks = $('sel-kind');
    if (!ks.options.length) {
      d.group_kinds.forEach(function (k) { ks.appendChild(el('option', { value: k[0], text: k[1] })); });
      ks.value = selKind;
    }
    var tb = $('sel-groups').tBodies[0]; clear(tb);
    (d.groups[selKind] || []).forEach(function (g) {
      function btn(label, action) {
        var b = el('button', { class: 'secondary small', text: label }); b.disabled = !g.eligible || !!d.running;
        b.addEventListener('click', function () { postSelection({ action: action, group: { kind: selKind, key: g.key } }).catch(function (e) { banner('bad', e.message); }); });
        return b;
      }
      tb.appendChild(el('tr', {}, [el('td', { text: g.key }), el('td', { text: String(g.count) }), el('td', { text: String(g.eligible) }), el('td', { text: String(g.gib) }),
        el('td', {}, [btn('Add', 'add'), document.createTextNode(' '), btn('Only', 'set'), document.createTextNode(' '), btn('Remove', 'remove')])]));
    });
    drawSelVolumes();
    drawOptions(d);
  }
  function drawSelVolumes() {
    if (!selData) return;
    var q = ($('sel-filter').value || '').toLowerCase(), tb = $('sel-table').tBodies[0]; clear(tb);
    var rows = selData.volumes.filter(function (v) { return !q || [v.id, v.name, v.project, v['class'], v.attached.join(' ')].join(' ').toLowerCase().indexOf(q) >= 0; });
    rows.slice(0, 600).forEach(function (v) {
      var cb = el('input', { type: 'checkbox' }); cb.checked = v.selected; cb.disabled = !v.eligible || selData.running;
      cb.addEventListener('change', function () { postSelection({ action: cb.checked ? 'add' : 'remove', ids: [v.id] }).catch(function (e) { banner('bad', e.message); }); });
      var tr = el('tr', { title: v.why || 'Click for the steps of this volume' }, [el('td', {}, [cb]), el('td', { class: 'mono', text: v.id.slice(-12) }), el('td', { text: v.name || '-' }), el('td', { text: v['class'] || '-' }),
        el('td', { text: v.project }), el('td', { text: String(v.size) }), el('td', { text: String(v.snapshots) }), el('td', { text: v.attached.join(', ') }),
        el('td', { text: v.eligible ? (v.selected ? 'selected' : 'not selected') : 'held back: ' + v.why })]);
      tr.addEventListener('click', function (e) { if (e.target.tagName === 'INPUT') return; showTab('live'); loadVolume(v.id, true); });
      tb.appendChild(tr);
    });
    $('sel-count').textContent = rows.length + ' shown';
    $('sel-more').textContent = rows.length > 600 ? 'and ' + (rows.length - 600) + ' more: use the filter or a group' : '';
  }
  function drawOptions(d) {
    var box = $('sel-options');
    if (box.firstChild && box.getAttribute('data-sig') === JSON.stringify(d.options)) return;
    if (box.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;     // do not redraw under the cursor
    box.setAttribute('data-sig', JSON.stringify(d.options)); clear(box);
    Object.keys(d.labels).forEach(function (k) {
      var lim = d.limits[k], wrap;
      if (typeof d.defaults[k] === 'boolean') {
        var cb = el('input', { type: 'checkbox' }); cb.checked = d.options[k];
        cb.addEventListener('change', function () { setOption(k, cb.checked); });
        wrap = el('label', { class: 'check', title: 'default: ' + (d.defaults[k] ? 'on' : 'off') }, [cb, document.createTextNode(' ' + d.labels[k])]);
      } else {
        var inp = el('input', { type: 'number', min: String(lim[0]), max: String(lim[1]), value: String(d.options[k]), title: lim[2] });
        inp.addEventListener('change', function () { setOption(k, parseInt(inp.value, 10)); });
        wrap = el('label', { title: lim[2] }, [document.createTextNode(d.labels[k] + ' (' + lim[0] + ' to ' + lim[1] + ', default ' + d.defaults[k] + ')'), inp]);
      }
      box.appendChild(wrap);
    });
  }
  function setOption(k, v) {
    var o = {}; o[k] = v;
    postSelection({ options: o }).then(function () { $('opt-status').textContent = 'Saved.'; }).catch(function (e) { banner('bad', e.message); loadSelection(); });
  }
  $('sel-kind').addEventListener('change', function () { selKind = $('sel-kind').value; if (selData) renderSelection(selData); });
  $('sel-filter').addEventListener('input', drawSelVolumes);
  $('sel-all').addEventListener('click', function () { postSelection({ action: 'add', ids: selData.volumes.filter(function (v) { return v.eligible; }).map(function (v) { return v.id; }) }).catch(function (e) { banner('bad', e.message); }); });
  $('sel-none').addEventListener('click', function () { postSelection({ action: 'none' }).catch(function (e) { banner('bad', e.message); }); });
  $('sel-reset').addEventListener('click', function () { postSelection({ action: 'reset' }).catch(function (e) { banner('bad', e.message); }); });
  $('opt-reset').addEventListener('click', function () { postSelection({ reset_options: true }).then(function () { $('opt-status').textContent = 'Back to the defaults.'; }).catch(function (e) { banner('bad', e.message); }); });

  // ---- workload risk: clear, mark ----
  var riskData = null;
  function loadRisk() {
    api('GET', 'risk').then(renderRisk).catch(function () { clear($('risk-cards')); clear($('risk-table').tBodies[0]); });
  }
  function renderRisk(d) {
    riskData = d;
    var c = $('risk-cards'); clear(c);
    [['High risk (not moved)', d.high - d.cleared], ['High risk, cleared by two people', d.cleared], ['Medium risk (shown, not blocked)', d.medium]].forEach(function (x) { c.appendChild(card(x[0], x[1])); });
    var tb = $('risk-table').tBodies[0]; clear(tb);
    d.rows.forEach(function (r) {
      var cb = el('input', { type: 'checkbox' }); cb.setAttribute('data-id', r.id); cb.disabled = r.level !== 'high' && !r.marked;
      tb.appendChild(el('tr', {}, [el('td', {}, [cb]), el('td', { class: 'mono', text: r.id.slice(-12) }), el('td', { text: r.name || '-' }), el('td', { text: r['class'] || '-' }),
        el('td', {}, [el('span', { class: 'ph ' + (r.level === 'high' ? (r.cleared ? 'ph-cutover' : 'ph-aborted') : 'ph-validate'), text: r.level === 'high' ? (r.cleared ? 'high, cleared' : 'high: not moved') : 'medium' })]),
        el('td', { text: r.reasons.join(' | ') }), el('td', { text: r.cleared ? (r.operator + ' and ' + r.reviewer) : (r.operator ? 'operator: ' + r.operator + ' (reviewer needed)' : (r.reviewer ? 'reviewer: ' + r.reviewer + ' (operator needed)' : '')) })]));
    });
    var adv = $('risk-advice'); clear(adv);
    if (d.rows.length) {
      adv.appendChild(el('p', { class: 'muted', text: d.note }));
      adv.appendChild(el('details', { class: 'cause' }, [el('summary', { text: 'Advice for the reasons above' }),
        el('ul', {}, Array.from(new Set([].concat.apply([], d.rows.map(function (r) { return r.advice; })))).map(function (a) { return el('li', { text: a }); }))]));
    }
  }
  function tickedRisk() { return Array.prototype.map.call($('risk-table').querySelectorAll('input[type=checkbox]:checked'), function (b) { return b.getAttribute('data-id'); }); }
  function riskAction(path, body, btn) {
    guard(btn, function () { return api('POST', path, body).then(function (d) { renderRisk(d); banner('ok', d.rerun || 'Done.'); loadSelection(); refreshStatus(); }); });
  }
  function riskClear(role, btn, withdraw) {
    var ids = tickedRisk(); if (!ids.length) { banner('warn', 'Tick the volumes first.'); return; }
    var by = window.prompt((withdraw ? 'Withdraw the clearance as ' : 'Clear as ') + role + '.\n\nYour name:'); if (!by) return;
    var ev = withdraw ? 'withdrawn' : window.prompt('Evidence (at least 20 characters): what was tested, what the owner agreed, what the way back is:');
    if (!ev) return;
    riskAction('risk/clear', { ids: ids, role: role, by: by, evidence: ev, withdraw: !!withdraw }, btn);
  }
  $('risk-op').addEventListener('click', function () { riskClear('operator', $('risk-op')); });
  $('risk-rv').addEventListener('click', function () { riskClear('reviewer', $('risk-rv')); });
  $('risk-wd').addEventListener('click', function () { riskClear('operator', $('risk-wd'), true); });
  $('risk-mark').addEventListener('click', function () {
    var ids = Array.prototype.map.call($('sel-table').querySelectorAll('input[type=checkbox]:checked'), function (b) { return b.closest('tr').children[1].textContent; });
    if (!ids.length || !selData) { banner('warn', 'Tick volumes in the list above (Chosen column) to mark them.'); return; }
    var full = selData.volumes.filter(function (v) { return ids.indexOf(v.id.slice(-12)) >= 0; }).map(function (v) { return v.id; });
    var by = window.prompt('Mark ' + full.length + ' volume(s) as a risk.\n\nYour name:'); if (!by) return;
    var note = window.prompt('Why (the application and the owner, at least 8 characters):'); if (!note) return;
    riskAction('risk/mark', { ids: full, by: by, note: note }, $('risk-mark'));
  });
  $('risk-unmark').addEventListener('click', function () {
    var ids = tickedRisk(); if (!ids.length) { banner('warn', 'Tick the marked volumes in the risk list first.'); return; }
    var by = window.prompt('Remove the mark.\n\nYour name:'); if (!by) return;
    riskAction('risk/mark', { ids: ids, by: by, withdraw: true }, $('risk-unmark'));
  });

  // ---- one volume, step by step ----
  var vdId = null, vdLast = 0;
  function loadVolume(id, force) {
    vdId = id;
    var now = Date.now(); if (!force && now - vdLast < 2500) return; vdLast = now;
    api('GET', 'volume?id=' + encodeURIComponent(id)).then(function (v) { renderVolume(v); if (force) $('vd').scrollIntoView({ behavior: 'smooth', block: 'start' }); }).catch(function (e) { banner('bad', e.message); });
  }
  function renderVolume(v) {
    var box = $('vd'); box.classList.remove('hidden');
    var opened = {}; Array.prototype.forEach.call(box.querySelectorAll('details[open]'), function (x) { opened[x.getAttribute('data-k')] = true; });
    clear(box);
    var close = el('button', { class: 'secondary small', text: 'Close' }); close.addEventListener('click', function () { vdId = null; box.classList.add('hidden'); });
    box.appendChild(el('div', { class: 'vd-head' }, [el('h3', { text: 'Volume ' + (v.name || v.id.slice(-12)) }), close]));
    box.appendChild(el('div', { class: 'muted mono', text: v.id + '   class ' + v['class'] + ' (' + v.class_title + ')   ' + v.size + ' GiB   ' + v.snapshots + ' snapshot(s)' + (v.attached_to.length ? '   attached to ' + v.attached_to.join(', ') : '') }));
    box.appendChild(el('div', { class: 'banner ' + (v.unmigrated && (v.unmigrated.outcome === 'stopped' || v.unmigrated.outcome === 'rolled back') ? 'bad' : v.ready ? 'warn' : 'ok') }, [el('b', { text: 'Right now: ' }), document.createTextNode(v.now)]));
    if (v.ready) {
      var rb = el('button', { class: 'primary', text: 'Release this volume for cutover' });
      rb.addEventListener('click', function () { releaseVolumes({ ids: [v.id] }, rb); });
      box.appendChild(rb);
    }
    box.appendChild(el('p', { class: 'muted', text: 'Method: ' + v.method + '. Source now: ' + v.source_now + ' Target now: ' + v.target_now }));
    if (v.risk) box.appendChild(el('div', { class: 'banner ' + (v.risk.level === 'high' ? 'bad' : 'warn') }, [el('b', { text: 'Workload risk (' + v.risk.level + '): ' }), document.createTextNode(v.risk.reasons.join(' | '))]));
    var ol = el('ol', { class: 'steps' });
    v.steps.forEach(function (s) {
      var d = el('details', { 'data-k': s.id }, [el('summary', {}, [el('span', { class: 'st st-' + s.status, text: s.status }), document.createTextNode(' ' + s.title)]),
        el('p', { text: s.does }), el('p', { class: 'muted' }, [el('b', { text: 'Touches: ' }), document.createTextNode(s.touches)]),
        el('p', { class: 'muted' }, [el('b', { text: 'Evidence recorded: ' }), document.createTextNode(s.evidence)]), el('p', { class: 'muted' }, [el('b', { text: 'Refused when: ' }), document.createTextNode(s.refused)])]);
      if (opened[s.id] || s.status === 'current') d.setAttribute('open', '');
      ol.appendChild(el('li', { class: 'step-' + s.status }, [d]));
    });
    box.appendChild(ol);
    if (v.readiness.length) {
      box.appendChild(el('h4', { text: 'Conditions for the cutover, asked now' }));
      box.appendChild(el('ul', { class: 'cond' }, v.readiness.map(function (c) { return el('li', { class: c.ok ? 'ok' : (c.pending ? 'pend' : 'no') }, [document.createTextNode((c.ok ? 'met: ' : c.pending ? 'not yet: ' : 'NOT met: ') + c.name + ' - ' + c.detail)]); })));
    }
    if (v.unmigrated) {
      var u = v.unmigrated;
      box.appendChild(el('h4', { text: 'Not migrated: ' + u.title }));
      box.appendChild(el('p', { text: u.why }));
      box.appendChild(el('p', { class: 'muted', text: 'Source: ' + u.source + ' Target: ' + u.target }));
      box.appendChild(el('ol', {}, u.steps.map(function (x) { return el('li', { text: x }); })));
    }
    if (v.events.length) {
      box.appendChild(el('h4', { text: 'What was recorded' }));
      var t = el('table', {}, [el('thead', {}, [el('tr', {}, [el('th', { text: 'Time' }), el('th', { text: 'Phase' }), el('th', { text: 'Evidence' })])]), el('tbody')]);
      v.events.forEach(function (e) { t.tBodies[0].appendChild(el('tr', {}, [el('td', { text: (e.at || '').replace('T', ' ').replace('Z', '') }), el('td', { text: e.phase }), el('td', { text: e.evidence })])); });
      box.appendChild(el('div', { class: 'table-wrap' }, [t]));
    }
  }
  function releaseVolumes(body, btn) {
    var by = window.prompt('Release for cutover. The cutover rules are still asked again for each volume.\n\nYour name:'); if (!by || by.trim().length < 3) return;
    body.by = by.trim();
    guard(btn, function () { return api('POST', 'release', body).then(function (l) { renderLive(l); banner('ok', 'Released. The cutover is attempted only if every condition holds.'); if (vdId) loadVolume(vdId, true); }); });
  }
  function signGates(names, btn) {
    var by = window.prompt('Sign as the SECOND person. You must not be the operator of this run.' + String.fromCharCode(10, 10) + 'Your name:' + (lastStatus && lastStatus.demo ? '  (demonstration: try "Demo reviewer")' : '')); if (!by || by.trim().length < 3) return;
    var note = window.prompt('What did you check? (at least 8 characters, kept in the journal)'); if (!note || note.trim().length < 8) return;
    guard(btn, function () {
      var chain = Promise.resolve(null);
      names.forEach(function (g) {
        chain = chain.then(function () { return api('POST', 'volume-gate', { gate: g, all: true, by: by.trim(), evidence: note.trim() }); });
      });
      return chain.then(function (l) { renderLive(l); banner('ok', 'Signed ' + names.join(' and ') + ' for the volumes that were at that point.'); if (vdId) loadVolume(vdId, true); });
    });
  }
  $('sign-g23').addEventListener('click', function () { signGates(['G2', 'G3'], $('sign-g23')); });
  $('sign-g4').addEventListener('click', function () { signGates(['G4'], $('sign-g4')); });
  $('sign-demo').addEventListener('click', function () {
    guard($('sign-demo'), function () { return api('POST', 'demo/simulate', { action: 'autosign', on: !(lastLive && lastLive.auto_sign) }).then(renderLive); });
  });
  $('live-release').addEventListener('click', function () { releaseVolumes({ all: true }, $('live-release')); });

  // ---- move (the engine) ----
  var moveTimer = null;
  function typeMap() { var t = $('mv-type').value.trim(); return t ? { '*': t } : {}; }
  function drawMove(m) {
    var cards = $('mv-cards'); clear(cards);
    [['Run', m.status], ['Queued', m.queued], ['Active', m.active], ['Finished', m.finished], ['Failed', m.failed]].forEach(function (x) { cards.appendChild(card(x[0], x[1])); });
    var tb = $('mv-table').tBodies[0]; clear(tb);
    (m.volumes || []).forEach(function (r) {
      var act = el('td');
      if (r.reversible) {
        var b = el('button', { class: 'secondary', text: 'Reverse' });
        b.addEventListener('click', function () {
          var by = window.prompt('Reverse ' + r.id + ' with a retype back.' + String.fromCharCode(10, 10) + 'Your name:'); if (!by) return;
          var why = window.prompt('Reason (at least 8 characters):'); if (!why) return;
          guard(b, function () { return api('POST', 'engine/reverse', { id: r.id, by: by.trim(), reason: why.trim() }).then(drawMove); });
        });
        act.appendChild(b);
      }
      tb.appendChild(el('tr', {}, [el('td', { text: r.id }), el('td', { text: r.class || '' }), el('td', { text: r.phase }),
        el('td', { text: r.gates.G2 ? 'signed' : '' }), el('td', { text: r.gates.G3 ? 'signed' : '' }), el('td', { text: r.gates.G4 ? 'signed' : '' }),
        el('td', { text: r.note || '' }), act]));
    });
    $('mv-more').textContent = m.more ? m.more + ' more volume(s) not shown.' : '';
    $('mv-status').textContent = m.halted ? 'HALTED by ' + m.halted.by + ': ' + m.halted.reason : '';
    if (moveTimer) { clearTimeout(moveTimer); moveTimer = null; }
    if (m.status === 'running' && currentTab === 'move') moveTimer = setTimeout(loadMove, 4000);
  }
  function loadMove() { return api('GET', 'engine').then(drawMove).catch(function () {}); }
  function drawDry(d) {
    var v = $('mv-dry-view'); clear(v);
    v.appendChild(el('div', { class: 'banner ok', text: 'Dry run: ' + d.counts.ok + ' ready, ' + d.counts.wait + ' waiting, ' + d.counts.blocked + ' blocked. ' + d.note }));
    var t = el('table', {}, [el('thead', {}, [el('tr', {}, ['Volume', 'Class', 'Result', 'Target type', 'Why or what would be sent'].map(function (h) { return el('th', { text: h }); }))]), el('tbody')]);
    d.volumes.forEach(function (r) {
      var why = r.error ? r.error : r.checks.filter(function (c) { return !c.ok; }).map(function (c) { return c.detail; }).join('; ');
      var calls = (r.calls || []).map(function (c) { return c.what + '  (undo: ' + c.undo + ')'; }).join(String.fromCharCode(10));
      var td = el('td', { text: why || calls }); td.style.whiteSpace = 'pre-wrap';
      t.tBodies[0].appendChild(el('tr', {}, [el('td', { text: r.id }), el('td', { text: r.class || '' }), el('td', { text: r.verdict }), el('td', { text: r.target_type || '' }), td]));
    });
    v.appendChild(el('div', { class: 'table-wrap' }, [t]));
  }
  $('mv-dry').addEventListener('click', function () {
    guard($('mv-dry'), function () { return api('POST', 'engine/dry-run', { type_map: typeMap() }).then(drawDry).then(refreshStatus); });
  });
  $('mv-start').addEventListener('click', function () {
    var by = window.prompt('Start the real run. Volumes are changed in the cloud.' + String.fromCharCode(10, 10) + 'Your name:'); if (!by || by.trim().length < 3) return;
    guard($('mv-start'), function () { return api('POST', 'engine/start', { by: by.trim(), type_map: typeMap() }).then(function (m) { drawMove(m); banner('ok', 'The run has started. Nothing is sent for a volume until G2 and G3 are signed by a second person.'); }); });
  });
  $('mv-stop').addEventListener('click', function () { guard($('mv-stop'), function () { return api('POST', 'engine/stop', {}).then(drawMove); }); });
  $('mv-refresh').addEventListener('click', loadMove);
  $('mv-release').addEventListener('click', function () { releaseVolumes({ all: true }, $('mv-release')); });
  $('mv-g23').addEventListener('click', function () { signGates(['G2', 'G3'], $('mv-g23')); });
  $('mv-g4').addEventListener('click', function () { signGates(['G4'], $('mv-g4')); });
  $('mv-validate').addEventListener('click', function () {
    var nl = String.fromCharCode(10, 10);
    var by = window.prompt('Gate G0: record that the lab tests for this OpenStack release were run and passed. This lifts the read-only state of the release.' + nl + 'Your name:'); if (!by) return;
    var second = window.prompt('The second person (a different person):'); if (!second) return;
    var ev = window.prompt('What was tested, on which cloud, with what result (at least 40 characters):'); if (!ev) return;
    guard($('mv-validate'), function () {
      return api('POST', 'validation', { by: by.trim(), second: second.trim(), evidence: ev.trim(), passed: true, environment: 'test' })
        .then(function (r) { banner('ok', 'Recorded the lab validation of ' + r.release + '. Run the pre-flight again.'); return refreshStatus(); });
    });
  });
  $('live-table').addEventListener('click', function (e) {
    var tr = e.target.closest ? e.target.closest('tr') : null;
    if (!tr || !tr.getAttribute('data-id') || e.target.tagName === 'BUTTON') return;
    loadVolume(tr.getAttribute('data-id'), true);
  });
  $('live-start').addEventListener('click', function () {
    guard($('live-start'), function () { return api('POST', 'demo/simulate', { action: 'start' }).then(renderLive); });
  });
  $('live-stop').addEventListener('click', function () {
    guard($('live-stop'), function () { return api('POST', 'demo/simulate', { action: 'stop' }).then(renderLive); });
  });

  // ---- reports ----
  function downloadReport(kind, btn) {
    guard(btn, function () {
      var body = { kind: kind, customer: $('rep-customer').value, prepared_by: $('rep-by').value, include_audit: $('rep-audit').checked };
      return fetch('/api/report', { method: 'POST', headers: { 'X-OpenMove-Token': TOKEN, 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        .then(function (r) {
          if (!r.ok) return r.json().then(function (d) { throw new Error(d.error || 'the report could not be built'); });
          var m = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/), name = m ? m[1] : 'OpenMove_report.docx';
          return r.blob().then(function (b) {
            var a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = name; document.body.appendChild(a); a.click(); a.remove();
            setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
            $('rep-status').textContent = 'Built ' + name + ' (' + Math.round(b.size / 1024) + ' KB). Your browser saved it to its download folder.';
          });
        });
    });
  }
  $('rep-before').addEventListener('click', function () { downloadReport('before', $('rep-before')); });
  $('rep-audit-only').addEventListener('click', function () { downloadReport('audit', $('rep-audit-only')); });
  $('rep-unmigrated').addEventListener('click', function () { downloadReport('unmigrated', $('rep-unmigrated')); });

  // ---- sortable tables: click a column header; click again to reverse ----
  // Works on every table with a header row, including tables drawn later. The chosen sort is kept when a table is redrawn
  // (the live view redraws every second). The inventory table sorts its data before the first rows are cut off.
  var sortState = {};
  function sortKey(t) {
    if (t.id) return t.id;
    var a = t.parentElement; while (a && !a.id) a = a.parentElement;
    var all = a ? a.querySelectorAll('table') : [];
    return (a ? a.id : 'page') + ':' + Array.prototype.indexOf.call(all, t);
  }
  function sortSig(t) { return Array.prototype.map.call(t.tHead.rows[0].cells, function (c) { return c.textContent; }).join('|'); }
  var NUMBER = /^-?\d[\d,]*(\.\d+)?\s*%?$/;
  function isEmpty(v) { return v === '' || v === '-'; }
  function compareValues(x, y, numeric, dir) {
    var ex = isEmpty(x), ey = isEmpty(y);
    if (ex || ey) return ex === ey ? 0 : (ex ? 1 : -1);          // empty cells always go last
    var c = numeric ? parseFloat(x.replace(/,/g, '')) - parseFloat(y.replace(/,/g, '')) : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: 'base' });
    return dir === 'desc' ? -c : c;
  }
  function looksNumeric(vals) { return vals.some(function (v) { return NUMBER.test(v); }) && vals.every(function (v) { return isEmpty(v) || NUMBER.test(v); }); }
  function sortRows(t, col, dir) {
    var tb = t.tBodies[0]; if (!tb) return;
    var groups = [];
    Array.prototype.forEach.call(tb.rows, function (r) {            // a full-width row (an open form) stays with the row above it
      if (r.querySelector('td[colspan]') && groups.length) groups[groups.length - 1].push(r); else groups.push([r]);
    });
    if (groups.length < 2) return;
    var vals = groups.map(function (g) { var c = g[0].cells[col]; return c ? c.textContent.trim() : ''; });
    var numeric = looksNumeric(vals);
    var order = vals.map(function (v, i) { return i; });
    order.sort(function (a, b) { return compareValues(vals[a], vals[b], numeric, dir) || (a - b); });
    if (order.every(function (v, i) { return v === i; })) return;   // already in order: change nothing
    var frag = document.createDocumentFragment();
    order.forEach(function (i) { groups[i].forEach(function (r) { frag.appendChild(r); }); });
    tb.appendChild(frag);
  }
  function markSorted(t) {
    var s = sortState[sortKey(t)], sig = sortSig(t);
    Array.prototype.forEach.call(t.tHead.rows[0].cells, function (c, i) {
      c.classList.add('sortable'); c.tabIndex = 0; c.setAttribute('role', 'columnheader');
      if (s && s.sig === sig && s.col === i) { c.setAttribute('data-sort', s.dir); c.setAttribute('aria-sort', s.dir === 'asc' ? 'ascending' : 'descending'); }
      else { c.removeAttribute('data-sort'); c.removeAttribute('aria-sort'); }
    });
  }
  function prepareTable(t) {
    if (!t.tHead || !t.tHead.rows.length || !t.tHead.rows[0].cells.length) return;
    markSorted(t);
    var s = sortState[sortKey(t)];
    if (s && s.sig === sortSig(t) && t.id !== 'inv-table') sortRows(t, s.col, s.dir);
  }
  function toggleSort(t, col) {
    var key = sortKey(t), sig = sortSig(t), s = sortState[key];
    var dir = (s && s.sig === sig && s.col === col && s.dir === 'asc') ? 'desc' : 'asc';
    sortState[key] = { col: col, dir: dir, sig: sig };
    markSorted(t);
    if (t.id === 'inv-table') drawRows(); else sortRows(t, col, dir);
  }
  var INV_VALUE = [function (v) { return v.id; }, function (v) { return v['class']; }, function (v) { return v.reason; },
    function (v) { return String(v.size); }, function (v) { return String(v.snapshots); }, function (v) { return v.status || ''; },
    function (v) { return v.backend || v.host || ''; },
    function (v) { return (v.attachments || []).map(function (a) { return a.server_id; }).filter(Boolean).join(', '); }];
  function sortInventoryRows(rows) {
    var s = sortState['inv-table'];
    if (!s || !INV_VALUE[s.col]) return rows;
    var get = INV_VALUE[s.col], numeric = looksNumeric(rows.map(function (v) { return get(v); }));
    return rows.map(function (v, i) { return [v, i]; }).sort(function (a, b) {
      return compareValues(get(a[0]), get(b[0]), numeric, s.dir) || (a[1] - b[1]);
    }).map(function (p) { return p[0]; });
  }
  document.addEventListener('click', function (e) {
    var th = e.target.closest ? e.target.closest('th') : null;
    if (!th || !th.parentElement.parentElement || th.parentElement.parentElement.tagName !== 'THEAD') return;
    var t = th.closest('table'); if (t && t.tHead) toggleSort(t, th.cellIndex);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var th = e.target.closest ? e.target.closest('th.sortable') : null;
    if (th) { e.preventDefault(); th.click(); }
  });
  var sortPending = {}, sortTimer = null;
  new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      var n = m.target.nodeType === 1 ? m.target : m.target.parentElement;
      var tables = [];
      if (n && n.closest && n.closest('table')) tables.push(n.closest('table'));
      Array.prototype.forEach.call(m.addedNodes, function (a) {
        if (a.nodeType !== 1) return;
        if (a.tagName === 'TABLE') tables.push(a); else if (a.querySelectorAll) Array.prototype.forEach.call(a.querySelectorAll('table'), function (x) { tables.push(x); });
      });
      tables.forEach(function (t) { sortPending[sortKey(t)] = t; });
    });
    clearTimeout(sortTimer);
    sortTimer = setTimeout(function () {
      var list = sortPending; sortPending = {};
      Object.keys(list).forEach(function (k) { prepareTable(list[k]); });
    }, 40);
  }).observe(document.body, { childList: true, subtree: true });
  document.querySelectorAll('table').forEach(prepareTable);

  refreshStatus();
  pollLive();
})();
