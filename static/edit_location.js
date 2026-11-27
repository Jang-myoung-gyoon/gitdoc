function versionLocation(node) {
  return { version_id: Number(node.dataset.versionId), version_label: node.dataset.versionLabel,
    branch: node.dataset.versionBranch };
}

function editLocationControl(root) {
  const button = root.querySelector('[data-edit-location]');
  const status = root.querySelector('[data-location-status]');
  let locationInfo = null;

  // Keep the DOM selection while operating the copy button with a mouse.
  button.addEventListener('mousedown', event => event.preventDefault());
  button.addEventListener('click', async () => {
    if (!locationInfo) return;
    const contents = JSON.stringify(locationInfo, null, 2);
    try {
      await navigator.clipboard.writeText(contents);
      button.textContent = '위치 복사됨';
      status.textContent = '에이전트에게 전달할 수정 위치를 복사했습니다.';
    } catch {
      const dialog = document.createElement('dialog');
      dialog.className = 'document-dialog';
      const body = document.createElement('div');
      body.className = 'document-dialog-body stack';
      const textarea = document.createElement('textarea');
      textarea.rows = 12;
      textarea.readOnly = true;
      textarea.setAttribute('aria-label', '에이전트에게 전달할 수정 위치');
      textarea.value = contents;
      const close = document.createElement('button');
      close.className = 'secondary';
      close.textContent = '닫기';
      close.addEventListener('click', () => dialog.close());
      body.append(textarea, close);
      dialog.append(body);
      dialog.addEventListener('close', () => dialog.remove());
      root.append(dialog);
      dialog.showModal();
      textarea.select();
    }
  });
  return (details, params = {}) => {
    button.disabled = !details;
    button.textContent = '수정 위치 복사';
    status.textContent = '';
    const url = new URL(root.dataset.locationUrl, window.location.href);
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));
    const viewedBranch = url.searchParams.get('branch');
    locationInfo = details && { workspace_id: root.dataset.workspaceId,
      document_id: Number(root.dataset.documentId), ...details,
      ...(viewedBranch && viewedBranch !== details.branch ? { view_branch: viewedBranch } : {}),
      url: url.href };
  };
}
