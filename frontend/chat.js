const $ = (id) => document.getElementById(id);
// The mobile keyboard can shrink and pan the visible viewport without changing 100dvh.
// Keep the app shell inside that viewport; only the conversation is scrollable.
function syncChatViewport() {
  const viewport = window.visualViewport;
  document.documentElement.style.setProperty("--chat-viewport-height", `${viewport?.height ?? window.innerHeight}px`);
  document.documentElement.style.setProperty("--chat-viewport-top", `${viewport?.offsetTop ?? 0}px`);
}
window.visualViewport?.addEventListener("resize", syncChatViewport);
window.visualViewport?.addEventListener("scroll", syncChatViewport);
window.addEventListener("resize", syncChatViewport);
syncChatViewport();
const conversation = $("conversation");
const list = $("message-list");
const intro = $("intro");
const introVideo = $("intro-video");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
function syncIntroVideo() {
  if (reducedMotion.matches || intro.hidden || document.hidden) introVideo.pause();
  else introVideo.play().catch(() => {});
}
reducedMotion.addEventListener?.("change", syncIntroVideo);
document.addEventListener("visibilitychange", syncIntroVideo);
syncIntroVideo();
const form = $("chat-form");
const input = $("message-input");
const imageInput = $("image-input");
const sendButton = $("send-button");
const attachment = $("attachment");
const attachmentImage = $("attachment-image");
const attachmentFileIcon = $("attachment-file-icon");
const attachmentName = $("attachment-name");
const attachmentNote = $("attachment-note");
const urlPreview = $("url-preview");
const urlHost = $("url-host");
const urlPreviewStatus = $("url-preview-status");
const globalStatus = $("global-status");
const pageReference = $("page-reference");
const pageReferenceHost = $("page-reference-host");
const emojiButton = $("emoji-button");
const emojiPanel = $("emoji-panel");
const SESSION_KEY = "yuling_chat_session_v2";
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const DOCUMENT_EXTENSIONS = new Set(["pdf", "docx", "pptx", "xlsx", "txt", "md", "csv"]);
const DOWNLOAD_ONLY_EXTENSIONS = new Set(["doc", "xls", "ppt", "wps", "et", "dps", "ofd"]);
const MAX_IMAGE_BYTES = 4_000_000;
const MAX_DOCUMENT_BYTES = 5_000_000;
let sessionId = localStorage.getItem(SESSION_KEY) || "";
let selectedFile = null;
let selectedImageUrl = "";
let sending = false;
let selectedPageRead = null;
let emojiPicker = null;
let emojiPickerLoading = false;
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
      ? `已读取摘录 · ${snapshotTime(pageRead.fetched_at)} · 可引用提问`
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

