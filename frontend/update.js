(() => {
  const button = document.getElementById("refresh-button");
  const label = document.getElementById("deployment-version");
  if (!button || !label) return;

  async function readVersion() {
    const response = await fetch("/api/v2/version", {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (response.status === 401) {
      window.location.assign("/login?next=/chat");
      return null;
    }
    if (!response.ok) throw new Error("version_unavailable");
    return response.json();
  }

  function showVersion(data) {
    label.textContent = data?.version || "版本号暂不可用";
    if (data?.commit) label.title = `代码 ${data.commit}`;
  }

  readVersion().then(showVersion).catch(() => {
    label.textContent = "版本号暂不可用";
  });

  button.addEventListener("click", async () => {
    if (button.disabled) return;
    const unsentText = document.getElementById("message-input")?.value.trim();
    const attachment = document.getElementById("attachment");
    const pendingMessage = document.querySelector(".message.pending");
    if ((unsentText || !attachment?.hidden || pendingMessage) &&
        !window.confirm("刷新会清除未发送的内容。现在刷新吗？")) return;
    button.disabled = true;
    button.classList.add("updating");
    button.setAttribute("aria-label", "正在检查最新版本");
    try {
      const data = await readVersion();
      if (!data) return;
      showVersion(data);
      const next = new URL("/chat", window.location.origin);
      next.searchParams.set("update", data.cache_key || String(Date.now()));
      window.location.replace(next.href);
    } catch {
      label.textContent = "更新检查失败，请重试";
    } finally {
      button.disabled = false;
      button.classList.remove("updating");
      button.setAttribute("aria-label", "刷新并获取最新版本");
    }
  });
})();
