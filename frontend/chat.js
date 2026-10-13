const $ = (id) => document.getElementById(id);
const conversation = $("conversation");
const list = $("message-list");
const intro = $("intro");
const form = $("chat-form");
const input = $("message-input");
const imageInput = $("image-input");
const sendButton = $("send-button");
const attachment = $("attachment");
const attachmentImage = $("attachment-image");
const attachmentName = $("attachment-name");
const urlPreview = $("url-preview");
const urlHost = $("url-host");
const urlPreviewStatus = $("url-preview-status");
const globalStatus = $("global-status");
const pageReference = $("page-reference");
const pageReferenceHost = $("page-reference-host");
const SESSION_KEY = "yuling_chat_session_v2";
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_IMAGE_BYTES = 4_000_000;
let sessionId = localStorage.getItem(SESSION_KEY) || "";
let selectedImage = null;
let selectedImageUrl = "";
let sending = false;
let selectedPageRead = null;
const messageImageUrls = new Set();
const savedKnowledgeEventIds = new Set();

function showStatus(message) {
  globalStatus.textContent = message;
  globalStatus.hidden = !message;
}

function displayTime(value) {
  const date = value ? new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value) : new Date();
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
}

function firstUrl(text) {
  const match = text.match(/https?:\/\/[^\s<>"']+/i);
  if (!match) return null;
  try {
    const url = new URL(match[0].replace(/[.,;!?)，。；！）]+$/, ""));
    if (!["http:", "https:"].includes(url.protocol)) return null;
    return url;
  } catch { return null; }
}

function linkCard(url) {
  const card = document.createElement("a");
  card.className = "link-card";
  card.href = url.href;
  card.target = "_blank";
  card.rel = "noopener noreferrer nofollow";
  const icon = document.createElement("span");
  icon.className = "link-icon";
  icon.textContent = "↗";
  const details = document.createElement("span");
  const title = document.createElement("strong");
  title.textContent = url.hostname;
  const subtitle = document.createElement("small");
  subtitle.textContent = "外部链接 · 未读取网页内容";
  details.append(title, subtitle);
  card.append(icon, details);
  return card;
}

function pageSourceUrl(originalUrl, pageRead) {
  if (pageRead?.status !== "read" || typeof pageRead.url !== "string") return null;
  try {
    const source = new URL(pageRead.url);
    return source.protocol === "https:" ? source : null;
  } catch { return null; }
}

function pageReadReference(originalUrl, pageRead) {
  const source = pageSourceUrl(originalUrl, pageRead);
  if (!source || source.href.split("#")[0] !== originalUrl.href.split("#")[0] ||
      typeof pageRead?.id !== "string" || !pageRead.id ||
      typeof pageRead.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(pageRead.sha256) ||
      !["text/html", "text/plain"].includes(pageRead.content_type)) return null;
  const fetchedTime = Date.parse(pageRead.fetched_at);
  if (!Number.isFinite(fetchedTime) || fetchedTime > Date.now() + 60_000 ||
      Date.now() - fetchedTime > 24 * 60 * 60 * 1000) return null;
  return { id: pageRead.id, hostname: source.hostname, fetchedAt: pageRead.fetched_at };
}

function snapshotTime(value) {
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(time);
}

function setPageReference(reference) {
  selectedPageRead = reference;
  pageReference.hidden = !reference;
  pageReferenceHost.textContent = reference
    ? `${reference.hostname} · ${snapshotTime(reference.fetchedAt)} 的摘录` : "";
  if (reference) {
    showStatus("已选择网页摘录。输入问题后发送，端倪只会参考有限摘录，不代表读懂整个网站。");
    input.focus();
  }
}

function addSourceCitation(bubble, citation) {
  if (!citation || typeof citation.url !== "string") return;
  let source;
  try {
    source = new URL(citation.url);
    if (source.protocol !== "https:") return;
  } catch { return; }
  const card = document.createElement("a");
  card.className = "source-citation";
  card.href = source.href;
  card.target = "_blank";
  card.rel = "noopener noreferrer nofollow";
  const label = document.createElement("strong");
  label.textContent = `网页摘录 · ${source.hostname}`;
  const detail = document.createElement("small");
  const excerpt = Number.isInteger(citation.excerpt_chars) && citation.excerpt_chars > 0
    ? `仅引用 ${citation.excerpt_chars} 字摘录` : "仅引用有限摘录";
  const fetched = snapshotTime(citation.fetched_at);
  detail.textContent = `${excerpt} · ${fetched ? `${fetched} 快照 · ` : ""}非完整网页`;
  card.append(label, detail);
  bubble.append(card);
}

function showPageRead(row, originalUrl, pageRead) {
  const card = row.querySelector(".link-card");
  if (!card || !originalUrl) return;
  const source = pageSourceUrl(originalUrl, pageRead);
  const title = card.querySelector("strong");
  const subtitle = card.querySelector("small");
  if (pageRead?.status === "read" && source) {
    card.href = source.href;
    title.textContent = source.hostname;
    const usable = pageReadReference(originalUrl, pageRead);
    subtitle.textContent = usable
      ? `已读取摘录 · ${snapshotTime(pageRead.fetched_at)} · 尚未用于回答`
      : "已读取快照 · 暂不可引用，请重新读取";
    card.title = pageRead.fetched_at ? `读取时间：${snapshotTime(pageRead.fetched_at)}` : "已读取的网页来源";
  } else if (pageRead?.status === "failed") {
    subtitle.textContent = "读取失败 · 未使用页面内容";
  } else if (pageRead?.status === "read") {
    subtitle.textContent = "读取结果缺少来源 · 状态未确认";
  } else {
    subtitle.textContent = "外部链接 · 未读取网页内容";
  }
}

function attachPageReadAction(footer, eventId, url, row, pageRead = null) {
  if (!eventId || url?.protocol !== "https:" || footer.querySelector(".page-read-action")) return;
  showPageRead(row, url, pageRead);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "page-read-action";
  const hasReference = !!pageReadReference(url, pageRead);
  button.textContent = hasReference ? "已读取网页" : pageRead?.status === "read" ? "重新读取" : "读取此网页";
  button.disabled = hasReference;
  button.setAttribute("aria-label", `读取 ${url.hostname} 的公开网页内容`);
  function addReferenceAction(artifact) {
    const reference = pageReadReference(url, artifact);
    if (!reference || footer.querySelector(".page-reference-action")) return;
    const cite = document.createElement("button");
    cite.type = "button";
    cite.className = "page-reference-action";
    cite.textContent = "引用此网页提问";
    cite.setAttribute("aria-label", `引用 ${reference.hostname} 的网页摘录提问`);
    cite.addEventListener("click", () => setPageReference(reference));
    footer.append(cite);
  }
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "读取中…";
    try {
      const result = await jsonResponse(await fetch("/api/v2/page-read", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_event_id: eventId }),
      }));
      const outcome = result.page_read;
      if (!outcome || !["read", "failed"].includes(outcome.status)) throw new Error("网页服务没有返回读取结果");
      showPageRead(row, url, outcome);
      const confirmedRead = !!pageReadReference(url, outcome);
      button.textContent = confirmedRead ? "已读取网页" : "重试读取";
      button.disabled = confirmedRead;
      if (outcome.status === "failed") showStatus("网页读取失败，端倪没有使用页面内容。可以稍后重试。");
      if (confirmedRead) {
        addReferenceAction(outcome);
        showStatus("网页已读取（试用暂存）。点击“引用此网页提问”后，输入问题才会提供有限摘录；尚未存为知识。");
      }
      if (outcome.status === "read" && !confirmedRead) showStatus("网页快照来源不符、不完整或已过期；这次不能引用，可以重试读取。");
    } catch (error) {
      button.disabled = false;
      button.textContent = "重试读取";
      if (error.payload?.page_read?.status === "failed") {
        showPageRead(row, url, error.payload.page_read);
        showStatus(`网页读取失败，端倪没有使用页面内容：${error.message}`);
      } else {
        showStatus(`网页读取状态未确认：${error.message}。刷新对话可查看记录。`);
      }
    }
  });
  footer.append(button);
  addReferenceAction(pageRead);
}