function addMessage({ role, text = "", imageUrl = "", imageName = "", document: documentInfo = null, url = null, timestamp = null, status = "", sourceKind = "", eventId = "", pageRead = null, sourceCitation = null }) {
  intro.hidden = true;
  syncIntroVideo();
  const row = document.createElement("article");
  row.className = `message ${role === "user" ? "user" : "assistant"}`;
  if (status) row.classList.add(status);
  const avatar = document.createElement("span");
  avatar.className = "bubble-avatar";
  avatar.setAttribute("aria-hidden", "true");
  if (role === "user") avatar.textContent = "我";
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
  let documentCard = null;
  if (documentInfo?.filename) {
    // Use a non-clickable card until the upload has a server-confirmed id.
    documentCard = documentInfo.id ? document.createElement("a") : document.createElement("div");
    documentCard.className = "document-card";
    if (documentInfo.id) {
      documentCard.href = `/api/v2/document/${encodeURIComponent(documentInfo.id)}`;
      documentCard.download = documentInfo.filename;
    }
    const icon = document.createElement("span");
    icon.className = "document-icon";
    icon.textContent = "▤";
    const name = document.createElement("span");
    name.className = "document-name";
    name.textContent = documentInfo.filename;
    documentCard.append(icon, name);
    if (["unsupported_extract", "no_text"].includes(documentInfo.extract_status)) {
      const note = document.createElement("small");
      note.className = "document-status";
      note.textContent = documentInfo.extract_status === "unsupported_extract"
        ? "已保存，可下载；此格式暂不支持内容解析"
        : "已保存，可下载；未抽取到文字";
      documentCard.append(note);
    }
    bubble.append(documentCard);
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
  return { row, meta, footer, documentCard };
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
  sendButton.disabled = sending || (!input.value.trim() && !selectedFile);
  input.style.height = "40px";
  input.style.height = `${Math.min(input.scrollHeight, 130)}px`;
  updateUrlPreview();
}

function closeEmojiPanel() {
  emojiPanel.hidden = true;
  emojiButton.setAttribute("aria-expanded", "false");
}

async function toggleEmojiPanel() {
  if (!emojiPanel.hidden) { closeEmojiPanel(); return; }
  emojiPanel.hidden = false;
  emojiButton.setAttribute("aria-expanded", "true");
  if (emojiPicker || emojiPickerLoading) return;
  emojiPickerLoading = true;
  emojiPanel.textContent = "正在加载表情…";
  try {
    const [{ Picker }, { default: zhCN }] = await Promise.all([
      import("/yuling-assets/emoji-picker/index.js"),
      import("/yuling-assets/emoji-picker/i18n/zh_CN.js"),
    ]);
    const picker = new Picker({
      locale: "zh",
      i18n: zhCN,
      dataSource: "/yuling-assets/emoji-picker/zh-data.json",
    });
    picker.addEventListener("emoji-click", (event) => {
      const emoji = event.detail?.unicode;
      if (!emoji || input.value.length - (input.selectionEnd - input.selectionStart) + emoji.length > input.maxLength) return;
      input.setRangeText(emoji, input.selectionStart, input.selectionEnd, "end");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      closeEmojiPanel();
      input.focus();
    });
    emojiPicker = picker;
    emojiPanel.replaceChildren(picker);
  } catch {
    closeEmojiPanel();
    showStatus("表情暂时无法加载，可以使用手机键盘自带的表情。");
  } finally {
    emojiPickerLoading = false;
  }
}

function clearAttachment() {
  if (selectedImageUrl) URL.revokeObjectURL(selectedImageUrl);
  selectedImageUrl = "";
  selectedFile = null;
  imageInput.value = "";
  attachment.hidden = true;
  attachmentImage.removeAttribute("src");
  attachmentImage.hidden = true;
  attachmentFileIcon.hidden = true;
  updateComposer();
}

function isImage(file) { return ALLOWED_TYPES.has(file.type); }
function setAttachment(file) {
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  const image = isImage(file);
  if (!image && !DOCUMENT_EXTENSIONS.has(extension) && !DOWNLOAD_ONLY_EXTENSIONS.has(extension)) {
    showStatus("格式暂不支持。可上传图片、PDF、Office、WPS 或 OFD 文件。");
    return;
  }
  if (!file.size || file.size > (image ? MAX_IMAGE_BYTES : MAX_DOCUMENT_BYTES)) {
    showStatus(image ? "图片大小需在 4 MB 内。" : "文件大小需在 5 MB 内。");
    return;
  }
  clearAttachment();
  selectedFile = file;
  if (image) {
    selectedImageUrl = URL.createObjectURL(file);
    attachmentImage.src = selectedImageUrl;
    attachmentImage.hidden = false;
    attachmentNote.textContent = "发送后会交给端倪查看";
  } else {
    attachmentFileIcon.hidden = false;
    attachmentNote.textContent = DOWNLOAD_ONLY_EXTENSIONS.has(extension)
      ? "发送后可下载；此格式暂不支持内容解析"
      : "仅抽取部分文字；扫描页与版式未必可读";
  }
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
  const requestedSessionId = sessionId;
  try {
    try {
      const knowledge = await jsonResponse(await fetch("/api/v2/knowledge", { credentials: "same-origin" }));
      for (const item of knowledge.items || []) {
        if (item.source_event_id) savedKnowledgeEventIds.add(item.source_event_id);
      }
    } catch { /* History remains usable if the optional knowledge list is unavailable. */ }
    const response = await fetch(`/api/v2/history?session_id=${encodeURIComponent(requestedSessionId)}`, { credentials: "same-origin" });
    const payload = await jsonResponse(response);
    const events = Array.isArray(payload.events) ? payload.events : [];
    for (const event of events) {
      const role = event.role || event.speaker;
      if (!["user", "assistant"].includes(role)) continue;
      addMessage({ role, text: event.text || event.content || "",
        imageUrl: event.image_id ? `/api/v2/image/${encodeURIComponent(event.image_id)}` : "",
        document: event.document || null,
        url: event.url || null, timestamp: event.created_at || event.timestamp,
        sourceKind: event.source_kind || "", eventId: event.id || "", pageRead: event.page_read || null,
        sourceCitation: event.source_citation || null });
    }
    if (events.length) showStatus("");
  } catch (error) {
    if (error.httpStatus === 404) {
      if (sessionId === requestedSessionId) {
        sessionId = "";
        localStorage.removeItem(SESSION_KEY);
      }
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
  const file = selectedFile;
  const imageFile = file && isImage(file);
  const messageImageUrl = imageFile ? URL.createObjectURL(file) : "";
  if (messageImageUrl) messageImageUrls.add(messageImageUrl);
  const url = firstUrl(text);
  if (!text && !file) return;
  sending = true;
  updateComposer();
  showStatus("");
  const visible = addMessage({ role: "user", text, imageUrl: messageImageUrl, imageName: file?.name,
    document: file && !imageFile ? { filename: file.name } : null, url, status: "pending" });
  let documentId = "";
  let documentExtractStatus = "";
  function linkSavedDocument() {
    if (!documentId || !visible.documentCard || visible.documentCard.tagName === "A") return;
    const linked = document.createElement("a");
    linked.className = visible.documentCard.className;
    linked.href = `/api/v2/document/${encodeURIComponent(documentId)}`;
    linked.download = file.name;
    linked.append(...visible.documentCard.childNodes);
    if (["unsupported_extract", "no_text"].includes(documentExtractStatus)) {
      const note = document.createElement("small");
      note.className = "document-status";
      note.textContent = documentExtractStatus === "unsupported_extract"
        ? "已保存，可下载；此格式暂不支持内容解析"
        : "已保存，可下载；未抽取到文字";
      linked.append(note);
    }
    visible.documentCard.replaceWith(linked);
  }
  try {
    let imageId = "";
    if (file) {
      const body = new FormData();
      body.append("file", file, file.name);
      const uploaded = await jsonResponse(await fetch(imageFile ? "/api/v2/upload-image" : "/api/v2/upload-document", {
        method: "POST", credentials: "same-origin", body,
      }));
      if (imageFile) {
        imageId = uploaded.image_id || "";
        if (!imageId) throw new Error("图片服务没有返回图片编号");
      } else {
        documentId = uploaded.document_id || "";
        documentExtractStatus = uploaded.document?.extract_status || "";
        if (!documentId) throw new Error("文件服务没有返回文件编号");
      }
    }
    const requestBody = { text };
    if (sessionId) requestBody.session_id = sessionId;
    if (imageId) requestBody.image_id = imageId;
    if (documentId) requestBody.document_id = documentId;
    if (url) requestBody.url = url.href;
    if (pageReadId) requestBody.page_read_id = pageReadId;
    const postChat = () => fetch("/api/v2/chat", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(requestBody),
    });
    let response = await postChat();
    // A Railway redeploy can erase the temporary SQLite session while this PWA
    // stays open. The server rejects an unknown session before saving an event,
    // so retry once with the already-uploaded file in a fresh session.
    if (response.status === 404 && requestBody.session_id && !pageReadId) {
      const failure = await response.clone().json().catch(() => null);
      if (failure?.error === "会话不存在") {
        sessionId = "";
        localStorage.removeItem(SESSION_KEY);
        delete requestBody.session_id;
        response = await postChat();
      }
    }
    const result = await jsonResponse(response);
    if (typeof result.reply !== "string") throw new Error("对话服务没有返回回复");
    if (result.session_id) {
      sessionId = String(result.session_id);
      localStorage.setItem(SESSION_KEY, sessionId);
    }
    visible.row.classList.remove("pending");
    visible.meta.textContent = displayTime();
    linkSavedDocument();
    if (result.user_event?.source_kind === "user_explicit") {
      attachKnowledgeAction(visible.footer, result.user_event.id, text);
      if (url) attachPageReadAction(visible.footer, result.user_event.id, url, visible.row, result.user_event.page_read || null);
    }
    addMessage({ role: "assistant", text: result.reply,
      sourceCitation: result.assistant_event?.source_citation || null });
    input.value = "";
    closeEmojiPanel();
    if (pageReadId && selectedPageRead?.id === pageReadId) setPageReference(null);
    clearAttachment();
    updateComposer();
    input.focus();
  } catch (error) {
    visible.row.classList.remove("pending");
    if (error.payload?.user_event) {
      linkSavedDocument();
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
      clearAttachment();
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
emojiButton.addEventListener("click", toggleEmojiPanel);
document.addEventListener("pointerdown", (event) => {
  if (!emojiPanel.hidden && !emojiPanel.contains(event.target) && event.target !== emojiButton) closeEmojiPanel();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !emojiPanel.hidden) { closeEmojiPanel(); emojiButton.focus(); }
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !event.repeat) {
    event.preventDefault();
    form.requestSubmit();
  }
});
input.addEventListener("paste", (event) => {
  const image = [...(event.clipboardData?.files || [])].find((file) => ALLOWED_TYPES.has(file.type));
  if (image) { event.preventDefault(); setAttachment(image); }
});
imageInput.addEventListener("change", () => { if (imageInput.files?.[0]) setAttachment(imageInput.files[0]); });
$("attach-button").addEventListener("click", () => imageInput.click());
$("remove-attachment").addEventListener("click", clearAttachment);
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

const installButton = $("install-button");
const installPanel = $("install-panel");
const installInstructions = $("install-instructions");
const standaloneMedia = window.matchMedia?.("(display-mode: standalone)");
let deferredInstallPrompt = null;

function isInstalled() {
  return !!(standaloneMedia?.matches || navigator.standalone === true);
}

function closeInstallPanel() {
  installPanel.hidden = true;
  installButton.setAttribute("aria-expanded", "false");
}

function refreshInstallButton() {
  installButton.hidden = isInstalled();
  if (installButton.hidden) closeInstallPanel();
}

function installHelpText() {
  const appleMobile = /iPhone|iPad|iPod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (appleMobile) {
    const safari = /Safari/i.test(navigator.userAgent) && !/CriOS|FxiOS|EdgiOS|MicroMessenger/i.test(navigator.userAgent);
    return safari
      ? "在 Safari 点底部的“分享”，再选“添加到主屏幕”。"
      : "请先在 Safari 打开这个网址，再点“分享” → “添加到主屏幕”。";
  }
  return /Android/i.test(navigator.userAgent)
    ? "在 Chrome 右上角菜单中选“安装应用”或“添加到主屏幕”。"
    : "打开浏览器菜单，选择“安装应用”或“添加到桌面”。";
}

function openInstallPanel() {
  installInstructions.textContent = installHelpText();
  installPanel.hidden = false;
  installButton.setAttribute("aria-expanded", "true");
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  refreshInstallButton();
});
window.addEventListener("appinstalled", () => {
  deferredInstallPrompt = null;
  refreshInstallButton();
});
standaloneMedia?.addEventListener?.("change", refreshInstallButton);
installButton.addEventListener("click", async () => {
  if (deferredInstallPrompt) {
    const prompt = deferredInstallPrompt;
    deferredInstallPrompt = null;
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      if (choice?.outcome === "accepted") { closeInstallPanel(); return; }
    } catch { /* Offer manual browser steps if the native prompt is unavailable. */ }
  }
  if (installPanel.hidden) openInstallPanel(); else closeInstallPanel();
});
$("install-close").addEventListener("click", () => { closeInstallPanel(); installButton.focus(); });
document.addEventListener("pointerdown", (event) => {
  if (!installPanel.hidden && !installPanel.contains(event.target) && !installButton.contains(event.target)) closeInstallPanel();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !installPanel.hidden) { closeInstallPanel(); installButton.focus(); }
});
refreshInstallButton();
updateComposer();
loadHistory();

