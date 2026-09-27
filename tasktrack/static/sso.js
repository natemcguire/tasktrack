(async () => {
  const fragment = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, '', location.pathname);
  try {
    const response = await fetch('/auth/sso/callback', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token: fragment.get('token'), state: fragment.get('state')})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || 'Sign-in failed.');
    location.replace(result.next || '/');
  } catch (error) {
    document.getElementById('retry').hidden = false;
    document.getElementById('status').textContent = error.message || 'Sign-in could not be completed. Please try again.';
  }
})();