function addMessage({ role, text = "", imageUrl = "", imageName = "", url = null, timestamp = null, status = "", sourceKind = "", eventId = "", pageRead = null, sourceCitation = null }) {
  intro.hidden = true;
  const row = document.createElement("article");
  row.className = `message ${role === "user" ? "user" : "assistant"}`;
  if (status) row.classList.add(status);
  const avatar = document.createElement("span");
  avatar.className = "bubble-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = role === "user" ? "我" : "倪";
  const stack = document.createElement("div");
  stack.className = "bubble-stack";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (imageUrl) {
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = imageName || "发送的图片";
    bubble.append(image);
  }
  const body = document.createElement("div");
  body.className = "message-text";
  body.textContent = text;
  bubble.append(body);
  const parsed = url instanceof URL ? url : firstUrl(url || text);
  let citedUrl = null;
  try { if (sourceCitation?.url) citedUrl = new URL(sourceCitation.url); } catch { /* Citation is optional. */ }
  if (parsed && !(role === "assistant" && citedUrl &&
      parsed.origin === citedUrl.origin && parsed.pathname === citedUrl.pathname)) {
    bubble.append(linkCard(parsed));
  }
  if (role === "assistant") addSourceCitation(bubble, sourceCitation);
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const sourceLabel = sourceKind === "livekit_browser_transcript" ? "语音转写 · " : "";
  meta.textContent = status === "pending" ? "发送中…" : status === "failed" ? "发送失败 · 未保存" : sourceLabel + displayTime(timestamp);
  const footer = document.createElement("div");
  footer.className = "message-footer";
  footer.append(meta);
  stack.append(bubble, footer);
  row.append(avatar, stack);
  list.append(row);
  if (role === "user" && sourceKind === "user_explicit" && eventId && text.trim()) {
    attachKnowledgeAction(footer, eventId, text);
  }
  if (parsed && role === "user") showPageRead(row, parsed, pageRead);
  if (parsed && role === "user" && sourceKind === "user_explicit" && eventId) {
    attachPageReadAction(footer, eventId, parsed, row, pageRead);
  }
  conversation.scrollTop = conversation.scrollHeight;
  return { row, meta, footer };
}

