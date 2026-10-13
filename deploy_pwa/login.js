const form = document.querySelector('#login-form');
const submit = document.querySelector('#login-submit');
const error = document.querySelector('#login-error');
const usernameInput = document.querySelector('#username');
const passwordInput = document.querySelector('#password');
const rememberInput = document.querySelector('#remember');

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  error.hidden = true;
  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  if (!username || !password) {
    error.textContent = '請輸入帳號與密碼。';
    error.hidden = false;
    return;
  }
  submit.disabled = true;
  submit.textContent = '登入中…';
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      cache: 'no-store',
      body: JSON.stringify({username, password, remember: rememberInput.checked})
    });
    if (!response.ok) {
      throw new Error(response.status === 401 ? '帳號或密碼不正確。' : '目前無法登入，請稍後再試。');
    }
    passwordInput.value = '';
    window.location.replace('/');
  } catch (cause) {
    error.textContent = cause.message || '目前無法登入，請稍後再試。';
    error.hidden = false;
    passwordInput.value = '';
    submit.disabled = false;
    submit.innerHTML = '登入語靈 <span aria-hidden="true">↗</span>';
  }
});
