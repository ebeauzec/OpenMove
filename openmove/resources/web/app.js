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

  // A message is fixed to the top of the window, so it is seen wherever the page is scrolled. A good message goes away by itself; a problem stays until it is closed.
  var bannerTimer = null;
  function banner(kind, text) {
    var b = $('banner');
    clearTimeout(bannerTimer);
    if (!text) { b.className = 'banner hidden'; return; }
    b.className = 'banner ' + kind; b.textContent = text;
    var x = el('button', { class: 'x', type: 'button', text: '\u00d7', title: 'Close this message', 'aria-label': 'Close this message' });
    x.addEventListener('click', function () { b.className = 'banner hidden'; });
    b.appendChild(x);
    if (kind === 'ok') bannerTimer = setTimeout(function () { b.className = 'banner hidden'; }, 9000);
  }
  function busy(btn, on) { btn.disabled = on; btn.classList.toggle('working', on); }
  function guard(btn, fn) {
    busy(btn, true); banner('', '');
    return fn().catch(function (e) { banner('bad', e.message); }).then(function () { busy(btn, false); });
  }

  // One clear box instead of a chain of pop-ups. If the server refuses, the reason is shown inside the box and the box stays open.
  // fields: {key, label, type (text, textarea, select, password), hint, value, min (least characters), options [[value, text]]}
  var me = '';                                                    // the name the person gave, kept for this page only
  function ask(o) {
    return new Promise(function (resolve) {
      var d = $('ask'); clear(d);
      var fields = o.fields || [], inputs = {};
      var err = el('div', { class: 'banner bad static hidden', role: 'alert' });
      var form = el('form', { method: 'dialog' });
      form.appendChild(el('h3', { text: o.title }));
      if (o.intro) form.appendChild(el('p', { class: 'muted pre', text: o.intro }));
      fields.forEach(function (f) {
        var inp;
        if (f.type === 'textarea') inp = el('textarea', { rows: String(f.rows || 3) });
        else if (f.type === 'select') { inp = el('select'); f.options.forEach(function (op) { inp.appendChild(el('option', { value: op[0], text: op[1] })); }); }
        else inp = el('input', { type: f.type || 'text' });
        if (f.placeholder) inp.setAttribute('placeholder', f.placeholder);
        if (f.value != null) inp.value = f.value;
        inputs[f.key] = inp;
        form.appendChild(el('label', {}, [f.label, inp].concat(f.hint ? [el('span', { class: 'hint', text: f.hint })] : [])));
      });
      form.appendChild(err);
      var ok = el('button', { class: o.danger ? 'primary danger' : 'primary', type: 'submit', text: o.ok || 'OK' });
      var cancel = el('button', { class: 'secondary', type: 'button', text: o.cancel || 'Cancel' });
      form.appendChild(el('div', { class: 'actions' }, [ok, cancel]));
      d.appendChild(form);
      var finished = false;
      function done(v) { if (finished) return; finished = true; if (d.open) d.close(); resolve(v); }
      function fail(msg) { err.textContent = msg; err.classList.remove('hidden'); ok.disabled = false; ok.classList.remove('working'); }
      cancel.addEventListener('click', function () { done(null); });
      d.addEventListener('cancel', function (ev) { ev.preventDefault(); done(null); });
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var vals = {}, bad = '';
        fields.forEach(function (f) {
          var v = inputs[f.key].value;
          if (f.type !== 'password') v = v.trim();
          vals[f.key] = v;
          if (!bad && f.min && v.length < f.min) bad = f.label + (f.min <= 3 ? ' is needed.' : ': please write at least ' + f.min + ' characters (you wrote ' + v.length + ').');
        });
        if (bad) { fail(bad); return; }
        err.classList.add('hidden');
        if (!o.submit) { done(vals); return; }
        ok.disabled = true; ok.classList.add('working');
        Promise.resolve().then(function () { return o.submit(vals); }).then(function (r) { done(r === undefined ? vals : r); }, function (e) { fail((e && e.message) || String(e)); });
      });
      d.showModal();
      (fields.length ? inputs[fields[0].key] : ok).focus();
    });
  }
  function nameField(label, hint, prefill) { return { key: 'by', label: label || 'Your name', hint: hint, min: 3, value: prefill === false ? '' : me }; }

  var currentTab = 'connect';
  // ---- tabs ----
  function showTab(name, force) {
    var w = wizardInfo(name);
    if (w && !w.open && !force) { banner('warn', 'Not yet. ' + w.needs); return; }
    document.querySelectorAll('#tabs button').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === name); });
    document.querySelectorAll('.panel').forEach(function (p) { p.classList.toggle('hidden', p.id !== 'tab-' + name); });
    if (name === 'journal') loadJournal();
    if (name === 'scan' && lastStatus.inventory) loadTree();
    if (name === 'preflight') fillBackends();
    if (name === 'live') { pollLive(true); loadUnmigrated(true); }
    if (name === 'select') { loadSelection(); loadRisk(); loadTree(); }
    if (name === 'move') loadMove();
    if (name === 'hosts') loadHosts();
    if (name === 'diagnostics') loadDiag();
    currentTab = name;
    drawWizard();
  }
  // The wizard is gated: the server says which step is open (every step before it is done) and what is missing for the next one.
  function rawStep(tab) {
    var list = (lastStatus && lastStatus.wizard) || [];
    for (var k = 0; k < list.length; k++) { if (list[k].id === tab) return list[k]; }
    return null;
  }
  var SCAN_STEPS = ['platform', 'inventory', 'audit'];              // on the page these three are one step, "Scan"
  function stageOf(id) { return SCAN_STEPS.indexOf(id) >= 0 ? 'scan' : id; }
  function wizardInfo(tab) {
    if (tab !== 'scan') return rawStep(tab);
    var parts = SCAN_STEPS.map(rawStep);
    if (!parts[0]) return null;
    return { id: 'scan', open: parts[0].open, done: parts.every(function (p) { return p && p.done; }), needs: parts[0].needs };
  }
  function wizardDone(tab) { var w = wizardInfo(tab); return !w || w.done; }
  // Back and Next walk through the numbered steps in order, so the tabs at the top are not the only way to move
  function wizardSteps() {
    return Array.prototype.filter.call(document.querySelectorAll('#tabs button'), function (b) { return /^\d+\./.test(b.textContent.trim()); });
  }
  // What each step is for, in plain words, shown at the top of the step. `todo` is what the person does; `after` is said once the step is done.
  var STAGES = {
    connect: { title: 'Connect to your cloud', what: 'Sign in to OpenStack with an administrator account. OpenMove only reads at this point. It changes nothing.',
      todo: ['Enter the address of your cloud and an administrator login.', 'Press Connect.'], after: 'You are connected. Go on to the scan.' },
    scan: { title: 'Scan your cloud', what: 'OpenMove reads your whole cloud: its version, its storage systems, and every project, instance and volume. It also looks for problems. It changes nothing.',
      todo: ['Press "Scan the cloud". It takes a minute or two.', 'Look through the list to see what you have. Each volume says how it will be handled.'], after: 'The scan is done. Look through the list, or go on to the safety checks.' },
    preflight: { title: 'Safety checks', what: 'Before anything is moved, OpenMove checks that the cloud and the storage are ready. A check that fails must be fixed. A check that OpenMove cannot do by itself needs a person to look and confirm. Then two different people approve.',
      todo: ['Choose where the volumes move from and to.', 'Press "Run the safety checks".', 'Fix anything that failed. Confirm anything that needs a person.', 'Two different people press Approve.'], after: 'The safety checks are approved. Go on and choose the volumes.' },
    select: { title: 'Choose what to move', what: 'Tick the volumes to move. You can tick a whole project, a whole instance or a single volume. Volumes that cannot be moved are shown with the reason, and they stay where they are.',
      todo: ['Tick what you want to move. The suggested choice is already ticked.', 'Check the volumes that may break if they are moved.', 'Press "Confirm my choice".'], after: 'Your choice is confirmed. Go on to the move.' },
    move: { title: 'Move the volumes', what: 'First rehearse: OpenMove lists every call it would make and sends none. Then start the move for real. A second person approves each volume before it is switched.',
      todo: ['Press "1. Rehearse" and read the result.', 'Press "2. Start moving".'], after: 'The move has been rehearsed. Press "2. Start moving", or watch the Progress step.' },
    live: { title: 'Progress', what: 'Shows each volume as it is copied, checked and switched, and everything that was refused. A second person approves each volume here.',
      todo: ['Watch the progress.', 'The second person presses Approve when volumes are ready.'], after: '' },
    reports: { title: 'Reports', what: 'Word documents and spreadsheets built from the same data the tool holds, so a report cannot disagree with what OpenMove knows.',
      todo: ['Choose the report you need and press its button.'], after: '' },
    journal: { title: 'History', what: 'Everything OpenMove did and everything people recorded.', todo: [] },
    hosts: { title: 'Helper servers', what: 'Servers that copy volumes with long-lived snapshots. You only need them if you have such volumes.', todo: [] },
    diagnostics: { title: 'Support', what: 'If something goes wrong, make a support bundle and send it to whoever supports you.', todo: [] }
  };
  function drawGuide() {
    var g = $('guide'), st = STAGES[currentTab]; clear(g);
    g.classList.toggle('hidden', !st);
    if (!st) return;
    var steps = wizardSteps(), i = -1;
    steps.forEach(function (b, k) { if (b.dataset.tab === currentTab) i = k; });
    var w = wizardInfo(currentTab), numbered = i >= 0;
    var head = el('div', { class: 'guide-head' });
    if (numbered) head.appendChild(el('span', { class: 'guide-step', text: 'Step ' + (i + 1) + ' of ' + steps.length }));
    head.appendChild(el('span', { class: 'guide-title', text: st.title }));
    if (numbered && w && (!w.open || ['connect', 'scan', 'preflight', 'select', 'move'].indexOf(currentTab) >= 0)) head.appendChild(el('span', { class: 'chip ' + (w.done ? 'ok' : (w.open ? 'warn' : '')), text: w.done ? 'Done' : (w.open ? 'Your turn' : 'Locked') }));
    g.appendChild(head);
    g.appendChild(el('p', { text: st.what }));
    if (numbered && w && !w.open) g.appendChild(el('div', { class: 'banner warn static', text: w.needs }));
    else if (numbered && w && w.done && st.after) g.appendChild(el('p', { class: 'guide-done', text: '\u2713 ' + st.after }));
    else if (st.todo && st.todo.length) g.appendChild(el('ol', { class: 'guide-todo' }, st.todo.map(function (x) { return el('li', { text: x }); })));
  }
  function drawWizard() {
    drawGuide();
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
      next.disabled = true; next.textContent = 'Next'; lab.textContent = currentTab === 'hosts' ? 'Hosts' : currentTab === 'diagnostics' ? 'Diagnostics' : 'Journal';
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
      if (!s[r[0]]) {
        if ($(r[1]).firstChild && !restoring[r[0]]) {
          clear($(r[1]));
          if (r[0] === 'checks') $('pf-stale').textContent = 'The earlier safety checks are no longer valid. This happens when the volumes are read again. Run the safety checks again.';
        }
        return;
      }
      if ($(r[1]).firstChild || restoring[r[0]]) return;
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
        cf.elements.password.placeholder = 'filled in for you (test lab)';
        cf.dataset.labFilled = '1';
      }
      if (s.demo) {
        var out = $('connect-result');
        if (!out.firstChild) out.appendChild(el('div', { class: 'banner ok', text: 'Demonstration: you are connected to the built-in demonstration cloud. Go on to step 2, Scan.' }));
        $('connect-form').classList.add('hidden');
      }
      var m = $('mode');
      if (!s.connected) { m.className = 'chip'; m.textContent = 'not connected'; m.title = ''; }
      else if (s.platform && s.read_only) { m.className = 'chip warn'; m.textContent = 'look only: no changes allowed'; m.title = 'This OpenStack release has not passed its lab tests yet, so OpenMove refuses every change.'; }
      else if (s.platform) { m.className = 'chip ok'; m.textContent = 'ready to move volumes'; m.title = 'This OpenStack release has passed its lab tests.'; }
      else { m.className = 'chip'; m.textContent = 'connected'; m.title = ''; }
      var cur = wizardInfo(currentTab);
      if (cur && !cur.open) {                      // the step we are on closed (disconnected, a new pre-flight run, a changed selection): go back to the first open one that is not done
        var open = (s.wizard || []).filter(function (w) { return w.open && !w.done; })[0] || (s.wizard || [])[0];
        if (open) showTab(stageOf(open.id), true);
      }
      drawWizard();
      drawMoveGate();
      restoreViews(s);
      if (deepLink) { var go = stageOf(deepLink); deepLink = ''; if (wizardInfo(go) || go === 'journal' || go === 'hosts' || go === 'diagnostics') showTab(go); }     // a link such as  /#preflight  opens that step if it is open
      return s;
    }).catch(function () {});
  }

  // ---- halt ----
  function askWho(title, intro, okText) {
    return ask({ title: title, intro: intro, ok: okText, danger: true, fields: [nameField('Your name'), { key: 'reason', label: 'Why?', type: 'textarea', min: 8, hint: 'A few words. This is saved in the history.' }] })
      .then(function (v) { if (v) me = v.by; return v; });
  }
  function drawHalt(h) {
    var b = $('halt-banner'); clear(b);
    b.classList.toggle('hidden', !h);
    $('halt-btn').classList.toggle('hidden', !!h);
    if (!h) return;
    b.appendChild(el('span', { text: 'EVERYTHING IS STOPPED. ' + h.by + ' pressed the emergency stop (' + h.at + '): ' + h.reason }));
    var r = el('button', { text: 'Allow changes again' });
    r.addEventListener('click', function () {
      askWho('Allow changes again?', 'Only do this once the reason for the stop is solved.', 'Allow changes').then(function (w) {
        if (w) api('POST', 'resume', w).then(refreshStatus).catch(function (e) { banner('bad', e.message); });
      });
    });
    b.appendChild(r);
  }
  $('halt-btn').addEventListener('click', function () {
    askWho('Emergency stop', 'This stops every change to the cloud at once. Nothing that changes the cloud will be sent until someone allows changes again. Use it when something looks wrong.', 'Stop everything').then(function (w) {
      if (w) api('POST', 'halt', w).then(refreshStatus).catch(function (e) { banner('bad', e.message); });
    });
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
    ['platform-view', 'inv-summary', 'audit-view', 'pf-view', 'scan-summary', 'scan-mode', 'tree-view', 'sel-tree'].forEach(function (id) { clear($(id)); });
    treeData = null; $('tree-box').classList.add('hidden'); $('scan-status').textContent = '';
    clear($('inv-table').tBodies[0]); $('inv-table').hidden = true; $('inv-more').textContent = '';
    ['inv-backend', 'pf-source', 'pf-target'].forEach(function (id) { $(id).value = ''; });
    $('rep-status').textContent = ''; $('inv-filter').hidden = true;
    inventory = null; attesting = null;
  }
  $('demo-scenario').addEventListener('change', function () {
    var sel = $('demo-scenario');
    sel.disabled = true;
    api('POST', 'demo/scenario', { id: sel.value }).then(function () {
      resetViews(); banner('ok', 'Switched the demonstration. Nothing has been read yet: start with step 2, Scan.');
      return refreshStatus();
    }).catch(function (e) { banner('bad', e.message); }).then(function () { sel.disabled = false; showTab('scan'); });
  });
  $('inv-demo-export').addEventListener('click', function () {
    guard($('inv-demo-export'), function () {
      return api('POST', 'demo/db-export', {}).then(function (inv) {
        renderInventory(inv); refreshStatus();
        banner('ok', 'Loaded the demonstration database export. Snapshot history and provider ids are now included.');
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
        out.appendChild(el('div', { class: 'banner ok', text: 'Connected. Your roles: ' + (r.roles || []).join(', ') + '.' }));
        if ((r.roles || []).indexOf('admin') < 0) out.appendChild(el('div', { class: 'banner warn',
          text: 'This account is not an administrator. OpenMove will not be able to see the storage systems, the hosts or the volumes of other projects.' }));
        return refreshStatus().then(function () { showTab('scan'); });
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
    var sm = $('scan-mode'); clear(sm);
    sm.appendChild(el('div', { class: 'banner ' + kind + ' static', text: prof.status === 'validated'
      ? 'Your OpenStack release (' + prof.release + ') has passed its lab tests. OpenMove may change the cloud when you tell it to.'
      : 'Look only. ' + String(prof.reason || '').replace(/^./, function (c) { return c.toUpperCase(); }).replace(/\.$/, '') + '. You can scan, check and make reports. OpenMove refuses every change until the lab tests for this release are recorded (step 5, Move, "Lab tests").' }));
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
    if (p.backends.length) v.appendChild(table(['Pool', 'Back-end', 'Family', 'Vendor', 'Driver version', 'Protocol', 'Total GiB', 'Free GiB'],
      p.backends.map(function (b) { return [b.pool, b.backend, b.family || 'unsupported (unknown)', b.vendor, b.driver_version, b.protocol, b.total_gb, b.free_gb]; })));
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
  var CLS_NAME = { A: 'Easy', B: 'Waits for snapshots', C: 'Careful copy', D: 'Instance is off', E: 'Not moved by this tool' };
  function clsName(c) { return CLS_NAME[c] || String(c || '-'); }
  function renderInventory(inv) {
    inventory = inv;
    var s = $('inv-summary'); clear(s);
    (inv.warnings || []).forEach(function (w) { s.appendChild(el('div', { class: 'banner warn', text: w })); });
    var cards = el('div', { class: 'cards' });
    cards.appendChild(el('div', { class: 'card' }, [el('div', { class: 'k', text: 'Volumes' }), el('div', { class: 'v', text: String(inv.total) })]));
    cards.appendChild(el('div', { class: 'card' }, [el('div', { class: 'k', text: 'Total GiB' }), el('div', { class: 'v', text: String(inv.total_gib) })]));
    ['A', 'B', 'C', 'D', 'E'].forEach(function (c) {
      cards.appendChild(el('div', { class: 'card' }, [el('div', { class: 'k', text: clsName(c) }), el('div', { class: 'v', text: String(inv.counts[c] || 0) })]));
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
    ['A', 'B', 'C', 'D', 'E'].forEach(function (c) { cs.appendChild(el('option', { value: c, text: clsName(c) })); });
    $('inv-filter').hidden = false; $('inv-table').hidden = false;
    drawRows();
    loadTree();
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
        el('td', {}, [el('span', { class: 'cls cls-' + v['class'], text: v['class'] }), document.createTextNode(' ' + clsName(v['class']))]),
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
  var STATUS_TEXT = { pass: 'OK', info: 'Info', warn: 'Needs attention', fail: 'Failed', not_checked: 'Not checked' };
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
      else if (inventory && !$('pf-source').value && !$('pf-target').value) {      // the storage system that holds most volumes is the one they move from; if one other remains, that is where they move to
        var count = {}, names = {};
        inventory.volumes.forEach(function (v) { if (v.backend) count[v.backend] = (count[v.backend] || 0) + 1; });
        p.backends.forEach(function (b) { if (b.backend) names[b.backend] = 1; });
        var src = Object.keys(count).sort(function (a, b) { return count[b] - count[a]; })[0];
        var others = Object.keys(names).filter(function (n) { return n !== src; });
        if (src) $('pf-source').value = src;
        if (src && others.length === 1) $('pf-target').value = others[0];
      }
    }).catch(function () {});
  }

  // ---- audit ----
  function renderAudit(a) {
    var v = $('audit-view'); clear(v);
    var fc = a.finding_counts || {};
    $('audit-count').textContent = '(' + a.findings.length + ' found' + (fc.high ? ', ' + fc.high + ' serious' : '') + ')';
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

  // ---- scan: platform, volumes and the health check in one go ----
  $('scan-run').addEventListener('click', function () {
    var st = $('scan-status');
    guard($('scan-run'), function () {
      st.textContent = 'Step 1 of 3: reading the version and the storage systems...';
      return api('POST', 'discover', {}).then(function (p) { renderPlatform(p); return refreshStatus(); }).then(function () {
        st.textContent = 'Step 2 of 3: reading every project, instance and volume...';
        return api('POST', 'inventory/run', { backend: $('inv-backend').value, window_days: Number($('inv-window').value) || 14 }).then(function (inv) { renderInventory(inv); return refreshStatus(); });
      }).then(function () {
        st.textContent = 'Step 3 of 3: running the health check...';
        return api('POST', 'audit/run', {}).then(function (a) { renderAudit(a); return refreshStatus(); });
      }).then(function () {
        st.textContent = 'The scan finished at ' + new Date().toLocaleTimeString() + '.';
        return loadTree();
      }).catch(function (e) { st.textContent = 'The scan stopped: ' + e.message; throw e; });
    });
  });

  // ---- the volume tree: project, instance, attached volumes ----
  var treeData = null, treeOpen = {};
  function loadTree() {
    return api('GET', 'tree').then(function (t) { treeData = t; drawTree(); drawSelTree(); drawScanSummary(); }).catch(function () {});
  }
  function legend(box) {
    clear(box); if (!treeData) return;
    var ul = el('ul', { class: 'plain' });
    treeData.legend.forEach(function (l) { ul.appendChild(el('li', {}, [el('span', { class: 'tag tag-' + l.level, text: l.label }), document.createTextNode(' ' + l.meaning)])); });
    box.appendChild(el('details', {}, [el('summary', { text: 'What do the labels mean?' }), ul]));
  }
  function tag(level, text, title) { return el('span', { class: 'tag tag-' + level, text: text, title: title || '' }); }
  function shortId(id) { return String(id).slice(0, 8); }
  function uniqueVols(vols) { var seen = {}; return vols.filter(function (v) { if (seen[v.id]) return false; seen[v.id] = 1; return true; }); }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }

  function volRow(v, opt) {
    var row = el('div', { class: 'vrow' });
    if (opt.check) {
      var cb = el('input', { type: 'checkbox', 'aria-label': 'Choose ' + (v.name || v.id) });
      cb.checked = v.verdict === 'Will be moved';
      cb.disabled = !v.verdict || v.verdict === 'Held back' || !!(selData && selData.running);
      cb.addEventListener('change', function () { postSelection({ action: cb.checked ? 'add' : 'remove', ids: [v.id] }).catch(function (e) { banner('bad', e.message); }).then(loadTree); });
      row.appendChild(cb);
    }
    var name = [el('b', { text: v.name || ('Volume ' + shortId(v.id)) }), el('span', { class: 'mono muted', text: ' ' + shortId(v.id) })];
    if (v.boot) name.push(tag('info', 'boot volume'));
    if (v.device) name.push(el('span', { class: 'muted small', text: ' ' + v.device }));
    var main = el('div', { class: 'vmain' }, [el('div', {}, name)]);
    if (v.why) main.appendChild(el('div', { class: 'muted small', text: v.why }));
    if (opt.check && v.verdict && v.verdict !== 'Held back') {
      var flag = el('button', { class: 'linklike', type: 'button', text: 'This volume may break if moved' });
      flag.addEventListener('click', function () { flagRisk(v, flag); });
      main.appendChild(flag);
    }
    row.appendChild(main);
    row.appendChild(el('span', { class: 'vsize', text: v.size + ' GiB' + (v.snapshots ? ' \u00b7 ' + plural(v.snapshots, 'snapshot') : '') }));
    row.appendChild(tag(v.level, v.label, v.meaning));
    if (v.verdict) row.appendChild(tag(v.verdict_level, v.verdict));
    return row;
  }
  // a box that chooses every volume that can be moved below a project or an instance
  function groupBox(vols) {
    var pick = uniqueVols(vols).filter(function (v) { return v.verdict && v.verdict !== 'Held back'; });
    var on = pick.filter(function (v) { return v.verdict === 'Will be moved'; }).length;
    var cb = el('input', { type: 'checkbox', class: 'gbox', 'aria-label': 'Choose all the volumes in here that can be moved' });
    cb.checked = pick.length > 0 && on === pick.length; cb.indeterminate = on > 0 && on < pick.length;
    cb.disabled = !pick.length || !!(selData && selData.running);
    cb.title = pick.length ? (on + ' of ' + pick.length + ' volumes that can be moved are chosen') : 'None of these volumes can be moved';
    cb.addEventListener('click', function (ev) { ev.stopPropagation(); });
    cb.addEventListener('change', function () {
      postSelection({ action: cb.checked ? 'add' : 'remove', ids: pick.map(function (v) { return v.id; }) }).catch(function (e) { banner('bad', e.message); }).then(loadTree);
    });
    return cb;
  }
  // a node whose contents are built the first time it is opened, so a large cloud stays quick
  function lazyNode(key, sumKids, build, force, mode) {
    var d = el('details', { class: 'node' }), body = el('div', { class: 'node-body' }), built = false, ok = mode + key;
    d.appendChild(el('summary', {}, sumKids.filter(Boolean))); d.appendChild(body);
    function fill() { if (built) return; built = true; build(body); }
    if (force || treeOpen[ok]) { d.open = true; fill(); }
    d.addEventListener('toggle', function () { if (!force) treeOpen[ok] = d.open; if (d.open) fill(); });
    return d;
  }
  function gibOf(vols) { return uniqueVols(vols).reduce(function (n, v) { return n + (Number(v.size) || 0); }, 0); }
  function instNode(p, i, opt, force, mode) {
    var up = String(i.status).toUpperCase() === 'ACTIVE';
    return lazyNode('i:' + p.id + ':' + i.id, [opt.check ? groupBox(i.volumes) : null, el('span', { class: 'ico', text: '\u25A3' }), el('b', { text: i.name }),
      tag(up ? 'ok' : 'warn', up ? 'running' : String(i.status).toLowerCase(), 'The state of the instance'),
      el('span', { class: 'muted small', text: plural(i.volumes.length, 'volume') + ', ' + gibOf(i.volumes) + ' GiB' + (i.host ? ' \u00b7 on ' + i.host : '') })],
      function (body) { i.volumes.forEach(function (v) { body.appendChild(volRow(v, opt)); }); }, force, mode);
  }
  function projNode(p, opt, force, forceInner, mode) {
    var all = p.loose.slice();
    p.instances.forEach(function (i) { all = all.concat(i.volumes); });
    return lazyNode('p:' + p.id, [opt.check ? groupBox(all) : null, el('span', { class: 'ico', text: '\u25A4' }), el('b', { text: p.name }),
      el('span', { class: 'muted small', text: plural(p.instances.length, 'instance') + ', ' + plural(uniqueVols(all).length, 'volume') + ', ' + gibOf(all) + ' GiB' })],
      function (body) {
        p.instances.forEach(function (i) { body.appendChild(instNode(p, i, opt, forceInner, mode)); });
        if (p.loose.length) {
          body.appendChild(lazyNode('l:' + p.id, [opt.check ? groupBox(p.loose) : null, el('span', { class: 'ico', text: '\u25AB' }), el('b', { text: 'Not attached' }),
            el('span', { class: 'muted small', text: plural(p.loose.length, 'volume') + ', ' + gibOf(p.loose) + ' GiB. Not attached to any instance.' })],
            function (b2) { p.loose.forEach(function (v) { b2.appendChild(volRow(v, opt)); }); }, forceInner, mode));
        }
        if (!p.instances.length && !p.loose.length) body.appendChild(el('p', { class: 'muted', text: 'No volumes.' }));
      }, force, mode);
  }
  function filterTree(data, q) {
    function hit(s) { return String(s || '').toLowerCase().indexOf(q) >= 0; }
    var out = [];
    data.projects.forEach(function (p) {
      var pm = hit(p.name), insts = [];
      p.instances.forEach(function (i) {
        var im = pm || hit(i.name), vs = i.volumes.filter(function (v) { return im || hit(v.name) || hit(v.id); });
        if (vs.length) insts.push(Object.assign({}, i, { volumes: vs }));
      });
      var loose = p.loose.filter(function (v) { return pm || hit(v.name) || hit(v.id); });
      if (insts.length || loose.length) out.push(Object.assign({}, p, { instances: insts, loose: loose }));
    });
    return { projects: out };
  }
  function renderTree(box, data, opt) {
    clear(box);
    var q = (opt.find || '').toLowerCase().trim(), view = q ? filterTree(data, q) : data;
    if (!view.projects.length) { box.appendChild(el('p', { class: 'muted', text: q ? 'Nothing matches "' + q + '".' : 'No volumes were found.' })); return; }
    var n = 0; view.projects.forEach(function (p) { p.instances.forEach(function (i) { n += i.volumes.length; }); n += p.loose.length; });
    var mode = opt.check ? 'c' : 's';
    view.projects.forEach(function (p) { box.appendChild(projNode(p, opt, !!q, !!q && n <= 600, mode)); });
  }
  function openAll(box, open) {
    var tries = 0;
    (function step() {
      var ds = box.querySelectorAll('details.node' + (open ? ':not([open])' : '[open]'));
      Array.prototype.forEach.call(ds, function (d) { d.open = open; });
      if (open && ds.length && tries++ < 4) setTimeout(step, 40);
    })();
  }
  function drawTree() {
    var box = $('tree-box');
    if (!treeData || !treeData.total) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');
    legend($('tree-legend')); renderTree($('tree-view'), treeData, { find: $('tree-find').value });
  }
  function drawSelTree() {
    if (!treeData) return;
    legend($('sel-legend')); renderTree($('sel-tree'), treeData, { check: true, find: $('sel-find').value });
  }
  function drawScanSummary() {
    var s = $('scan-summary'); clear(s);
    if (!treeData || !treeData.total || !inventory) return;
    var insts = 0; treeData.projects.forEach(function (p) { insts += p.instances.length; });
    s.appendChild(el('p', { class: 'lead', text: 'Found ' + plural(treeData.total, 'volume') + ' (' + treeData.gib + ' GiB) in ' + plural(treeData.projects.length, 'project') +
      ', attached to ' + plural(insts, 'instance') + '.' }));
    var cards = el('div', { class: 'cards' });
    treeData.legend.forEach(function (l) {
      cards.appendChild(el('div', { class: 'card lvl-' + l.level, title: l.meaning }, [el('div', { class: 'k', text: l.label }), el('div', { class: 'v', text: String((inventory.counts || {})[l.code] || 0) }), el('div', { class: 'muted small', text: l.meaning })]));
    });
    s.appendChild(cards);
    (inventory.warnings || []).forEach(function (w) { s.appendChild(el('div', { class: 'banner warn static', text: w })); });
  }
  $('tree-find').addEventListener('input', drawTree);
  $('sel-find').addEventListener('input', drawSelTree);
  $('tree-open').addEventListener('click', function () { openAll($('tree-view'), true); });
  $('tree-close').addEventListener('click', function () { openAll($('tree-view'), false); });

  // ---- safety checks (the pre-flight) and the gate ----
  var GATE_TEXT = { approved: 'All clear: you can go on', blocked: 'Stopped: something failed', open: 'Not ready: a person must confirm some checks',
                    awaiting_signoff: 'Almost ready: two approvals are needed', stale: 'Out of date: run the safety checks again' };

  function isBlocker(c, k) { return k.status === 'fail' && (c.platform.indexOf(k) >= 0 || k.id === 'V9' || k.id === 'V10'); }

  // A person says they checked it. Errors stay inside the box, so they are never missed.
  function confirmCheck(c, k) {
    return ask({ title: 'Confirm: ' + k.title,
      intro: 'OpenMove could not check this by itself. By confirming, you say that a person has checked it and it is fine, or that the risk is accepted. Your name and what you checked are saved in the history and in the report.\n\nWhat OpenMove saw: ' + (k.evidence || 'nothing'),
      fields: [nameField('Your name'), { key: 'note', label: 'What did you check?', type: 'textarea', min: 8, hint: 'A few words are enough. For example: "Looked at the Cinder host on 4 Nov. The path is fine."' }],
      ok: 'Confirm',
      submit: function (v) {
        return api('POST', 'checks/attest', { check_id: k.id, by: v.by, note: v.note }).then(function (r) { me = v.by; renderChecks(r); refreshStatus(); return v; }, function (e) {
          if (/pre-flight checks first/.test(e.message)) { clear($('pf-view')); refreshStatus(); throw new Error('The safety checks are no longer held by OpenMove (the volumes were read again, or OpenMove was restarted). Close this box and run the safety checks again.'); }
          throw e;
        });
      } }).then(function (v) { if (v) banner('ok', 'Confirmed: ' + k.title + '.'); });
  }
  function undoConfirm(k, btn) {
    ask({ title: 'Take back the confirmation', intro: k.title, fields: [nameField('Your name')], ok: 'Take it back',
      submit: function (v) { return api('POST', 'checks/attest', { check_id: k.id, by: v.by, withdraw: true }).then(function (r) { me = v.by; renderChecks(r); refreshStatus(); }); } });
  }
  function checkItem(c, k, mode) {
    var att = (c.attestations || {})[k.id];
    var box = el('div', { class: 'citem citem-' + mode }, [
      el('div', { class: 'ctitle' }, [chip(k.status), document.createTextNode(' '), el('b', { text: k.title }), el('span', { class: 'cid', text: k.id })]),
      el('div', { class: 'cevidence', text: k.evidence || '' })]);
    if (k.meaning) box.appendChild(el('div', { class: 'muted small', text: k.meaning }));
    if (mode === 'needs') {
      var b = el('button', { class: 'primary', type: 'button', text: 'I have checked this: confirm' });
      b.addEventListener('click', function () { confirmCheck(c, k); });
      box.appendChild(el('div', { class: 'actions' }, [b]));
    }
    if (mode === 'confirmed' && att) {
      var u = el('button', { class: 'secondary', type: 'button', text: 'Take back' });
      u.addEventListener('click', function () { undoConfirm(k, u); });
      box.appendChild(el('div', { class: 'cconfirmed' }, [document.createTextNode('\u2713 Confirmed by ' + att.by + ' on ' + (att.at || '').slice(0, 10) + ': ' + att.note + '  '), u]));
    }
    if (k.findings && k.findings.length) {
      var ulf = el('ul'); k.findings.slice(0, 100).forEach(function (f) { ulf.appendChild(el('li', {}, [chip(f.status), document.createTextNode(' '), el('span', { class: 'mono', text: f.volume }), document.createTextNode('  ' + f.evidence)])); });
      if (k.findings.length > 100) ulf.appendChild(el('li', { text: 'and ' + (k.findings.length - 100) + ' more (all are in the report)' }));
      box.appendChild(el('details', {}, [el('summary', { text: 'Which volumes (' + k.findings.length + ')' }), ulf]));
    }
    return box;
  }
  function signBox(c, role, label, help) {
    var so = (c.gate.signoffs || {})[role], box = el('div', { class: 'card sign' }, [el('div', { class: 'k', text: label })]);
    if (so) {
      box.appendChild(el('div', { class: 'v', text: '\u2713 ' + so.by })); box.appendChild(el('div', { class: 'muted small', text: 'Approved ' + String(so.at).replace('T', ' ').replace('Z', ' UTC') }));
      return box;
    }
    var ready = !c.gate.blockers.length && !c.gate.open_items.length, go = el('button', { class: 'primary', type: 'button', text: 'Approve' });
    go.disabled = !ready;
    go.addEventListener('click', function () {
      ask({ title: 'Approve the safety checks (' + label.toLowerCase() + ')',
        intro: 'You are saying that you have read the safety checks and that the move may go ahead. Two different people must approve.',
        fields: [nameField('Your name', role === 'reviewer' ? 'You must be a different person from the first approver.' : '', role === 'operator')], ok: 'Approve',
        submit: function (v) { return api('POST', 'checks/signoff', { role: role, by: v.by }).then(function (r) { if (role === 'operator') me = v.by; renderChecks(r); refreshStatus(); }); } })
        .then(function (v) { if (v) banner('ok', 'Approved by ' + v.by + '.'); });
    });
    box.appendChild(el('div', { class: 'muted small', text: help })); box.appendChild(go);
    if (!ready) box.appendChild(el('div', { class: 'muted small', text: 'Available when nothing has failed and everything that needs a person is confirmed.' }));
    return box;
  }
  function renderChecks(c) {
    var v = $('pf-view'); clear(v); $('pf-stale').textContent = '';
    var g = c.gate, att = c.attestations || {}, all = c.platform.concat(c.volume);
    var blockers = all.filter(function (k) { return isBlocker(c, k); });
    var needs = all.filter(function (k) { return (k.status === 'warn' || k.status === 'not_checked') && !att[k.id]; });
    var confirmed = all.filter(function (k) { return (k.status === 'warn' || k.status === 'not_checked') && att[k.id]; });
    var passed = c.platform.filter(function (k) { return k.status === 'pass' || k.status === 'info'; });
    v.appendChild(el('div', { class: 'gate gate-' + g.status }, [el('div', { class: 'gate-title', text: GATE_TEXT[g.status] || g.status }), el('div', { text: g.message })]));

    var todo = el('ol', { class: 'todo' });
    function line(ok, text) { todo.appendChild(el('li', { class: ok ? 'done' : 'open' }, [el('span', { class: 'tick', text: ok ? '\u2713' : '\u2022' }), document.createTextNode(' ' + text)])); }
    line(!g.blockers.length, g.blockers.length ? plural(g.blockers.length, 'check') + ' failed. Fix the cause, then run the safety checks again.' : 'Nothing has failed.');
    line(!g.open_items.length, g.open_items.length ? plural(g.open_items.length, 'check') + ' need a person to look and confirm.' : 'Every check that needs a person has been confirmed.');
    line(g.status === 'approved', g.status === 'approved' ? 'Two different people have approved.' : 'Two different people must approve (below).');
    v.appendChild(todo);
    v.appendChild(el('p', { class: 'muted', text: c.admitted.length + ' of ' + plural(c.volumes_total, 'volume') + ' can be moved. ' + (c.access === 'full'
      ? 'OpenMove can reach the cloud, the helper servers and the arrays, so volumes with long-lived snapshots can be moved too.'
      : 'OpenMove can reach the OpenStack cloud only.' + (c.no_mover && c.no_mover.length ? ' ' + plural(c.no_mover.length, 'volume') + ' with long-lived snapshots stay behind: they need a helper server (see "Helper servers").' : '')) }));

    if (blockers.length || g.blockers.some(function (b) { return b.id === '-'; })) {
      v.appendChild(el('h3', { text: 'Fix these first' }));
      blockers.forEach(function (k) { v.appendChild(checkItem(c, k, 'fail')); });
      g.blockers.filter(function (b) { return b.id === '-'; }).forEach(function (b) { v.appendChild(el('div', { class: 'citem citem-fail' }, [el('div', { class: 'ctitle' }, [el('b', { text: b.title })]), el('div', { class: 'cevidence', text: b.evidence })])); });
    }
    if (needs.length) {
      v.appendChild(el('h3', { text: 'A person needs to check these (' + needs.length + ')' }));
      v.appendChild(el('p', { class: 'muted', text: 'OpenMove could not check these by itself, often because it has no access yet. Check each one yourself. Then press "I have checked this" and write down what you checked.' }));
      needs.forEach(function (k) { v.appendChild(checkItem(c, k, 'needs')); });
    }
    if (confirmed.length) {
      v.appendChild(el('h3', { text: 'Confirmed by a person (' + confirmed.length + ')' }));
      confirmed.forEach(function (k) { v.appendChild(checkItem(c, k, 'confirmed')); });
    }
    v.appendChild(el('h3', { text: 'Approval by two different people' }));
    v.appendChild(el('p', { class: 'muted', text: 'One person runs the move. A second, independent person reviews it. A new run of the safety checks needs new approvals.' }));
    v.appendChild(el('div', { class: 'signs' }, [signBox(c, 'operator', 'First approver', 'The person who runs the move.'), signBox(c, 'reviewer', 'Second approver', 'A different person, who reviews the checks.')]));

    v.appendChild(el('details', { class: 'more' }, [el('summary', { text: 'Checks that passed (' + passed.length + ')' }),
      table(['Check', 'Result', 'What OpenMove saw'], passed.map(function (k) { return [k.title, STATUS_TEXT[k.status], k.evidence]; }))]));
    var rows = c.volume.filter(function (x) { return x.counts; });
    v.appendChild(el('details', { class: 'more' }, [el('summary', { text: 'Results for each volume' }),
      table(['Check', 'OK', 'Needs attention', 'Failed', 'Not checked'], rows.map(function (k) { return [k.title, k.counts.pass, k.counts.warn, k.counts.fail, k.counts.not_checked]; })),
      el('p', { class: 'muted', text: 'A volume that fails one of its checks is not moved. It stays where it is. The reason is shown in the list of volumes.' })]));
    v.appendChild(el('details', { class: 'more' }, [el('summary', { text: 'Checks that run later, during and after the move (' + c.later.length + ')' }),
      table(['Check', 'When', 'Needs'], c.later.map(function (k) { return [k.title, k.phase, k.needs.join(', ')]; }))]));
  }
  $('pf-run').addEventListener('click', function () {
    if (!$('pf-source').value || !$('pf-target').value) { banner('warn', 'First choose where the volumes move from and where they move to.'); return; }
    guard($('pf-run'), function () {
      return api('POST', 'checks/run', { source_backend: $('pf-source').value, target_backend: $('pf-target').value, access: $('pf-access').value })
        .then(function (c) { renderChecks(c); return refreshStatus(); });
    });
  });
  $('pf-demo-validate').addEventListener('click', function () {
    guard($('pf-demo-validate'), function () {
      return api('POST', 'demo/validate', {}).then(function () { banner('ok', 'Recorded a lab validation of the demonstration cloud. Run the safety checks again.'); return refreshStatus(); });
    });
  });

  $('pf-lab-approve').addEventListener('click', function () {
    guard($('pf-lab-approve'), function () {
      return api('POST', 'lab/approve', {}).then(function (r) { renderChecks(r); banner('ok', 'Test lab: the open checks were confirmed and two lab users approved. This is not a customer approval.'); return refreshStatus(); });
    });
  });
  $('pf-demo-approve').addEventListener('click', function () {
    guard($('pf-demo-approve'), function () {
      return api('POST', 'demo/approve', {}).then(function (r) { renderChecks(r); banner('ok', 'Demonstration: the open checks were confirmed and two made-up people approved.'); return refreshStatus(); });
    });
  });

  // ---- live view ----
  var PHASES = [['precheck', 'Pre-checks'], ['validate', 'Plan check'], ['copy', 'Copy'], ['sync', 'Keep in step'], ['recheck', 'Check again'],
                ['cutover', 'Switch'], ['postcheck', 'Check after'], ['soak', 'Wait period'], ['done', 'Done'], ['aborted', 'Stopped']];
  var PHASE_HELP = {
    precheck: 'The safety checks are approved for this run. Nothing has been touched.',
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
      ? 'DEMONSTRATION: this shows a simulated move with made-up data and a made-up clock. The real order and the real safety rules apply. Nothing is sent to any system.'
      : 'This page fills in while a move is running. Every volume shows its stage, and every refusal appears in the event feed.';
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
    $('sign-demo').textContent = l.auto_sign ? 'Demo: stop the made-up second person approving' : 'Demo: a made-up second person approves';
    $('sign-status').textContent = (l.need_g23 ? l.need_g23 + ' volume(s) wait for your approval of G2 and G3. ' : '') + (l.need_g4 ? l.need_g4 + ' volume(s) wait for your approval of G4. ' : '') +
      (l.need_g23 || l.need_g4 ? 'The signer must not be the operator of the run.' : '');
    $('live-ready').textContent = l.ready ? l.ready + ' volume(s) are copied and checked, and wait for your release. You can change this in step 4, Choose.' : '';
    var gp = $('gprog');
    gp.classList.toggle('hidden', !l.started);
    $('gprog-fill').style.width = l.overall + '%';
    $('gprog-text').textContent = 'Move ' + l.overall.toFixed(0) + '% done  |  ' + l.active + ' in progress' + (l.halted ? '  |  EVERYTHING IS STOPPED' : '');
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

  // ---- choose: the volumes in a tree, by group, and the settings ----
  var selData = null, selKind = 'class', selTimer = null;
  function needName(why) {                                          // every choice is saved with a name: ask once, then remember it for this page
    if (me) return Promise.resolve(me);
    return ask({ title: 'Who are you?', intro: why, fields: [nameField('Your name')], ok: 'Continue' }).then(function (v) { if (v) me = v.by; return me; });
  }
  $('sel-confirm').addEventListener('click', function () {
    var n = selData ? selData.selected : 0, gib = selData ? selData.selected_gib : 0;
    ask({ title: 'Confirm your choice',
      intro: 'You are confirming that these ' + plural(n, 'volume') + ' (' + gib + ' GiB) are the ones to move. If you change your choice later, or run the safety checks again, you must confirm again.',
      fields: [nameField('Your name')], ok: 'Confirm',
      submit: function (v) { return api('POST', 'selection/confirm', { by: v.by }).then(function (r) { me = v.by; return refreshStatus().then(function () { return r; }); }); } })
      .then(function (r) { if (r && r.volumes != null) banner('ok', 'Choice confirmed: ' + plural(r.volumes, 'volume') + '. Go on to step 5, Move.'); });
  });
  function drawSelConfirm() {
    var ok = wizardDone('select'), b = $('sel-confirm');
    b.textContent = ok ? 'Choice confirmed' : 'Confirm my choice';
    b.disabled = ok;
    $('sel-confirm-note').textContent = ok ? 'Confirmed. If you change the choice, or run the safety checks again, you must confirm again.' :
      'The next step (Move) stays closed until you confirm your choice.';
  }
  function drawMoveGate() {
    var w = wizardInfo('move');
    if (w) { drawSelConfirm(); }
    var st = $('mv-start');
    if (st) {                                       // a real run needs a rehearsal first; the server enforces it too
      st.disabled = !wizardDone('move');
      st.title = st.disabled ? 'Rehearse first (button 1).' : '';
    }
  }
  function loadSelection() {
    api('GET', 'selection').then(renderSelection).catch(function (e) {
      $('sel-summary').className = 'banner warn static'; $('sel-summary').textContent = 'Not available yet: ' + e.message + ' Run the safety checks first.';
    });
  }
  function postSelection(body) {
    return needName('Your name is saved with every choice, so there is a record of who chose what.').then(function (by) {
      if (!by) return null;
      body.by = by;
      return api('POST', 'selection', body).then(function (d) {
        renderSelection(d); refreshStatus();
        if (d.refused && d.refused.length) banner('warn', plural(d.refused.length, 'volume') + ' could not be chosen: ' + d.refused[0].reason + '.');
        return d;
      });
    });
  }
  function renderSelection(d) {
    selData = d;
    var sum = $('sel-summary');
    sum.className = 'banner static ' + (d.selected ? 'ok' : 'warn');
    sum.textContent = d.selected + ' of the ' + d.eligible + ' volumes that can be moved are chosen (' + d.selected_gib + ' GiB). ' + (d.total - d.eligible) + ' of ' + d.total +
      ' volumes will stay where they are, because the safety checks held them back. ' + (d.default ? 'This is the suggested choice.' : 'You changed the suggested choice.') +
      (d.running ? ' A move is running: stop it to change the choice.' : '');
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
        b.addEventListener('click', function () { postSelection({ action: action, group: { kind: selKind, key: g.key } }).catch(function (e) { banner('bad', e.message); }).then(loadTree); });
        return b;
      }
      tb.appendChild(el('tr', {}, [el('td', { text: g.key }), el('td', { text: String(g.count) }), el('td', { text: String(g.eligible) }), el('td', { text: String(g.gib) }),
        el('td', {}, [btn('Add them', 'add'), document.createTextNode(' '), btn('Only these', 'set'), document.createTextNode(' '), btn('Remove them', 'remove')])]));
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
      cb.addEventListener('change', function () { postSelection({ action: cb.checked ? 'add' : 'remove', ids: [v.id] }).catch(function (e) { banner('bad', e.message); }).then(loadTree); });
      var tr = el('tr', { title: v.why || 'Click for the steps of this volume' }, [el('td', {}, [cb]), el('td', { class: 'mono', text: v.id.slice(-12) }), el('td', { text: v.name || '-' }), el('td', { text: clsName(v['class']) }),
        el('td', { text: v.project }), el('td', { text: String(v.size) }), el('td', { text: String(v.snapshots) }), el('td', { text: v.attached.join(', ') }),
        el('td', { text: v.eligible ? (v.selected ? 'chosen' : 'not chosen') : 'stays where it is: ' + v.why })]);
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
        wrap = el('label', { class: 'check', title: 'safe value: ' + (d.defaults[k] ? 'on' : 'off') }, [cb, document.createTextNode(' ' + d.labels[k])]);
      } else {
        var inp = el('input', { type: 'number', min: String(lim[0]), max: String(lim[1]), value: String(d.options[k]), title: lim[2] });
        inp.addEventListener('change', function () { setOption(k, parseInt(inp.value, 10)); });
        wrap = el('label', { title: lim[2] }, [document.createTextNode(d.labels[k] + ' (' + lim[0] + ' to ' + lim[1] + ', safe value ' + d.defaults[k] + ')'), inp]);
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
  $('sel-all').addEventListener('click', function () { postSelection({ action: 'add', ids: selData.volumes.filter(function (v) { return v.eligible; }).map(function (v) { return v.id; }) }).catch(function (e) { banner('bad', e.message); }).then(loadTree); });
  $('sel-none').addEventListener('click', function () { postSelection({ action: 'none' }).catch(function (e) { banner('bad', e.message); }).then(loadTree); });
  $('sel-reset').addEventListener('click', function () { postSelection({ action: 'reset' }).catch(function (e) { banner('bad', e.message); }).then(loadTree); });
  $('opt-reset').addEventListener('click', function () { postSelection({ reset_options: true }).then(function () { $('opt-status').textContent = 'Back to the safe values.'; }).catch(function (e) { banner('bad', e.message); }); });

  // ---- volumes that may break if they are moved: check, mark ----
  var riskData = null;
  function loadRisk() {
    api('GET', 'risk').then(renderRisk).catch(function () { clear($('risk-cards')); clear($('risk-table').tBodies[0]); });
  }
  function renderRisk(d) {
    riskData = d;
    var c = $('risk-cards'); clear(c);
    [['May break: not moved yet', d.high - d.cleared], ['May break: checked by two people', d.cleared], ['Worth a look (not blocked)', d.medium]].forEach(function (x) { c.appendChild(card(x[0], x[1])); });
    var tb = $('risk-table').tBodies[0]; clear(tb);
    d.rows.forEach(function (r) {
      var cb = el('input', { type: 'checkbox' }); cb.setAttribute('data-id', r.id); cb.disabled = r.level !== 'high';
      var by = r.cleared ? (r.operator + ' and ' + r.reviewer) : (r.operator ? 'first person: ' + r.operator + ' (a second person is needed)' : (r.reviewer ? 'second person: ' + r.reviewer + ' (a first person is needed)' : ''));
      var last = el('td', {}, [document.createTextNode(by)]);
      if (r.marked) {
        var un = el('button', { class: 'linklike', type: 'button', text: ' Remove my mark' });
        un.addEventListener('click', function () {
          ask({ title: 'Remove the mark', intro: r.name || r.id, fields: [nameField('Your name')], ok: 'Remove the mark',
            submit: function (v) { me = v.by; return api('POST', 'risk/mark', { ids: [r.id], by: v.by, withdraw: true }).then(function (x) { renderRisk(x); loadSelection(); refreshStatus(); loadTree(); }); } })
            .then(function (v) { if (v) banner('ok', 'The mark was removed. The safety checks were run again, so they need the two approvals again.'); });
        });
        last.appendChild(un);
      }
      tb.appendChild(el('tr', {}, [el('td', {}, [cb]), el('td', { class: 'mono', text: r.id.slice(-12) }), el('td', { text: r.name || '-' }), el('td', { text: clsName(r['class']) }),
        el('td', {}, [el('span', { class: 'ph ' + (r.level === 'high' ? (r.cleared ? 'ph-cutover' : 'ph-aborted') : 'ph-validate'), text: r.level === 'high' ? (r.cleared ? 'checked by two people' : 'may break: not moved') : 'worth a look' })]),
        el('td', { text: r.reasons.join(' | ') }), last]));
    });
    var adv = $('risk-advice'); clear(adv);
    if (d.rows.length) {
      adv.appendChild(el('details', { class: 'cause' }, [el('summary', { text: 'What to do about these' }),
        el('ul', {}, Array.from(new Set([].concat.apply([], d.rows.map(function (r) { return r.advice; })))).map(function (a) { return el('li', { text: a }); }))]));
    } else {
      adv.appendChild(el('p', { class: 'muted', text: 'OpenMove found nothing that looks risky. It can only read hints, so use "This volume may break if moved" in the list above for anything you know about.' }));
    }
  }
  function tickedRisk() { return Array.prototype.map.call($('risk-table').querySelectorAll('input[type=checkbox]:checked'), function (b) { return b.getAttribute('data-id'); }); }
  function riskClear(role, btn, withdraw) {
    var ids = tickedRisk(); if (!ids.length) { banner('warn', 'Tick the volumes in the table first.'); return; }
    var fields = [nameField('Your name', role === 'reviewer' ? 'You must be a different person from the first one.' : '', role === 'operator')];
    if (!withdraw) fields.push({ key: 'evidence', label: 'What did you check?', type: 'textarea', min: 20, hint: 'What you tested, what the owner agreed, and how to go back. At least 20 characters.' });
    ask({ title: withdraw ? 'Take back your check' : (role === 'operator' ? 'I have checked these volumes (first person)' : 'I have checked these volumes (second person)'),
      intro: withdraw ? plural(ids.length, 'volume') : 'You are saying that you looked at ' + plural(ids.length, 'volume') + ' and that moving them is safe. Two different people must check each volume.',
      fields: fields, ok: withdraw ? 'Take it back' : 'Save my check',
      submit: function (v) {
        return api('POST', 'risk/clear', { ids: ids, role: role, by: v.by, evidence: withdraw ? 'withdrawn' : v.evidence, withdraw: !!withdraw }).then(function (d) {
          if (role === 'operator') me = v.by;
          renderRisk(d); loadSelection(); refreshStatus(); loadTree(); return d;
        });
      } }).then(function (d) { if (d && d.rerun) banner('ok', d.rerun); });
  }
  $('risk-op').addEventListener('click', function () { riskClear('operator', $('risk-op')); });
  $('risk-rv').addEventListener('click', function () { riskClear('reviewer', $('risk-rv')); });
  $('risk-wd').addEventListener('click', function () { riskClear('operator', $('risk-wd'), true); });
  function flagRisk(v, btn) {
    ask({ title: 'This volume may break if moved',
      intro: 'Use this when you know the volume carries something that can break after a move, for example an old Windows server. OpenMove will not move it until two different people have checked it.\n\nVolume: ' + (v.name || v.id),
      fields: [nameField('Your name'), { key: 'note', label: 'What does it carry, and who owns it?', type: 'textarea', min: 8 }], ok: 'Mark it as a risk',
      submit: function (x) { me = x.by; return api('POST', 'risk/mark', { ids: [v.id], by: x.by, note: x.note }).then(function (d) { renderRisk(d); loadSelection(); refreshStatus(); loadTree(); }); } })
      .then(function (x) { if (x) banner('ok', 'Marked as a risk. The safety checks were run again, so they need the two approvals again.'); });
  }

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
      var rb = el('button', { class: 'primary', text: 'Release this volume for the switch' });
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
  var GATE_PLAIN = { G2: 'the copy is verified', G3: 'the volume is ready to switch', G4: 'the switch is verified' };
  function releaseVolumes(body, btn) {
    ask({ title: 'Release volumes for the switch',
      intro: 'These volumes are copied and checked. Releasing them lets OpenMove switch the instances over to the new volumes. OpenMove asks every safety rule again for each volume, so a volume that is not ready is skipped.',
      fields: [nameField('Your name')], ok: 'Release',
      submit: function (v) { me = v.by; body.by = v.by; return api('POST', 'release', body).then(function (l) { renderLive(l); if (vdId) loadVolume(vdId, true); }); } })
      .then(function (v) { if (v) banner('ok', 'Released. A volume is switched only if every condition holds.'); });
  }
  function signGates(names, btn) {
    ask({ title: 'Approve as the second person',
      intro: 'You must not be the person running this move.\n' + names.map(function (g) { return g + ': ' + GATE_PLAIN[g]; }).join('\n') + '\n\nSay what you checked. It is saved in the history.' +
        (lastStatus && lastStatus.demo ? '\n(Demonstration: you can use the name "Demo reviewer".)' : ''),
      fields: [nameField('Your name', 'Not the person running the move.', false), { key: 'note', label: 'What did you check?', type: 'textarea', min: 8 }], ok: 'Approve',
      submit: function (v) {
        var chain = Promise.resolve(null);
        names.forEach(function (g) { chain = chain.then(function () { return api('POST', 'volume-gate', { gate: g, all: true, by: v.by, evidence: v.note }); }); });
        return chain.then(function (l) { renderLive(l); if (vdId) loadVolume(vdId, true); });
      } }).then(function (v) { if (v) banner('ok', 'Approved ' + names.join(' and ') + ' for the volumes that were at that point.'); });
  }
  $('sign-g23').addEventListener('click', function () { signGates(['G2', 'G3'], $('sign-g23')); });
  $('sign-g4').addEventListener('click', function () { signGates(['G4'], $('sign-g4')); });
  $('sign-demo').addEventListener('click', function () {
    guard($('sign-demo'), function () { return api('POST', 'demo/simulate', { action: 'autosign', on: !(lastLive && lastLive.auto_sign) }).then(renderLive); });
  });
  $('live-release').addEventListener('click', function () { releaseVolumes({ all: true }, $('live-release')); });

  // ---- hosts (the migration host and the Cinder host) ----
  function fmtBytes(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + ' MiB' : n > 1024 ? (n / 1024).toFixed(1) + ' KiB' : n + ' B'; }
  function drawChecks(c) {
    var tb = $('hs-checks').tBodies[0]; clear(tb);
    $('hs-status').textContent = c ? (c.ready ? 'Ready: volumes with long-lived snapshots can be moved (' + c.family + ').' : 'Not ready. ' + (c.why || '')) : '';
    (c && c.checks || []).forEach(function (x) {
      tb.appendChild(el('tr', { class: 'chk-' + x.status }, [el('td', { text: x.id + ' ' + x.title }), el('td', { text: x.status }), el('td', { text: x.detail }), el('td', { text: x.status === 'pass' ? '' : x.hint })]));
    });
  }
  function hostAction(btn, path, body, done) {
    guard(btn, function () { return api('POST', path, body).then(done || drawHosts); });
  }
  function drawHosts(d) {
    $('hs-key').textContent = d.public_key || '(it is created when you save the first host)';
    $('hs-notes').textContent = [].concat((d.ssh && d.ssh.notes) || []).join(' ');
    var tb = $('hs-table').tBodies[0]; clear(tb);
    (d.hosts || []).forEach(function (h) {
      var acts = el('td');
      function btn(label, fn) { var b = el('button', { class: 'secondary', text: label }); b.addEventListener('click', function () { fn(b); }); acts.appendChild(b); }
      if (h.transport !== 'local' && !h.pinned) btn('Check the server\'s identity', function (b) {
        guard(b, function () {
          return api('POST', 'hosts/scan', { name: h.name }).then(function (r) {
            var fps = r.keys.map(function (k) { return k.type + ' ' + k.fingerprint; }).join('\n');
            return ask({ title: 'Check the identity of ' + h.name,
              intro: 'The server offers these keys:\n\n' + fps + '\n\nCompare them with the fingerprint that the server\'s administrator gives you. Then type the fingerprint you checked.',
              fields: [{ key: 'fp', label: 'Fingerprint you checked', min: 10, placeholder: 'SHA256:...' }, nameField('Your name')], ok: 'It matches: trust this server',
              submit: function (v) { me = v.by; return api('POST', 'hosts/trust', { name: h.name, fingerprint: v.fp, by: v.by }).then(drawHosts); } });
          });
        });
      });
      btn('Install the data mover', function (b) { hostAction(b, 'hosts/mover', { name: h.name, action: 'install' }, function () { banner('ok', 'The data mover is installed on ' + h.name + '.'); }); });
      btn('Remove the data mover', function (b) { hostAction(b, 'hosts/mover', { name: h.name, action: 'remove' }, function () { banner('ok', 'Nothing of OpenMove is left on ' + h.name + '.'); }); });
      btn('Forget this server', function (b) {
        ask({ title: 'Forget ' + h.name + '?', intro: 'OpenMove stops using this server. Nothing is changed on the server.', ok: 'Forget it', danger: true })
          .then(function (v) { if (v) hostAction(b, 'hosts/remove', { name: h.name }); });
      });
      tb.appendChild(el('tr', {}, [el('td', { text: h.name }), el('td', { text: (h.roles || []).join(', ') }), el('td', { text: h.address ? h.address + ':' + (h.port || 22) : 'this machine' }),
        el('td', { text: (h.user || '') + (h.sudo ? ' (sudo)' : '') }), el('td', { text: h.transport === 'local' ? 'n/a' : h.pinned ? 'checked and pinned' : 'not checked yet' }), acts]));
    });
    drawChecks(d.checks);
  }
  function loadHosts() { return api('GET', 'hosts').then(drawHosts).catch(function (e) { banner('bad', e.message); }); }
  function setupScript(btn, done) {
    var q = 'hosts/setup-script?role=' + encodeURIComponent($('hs-sc-role').value) + '&user=' + encodeURIComponent($('hs-sc-user').value.trim() || 'openmove') +
      '&from=' + encodeURIComponent($('hs-sc-from').value.trim());
    guard(btn, function () { return api('GET', q).then(done); });
  }
  $('hs-sc-show').addEventListener('click', function () {
    setupScript($('hs-sc-show'), function (d) { $('hs-sc-cmd').textContent = d.command + '\n\n' + d.notes.join('\n'); });
  });
  $('hs-sc-download').addEventListener('click', function () {
    setupScript($('hs-sc-download'), function (d) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([d.script], { type: 'text/x-shellscript' }));
      a.download = d.filename; document.body.appendChild(a); a.click(); document.body.removeChild(a);
      $('hs-sc-cmd').textContent = d.command + '\n\n' + d.notes.join('\n');
    });
  });
  $('hs-save').addEventListener('click', function () {
    var roles = []; if ($('hs-role-mover').checked) roles.push('mover'); if ($('hs-role-cinder').checked) roles.push('cinder');
    hostAction($('hs-save'), 'hosts/save', { host: { name: $('hs-name').value.trim(), address: $('hs-address').value.trim(), port: parseInt($('hs-port').value, 10) || 22,
      user: $('hs-user').value.trim() || 'root', sudo: $('hs-sudo').checked, roles: roles, python: $('hs-python').value.trim(), cinder_user: $('hs-cuser').value.trim() } });
  });
  $('hs-check').addEventListener('click', function () {
    hostAction($('hs-check'), 'hosts/check', { bench: $('hs-bench').checked }, function (c) { drawChecks(c); });
  });

  // ---- set up a new host from here ----
  function suList(id) { return $(id).value.split(/\r?\n/).map(function (x) { return x.trim(); }).filter(Boolean); }
  function suBody(checkOnly) {
    var roles = []; if ($('su-role-mover').checked) roles.push('mover'); if ($('su-role-cinder').checked) roles.push('cinder');
    var arrays = []; if ($('su-arr-pure').checked) arrays.push('pure'); if ($('su-arr-sf').checked) arrays.push('solidfire');
    var proto = $('su-proto').value;
    return {
      name: $('su-name').value.trim(), address: $('su-address').value.trim(), port: parseInt($('su-port').value, 10) || 22, user: $('su-user').value.trim() || 'root',
      password: $('su-password').value, roles: roles, service_user: $('su-service').value.trim() || 'openmove', from_address: $('su-from').value.trim(),
      cinder_python: $('su-cpython').value.trim(), fingerprint: $('su-fp').value.trim(), by: $('su-by').value.trim(), check_only: !!checkOnly,
      storage: proto === 'none' ? { protocol: 'none' } : { protocol: proto, portals: suList('su-portals'), initiator: $('su-initiator').value.trim(),
        chap_user: $('su-chap-user').value.trim(), chap_secret: $('su-chap-secret').value, arrays: arrays, multipath_extra: $('su-mpextra').value,
        replacement_timeout: parseInt($('su-timeo').value, 10) || 15 }
    };
  }
  function drawSetup(r) {
    var box = $('su-result'); clear(box);
    var tb = el('tbody');
    (r.steps || []).forEach(function (s) {
      var det = el('td', { text: s.detail });
      if (s.output) { var d = el('details', {}, [el('summary', { text: 'Complete output' }), el('pre', { class: 'code', text: s.output })]); det.appendChild(d); }
      tb.appendChild(el('tr', { class: 'chk-' + (s.status === 'ok' ? 'pass' : s.status === 'fail' ? 'fail' : 'warn') }, [el('td', { text: s.id + ' ' + s.title }), el('td', { text: s.status }), det]));
    });
    box.appendChild(el('div', { class: 'table-wrap' }, [el('table', {}, [el('thead', {}, [el('tr', {}, [el('th', { text: 'Step' }), el('th', { text: 'Result' }), el('th', { text: 'What happened' })])]), tb])]));
    if (r.initiator) box.appendChild(el('p', {}, [el('strong', { text: 'Initiator name to register on each array: ' }), el('code', { text: r.initiator })]));
    if (r.storage && r.storage.wwpns && r.storage.wwpns.length) box.appendChild(el('p', {}, [el('strong', { text: 'Fibre Channel port names to zone and register: ' }), el('code', { text: r.storage.wwpns.join(', ') })]));
    if (r.checks && r.checks.length) {
      var ct = el('tbody');
      r.checks.forEach(function (x) { ct.appendChild(el('tr', { class: 'chk-' + x.status }, [el('td', { text: x.id + ' ' + x.title }), el('td', { text: x.status }), el('td', { text: x.detail })])); });
      box.appendChild(el('h4', { text: 'Checks of the new host' }));
      box.appendChild(el('div', { class: 'table-wrap' }, [el('table', {}, [el('thead', {}, [el('tr', {}, [el('th', { text: 'Check' }), el('th', { text: 'Result' }), el('th', { text: 'What was seen' })])]), ct])]));
    }
    $('su-status').textContent = r.ok ? (r.check_only ? 'Nothing is missing.' : 'The host is set up and registered. The password has been dropped.') : 'The setup did not finish. Read the steps; run it again after the fix.';
  }
  function runSetup(btn, checkOnly) {
    var body = suBody(checkOnly);
    guard(btn, function () {
      return api('POST', 'hosts/setup', body).then(function (r) {
        $('su-password').value = ''; $('su-chap-secret').value = '';
        drawSetup(r);
        if (r.hosts) drawHosts(r.hosts);
        banner(r.ok ? 'ok' : 'bad', r.ok ? (checkOnly ? 'The host needs nothing more.' : 'The host ' + body.name + ' is set up.') : 'The host setup did not finish: see the steps.');
      });
    });
  }
  $('su-proto').addEventListener('change', function () { $('su-storage').classList.toggle('hidden', $('su-proto').value === 'none'); });
  $('su-probe').addEventListener('click', function () {
    guard($('su-probe'), function () {
      return api('POST', 'hosts/probe', { address: $('su-address').value.trim(), port: parseInt($('su-port').value, 10) || 22 }).then(function (r) {
        $('su-keys').textContent = r.keys.map(function (k) { return k.type + ' ' + k.fingerprint; }).join('\n') + (r.pinned ? '\n\nThis key is already pinned: no need to type the fingerprint again.' : '');
      });
    });
  });
  $('su-check').addEventListener('click', function () { runSetup($('su-check'), true); });
  $('su-run').addEventListener('click', function () { runSetup($('su-run'), false); });

  // ---- diagnostics (the log, the call trace, the support bundle) ----
  var diagInfo = null;
  function drawDiag(d) {
    diagInfo = d;
    $('dg-level').value = d.settings.redaction; $('dg-log-level').value = d.settings.log_redaction;
    $('dg-text').textContent = d.text[$('dg-level').value] || '';
    var tb = $('dg-bundles').tBodies[0]; clear(tb);
    (d.bundles || []).forEach(function (b) {
      var a = el('a', { text: 'Download', href: '#' });
      a.addEventListener('click', function (ev) {
        ev.preventDefault();
        fetch('/api/diagnostics/download?name=' + encodeURIComponent(b.name), { headers: { 'X-OpenMove-Token': TOKEN } }).then(function (r) { return r.blob(); }).then(function (blob) {
          var u = URL.createObjectURL(blob), x = el('a', { href: u, download: b.name }); document.body.appendChild(x); x.click(); document.body.removeChild(x); URL.revokeObjectURL(u);
        });
      });
      tb.appendChild(el('tr', {}, [el('td', { text: b.name }), el('td', { text: (b.at || '').replace('T', ' ').replace('Z', '') }), el('td', { text: fmtBytes(b.bytes) }), el('td', {}, [a])]));
    });
    var lt = $('dg-logs').tBodies[0]; clear(lt);
    (d.logs || []).forEach(function (f) { lt.appendChild(el('tr', {}, [el('td', { text: f.name }), el('td', { text: fmtBytes(f.bytes) })])); });
    $('dg-folder').textContent = 'Bundles are kept in ' + d.folder + '. They stay on this machine until you copy them.';
  }
  function loadDiag() { return api('GET', 'diagnostics').then(drawDiag).catch(function (e) { banner('bad', e.message); }); }
  $('dg-level').addEventListener('change', function () { if (diagInfo) $('dg-text').textContent = diagInfo.text[$('dg-level').value] || ''; });
  $('dg-refresh').addEventListener('click', loadDiag);
  $('dg-bundle').addEventListener('click', function () {
    guard($('dg-bundle'), function () {
      return api('POST', 'diagnostics/bundle', { level: $('dg-level').value, remote: $('dg-remote').checked, reason: 'requested from the screen' }).then(function (r) {
        banner('ok', 'Support bundle written: ' + r.name + ' (' + r.files + ' files, redaction ' + r.redaction + ').'); return loadDiag();
      });
    });
  });
  $('dg-log-save').addEventListener('click', function () {
    guard($('dg-log-save'), function () { return api('POST', 'diagnostics/settings', { log_redaction: $('dg-log-level').value, redaction: $('dg-level').value }).then(drawDiag); });
  });
  $('dg-decode').addEventListener('click', function () {
    guard($('dg-decode'), function () {
      return api('POST', 'diagnostics/decode', { token: $('dg-token').value.trim() }).then(function (r) { $('dg-decoded').textContent = r.token + ' = ' + r.value; });
    });
  });

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
        var b = el('button', { class: 'secondary', text: 'Put back' });
        b.addEventListener('click', function () {
          ask({ title: 'Put ' + r.id.slice(-12) + ' back', intro: r.method === 'staged' ? 'This is a careful copy the other way, with a safety copy.' : 'This moves the volume back to its first volume type.',
            fields: [nameField('Your name'), { key: 'why', label: 'Why?', type: 'textarea', min: 8 }], ok: 'Put it back', danger: true,
            submit: function (v) { me = v.by; return api('POST', 'engine/reverse', { id: r.id, by: v.by, reason: v.why }).then(drawMove); } });
        });
        act.appendChild(b);
      }
      var st = r.stage, prog = st ? ('images ' + st.verified + '/' + st.images + (st.cut ? ', step ' + st.cut : '') + (st.frozen ? ', frozen' : '') + '. ') : '';
      var what = el('td', { text: prog + (r.note || '') });
      if (r.error) what.appendChild(el('div', { class: 'muted', text: 'Last error: ' + r.error }));
      tb.appendChild(el('tr', {}, [el('td', { text: r.id }), el('td', { text: r.class || '' }), el('td', { text: r.method === 'staged' ? 'careful copy' : 'storage retype' }), el('td', { text: r.phase }),
        el('td', { text: r.gates.G2 ? 'signed' : '' }), el('td', { text: r.gates.G3 ? 'signed' : '' }), el('td', { text: r.gates.G4 ? 'signed' : '' }),
        what, act]));
    });
    $('mv-more').textContent = m.more ? m.more + ' more volume(s) not shown.' : '';
    $('mv-status').textContent = m.halted ? 'HALTED by ' + m.halted.by + ': ' + m.halted.reason : '';
    if (moveTimer) { clearTimeout(moveTimer); moveTimer = null; }
    if (m.status === 'running' && currentTab === 'move') moveTimer = setTimeout(loadMove, 4000);
  }
  function loadMove() { return api('GET', 'engine').then(drawMove).catch(function () {}); }
  function drawDry(d) {
    var v = $('mv-dry-view'); clear(v);
    v.appendChild(el('div', { class: 'banner ok static', text: 'Rehearsal done. Nothing was sent. ' + d.counts.ok + ' ready, ' + d.counts.wait + ' waiting, ' + d.counts.blocked + ' blocked. ' + d.note }));
    var t = el('table', {}, [el('thead', {}, [el('tr', {}, ['Volume', 'Group', 'Result', 'Move to type', 'Why not, or what would be sent'].map(function (h) { return el('th', { text: h }); }))]), el('tbody')]);
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
    ask({ title: 'Start moving', intro: 'This changes volumes in your cloud. For each volume, nothing is sent until a second person has approved it.',
      fields: [nameField('Your name')], ok: 'Start moving', danger: true,
      submit: function (v) { me = v.by; return api('POST', 'engine/start', { by: v.by, type_map: typeMap(), start_at: $('mv-from').value.trim(), stop_at: $('mv-until').value.trim() }).then(drawMove); } })
      .then(function (v) { if (v) banner('ok', 'Started. For each volume, nothing is sent until a second person approves G2 and G3 (see step 6, Progress).'); });
  });
  $('mv-rev-preview').addEventListener('click', function () {
    guard($('mv-rev-preview'), function () {
      return api('POST', 'engine/reverse', { preview: true }).then(function (d) {
        var v = $('mv-dry-view'); clear(v);
        v.appendChild(el('h3', { text: 'What putting the whole run back would do (nothing was changed)' }));
        v.appendChild(el('p', { class: 'muted', text: d.note }));
        if (!d.volumes.length) v.appendChild(el('p', { text: 'No volume has been cut over, so there is nothing to put back.' }));
        d.volumes.forEach(function (r) {
          v.appendChild(el('p', {}, [el('strong', { text: r.id + ': ' }), document.createTextNode(r.can ? r.calls.map(function (c) { return c.what + ' (undo: ' + c.undo + ')'; }).join('; ') : r.why)]));
        });
      });
    });
  });
  $('mv-rev-all').addEventListener('click', function () {
    ask({ title: 'Put the whole move back', intro: 'Every volume that was switched in this move goes back to its first volume type. Read the preview first.',
      fields: [nameField('Your name'), { key: 'why', label: 'Why?', type: 'textarea', min: 8 }], ok: 'Put everything back', danger: true,
      submit: function (v) { me = v.by; return api('POST', 'engine/reverse', { all: true, by: v.by, reason: v.why }).then(drawMove); } });
  });
  $('mv-stop').addEventListener('click', function () { guard($('mv-stop'), function () { return api('POST', 'engine/stop', {}).then(drawMove); }); });
  $('mv-refresh').addEventListener('click', loadMove);
  $('mv-release').addEventListener('click', function () { releaseVolumes({ all: true }, $('mv-release')); });
  $('mv-g23').addEventListener('click', function () { signGates(['G2', 'G3'], $('mv-g23')); });
  $('mv-g4').addEventListener('click', function () { signGates(['G4'], $('mv-g4')); });
  $('mv-validate').addEventListener('click', function () {
    ask({ title: 'Record gate G0: the lab tests passed',
      intro: 'You are recording that the lab tests for this OpenStack release were run and passed. This lifts the look-only state of the release. Two different people are needed.',
      fields: [nameField('Your name'), { key: 'second', label: 'The second person', hint: 'A different person.', min: 3 },
               { key: 'ev', label: 'What was tested, on which cloud, with what result', type: 'textarea', rows: 4, min: 40 }], ok: 'Record G0',
      submit: function (v) { me = v.by; return api('POST', 'validation', { by: v.by, second: v.second, evidence: v.ev, passed: true, environment: 'test' }).then(function (r) { refreshStatus(); return r; }); } })
      .then(function (r) { if (r && r.release) banner('ok', 'Recorded the lab validation of ' + r.release + '. Run the safety checks again.'); });
  });
  // ---- Phase 0 lab tests ----
  function drawPhase0(d) {
    var v = $('p0-view'), s = d.summary;
    $('p0-count').textContent = '(' + s.passed + ' of ' + s.required + ' required passed' + (s.failed ? ', ' + s.failed + ' failed' : '') + ')';
    v.innerHTML = '';
    d.tests.forEach(function (t) {
      var res = t.result === 'not run' ? (t.required ? 'not run' : 'not required') : t.result;
      var box = el('details', { class: 'p0-test' }, [
        el('summary', {}, [el('strong', { text: t.id + '  ' }), document.createTextNode(t.title + ' - '), el('span', { class: 'p0-' + t.result.replace(' ', '-'), text: res })]),
        el('p', {}, [el('strong', { text: 'Proves: ' }), document.createTextNode(t.proves)]),
        el('p', {}, [el('strong', { text: 'Passes when: ' }), document.createTextNode(t.pass)]),
        el('p', {}, [el('strong', { text: 'How: ' }), document.createTextNode(t.how)])]);
      if (t.by) box.appendChild(el('p', { class: 'muted', text: 'Recorded by ' + t.by + ' on ' + (t.at || '').slice(0, 10) + ': ' + (t.evidence || '') }));
      if (t.required) {
        var b = el('button', { class: 'secondary', text: 'Record the result of ' + t.id });
        b.addEventListener('click', function () { recordPhase0(t, b); });
        box.appendChild(b);
      }
      v.appendChild(box);
    });
  }
  function loadPhase0() { return api('GET', 'phase0').then(drawPhase0).catch(function () { $('p0-count').textContent = '(scan the cloud first)'; }); }
  function recordPhase0(t, btn) {
    ask({ title: 'Record the result of ' + t.id, intro: t.title,
      fields: [nameField('Your name'), { key: 'result', label: 'Result', type: 'select', options: [['pass', 'Passed'], ['fail', 'Failed']] },
               { key: 'ev', label: 'What you did and what you saw', type: 'textarea', rows: 4, min: 40 }], ok: 'Save the result',
      submit: function (v) { me = v.by; return api('POST', 'phase0/record', { test: t.id, result: v.result, by: v.by, evidence: v.ev }).then(drawPhase0); } });
  }
  $('p0-box').addEventListener('toggle', function () { if ($('p0-box').open) loadPhase0(); });
  $('p0-g0').addEventListener('click', function () {
    ask({ title: 'Record gate G0 from the lab test results', intro: 'This uses the results recorded above. Two different people are needed.',
      fields: [nameField('Your name'), { key: 'second', label: 'The second person', hint: 'A different person.', min: 3 }], ok: 'Record G0',
      submit: function (v) { me = v.by; return api('POST', 'validation', { by: v.by, second: v.second, from_tests: true, environment: 'test' }).then(function (r) { refreshStatus(); return r; }); } })
      .then(function (r) { if (r && r.release) banner('ok', 'Recorded the lab validation of ' + r.release + '. Run the safety checks again.'); });
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
  $('rep-update').addEventListener('click', function () { downloadReport('update', $('rep-update')); });
  $('rep-after').addEventListener('click', function () { downloadReport('after', $('rep-after')); });
  $('rep-xlsx').addEventListener('click', function () { downloadReport('volumes_xlsx', $('rep-xlsx')); });
  $('rep-csv').addEventListener('click', function () { downloadReport('volumes_csv', $('rep-csv')); });

  // ---- help, in plain words ----
  $('help-btn').addEventListener('click', function () {
    var d = $('help'); clear(d);
    function list(rows) { var l = el('dl'); rows.forEach(function (r) { l.appendChild(el('dt', { text: r[0] })); l.appendChild(el('dd', { text: r[1] })); }); return l; }
    var close = el('button', { class: 'primary', type: 'button', text: 'Close' });
    close.addEventListener('click', function () { d.close(); });
    d.appendChild(el('h2', { text: 'How OpenMove works' }));
    d.appendChild(el('p', { text: 'OpenMove moves OpenStack volumes from one storage system to another. It checks everything before it starts, moves the volumes one after another, proves that each one arrived correctly, and keeps a record of who did what. It moves nothing until you say so. Two different people must approve.' }));
    d.appendChild(el('h3', { text: 'The seven steps' }));
    d.appendChild(list([
      ['1. Connect', 'Sign in to your cloud with an administrator account.'],
      ['2. Scan', 'OpenMove reads your cloud and shows every project, instance and volume. It changes nothing.'],
      ['3. Safety checks', 'OpenMove checks that the cloud and the storage are ready. Anything it cannot check itself, a person checks and confirms. Two people approve.'],
      ['4. Choose', 'Tick the volumes to move. A whole project, a whole instance or a single volume.'],
      ['5. Move', 'Rehearse first. Then start. OpenMove moves the chosen volumes and checks each one.'],
      ['6. Progress', 'Watch each volume. A second person approves each volume before it is switched.'],
      ['7. Reports', 'Word and Excel reports for the customer and for your own records.']]));
    d.appendChild(el('h3', { text: 'How volumes are grouped' }));
    d.appendChild(list([['Easy', 'No snapshots. The storage system moves it in one step.'], ['Waits for snapshots', 'Its snapshots are about to expire. OpenMove waits, then moves it like an easy one.'],
      ['Careful copy', 'It has snapshots that must be kept. It is copied with the careful method and needs a helper server.'], ['Instance is off', 'The instance is not running. Start it, then scan again.'],
      ['Not moved by this tool', 'Encrypted, shared or grouped volumes, for example. They are listed so that a person can plan them.']]));
    d.appendChild(el('h3', { text: 'Words you will see' }));
    d.appendChild(list([
      ['Safety check', 'A test that OpenMove runs before it moves anything.'],
      ['Confirm', 'A person looks at something OpenMove cannot check, and writes down what they found.'],
      ['Approve', 'Two different people agree that the checks are met. One person alone cannot start a move.'],
      ['Rehearse', 'OpenMove lists every call it would make. It sends none of them.'],
      ['Switch', 'The moment an instance starts to use the new volume.'],
      ['Wait period', 'After the switch the old volume is kept for some days, so the move can be undone.'],
      ['Put back', 'Moves a volume back to where it was.'],
      ['Emergency stop', 'Stops every change at once. Use it when something looks wrong.'],
      ['Helper server', 'A Linux server that copies volumes with long-lived snapshots. You only need one for those.'],
      ['History', 'The permanent record of everything that happened. Nobody can edit it.']]));
    d.appendChild(el('div', { class: 'actions' }, [close]));
    d.showModal();
  });

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
