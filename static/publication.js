document.getElementById('publication-form')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget, button = form.querySelector('button');
  const error = document.getElementById('publication-error');
  error.textContent = ''; btnBusy(button, true);
  try {
    const response = await fetch(form.dataset.url, {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirmar: document.getElementById('publication-confirm').checked,
        versao_esperada: Number(form.dataset.version), rascunho_hash: form.dataset.hash,
        motivo: document.getElementById('publication-reason').value})});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.erro || 'Não foi possível publicar.');
    window.location.reload();
  } catch (failure) { error.textContent = failure.message || 'Não foi possível publicar. Tentar novamente.'; }
  finally { btnBusy(button, false); }
});
