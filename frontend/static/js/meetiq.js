(() => {
  const mobile = window.matchMedia('(max-width: 800px)');
  const close = () => { if (document.querySelector('.admin-sidebar')) document.body.classList.add('admin-sidebar-closed'); if (document.querySelector('.sidebar')) document.body.classList.add('sidebar-closed'); };
  if (mobile.matches) close();
  mobile.addEventListener?.('change', () => { if (mobile.matches) close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  document.addEventListener('click', e => { if (mobile.matches && e.target.closest('nav') && e.target.closest('a')) close(); });
  let dirty = false;
  document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(form => {
    form.addEventListener('input', () => { dirty = true; });
    form.addEventListener('change', () => { dirty = true; });
    form.addEventListener('submit', () => { dirty = false; });
  });
  window.addEventListener('beforeunload', event => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });
})();