const memoryButton = $("memory-button");
const memoryPanel = $("memory-panel");
const memoryChoice = $("memory-choice");
const memorySettings = $("memory-settings");
const memoryStateLabel = $("memory-state");
const memoryToggle = $("memory-toggle");
const memoryFacts = $("memory-facts");
const memoryError = $("memory-error");
let profile = null;
let profileBusy = false;

function showMemoryError(message) {
  memoryError.textContent = message;
  memoryError.hidden = !message;
}

function closeMemoryPanel() {
  memoryPanel.hidden = true;
  memoryButton.setAttribute("aria-expanded", "false");
}

function openMemoryPanel() {
  closeInstallPanel();
  memoryPanel.hidden = false;
  memoryButton.setAttribute("aria-expanded", "true");
}

function renderProfile() {
  if (!profile) return;
  const name = typeof profile.identity?.display_name === "string"
    ? profile.identity.display_name : "你";
  $("memory-title").textContent = `关于 ${name} 的记忆`;
  memoryChoice.hidden = !!profile.consent_decided;
  memorySettings.hidden = !profile.consent_decided;
  if (!profile.consent_decided) return;
  memoryStateLabel.textContent = profile.consent_enabled
    ? "已允许记录你主动分享的个人线索。"
    : "已暂停记录新的个人线索。";
  memoryToggle.textContent = profile.consent_enabled ? "暂停记录" : "允许记录";
  memoryToggle.disabled = profileBusy;
  memoryFacts.replaceChildren();
  const facts = Array.isArray(profile.facts) ? profile.facts : [];
  if (!facts.length) {
    const empty = document.createElement("li");
    empty.className = "memory-empty";
    empty.textContent = "还没有个人线索。";
    memoryFacts.append(empty);
  }
  for (const fact of facts.slice(0, 20)) {
    if (typeof fact?.id !== "string" || typeof fact.text !== "string") continue;
    const item = document.createElement("li");
    const copy = document.createElement("span");
    copy.textContent = fact.text;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "删除";
    remove.setAttribute("aria-label", `删除线索：${fact.text}`);
    remove.disabled = profileBusy;
    remove.addEventListener("click", () => deleteProfileFact(fact.id));
    item.append(copy, remove);
    memoryFacts.append(item);
  }
}