function attachKnowledgeAction(footer, eventId, originalText) {
  const content = originalText.trim().slice(0, 4000);
  if (!eventId || !content || footer.querySelector(".knowledge-action")) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "knowledge-action";
  button.textContent = savedKnowledgeEventIds.has(eventId) ? "已存为知识" : "存为知识";
  button.disabled = savedKnowledgeEventIds.has(eventId);
  if (button.disabled) button.title = "试用暂存；重新部署可能清空";
  button.setAttribute("aria-label", "将这条用户消息存为知识");
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      await jsonResponse(await fetch("/api/v2/knowledge", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: content.replace(/\s+/g, " ").slice(0, 80),
          content, source_event_id: eventId }),
      }));
      savedKnowledgeEventIds.add(eventId);
      button.textContent = "已存为知识";
      button.title = "试用暂存；重新部署可能清空";
      showStatus("已存为知识（试用暂存）；重新部署可能清空。");
    } catch (error) {
      button.disabled = false;
      button.textContent = "保存失败 · 重试";
      showStatus(`未能存为知识：${error.message}`);
    }
  });
  footer.append(button);
}

function updateUrlPreview() {
  const url = firstUrl(input.value);
  urlPreview.hidden = !url;
  urlHost.textContent = url ? url.hostname : "";
  urlPreviewStatus.textContent = url?.protocol === "https:"
    ? "网址卡片 · 发送后可选择读取网页" : "网址卡片 · 未读取网页内容";
}

