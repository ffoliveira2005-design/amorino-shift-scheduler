/* The sidebar shares the editor's exact-cell add, conflicts, hours and undo. */
'use strict';
let scheduleTeamSelected = '';
let scheduleTeamFilter = 'todos';
const scheduleTeamPanel = document.getElementById('schedule-team');
const scheduleTeamCards = Array.from(document.querySelectorAll('.schedule-team-person'));
const scheduleTeamSearch = document.getElementById('schedule-team-search');
const scheduleTeamDay = document.getElementById('schedule-team-day');

function setScheduleTeamOpen(open, focusSearch=false) {
  if(open && panelOpen)toggleAssignPanel();
  scheduleTeamPanel.hidden = !open;
  document.getElementById('btn-schedule-team').setAttribute('aria-expanded', String(open));
  if(open && focusSearch)scheduleTeamSearch.focus();
}

function clearScheduleTeamSelection() {
  scheduleTeamSelected = '';
  document.body.classList.remove('team-person-selected');
  document.getElementById('team-selection').hidden = true;
  scheduleTeamCards.forEach(card => card.setAttribute('aria-pressed', 'false'));
}

function selectScheduleTeamPerson(sigla) {
  if(_assignInFlight)return;
  closeQuickAdd(false);
  if(scheduleTeamSelected === sigla){clearScheduleTeamSelection();return;}
  scheduleTeamSelected = sigla;
  document.body.classList.add('team-person-selected');
  document.getElementById('team-selection').hidden = false;
  document.getElementById('team-selection-text').textContent =
    (FUNC_INFO[sigla]?.nome || sigla) + ' selecionado. Escolher o + do turno pretendido.';
  scheduleTeamCards.forEach(card => card.setAttribute('aria-pressed', String(card.dataset.sigla === sigla)));
  if(window.matchMedia('(max-width:1099px)').matches){
    setScheduleTeamOpen(false);
    const add = Array.from(document.querySelectorAll('.chip-add')).find(button => button.getClientRects().length);
    add?.focus();
  }
}

function assignSelectedScheduleTeam(event, cell) {
  if(!scheduleTeamSelected || !cell || event?.target.closest('.chip'))return false;
  event?.preventDefault();
  event?.stopImmediatePropagation();
  if(_assignInFlight)return true;
  const sigla = scheduleTeamSelected;
  _drag = {sigla, fromPanel:true, fromTeam:true, tipo:FUNC_INFO[sigla]?.tipo_contrato || 'contrato'};
  // onDrop captures the person and target synchronously before its first await.
  onDrop({preventDefault(){}, currentTarget:cell}, cell.dataset.loja, cell.dataset.horario, cell.dataset.dia);
  onPanelDragEnd();
  return true;
}

function refreshScheduleTeam() {
  const query = normalizeScheduleSearch(scheduleTeamSearch.value.trim());
  const day = scheduleTeamDay.value;
  let visible = 0;
  scheduleTeamCards.forEach(card => {
    const sigla = card.dataset.sigla;
    const info = FUNC_INFO[sigla] || {};
    const type = info.tipo_contrato || 'contrato';
    const matches = (scheduleTeamFilter === 'todos' || type === scheduleTeamFilter) &&
      (!query || normalizeScheduleSearch(sigla + ' ' + (info.nome || '')).includes(query));
    card.hidden = !matches;
    if(matches)visible++;
    const total = getTotalHours(sigla);
    const hours = card.querySelector('.schedule-team-hours');
    hours.textContent = scheduleHoursReady ? total.toLocaleString('pt-PT', {maximumFractionDigits:1}) + ' h planeadas' : '… h';
    hours.classList.toggle('over-hours', scheduleHoursReady && type === 'contrato' && info.horas_contrato > 0 && total > info.horas_contrato);
    hours.title = scheduleHoursReady ? 'Horas planeadas nesta semana, incluindo descargas.' : 'A carregar horas da semana.';
    const availability = card.querySelector('.schedule-team-availability');
    availability.hidden = !day;
    const disp = DISPS[sigla]?.[day];
    availability.dataset.tipo = disp?.tipo || '';
    const labels = {disponivel:'Disponível', indisponivel:'Indisponível', preferencia:'Com preferência', extra:'Disponível para descarga'};
    availability.textContent = day ? day + ' · ' + (labels[disp?.tipo] || 'Sem resposta') : '';
    availability.title = disp?.texto || '';
  });
  document.getElementById('schedule-team-count').textContent = visible + ' de ' + scheduleTeamCards.length + ' pessoas';
  document.getElementById('schedule-team-empty').hidden = visible > 0;
}

document.getElementById('btn-schedule-team').addEventListener('click', () => setScheduleTeamOpen(scheduleTeamPanel.hidden, true));
document.getElementById('schedule-team-close').addEventListener('click', () => {
  setScheduleTeamOpen(false);
  document.getElementById('btn-schedule-team').focus();
});
document.getElementById('team-selection-cancel').addEventListener('click', () => {
  clearScheduleTeamSelection();
  document.getElementById('btn-schedule-team').focus();
});
scheduleTeamSearch.addEventListener('input', refreshScheduleTeam);
scheduleTeamDay.addEventListener('change', refreshScheduleTeam);
document.querySelectorAll('[data-team-filter]').forEach(button => button.addEventListener('click', () => {
  scheduleTeamFilter = button.dataset.teamFilter;
  document.querySelectorAll('[data-team-filter]').forEach(control => control.setAttribute('aria-pressed', String(control === button)));
  refreshScheduleTeam();
}));
scheduleTeamCards.forEach(card => {
  card.addEventListener('click', () => selectScheduleTeamPerson(card.dataset.sigla));
  card.addEventListener('dragstart', event => {
    if(_assignInFlight){event.preventDefault();return;}
    closeQuickAdd(false);
    clearScheduleTeamSelection();
    onPanelDragStart(event, card.dataset.sigla, FUNC_INFO[card.dataset.sigla]?.tipo_contrato);
    _drag.fromTeam = true;
  });
  card.addEventListener('dragend', onPanelDragEnd);
});
document.addEventListener('keydown', event => {
  if(event.key !== 'Escape' || event.defaultPrevented)return;
  if(scheduleTeamSelected){clearScheduleTeamSelection();event.preventDefault();}
  else if(event.target.closest('#schedule-team')){
    setScheduleTeamOpen(false);
    document.getElementById('btn-schedule-team').focus();
    event.preventDefault();
  }
});
setScheduleTeamOpen(!window.matchMedia('(max-width:1099px)').matches);
refreshScheduleTeam();