async function loadProfile({ firstLoad = false } = {}) {
  try {
    const result = await jsonResponse(await fetch("/api/v2/profile", { credentials: "same-origin" }));
    profile = result;
    memoryButton.hidden = false;
    showMemoryError("");
    renderProfile();
    if (firstLoad && !profile.consent_decided) openMemoryPanel();
  } catch (error) {
    // The profile storage is optional until a persistent volume is configured.
    if (error.httpStatus === 503) { memoryButton.hidden = true; closeMemoryPanel(); return; }
    if (error.httpStatus === 401) return;
    memoryButton.hidden = false;
    showMemoryError("记忆设置暂时无法读取，请稍后重试。");
  }
}

async function setProfileConsent(enabled) {
  if (profileBusy) return;
  profileBusy = true;
  for (const button of [$("memory-allow"), $("memory-decline"), memoryToggle]) button.disabled = true;
  showMemoryError("");
  try {
    await jsonResponse(await fetch("/api/v2/profile/consent", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }));
    await loadProfile();
  } catch {
    showMemoryError("设置没有确认保存，请重试。");
  } finally {
    profileBusy = false;
    for (const button of [$("memory-allow"), $("memory-decline"), memoryToggle]) button.disabled = false;
    renderProfile();
  }
}

async function deleteProfileFact(factId) {
  if (profileBusy) return;
  profileBusy = true;
  renderProfile();
  showMemoryError("");
  try {
    await jsonResponse(await fetch(`/api/v2/profile/facts/${encodeURIComponent(factId)}`, {
      method: "DELETE", credentials: "same-origin",
    }));
    await loadProfile();
  } catch {
    showMemoryError("删除结果未确认，请重新查看后再试。");
  } finally {
    profileBusy = false;
    renderProfile();
  }
}

memoryButton.addEventListener("click", async () => {
  if (!memoryPanel.hidden) { closeMemoryPanel(); return; }
  openMemoryPanel();
  if (!profile) await loadProfile();
});
$("memory-close").addEventListener("click", () => { closeMemoryPanel(); memoryButton.focus(); });
$("memory-allow").addEventListener("click", () => setProfileConsent(true));
$("memory-decline").addEventListener("click", () => setProfileConsent(false));
memoryToggle.addEventListener("click", () => setProfileConsent(!profile.consent_enabled));
document.addEventListener("pointerdown", (event) => {
  if (!memoryPanel.hidden && !memoryPanel.contains(event.target) && !memoryButton.contains(event.target)) closeMemoryPanel();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !memoryPanel.hidden) { closeMemoryPanel(); memoryButton.focus(); }
});
loadProfile({ firstLoad: true });
