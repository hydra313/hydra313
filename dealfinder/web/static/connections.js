document.querySelectorAll('[data-connect-site], [data-stop-site]').forEach(button => {
  button.addEventListener('click', () => {
    const installed = document.querySelector('[data-helper-ready="true"]');
    if (!installed) document.getElementById('helper-connection').textContent =
      'Install or update the browser helper to version 1.2, then reload this page in that Chrome or Edge browser. Open the setup steps above.';
  });
});
