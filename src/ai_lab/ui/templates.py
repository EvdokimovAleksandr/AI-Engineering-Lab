"""Embedded local UI. No React, no database, no auth."""

INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <title>AI Engineering Lab</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: Georgia, serif; max-width: 880px; margin: 2rem auto; padding: 0 1rem; }
    h1 { font-size: 1.4rem; }
    textarea { width: 100%; min-height: 8rem; }
    select, button { font: inherit; margin-right: .5rem; margin-top: .5rem; }
    pre { background: #1111; padding: 1rem; overflow: auto; white-space: pre-wrap; }
    .row { margin: .75rem 0; }
    .hitl { color: #a40; font-weight: bold; }
  </style>
</head>
<body>
  <h1>AI Engineering Lab</h1>
  <p>Локальный интерфейс постановки задачи. Orchestration — существующий LabRuntime.</p>
  <div class="row">
    <label>Engineering problem<br/>
      <textarea id="problem" placeholder="Рассчитай напряжение в волокне диаметром 5 мкм при силе 0.2 N"></textarea>
    </label>
  </div>
  <div class="row">
    <label>Project
      <select id="project"></select>
    </label>
  </div>
  <div class="row">
    <button id="plan">Create Plan</button>
    <button id="run">Run</button>
  </div>
  <p id="progress"></p>
  <pre id="out"></pre>
  <p>
    <a id="link-graph" href="#">View task graph</a> ·
    <a id="link-evidence" href="#">View evidence</a> ·
    <a id="link-comp" href="#">View computation</a> ·
    <a id="link-ver" href="#">View verification</a>
  </p>
<script>
async function loadProjects() {
  const r = await fetch('/api/projects');
  const data = await r.json();
  const sel = document.getElementById('project');
  sel.innerHTML = '';
  (data.projects || []).forEach(p => {
    const o = document.createElement('option');
    o.value = p; o.textContent = p;
    sel.appendChild(o);
  });
}
async function submit(action) {
  const body = {
    problem: document.getElementById('problem').value,
    project: document.getElementById('project').value,
    action
  };
  document.getElementById('progress').textContent = action === 'plan'
    ? 'Problem → Planning → Validation'
    : 'Problem → Planning → Validation → Execution → Verification → Red Team → Adjudication → Result';
  const r = await fetch('/api/runs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const data = await r.json();
  document.getElementById('out').textContent = JSON.stringify(data, null, 2);
  if (data.run_id) {
    const st = await fetch('/api/runs/' + data.run_id + '/status').then(x => x.json());
    const res = await fetch('/api/runs/' + data.run_id + '/result').then(x => x.json());
    document.getElementById('out').textContent = JSON.stringify({created: data, status: st, result: res}, null, 2);
    if (st.hitl_required) {
      document.getElementById('progress').innerHTML = '<span class="hitl">Human approval required</span>';
    }
    document.getElementById('link-graph').href = '/api/runs/' + data.run_id;
  }
}
document.getElementById('plan').onclick = () => submit('plan');
document.getElementById('run').onclick = () => submit('run');
loadProjects();
</script>
</body>
</html>
"""