function updateComposer() {
  sendButton.disabled = sending || (!input.value.trim() && !selectedImage);
  input.style.height = "40px";
  input.style.height = `${Math.min(input.scrollHeight, 130)}px`;
  updateUrlPreview();
}

function clearImage() {
  if (selectedImageUrl) URL.revokeObjectURL(selectedImageUrl);
  selectedImageUrl = "";
  selectedImage = null;
  imageInput.value = "";
  attachment.hidden = true;
  attachmentImage.removeAttribute("src");
  updateComposer();
}

function setImage(file) {
  if (!ALLOWED_TYPES.has(file.type)) { showStatus("图片格式不支持。请使用 JPG、PNG 或 WebP。"); return; }
  if (file.size > MAX_IMAGE_BYTES) { showStatus("图片超过 4 MB，请换一张较小的图片。"); return; }
  clearImage();
  selectedImage = file;
  selectedImageUrl = URL.createObjectURL(file);
  attachmentImage.src = selectedImageUrl;
  attachmentName.textContent = file.name;
  attachment.hidden = false;
  showStatus("");
  updateComposer();
}

async function jsonResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    localStorage.removeItem(SESSION_KEY);
    window.location.href = "/login?next=/chat";
    throw new Error("请先登录");
  }
  if (!response.ok) {
    const error = new Error(payload.error || payload.message || `服务暂时不可用（${response.status}）`);
    error.payload = payload;
    error.httpStatus = response.status;
    throw error;
  }
  return payload;
}

