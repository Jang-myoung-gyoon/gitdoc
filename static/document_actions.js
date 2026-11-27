const actionPositions = new WeakMap();

function openDocumentDialog(dialog, opener = null) {
  if (dialog.open) return;
  const position = { top: window.scrollY, left: window.scrollX, behavior: 'instant' };
  actionPositions.set(dialog, { position, opener });
  dialog.showModal();
  window.scrollTo(position);
}

for (const button of document.querySelectorAll('[data-open-document-dialog]')) {
  const dialog = document.getElementById(button.getAttribute('aria-controls'));
  button.addEventListener('click', () => openDocumentDialog(dialog, button));
}

function openActionFromAnchor() {
  const target = document.getElementById(location.hash.slice(1));
  const dialog = target?.closest('.document-dialog');
  if (dialog) openDocumentDialog(dialog);
}

for (const dialog of document.querySelectorAll('.document-dialog')) {
  dialog.addEventListener('close', () => {
    const saved = actionPositions.get(dialog);
    if (saved) {
      saved.opener?.focus({ preventScroll: true });
      window.scrollTo(saved.position);
      actionPositions.delete(dialog);
    }
    if (dialog.contains(document.getElementById(location.hash.slice(1)))) {
      history.replaceState(history.state, '', location.pathname + location.search);
    }
  });
}

addEventListener('hashchange', openActionFromAnchor);
openActionFromAnchor();
