/* Week links retain the view, remain ordinary deep links and use the shared
   confirmation dialog for any local input still waiting to be saved. */
'use strict';
(function setupScheduleWeeks() {
  const navigation = document.querySelector('.schedule-week-nav');
  if (!navigation || !navigation.dataset.semana) return;
  const arrows = Array.from(navigation.querySelectorAll('a[data-week-direction]'));
  const initial = new URLSearchParams(location.search);
  const search = document.getElementById('chip-search');
  const supervisor = typeof setFilter === 'function';

  function syncWeekLinks() {
    const current = new URL(location.href);
    current.searchParams.set('semana', navigation.dataset.semana);
    const person = typeof activeSearch === 'string' ? activeSearch : '';
    const values = {q: person ? '' : search?.value.trim(), pessoa: person,
      tipo: supervisor && activeFilter !== 'todos' ? activeFilter : ''};
    const store = document.querySelector('.loja-tab[aria-selected="true"]');
    values.loja = store?.dataset.lojaTab === 'all' ? '' : store?.dataset.lojaTab;
    values.meus = document.body.classList.contains('my-shifts-mode') ? '1' : '';
    const teamSearch = document.getElementById('schedule-team-search');
    if (teamSearch) {
      values.equipa_q = teamSearch.value.trim();
      values.equipa_tipo = typeof scheduleTeamFilter === 'string' && scheduleTeamFilter !== 'todos' ? scheduleTeamFilter : '';
      const day = document.getElementById('schedule-team-day');
      values.equipa_dia = day?.value ? String(day.selectedIndex - 1) : '';
    }
    if (typeof RANK_FILTER !== 'undefined') values.rank = Array.from(RANK_FILTER).join(',');
    Object.entries(values).forEach(([key, value]) => {
      if (value) current.searchParams.set(key, value);
      else current.searchParams.delete(key);
    });
    history.replaceState(history.state, '', current.pathname + current.search + current.hash);
    arrows.forEach(arrow => {
      const target = new URL(arrow.href, location.href);
      const week = target.searchParams.get('semana');
      target.search = current.search;
      target.searchParams.set('semana', week);
      target.hash = current.hash;
      arrow.href = target.pathname + target.search + target.hash;
    });
  }

  // Restore explicit link filters first; existing session preferences provide
  // defaults only when the link has no corresponding setting.
  const type = initial.get('tipo');
  if (supervisor && ['todos', 'contrato', 'turnos'].includes(type)) setFilter(type);
  const person = initial.get('pessoa');
  if (person && typeof FUNC_INFO !== 'undefined' && FUNC_INFO[person]) pickEmployee(person);
  else if (search && initial.has('q')) {search.value = initial.get('q'); onSearchInput();}
  const store = initial.get('loja');
  if (store && typeof window.setLojaTab === 'function') window.setLojaTab(store);
  if (initial.has('meus') && typeof window.toggleMyShifts === 'function') {
    const wanted = initial.get('meus') === '1';
    if (document.body.classList.contains('my-shifts-mode') !== wanted) window.toggleMyShifts();
  }
  const teamSearch = document.getElementById('schedule-team-search');
  if (teamSearch) {
    teamSearch.value = initial.get('equipa_q') || '';
    const type = initial.get('equipa_tipo');
    if (['todos', 'contrato', 'turnos'].includes(type)) {
      document.querySelector('[data-team-filter="' + type + '"]')?.click();
    }
    const day = document.getElementById('schedule-team-day');
    const index = Number(initial.get('equipa_dia'));
    if (initial.has('equipa_dia') && Number.isInteger(index) && index >= 0 && index < 7 && day?.options[index + 1]) day.selectedIndex = index + 1;
    if (typeof refreshScheduleTeam === 'function') refreshScheduleTeam();
  }
  if (initial.has('rank') && typeof toggleRankFilter === 'function') {
    const allowed = ['trainee', 'staff', 'shift_supervisor', 'store_manager'];
    initial.get('rank').split(',').filter(rank => allowed.includes(rank)).forEach(toggleRankFilter);
  }
  syncWeekLinks();

  const controls = '#chip-search, .filter-tag, .loja-tab, #btn-my-shifts, #schedule-team-search, #schedule-team-day, [data-team-filter], .rank-pill-btn';
  ['input', 'change', 'click'].forEach(name => document.addEventListener(name, event => {
    if (event.target.closest(controls)) syncWeekLinks();
  }));
  window.addEventListener('hashchange', syncWeekLinks);
  navigation.addEventListener('click', async event => {
    const arrow = event.target.closest('a[data-week-direction]');
    if (!arrow) return;
    syncWeekLinks();
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const saving = (typeof _assignInFlight !== 'undefined' && _assignInFlight) ||
      (typeof quickAddInProgress !== 'undefined' && quickAddInProgress) ||
      (typeof movesInFlight !== 'undefined' && movesInFlight.size > 0);
    if (saving) {
      event.preventDefault();
      toast('Aguardar a conclusão da gravação antes de mudar de semana.', true);
      return;
    }
    const unsavedTargets = Array.from(document.querySelectorAll('.targets-grid input'))
      .some(input => input.value !== input.defaultValue);
    const quickAdd = document.getElementById('qa-pop');
    const unsavedShift = quickAdd?.style.display === 'block' &&
      (document.getElementById('qa-func')?.value || document.getElementById('qa-nota')?.value);
    if (unsavedTargets || unsavedShift) {
      event.preventDefault();
      if (await confirmDialog('Existem alterações por guardar. Mudar de semana?', {confirmLabel:'Mudar de semana'})) location.assign(arrow.href);
    }
  });
  navigation.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const direction = ['ArrowLeft', 'Home'].includes(event.key) ? 'previous' : 'next';
    const arrow = arrows.find(control => control.dataset.weekDirection === direction);
    if (arrow) {event.preventDefault(); arrow.focus();}
  });
})();
