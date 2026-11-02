// Presentation uses the same live figures and links as the overview.
(function () {
  const start = document.getElementById('mg-present-start');
  if (!start) return;
  const toolbar = document.getElementById('mg-present-toolbar');
  const pager = document.getElementById('mg-present-pager');
  const summary = document.getElementById('mg-week-summary');
  const metrics = document.getElementById('mg-week-metrics');
  const message = document.getElementById('mg-present-message');
  const layout = document.getElementById('mg-layout');
  const coverage = document.getElementById('mg-coverage');
  const priorities = document.getElementById('mg-priorities');
  const hours = document.getElementById('mg-hours');
  const previous = document.getElementById('mg-present-prev');
  const next = document.getElementById('mg-present-next');
  const close = document.getElementById('mg-present-close');
  const position = document.getElementById('mg-present-position');
  const steps = Array.from(document.querySelectorAll('[data-presentation-step]'));
  const labels = ['Semana', 'Cobertura', 'Decisões'];
  let presenting = false, step = 0;

  function updateAddress() {
    const url = new URL(location.href);
    if (presenting) {
      url.searchParams.set('apresentacao', '1');
      url.searchParams.set('capitulo', String(step));
    } else {
      url.searchParams.delete('apresentacao');
      url.searchParams.delete('capitulo');
    }
    history.replaceState(null, '', url);
  }

  function showStep(index) {
    step = Math.max(0, Math.min(2, Math.trunc(index)));
    document.body.dataset.presentationStep = String(step);
    summary.hidden = metrics.hidden = step !== 0;
    message.hidden = step !== 0;
    layout.hidden = step === 0;
    coverage.hidden = step !== 1;
    priorities.hidden = hours.hidden = step !== 2;
    previous.disabled = step === 0;
    next.disabled = step === 2;
    position.textContent = `${step + 1} de 3 · ${labels[step]}`;
    steps.forEach(button => button.setAttribute('aria-pressed',
      String(Number(button.dataset.presentationStep) === step)));
    updateAddress();
    window.scrollTo({top: 0, behavior: 'instant'});
  }

  function enterPresentation(index = 0, focus = true) {
    presenting = true;
    document.body.classList.add('mg-presenting');
    toolbar.hidden = pager.hidden = false;
    showStep(index);
    if (focus) close.focus();
  }

  function exitPresentation() {
    presenting = false;
    document.body.classList.remove('mg-presenting');
    delete document.body.dataset.presentationStep;
    toolbar.hidden = pager.hidden = message.hidden = true;
    [summary, metrics, layout, coverage, priorities, hours].forEach(el => { el.hidden = false; });
    updateAddress();
    start.focus();
  }

  start.addEventListener('click', () => enterPresentation());
  close.addEventListener('click', exitPresentation);
  previous.addEventListener('click', () => showStep(step - 1));
  next.addEventListener('click', () => showStep(step + 1));
  steps.forEach(button => button.addEventListener('click',
    () => showStep(Number(button.dataset.presentationStep))));
  document.addEventListener('keydown', event => {
    if (!presenting || event.defaultPrevented) return;
    if (event.key === 'Escape') { event.preventDefault(); exitPresentation(); }
    else if (!['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target.tagName)) {
      if (event.key === 'ArrowRight') { event.preventDefault(); showStep(step + 1); }
      if (event.key === 'ArrowLeft') { event.preventDefault(); showStep(step - 1); }
    }
  });
  // Print the complete overview even when only one chapter is on screen.
  const onScreen = new Map();
  window.addEventListener('beforeprint', () => {
    if (onScreen.size) return;
    [summary, metrics, layout, coverage, priorities, hours].forEach(el => {
      onScreen.set(el, el.hidden); el.hidden = false;
    });
  });
  window.addEventListener('afterprint', () => {
    onScreen.forEach((hidden, el) => { el.hidden = hidden; }); onScreen.clear();
  });
  const initial = new URL(location.href).searchParams;
  if (initial.get('apresentacao') === '1') {
    const chapter = Number(initial.get('capitulo'));
    enterPresentation(Number.isFinite(chapter) ? chapter : 0, false);
  }
})();