async function loadHistory() {
  if (!sessionId) return;
  try {
    try {
      const knowledge = await jsonResponse(await fetch("/api/v2/knowledge", { credentials: "same-origin" }));
      for (const item of knowledge.items || []) {
        if (item.source_event_id) savedKnowledgeEventIds.add(item.source_event_id);
      }
    } catch { /* History remains usable if the optional knowledge list is unavailable. */ }
    const response = await fetch(`/api/v2/history?session_id=${encodeURIComponent(sessionId)}`, { credentials: "same-origin" });
    const payload = await jsonResponse(response);
    const events = Array.isArray(payload.events) ? payload.events : [];
    for (const event of events) {
      const role = event.role || event.speaker;
      if (!["user", "assistant"].includes(role)) continue;
      addMessage({ role, text: event.text || event.content || "",
        imageUrl: event.image_id ? `/api/v2/image/${encodeURIComponent(event.image_id)}` : "",
        url: event.url || null, timestamp: event.created_at || event.timestamp,
        sourceKind: event.source_kind || "", eventId: event.id || "", pageRead: event.page_read || null,
        sourceCitation: event.source_citation || null });
    }
    if (events.length) showStatus("");
  } catch (error) {
    if (error.httpStatus === 404) {
      sessionId = "";
      localStorage.removeItem(SESSION_KEY);
      showStatus("之前的会话未找到，可以开始新对话。");
    } else {
      showStatus(`暂时无法读取对话记录：${error.message}`);
    }
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (sending) return;
  const text = input.value.trim();
  const pageReadId = text ? selectedPageRead?.id || "" : "";
  const file = selectedImage;
  const messageImageUrl = file ? URL.createObjectURL(file) : "";
  if (messageImageUrl) messageImageUrls.add(messageImageUrl);
  const url = firstUrl(text);
  if (!text && !file) return;
  sending = true;
  updateComposer();
  showStatus("");
  const visible = addMessage({ role: "user", text, imageUrl: messageImageUrl, imageName: file?.name, url, status: "pending" });
  try {
    let imageId = "";
    if (file) {
      const body = new FormData();
      body.append("file", file, file.name);
      const uploaded = await jsonResponse(await fetch("/api/v2/upload-image", { method: "POST", credentials: "same-origin", body }));
      imageId = uploaded.image_id || "";
      if (!imageId) throw new Error("图片服务没有返回图片编号");
    }
    const requestBody = { text };
    if (sessionId) requestBody.session_id = sessionId;
    if (imageId) requestBody.image_id = imageId;
    if (url) requestBody.url = url.href;
    if (pageReadId) requestBody.page_read_id = pageReadId;
    const result = await jsonResponse(await fetch("/api/v2/chat", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(requestBody),
    }));
    if (typeof result.reply !== "string") throw new Error("对话服务没有返回回复");
    if (result.session_id) {
      sessionId = String(result.session_id);
      localStorage.setItem(SESSION_KEY, sessionId);
    }
    visible.row.classList.remove("pending");
    visible.meta.textContent = displayTime();
    if (result.user_event?.source_kind === "user_explicit") {
      attachKnowledgeAction(visible.footer, result.user_event.id, text);
      if (url) attachPageReadAction(visible.footer, result.user_event.id, url, visible.row, result.user_event.page_read || null);
    }
    addMessage({ role: "assistant", text: result.reply,
      sourceCitation: result.assistant_event?.source_citation || null });
    input.value = "";
    if (pageReadId && selectedPageRead?.id === pageReadId) setPageReference(null);
    clearImage();
    updateComposer();
    input.focus();
  } catch (error) {
    visible.row.classList.remove("pending");
    if (error.payload?.user_event) {
      if (error.payload.session_id) {
        sessionId = String(error.payload.session_id);
        localStorage.setItem(SESSION_KEY, sessionId);
      }
      visible.meta.textContent = "已保存 · 端倪暂时无法回复";
      if (error.payload.user_event.source_kind === "user_explicit") {
        attachKnowledgeAction(visible.footer, error.payload.user_event.id, text);
        if (url) attachPageReadAction(visible.footer, error.payload.user_event.id, url, visible.row,
          error.payload.user_event.page_read || null);
      }
      input.value = "";
      if (pageReadId && selectedPageRead?.id === pageReadId) setPageReference(null);
      clearImage();
      showStatus(error.message || "消息已保存，端倪暂时无法回复。");
    } else {
      visible.row.classList.add("failed");
      visible.meta.textContent = error.httpStatus && error.httpStatus < 500
        ? "发送失败 · 未保存" : "发送状态未确认 · 刷新查看";
      if (pageReadId && [400, 404].includes(error.httpStatus)) {
        if (selectedPageRead?.id === pageReadId) setPageReference(null);
        showStatus("网页摘录已过期或试用记录已清空。问题未发送；请重新读取网址后再提问。");
      } else {
        showStatus(error.message || "消息发送失败，请稍后重试。");
      }
    }
  } finally {
    sending = false;
    updateComposer();
  }
});

input.addEventListener("input", updateComposer);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !event.repeat) {
    event.preventDefault();
    form.requestSubmit();
  }
});
input.addEventListener("paste", (event) => {
  const image = [...(event.clipboardData?.files || [])].find((file) => ALLOWED_TYPES.has(file.type));
  if (image) { event.preventDefault(); setImage(image); }
});
imageInput.addEventListener("change", () => { if (imageInput.files?.[0]) setImage(imageInput.files[0]); });
$("attach-button").addEventListener("click", () => imageInput.click());
$("remove-attachment").addEventListener("click", clearImage);
$("remove-page-reference").addEventListener("click", () => setPageReference(null));
$("remove-url").addEventListener("click", () => {
  const url = firstUrl(input.value);
  if (!url) return;
  input.value = input.value.replace(/https?:\/\/[^\s<>"']+/i, "").trim();
  updateComposer();
  input.focus();
});
window.addEventListener("beforeunload", () => {
  if (selectedImageUrl) URL.revokeObjectURL(selectedImageUrl);
  for (const url of messageImageUrls) URL.revokeObjectURL(url);
});
updateComposer();
loadHistory();
