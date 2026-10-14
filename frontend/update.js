(() => {
  const view = document.body.dataset.yulingView;
  const button = document.getElementById("refresh-button");
  const label = document.getElementById("deployment-version");
  if (view !== "chat" && view !== "call") return;
  let refreshing = false;
  function callParent() {
    try {
      return view === "chat" && window.parent !== window &&
        window.parent.document.body.dataset.yulingView === "call" ? window.parent : null;
    } catch { return null; }
  }

  async function prepareCallRefresh(callWindow) {
    if (!callWindow) return true;
    if (typeof callWindow.YulingPrepareRefresh === "function") {
      return !!(await callWindow.YulingPrepareRefresh());
    }
    // A previously loaded call shell has no refresh guard yet. Let its newly
    // loaded chat iframe upgrade the shell only when the old UI is clearly idle.
    try {
      const call = callWindow.document;
      return call.getElementById("callLabel")?.textContent?.trim() === "拨打" &&
        call.getElementById("callCircle")?.classList.contains("go") === true;
    } catch { return false; }
  }

  async function readVersion() {
    const response = await fetch("/api/v2/version", {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (response.status === 401) {
      window.location.assign(`/login?next=/${view}`);
      return null;
    }
    if (!response.ok) throw new Error("version_unavailable");
    return response.json();
  }

  function showVersion(data) {
    if (!label) return;
    label.textContent = data?.version || "版本号暂不可用";
    if (data?.commit) label.title = `代码 ${data.commit}`;
  }

  if (label) readVersion().then(showVersion).catch(() => {
    label.textContent = "版本号暂不可用";
  });

  async function refreshLatest() {
    if (refreshing || button?.disabled) return false;
    const parent = callParent();
    const callWindow = parent || (view === "call" ? window : null);
    if (!(await prepareCallRefresh(callWindow))) {
      if (label) label.textContent = "請先掛斷通話再更新";
      else window.YulingRefreshNotice?.("請先掛斷通話再更新");
      return false;
    }
    const unsentText = document.getElementById("message-input")?.value.trim();
    const attachment = document.getElementById("attachment");
    const pendingMessage = document.querySelector(".message.pending");
    const urlPreview = document.getElementById("url-preview");
    const pageReference = document.getElementById("page-reference");
    if (pendingMessage) {
      if (label) label.textContent = "訊息傳送中，稍後再刷新";
      return false;
    }
    if (view === "chat" && (unsentText || !attachment?.hidden ||
        !urlPreview?.hidden || !pageReference?.hidden) &&
        !window.confirm("目前有未送出的內容，刷新可能清除附件。要繼續嗎？")) return false;
    refreshing = true;
    if (button) {
      button.disabled = true;
      button.classList.add("updating");
      button.setAttribute("aria-label", "正在檢查最新版本");
    }
    try {
      const data = await readVersion();
      if (!data) return false;
      showVersion(data);
      const next = new URL(parent ? "/call" : `/${view}`, window.location.origin);
      next.searchParams.set("update", data.cache_key || String(Date.now()));
      if (parent) next.searchParams.set("view", "chat");
      (parent || window).location.replace(next.href);
      return true;
    } catch {
      if (label) label.textContent = "更新檢查失敗，請重試";
      else window.YulingRefreshNotice?.("更新檢查失敗，請再試一次");
      return false;
    } finally {
      refreshing = false;
      if (button) {
        button.disabled = false;
        button.classList.remove("updating");
        button.setAttribute("aria-label", "刷新並取得最新版本");
      }
    }
  }
  window.YulingRefreshLatest = refreshLatest;
  button?.addEventListener("click", refreshLatest);
})();
