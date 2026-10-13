const form = document.querySelector('#login-form');
const submit = document.querySelector('#login-submit');
const error = document.querySelector('#login-error');
const usernameInput = document.querySelector('#username');
const passwordInput = document.querySelector('#password');
const passwordToggle = document.querySelector('#password-toggle');
const rememberInput = document.querySelector('#remember');

function showPassword(show) {
  passwordInput.type = show ? 'text' : 'password';
  passwordToggle.setAttribute('aria-pressed', String(show));
  passwordToggle.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
}

passwordToggle.addEventListener('click', () => {
  showPassword(passwordInput.type === 'password');
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  error.hidden = true;
  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  if (!username || !password) {
    error.textContent = '请输入帐号与密码。';
    error.hidden = false;
    return;
  }
  submit.disabled = true;
  submit.textContent = '登录中…';
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      cache: 'no-store',
      body: JSON.stringify({username, password, remember: rememberInput.checked})
    });
    if (!response.ok) {
      throw new Error(response.status === 401 ? '帐号或密码不正确。' : '目前无法登录，请稍后再试。');
    }
    passwordInput.value = '';
    showPassword(false);
    const next = new URLSearchParams(window.location.search).get('next');
    window.location.replace(next === '/call' || next === '/chat' ? next : '/chat');
  } catch (cause) {
    error.textContent = cause.message || '目前无法登录，请稍后再试。';
    error.hidden = false;
    passwordInput.value = '';
    showPassword(false);
    submit.disabled = false;
    submit.innerHTML = '登录语灵 <span aria-hidden="true">↗</span>';
  }
});
