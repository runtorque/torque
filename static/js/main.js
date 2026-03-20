/* Keyboard bindings and boot */

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeModals(); closeBroadcast(); closeMenus(); }
});
document.addEventListener('click', () => closeMenus());
document.querySelectorAll('.overlay').forEach(o => {
  o.addEventListener('click', (e) => { if (e.target === o) closeModals(); });
});

['add-name-input', 'add-cmd-input', 'add-dir-input'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitAdd();
    if (e.key === 'Escape') closeModals();
  });
});

connect();
