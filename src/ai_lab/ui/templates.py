"""Embedded laboratory UI (stdlib HTML/JS). No React — thin window into LabRuntime."""

# data-demo is flipped by the HTTP server when --demo is set.
INDEX_HTML = """<!DOCTYPE html>
<html lang="ru" data-demo="false">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>AI Engineering Lab — инженерная лаборатория</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #0f1419;
      --bg-elev: #171e26;
      --bg-soft: #1c2530;
      --line: #2a3542;
      --text: #e7eef7;
      --muted: #93a4b8;
      --accent: #3d9cf0;
      --accent-dim: rgba(61,156,240,.15);
      --ok: #3ecf8e;
      --warn: #e6b84d;
      --bad: #ef6b6b;
      --pending: #6b7c90;
      --tip-bg: #1a2430;
      --tip-fg: #e7eef7;
      --font: "IBM Plex Sans", "Segoe UI", sans-serif;
      --mono: "IBM Plex Mono", ui-monospace, monospace;
      --display: "IBM Plex Serif", Georgia, serif;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f4f7fb;
        --bg-elev: #ffffff;
        --bg-soft: #eef3f9;
        --line: #d5dee8;
        --text: #142033;
        --muted: #5b6b7c;
        --accent: #1a6fbf;
        --accent-dim: rgba(26,111,191,.12);
        --tip-bg: #1c2a3a;
        --tip-fg: #f4f7fb;
      }
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; }
    body {
      font-family: var(--font);
      background:
        radial-gradient(1200px 600px at 10% -10%, rgba(61,156,240,.12), transparent 55%),
        radial-gradient(900px 500px at 90% 0%, rgba(62,207,142,.08), transparent 50%),
        var(--bg);
      color: var(--text);
      line-height: 1.45;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .shell { max-width: 1100px; margin: 0 auto; padding: 1.25rem 1.25rem 3rem; }
    header.app {
      display: flex; align-items: center; justify-content: space-between;
      gap: 1rem; margin-bottom: 2rem; padding-bottom: .75rem;
      border-bottom: 1px solid var(--line);
    }
    .brand { font-family: var(--display); font-size: 1.15rem; letter-spacing: .02em; }
    .brand span { color: var(--muted); font-family: var(--font); font-size: .8rem; margin-left: .5rem; }
    nav a { margin-left: 1rem; color: var(--muted); font-size: .9rem; }
    nav a:hover { color: var(--text); }
    h1 { font-family: var(--display); font-weight: 500; font-size: clamp(1.6rem, 3vw, 2.2rem); margin: 0 0 .5rem; }
    h2 { font-size: 1.05rem; font-weight: 600; margin: 0 0 .75rem; letter-spacing: .01em; }
    h3 { font-size: .92rem; margin: 0 0 .5rem; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
    .lead { color: var(--muted); max-width: 38rem; margin-bottom: 1.5rem; }
    .panel {
      background: var(--bg-elev);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 1.25rem;
    }
    textarea, input, select {
      width: 100%; font: inherit; color: var(--text);
      background: var(--bg-soft); border: 1px solid var(--line);
      border-radius: 10px; padding: .85rem 1rem;
    }
    textarea { min-height: 9rem; resize: vertical; }
    textarea:focus, input:focus { outline: 2px solid var(--accent-dim); border-color: var(--accent); }
    .row { display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; margin-top: 1rem; }
    .optional { margin-top: 1rem; }
    .optional summary { cursor: pointer; color: var(--muted); font-size: .9rem; }
    .optional .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .75rem; margin-top: .75rem; }
    button, .btn {
      font: inherit; cursor: pointer; border: 1px solid transparent;
      border-radius: 999px; padding: .65rem 1.25rem;
      background: var(--accent); color: #fff; font-weight: 600;
    }
    button.secondary, .btn.secondary {
      background: transparent; color: var(--text); border-color: var(--line);
    }
    button:disabled { opacity: .5; cursor: not-allowed; }
    .hint { font-size: .85rem; color: var(--muted); margin-top: .75rem; }
    .list { list-style: none; padding: 0; margin: 1.5rem 0 0; }
    .list li {
      display: flex; justify-content: space-between; gap: 1rem; align-items: center;
      padding: .85rem 0; border-top: 1px solid var(--line);
    }
    .list li:first-child { border-top: 0; }
    .badge {
      display: inline-flex; align-items: center; gap: .35rem;
      font-size: .72rem; font-weight: 700; letter-spacing: .04em;
      padding: .25rem .55rem; border-radius: 999px; border: 1px solid var(--line);
      text-transform: uppercase; white-space: nowrap;
    }
    .badge.ok { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 40%, var(--line)); background: color-mix(in srgb, var(--ok) 12%, transparent); }
    .badge.warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 40%, var(--line)); background: color-mix(in srgb, var(--warn) 12%, transparent); }
    .badge.bad { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); background: color-mix(in srgb, var(--bad) 12%, transparent); }
    .badge.run { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 40%, var(--line)); background: var(--accent-dim); }
    .badge.muted { color: var(--muted); }
    .layout-2 {
      display: grid; grid-template-columns: minmax(220px, 280px) 1fr; gap: 1.25rem;
    }
    @media (max-width: 800px) { .layout-2 { grid-template-columns: 1fr; } }
    .pipeline { list-style: none; padding: 0; margin: 0; }
    .pipeline li {
      position: relative; padding: .55rem .25rem .55rem 1.4rem;
      color: var(--muted); border-left: 2px solid var(--line); margin-left: .45rem;
    }
    .pipeline li::before {
      content: ""; position: absolute; left: -.42rem; top: .85rem;
      width: .65rem; height: .65rem; border-radius: 50%;
      background: var(--pending); border: 2px solid var(--bg-elev);
    }
    .pipeline li.done { color: var(--text); }
    .pipeline li.done::before { background: var(--ok); }
    .pipeline li.running { color: var(--text); }
    .pipeline li.running::before {
      background: var(--accent);
      box-shadow: 0 0 0 4px var(--accent-dim);
      animation: pulse 1.4s ease-in-out infinite;
    }
    .pipeline li.failed::before { background: var(--bad); }
    @keyframes pulse {
      0%, 100% { box-shadow: 0 0 0 3px var(--accent-dim); }
      50% { box-shadow: 0 0 0 7px transparent; }
    }
    .activity {
      font-family: var(--mono); font-size: .82rem; color: var(--muted);
      max-height: 220px; overflow: auto; background: var(--bg-soft);
      border-radius: 10px; padding: .75rem 1rem; border: 1px solid var(--line);
    }
    .activity div { margin: .2rem 0; }
    .stats { display: flex; flex-wrap: wrap; gap: .75rem; margin-top: 1rem; }
    .stat {
      flex: 1 1 120px; background: var(--bg-soft); border: 1px solid var(--line);
      border-radius: 10px; padding: .75rem;
    }
    .stat b { display: block; font-size: 1.25rem; font-family: var(--display); }
    .stat span { color: var(--muted); font-size: .8rem; }
    .hero-result {
      text-align: center; padding: 2rem 1rem; border-radius: 14px;
      background: linear-gradient(180deg, var(--accent-dim), transparent);
      border: 1px solid var(--line); margin-bottom: 1.25rem;
    }
    .hero-result .value {
      font-family: var(--display); font-size: clamp(2rem, 5vw, 3rem); margin: .5rem 0;
    }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: .75rem; }
    .card {
      background: var(--bg-soft); border: 1px solid var(--line); border-radius: 12px; padding: 1rem;
    }
    .card .v { font-family: var(--display); font-size: 1.4rem; margin: .35rem 0; }
    .section { margin-top: 1.5rem; }
    .chain { font-family: var(--mono); font-size: .85rem; color: var(--muted); }
    .chain div { padding: .2rem 0; }
    details.raw { margin-top: .75rem; }
    details.raw summary { cursor: pointer; color: var(--muted); font-size: .85rem; }
    pre.raw {
      font-family: var(--mono); font-size: .75rem; overflow: auto;
      background: var(--bg); border-radius: 8px; padding: .75rem; max-height: 320px;
    }
    .demo-banner {
      display: none; margin-bottom: 1rem; padding: .65rem 1rem; border-radius: 10px;
      background: var(--accent-dim); border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--line));
      font-size: .9rem;
    }
    html[data-demo="true"] .demo-banner { display: block; }
    .trust-tag {
      font-size: .7rem; text-transform: uppercase; letter-spacing: .05em;
      color: var(--muted); border: 1px dashed var(--line); border-radius: 6px; padding: .1rem .35rem;
    }
    .error-box {
      border: 1px solid color-mix(in srgb, var(--bad) 40%, var(--line));
      background: color-mix(in srgb, var(--bad) 10%, transparent);
      border-radius: 12px; padding: 1.25rem; margin-bottom: 1rem;
    }
    .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); border: 0; }
    .field-label {
      display: flex; align-items: center; gap: .35rem;
      font-size: .85rem; color: var(--muted); margin-bottom: .35rem;
    }
    /* Тултипы: подсказки при наведении / фокусе */
    .tip {
      position: relative;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 1.1rem; height: 1.1rem;
      border-radius: 50%;
      border: 1px solid var(--line);
      background: var(--bg-soft);
      color: var(--muted);
      font-size: .72rem;
      font-weight: 700;
      cursor: help;
      flex-shrink: 0;
      vertical-align: middle;
    }
    .tip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    .has-tip { position: relative; }
    .tip::after, .has-tip[data-tip]::after {
      content: attr(data-tip);
      position: absolute;
      z-index: 40;
      left: 50%;
      bottom: calc(100% + 8px);
      transform: translateX(-50%) translateY(4px);
      width: max-content;
      max-width: min(280px, 70vw);
      padding: .55rem .7rem;
      border-radius: 8px;
      background: var(--tip-bg);
      color: var(--tip-fg);
      font-size: .78rem;
      font-weight: 400;
      line-height: 1.35;
      letter-spacing: 0;
      text-transform: none;
      white-space: normal;
      text-align: left;
      box-shadow: 0 8px 24px rgba(0,0,0,.28);
      opacity: 0;
      pointer-events: none;
      transition: opacity .15s ease, transform .15s ease;
    }
    .tip::before, .has-tip[data-tip]::before {
      content: "";
      position: absolute;
      z-index: 41;
      left: 50%;
      bottom: calc(100% + 2px);
      transform: translateX(-50%);
      border: 6px solid transparent;
      border-top-color: var(--tip-bg);
      opacity: 0;
      pointer-events: none;
      transition: opacity .15s ease;
    }
    .tip:hover::after, .tip:focus::after,
    .has-tip[data-tip]:hover::after, .has-tip[data-tip]:focus-within::after {
      opacity: 1; transform: translateX(-50%) translateY(0);
    }
    .tip:hover::before, .tip:focus::before,
    .has-tip[data-tip]:hover::before, .has-tip[data-tip]:focus-within::before {
      opacity: 1;
    }
    /* Тултип снизу, если мало места сверху у кнопок внизу панели */
    .tip.tip-below::after, .has-tip.tip-below[data-tip]::after {
      bottom: auto; top: calc(100% + 8px);
      transform: translateX(-50%) translateY(-4px);
    }
    .tip.tip-below::before, .has-tip.tip-below[data-tip]::before {
      bottom: auto; top: calc(100% + 2px);
      border-top-color: transparent; border-bottom-color: var(--tip-bg);
    }
    .tip.tip-below:hover::after, .tip.tip-below:focus::after,
    .has-tip.tip-below[data-tip]:hover::after {
      transform: translateX(-50%) translateY(0);
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="app">
      <div class="brand has-tip" data-tip="Это не чат с ИИ, а рабочее место инженерной лаборатории: вы задаёте задачу, система сама исследует, считает, проверяет и выдаёт отчёт с доказательствами." tabindex="0">
        AI Engineering Lab <span>инженерная лаборатория</span>
      </div>
      <nav>
        <a href="/" class="has-tip" data-tip="Главный экран: опишите задачу одним текстом и запустите исследование.">Главная</a>
        <a href="#projects" id="nav-projects" class="has-tip" data-tip="Проекты группируют запуски (runs) по одной теме. История сохраняется на диске.">Проекты</a>
      </nav>
    </header>
    <div class="demo-banner" id="demo-banner">
      Демо-режим: детерминированный mock-провайдер, пример задачи (нагреватель) уже подставлен.
      Оркестрация идёт через LabRuntime — без фальшивых таймеров прогресса.
      <span class="tip tip-below" tabindex="0" data-tip="В демо не нужны внешние API-ключи. Результат строится тем же конвейером, что и в обычном режиме.">?</span>
    </div>
    <main id="app" aria-live="polite"></main>
  </div>
<script>
(function () {
  const app = document.getElementById('app');
  const DEMO = document.documentElement.getAttribute('data-demo') === 'true';
  const DEMO_PROBLEM = "Рассчитать необходимую электрическую мощность нагревателя для нагрева 20 литров воды с 20°C до 80°C за 30 минут при оценочных теплопотерях 15%. Использовать Q = m c ΔT и P = Q / t с явным учётом потерь.";

  const STAGE_RU = {
    UNDERSTANDING: 'Понимание задачи',
    DECOMPOSITION: 'Декомпозиция',
    RESEARCH: 'Исследование',
    HYPOTHESIS: 'Гипотезы',
    ANALYSIS: 'Анализ',
    PLANNING: 'Планирование',
    CALCULATION: 'Расчёт',
    SIMULATION: 'Симуляция',
    VERIFICATION: 'Верификация',
    RED_TEAM: 'Независимый обзор',
    ADJUDICATION: 'Арбитраж',
    SYNTHESIS: 'Синтез отчёта',
    EXPERIMENT: 'Эксперимент'
  };

  const STATUS_RU = {
    PASS: 'ПОДТВЕРЖДЕНО',
    SUPPORTED: 'ПОДТВЕРЖДЕНО',
    SUCCESS: 'УСПЕХ',
    DONE: 'ГОТОВО',
    COMPLETED: 'ЗАВЕРШЕНО',
    VERIFIED: 'ПРОВЕРЕНО',
    OK: 'ОК',
    RUNNING: 'ВЫПОЛНЯЕТСЯ',
    PARTIAL: 'ЧАСТИЧНО',
    PENDING: 'ОЖИДАНИЕ',
    PLANNED: 'СПЛАНИРОВАНО',
    INSUFFICIENT_EVIDENCE: 'НЕДОСТАТОЧНО ДОКАЗАТЕЛЬСТВ',
    DISPUTED: 'СПОРНО',
    WARN: 'ВНИМАНИЕ',
    AWAITING_HUMAN: 'НУЖНО РЕШЕНИЕ ЧЕЛОВЕКА',
    FAIL: 'ПРОВАЛ',
    FAILED: 'ОШИБКА',
    ERROR: 'ОШИБКА',
    UNVERIFIED: 'НЕ ПРОВЕРЕНО',
    NOT_AVAILABLE: 'НЕТ ДАННЫХ',
    NOT_RUN: 'НЕ ЗАПУЩЕНО',
    Assumed: 'Допущение',
    Given: 'Дано',
    Derived: 'Выведено',
    Estimated: 'Оценка',
    Unverified: 'Не проверено'
  };

  const ORIGIN_RU = {
    derived: 'выведено системой',
    user: 'задано пользователем',
    benchmark: 'требование бенчмарка',
    policy_lock: 'зафиксировано политикой',
    assumed: 'допущение'
  };

  function tip(text) {
    return `<span class="tip" tabindex="0" data-tip="${esc(text)}" role="img" aria-label="Подсказка">?</span>`;
  }

  function tipBelow(text) {
    return `<span class="tip tip-below" tabindex="0" data-tip="${esc(text)}" role="img" aria-label="Подсказка">?</span>`;
  }

  function stageLabel(stage) {
    const key = String(stage || '');
    return STAGE_RU[key] || STAGE_RU[key.toUpperCase()] || key;
  }

  function statusLabel(status) {
    if (status == null || status === '') return '';
    const key = String(status);
    return STATUS_RU[key] || STATUS_RU[key.toUpperCase()] || key;
  }

  function route() {
    let path = location.pathname;
    while (path.length > 1 && path.endsWith('/')) path = path.slice(0, -1);
    if (path.startsWith('/runs/')) {
      const id = decodeURIComponent(path.slice('/runs/'.length).split('/')[0]);
      return renderRun(id);
    }
    if (path.startsWith('/projects/')) {
      const id = decodeURIComponent(path.slice('/projects/'.length).split('/')[0]);
      return renderProject(id);
    }
    return renderHome();
  }

  function badgeClass(status) {
    const s = String(status || '').toUpperCase();
    if (['PASS', 'SUPPORTED', 'SUCCESS', 'DONE', 'COMPLETED', 'VERIFIED', 'OK'].includes(s)) return 'ok';
    if (['RUNNING', 'PARTIAL', 'PENDING', 'PLANNED'].includes(s)) return 'run';
    if (['INSUFFICIENT_EVIDENCE', 'DISPUTED', 'WARN', 'AWAITING_HUMAN'].includes(s)) return 'warn';
    if (['FAIL', 'FAILED', 'ERROR', 'BAD'].includes(s)) return 'bad';
    return 'muted';
  }

  function badge(status, tipText) {
    if (!status) return '';
    const label = statusLabel(status);
    const tipAttr = tipText
      ? ` class="badge ${badgeClass(status)} has-tip" data-tip="${esc(tipText)}" tabindex="0"`
      : ` class="badge ${badgeClass(status)}"`;
    return `<span${tipAttr}>${esc(label)}</span>`;
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  async function api(path, opts) {
    const r = await fetch(path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText || 'Ошибка запроса');
    return data;
  }

  async function renderHome() {
    let projects = [];
    let recent = [];
    try {
      const p = await api('/api/projects');
      projects = p.projects || [];
      const rr = await api('/api/runs?limit=8');
      recent = rr.runs || [];
    } catch (e) {
      app.innerHTML = `<div class="error-box">Не удалось загрузить данные: ${esc(e.message)}</div>`;
      return;
    }
    app.innerHTML = `
      <h1>Что вы хотите исследовать или решить?</h1>
      <p class="lead">
        Опишите вопрос, теорию, гипотезу или инженерную задачу одним текстом.
        Лаборатория сама выберет конвейер — вам не нужно выбирать модель, агента или песочницу.
        ${tip('Это не диалог с чат-ботом. Один запрос создаёт исследование (run) с артефактами, проверками и отчётом.')}
      </p>
      <div class="panel">
        <div class="field-label">
          Формулировка задачи
          ${tip('Пишите обычным языком: «Сколько мощности нужно…», «Можно ли произвести…», «Проверь гипотезу…». Система сама решит, нужен ли расчёт, исследование или симуляция.')}
        </div>
        <label class="sr-only" for="problem">Задача</label>
        <textarea id="problem" placeholder="Например: можно ли промышленно производить искусственный паучий шёлк? Сколько энергии нужно, чтобы нагреть 20 литров воды?">${DEMO ? esc(DEMO_PROBLEM) : ''}</textarea>
        <details class="optional">
          <summary class="has-tip" data-tip="Необязательные поля. Для базового запуска достаточно только текста задачи выше.">
            Дополнительные параметры (необязательно)
          </summary>
          <div class="grid">
            <label>
              <span class="field-label">Имя проекта ${tip('Как назвать папку исследования. Если пусто — имя сгенерируется автоматически.')}</span>
              <input id="pname" placeholder="автоматически"/>
            </label>
            <label>
              <span class="field-label">Домен ${tip('Подсказка для маршрутизации: материалы, теплотехника, механика и т.п. Не обязательна.')}</span>
              <input id="domain" placeholder="например, материаловедение"/>
            </label>
            <label>
              <span class="field-label">Глубина ${tip('Кратко или подробно. Не заменяет выбор конвейера — это ориентир для системы.')}</span>
              <input id="depth" placeholder="быстро / тщательно"/>
            </label>
            <label>
              <span class="field-label">Ограничения ${tip('Единицы, бюджет, допущения, что нельзя менять. Попадёт в контекст задачи.')}</span>
              <input id="constraints" placeholder="единицы, лимиты, бюджет"/>
            </label>
          </div>
        </details>
        <div class="row">
          <button type="button" class="secondary has-tip tip-below" id="btn-new-project"
            data-tip="Создаёт только проект (папку) без запуска. Полезно, если хотите сначала сохранить формулировку.">
            + Только проект
          </button>
          <button type="button" class="has-tip tip-below" id="btn-run"
            data-tip="Создаёт проект (если нужно) и сразу запускает исследование. Откроется страница хода работы со стадиями в реальном времени.">
            Запустить исследование
          </button>
        </div>
        <p class="hint">
          Один запрос → один запуск лаборатории. Прогресс берётся из реальных событий backend, а не из анимации.
          ${tipBelow('После запуска URL вида /runs/… можно обновить или открыть позже — состояние восстановится с диска.')}
        </p>
      </div>
      <section class="section">
        <h2>Недавние исследования ${tip('Последние запуски по всем проектам. Нажмите, чтобы открыть отчёт или ход работы.')}</h2>
        <ul class="list" id="recent">${recent.length ? recent.map(r => `
          <li>
            <div>
              <a href="/runs/${esc(r.run_id)}" class="has-tip" data-tip="Открыть запуск ${esc(r.run_id)}. Если исследование ещё идёт — увидите живой прогресс; если завершено — структурированный отчёт.">${esc(r.problem_preview || r.run_id)}</a>
              <div class="hint">${esc(r.project_id)} · ${esc(r.started_at || '')}</div>
            </div>
            ${badge(r.engineering_status || r.status, 'Инженерный итог (не путать с «технически код выполнился»): подтверждено ли требуемое доказательство.')}
          </li>`).join('') : '<li><span class="hint">Пока нет запусков — опишите задачу выше и нажмите «Запустить исследование»</span></li>'}
        </ul>
      </section>
      <section class="section" id="projects">
        <h2>Проекты ${tip('Проект — контейнер для нескольких запусков по одной теме. Артефакты хранятся в projects/… на диске.')}</h2>
        <ul class="list">${projects.length ? projects.map(p => `
          <li>
            <div>
              <a href="/projects/${esc(p.project_id)}" class="has-tip" data-tip="Открыть проект: история запусков и возможность стартовать новый run.">${esc(p.title || p.project_id)}</a>
              <div class="hint">${esc(p.run_count)} исслед.${p.domain ? ' · ' + esc(p.domain) : ''}</div>
            </div>
            ${badge(p.last_engineering_outcome || '—', 'Итог последнего запуска в этом проекте.')}
          </li>`).join('') : '<li><span class="hint">Проектов пока нет</span></li>'}
        </ul>
      </section>
    `;
    document.getElementById('btn-run').onclick = () => startInvestigation(false);
    document.getElementById('btn-new-project').onclick = () => startInvestigation(true);
  }

  async function startInvestigation(projectOnly) {
    const problem = document.getElementById('problem').value.trim();
    if (!problem) { alert('Сначала опишите задачу.'); return; }
    const body = {
      problem,
      title: document.getElementById('pname').value.trim() || undefined,
      name: document.getElementById('pname').value.trim() || undefined,
      domain: document.getElementById('domain').value.trim() || undefined,
      depth: document.getElementById('depth').value.trim() || undefined,
      constraints: document.getElementById('constraints').value.trim() || undefined,
      create_project: true,
      wait: false,
      action: 'run'
    };
    try {
      if (projectOnly) {
        const created = await api('/api/projects', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body)
        });
        location.href = '/projects/' + created.project_id;
        return;
      }
      const created = await api('/api/runs', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      location.href = '/runs/' + created.run_id;
    } catch (e) {
      alert(e.message);
    }
  }

  async function renderProject(projectId) {
    try {
      const data = await api('/api/projects/' + encodeURIComponent(projectId));
      app.innerHTML = `
        <p class="hint"><a href="/" class="has-tip" data-tip="Вернуться на главный экран лаборатории.">← Главная</a></p>
        <h1>${esc(data.title || projectId)}</h1>
        <p class="lead">${esc(data.problem_preview || '')}</p>
        <div class="panel">
          <h2>Новый запуск ${tip('Каждый запуск — отдельное исследование. История предыдущих не перезаписывается.')}</h2>
          <div class="field-label">Формулировка ${tip('Можно изменить условие и запустить снова — это будет новый run, а не правка старого отчёта.')}</div>
          <textarea id="problem" placeholder="Уточнение или новая постановка…">${esc(data.problem_preview || '')}</textarea>
          <div class="row">
            <button id="btn-run" class="has-tip tip-below" data-tip="Запускает исследование в этом проекте и открывает страницу хода работы.">
              Запустить исследование
            </button>
          </div>
        </div>
        <section class="section">
          <h2>История запусков ${tip('Список run_id с датой, профилем workflow и инженерным статусом.')}</h2>
          <ul class="list">${(data.runs || []).map(r => `
            <li>
              <div>
                <a href="/runs/${esc(r.run_id)}" class="has-tip" data-tip="Открыть этот запуск.">${esc(r.run_id)}</a>
                <div class="hint">${esc(r.started_at || '')} · ${esc(r.workflow_profile || '')}</div>
              </div>
              ${badge(r.engineering_status || r.status)}
            </li>`).join('') || '<li><span class="hint">Запусков пока нет</span></li>'}
          </ul>
        </section>
      `;
      document.getElementById('btn-run').onclick = async () => {
        const problem = document.getElementById('problem').value.trim();
        if (!problem) return alert('Нужна формулировка задачи');
        const created = await api('/api/projects/' + encodeURIComponent(projectId) + '/runs', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ problem, wait: false })
        });
        location.href = '/runs/' + created.run_id;
      };
    } catch (e) {
      app.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    }
  }

  let es = null;
  let pollTimer = null;

  async function renderRun(runId) {
    if (es) { es.close(); es = null; }
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    app.innerHTML = `<p class="hint">Загрузка запуска…</p>`;
    await refreshRun(runId);
  }

  async function refreshRun(runId) {
    try {
      const status = await api('/api/runs/' + encodeURIComponent(runId) + '/status');
      const life = String(status.lifecycle_status || '');
      const done = ['COMPLETED', 'FAILED', 'ERROR', 'PLANNED'].includes(life)
        && !['RUNNING'].includes(life);
      if (done && life !== 'PLANNED' && status.final_state) {
        const result = await api('/api/runs/' + encodeURIComponent(runId) + '/result');
        renderResultPage(runId, status, result);
        return;
      }
      if (life === 'PLANNED') {
        renderLivePage(runId, status);
        return;
      }
      renderLivePage(runId, status);
      connectStream(runId);
      if (!pollTimer) {
        pollTimer = setInterval(async () => {
          try {
            const st = await api('/api/runs/' + encodeURIComponent(runId) + '/status');
            if (['COMPLETED', 'FAILED', 'ERROR'].includes(st.lifecycle_status)) {
              clearInterval(pollTimer); pollTimer = null;
              if (es) { es.close(); es = null; }
              const res = await api('/api/runs/' + encodeURIComponent(runId) + '/result');
              renderResultPage(runId, st, res);
            } else {
              updateLive(st);
            }
          } catch (_) { /* переподключение на следующем тике */ }
        }, 1500);
      }
    } catch (e) {
      app.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    }
  }

  function pipelineHtml(pipeline) {
    return `<ul class="pipeline">${(pipeline || []).map(n => {
      const cls = n.status === 'DONE' ? 'done' : n.status === 'RUNNING' ? 'running' : n.status === 'FAILED' ? 'failed' : '';
      const tipText = 'Стадия из реального TaskGraph. Статус приходит с backend, а не из таймера в браузере.';
      return `<li class="${cls} has-tip" data-tip="${esc(tipText)}" tabindex="0">
        <strong>${esc(stageLabel(n.stage))}</strong>
        <span class="hint">${esc(statusLabel(n.status))}</span>
      </li>`;
    }).join('')}</ul>`;
  }

  function renderLivePage(runId, status) {
    app.innerHTML = `
      <p class="hint">
        <a href="/projects/${esc(status.project_id)}" class="has-tip" data-tip="Вернуться к проекту и истории запусков.">← ${esc(status.project_id)}</a>
      </p>
      <h1>Исследование ${tip('Пока лаборатория работает, слева — стадии конвейера, справа — живой журнал событий с сервера (SSE).')}</h1>
      <p class="lead">
        <span class="trust-tag has-tip" tabindex="0" data-tip="Метка недоверенного пользовательского ввода. Внешние источники и выводы LLM тоже помечаются отдельно от проверенных фактов.">ВВОД ПОЛЬЗОВАТЕЛЯ</span>
        Запуск <code class="has-tip" tabindex="0" data-tip="Идентификатор запуска. Сохраните URL — после обновления страницы состояние восстановится.">${esc(runId)}</code>
        · ${badge(status.lifecycle_status, 'Технический статус процесса: выполняется, завершён, ошибка…')}
        ${badge(status.engineering_status, 'Инженерный итог: достаточно ли доказательств для требуемого результата. Может отличаться от «код успешно выполнился».')}
      </p>
      <div class="layout-2">
        <div class="panel">
          <h3>Конвейер ${tip('Состав стадий зависит от задачи: простой расчёт короче, исследование с симуляцией длиннее. Список строится из TaskGraph.')}</h3>
          <div id="pipeline">${pipelineHtml(status.pipeline)}</div>
        </div>
        <div class="panel">
          <h3>Журнал событий ${tip('Поток Server-Sent Events с backend. Если связь оборвётся, статус продолжит обновляться опросом — новый run не создаётся.')}</h3>
          <div class="activity" id="activity" aria-label="События запуска"></div>
          <div class="stats">
            <div class="stat has-tip" tabindex="0" data-tip="Сколько величин/выводов система считает обязательными для ответа на вашу задачу.">
              <span>Требуемые выходы</span><b id="req">${esc(status.required_outputs ?? '—')}</b>
            </div>
            <div class="stat has-tip" tabindex="0" data-tip="Сколько требуемых выходов уже подтверждены детерминированными проверками.">
              <span>Проверено</span><b id="ver">${esc(status.verified_outputs ?? '—')}</b>
            </div>
            <div class="stat has-tip" tabindex="0" data-tip="Полнота доказательной базы: SUPPORTED / PARTIAL / INSUFFICIENT_EVIDENCE и т.д.">
              <span>Доказательства</span><b id="ev">${esc(statusLabel(status.evidence_status) || '—')}</b>
            </div>
          </div>
          <p class="hint" id="live-hint">Ожидание событий лаборатории…</p>
        </div>
      </div>
    `;
  }

  function updateLive(status) {
    const pipe = document.getElementById('pipeline');
    if (pipe) pipe.innerHTML = pipelineHtml(status.pipeline);
    const req = document.getElementById('req');
    if (req) req.textContent = status.required_outputs ?? '—';
    const ver = document.getElementById('ver');
    if (ver) ver.textContent = status.verified_outputs ?? '—';
    const ev = document.getElementById('ev');
    if (ev) ev.textContent = statusLabel(status.evidence_status) || '—';
  }

  const EVENT_RU = {
    'run.created': 'Запуск создан',
    'pipeline.ready': 'Конвейер готов',
    'stage.started': 'Стадия началась',
    'stage.completed': 'Стадия завершена',
    'stage.failed': 'Стадия с ошибкой',
    'task.started': 'Задача началась',
    'task.completed': 'Задача завершена',
    'task.failed': 'Задача с ошибкой',
    'run.completed': 'Исследование завершено',
    'run.failed': 'Исследование завершилось с ошибкой',
    'agent_start': 'Агент стартовал'
  };

  function connectStream(runId) {
    if (es) es.close();
    const box = document.getElementById('activity');
    es = new EventSource('/api/runs/' + encodeURIComponent(runId) + '/stream');
    const onAny = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (!box) return;
        const line = document.createElement('div');
        const msg = data.message || ev.type;
        const ru = EVENT_RU[msg] || msg;
        const stage = data.data && data.data.stage ? ` [${stageLabel(data.data.stage)}]` : '';
        line.textContent = `${ru}${stage}`;
        box.appendChild(line);
        box.scrollTop = box.scrollHeight;
        const hint = document.getElementById('live-hint');
        if (hint) hint.textContent = ru;
      } catch (_) {}
    };
    ['run.created','pipeline.ready','stage.started','stage.completed','stage.failed',
     'task.started','task.completed','task.failed','run.completed','run.failed','message'
    ].forEach(name => es.addEventListener(name, onAny));
    es.onerror = () => {
      const hint = document.getElementById('live-hint');
      if (hint) hint.textContent = 'Поток прерван — продолжаем через опрос статуса…';
    };
  }

  function renderResultPage(runId, status, result) {
    const eng = result.engineering_outcome || status.engineering_status;
    const insufficient = ['INSUFFICIENT_EVIDENCE', 'FAIL', 'DISPUTED'].includes(String(eng || '').toUpperCase())
      || String(result.report_gate || '').includes('INSUFFICIENT');
    const numbers = result.key_numbers || [];
    const primary = numbers[0];
    const missing = (result.evidence && result.evidence.missing_required_outputs) || [];

    const hero = insufficient ? `
      <div class="hero-result error-box" style="background:transparent">
        <h3>Исследование завершено ${tip('Это не «падение» системы: лаборатория честно сообщает, что доказательств недостаточно.')}</h3>
        <div class="value" style="font-size:1.6rem">${badge(eng || 'INSUFFICIENT_EVIDENCE')}</div>
        <p>${esc(result.executive_summary || 'Лаборатория не смогла подтвердить запрошенный результат.')}</p>
        ${missing.length ? `<p><strong>Недостающие доказательства</strong> ${tip('Что требовалось установить, но не было подтверждено верифицированным расчётом/источником.')}</p><ul>${missing.map(m => `<li>${esc(typeof m === 'string' ? m : JSON.stringify(m))}</li>`).join('')}</ul>` : ''}
      </div>` : `
      <div class="hero-result">
        <h3>Результат ${tip('Крупный итог показывается как авторитетный только если есть проверенные claims. Иначе статус будет «не проверено» или «недостаточно доказательств».')}</h3>
        ${primary ? `<div class="hint">${esc(primary.label || 'Основной выход')}</div>
          <div class="value">${esc(primary.value)}${primary.unit ? ' ' + esc(primary.unit) : ''}</div>` : ''}
        <div>${badge(eng || result.report_gate || 'COMPLETED')}</div>
        <p class="lead" style="margin:1rem auto 0;max-width:36rem">${esc(result.executive_summary || '')}</p>
      </div>`;

    app.innerHTML = `
      <p class="hint">
        <a href="/projects/${esc(result.project_id || status.project_id)}" class="has-tip" data-tip="Вернуться к проекту.">← Проект</a> ·
        <a href="/api/runs/${esc(runId)}/export?format=markdown" class="has-tip" data-tip="Скачать человекочитаемый отчёт в Markdown из сохранённых артефактов.">Экспорт Markdown</a> ·
        <a href="/api/runs/${esc(runId)}/export?format=json" class="has-tip" data-tip="Структурированный JSON отчёта для дальнейшей обработки.">Экспорт JSON</a>
      </p>
      ${hero}
      ${numbers.length > 1 ? `<section class="section"><h2>Ключевые величины ${tip('Числа из verified_results. Непроверенные значения не выдаются за финальный ответ.')}</h2><div class="cards">${numbers.map(n => `
        <div class="card"><div class="hint">${esc(n.label)}</div><div class="v">${esc(n.value)}${n.unit ? ' ' + esc(n.unit) : ''}</div>
        ${badge(n.verified ? 'VERIFIED' : 'UNVERIFIED')}</div>`).join('')}</div></section>` : ''}
      <section class="section panel">
        <h2>Почему такой результат ${tip('Краткая цепочка рассуждения/расчёта по артефактам. Не скрытый chain-of-thought модели, а структурированные шаги.')}</h2>
        <div class="chain">${(result.why || []).map(s => `<div>→ ${esc(s)}</div>`).join('') || '<div class="hint">Цепочка не записана</div>'}</div>
      </section>
      <section class="section panel">
        <h2>Доказательства и уверенность ${tip('Нет «94% уверенности» без методики. Показываем статусы доказательств, детерминированных проверок и независимого обзора.')}</h2>
        <div class="stats">
          <div class="stat has-tip" tabindex="0" data-tip="Итог по полноте evidence относительно required outputs.">
            <span>Доказательства</span><b>${esc(statusLabel((result.confidence||{}).evidence_status) || '—')}</b>
          </div>
          <div class="stat has-tip" tabindex="0" data-tip="Результат Pint/AST и детерминированных проверок — авторитетен для чисел.">
            <span>Детерминированно</span><b>${esc(statusLabel((result.confidence||{}).deterministic_verification) || '—')}</b>
          </div>
          <div class="stat has-tip" tabindex="0" data-tip="Verification и Red Team работают независимо (blind bundle), затем арбитраж.">
            <span>Независимый обзор</span><b>${esc((result.confidence||{}).independent_review || '—')}</b>
          </div>
          <div class="stat has-tip" tabindex="0" data-tip="Статус инженерной симуляции, если она была в конвейере.">
            <span>Симуляция</span><b>${esc(statusLabel((result.confidence||{}).simulation_validation) || '—')}</b>
          </div>
        </div>
        <p class="hint">Структурированный арбитраж важнее текста LLM. Повествование не может переписать engineering_outcome.</p>
      </section>
      <section class="section panel">
        <h2>Требуемые выходы ${tip('Что лаборатория решила необходимым установить. Основа контракта задачи: user / derived / policy_lock.')} <span class="trust-tag">КОНТРАКТ</span></h2>
        <ul>${(result.required_outputs || []).map(o => {
          const origin = o.origin || 'derived';
          return `<li>${esc(o.name || o)} <span class="hint">(${esc(ORIGIN_RU[origin] || origin)})</span></li>`;
        }).join('') || '<li class="hint">Не указаны</li>'}</ul>
      </section>
      <section class="section panel">
        <h2>Утверждения (claims) ${tip('Каждое важное утверждение хранится как claim с источником/расчётом. Можно проследить provenance.')}</h2>
        <div class="cards">${(result.claims || []).slice(0, 12).map(c => `
          <div class="card has-tip" tabindex="0" data-tip="Тип знания и жизненный цикл claim. CALCULATION/FACT отличаются от ASSUMPTION.">
            <div class="hint"><span class="trust-tag">${esc(c.kind || 'CLAIM')}</span> ${badge(c.lifecycle || '')}</div>
            <div>${esc(c.statement)}</div>
            <div class="hint">${esc(c.claim_id || '')}${c.computation_artifact_id ? ' · расчёт ' + esc(c.computation_artifact_id) : ''}</div>
          </div>`).join('') || '<div class="hint">Утверждений нет</div>'}</div>
      </section>
      <section class="section panel">
        <h2>Допущения ${tip('Не смешиваются с фактами. Статусы: Дано / Допущение / Выведено / Оценка.')}</h2>
        <ul>${(result.assumptions || []).map(a => `<li>${badge(a.status)} ${esc(a.text)}</li>`).join('') || '<li class="hint">Не извлечены</li>'}</ul>
      </section>
      <section class="section panel">
        <h2>Ограничения ${tip('Обязательный блок: что не проверялось экспериментально, какие упрощения модели, где остаётся неопределённость.')}</h2>
        <ul>${(result.limitations || []).map(l => `<li>${esc(l)}</li>`).join('') || '<li class="hint">Ограничения не указаны</li>'}</ul>
      </section>
      <section class="section panel">
        <h2>Происхождение (provenance) ${tip('Цепочка Problem → … → Decision. Сырые артефакты доступны по ссылкам ниже.')}</h2>
        <div class="chain">${((result.provenance||{}).chain||[]).map(s => `<div>${esc(s)}</div>`).join('')}</div>
        <div class="row" style="margin-top:1rem">
          <a class="btn secondary has-tip tip-below" href="/api/runs/${esc(runId)}"
            data-tip="Полный JSON запуска: манифест, статус, результат. Для отладки и воспроизводимости.">
            Открыть JSON запуска
          </a>
        </div>
        <details class="raw">
          <summary class="has-tip" data-tip="Сырые synthesis / adjudication / verification. Progressive disclosure: сначала смысл, потом детали.">
            Сырые артефакты синтеза и арбитража
          </summary>
          <pre class="raw">${esc(JSON.stringify({adjudication: result.adjudication, synthesis: result.synthesis, verification: result.verification}, null, 2))}</pre>
        </details>
      </section>
      <section class="section">
        <h3>Конвейер (завершён) ${tip('Итоговый состав стадий этого запуска.')}</h3>
        ${pipelineHtml(status.pipeline)}
      </section>
    `;
  }

  window.addEventListener('popstate', route);
  route();
})();
</script>
</body>
</html>
"""
