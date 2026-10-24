document.getElementById('ebay-page')?.addEventListener('paste', event => {
  const html = event.clipboardData?.getData('text/html');
  if (html) { event.preventDefault(); event.target.value = html; }
});
document.getElementById('connect-ebay-helper')?.addEventListener('click', event => {
  if (event.currentTarget.dataset.helperReady !== 'true') {
    document.getElementById('helper-connection').textContent = 'Install the browser helper using the steps above, then reload this page in that browser.';
  }
});
document.getElementById('stop-ebay-helper')?.addEventListener('click', () => {
  if (document.getElementById('connect-ebay-helper')?.dataset.helperReady !== 'true') {
    document.getElementById('helper-connection').textContent = 'No browser helper is connected in this browser.';
  }
});
