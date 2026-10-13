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
const globalStatus = $("global-status");
const SESSION_KEY = "yuling_chat_session_v2";
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_IMAGE_BYTES = 4_000_000;
let sessionId = localStorage.getItem(SESSION_KEY) || "";
let selectedImage = null;
let selectedImageUrl = "";
let sending = false;
const messageImageUrls = new Set();

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

function addMessage({ role, text = "", imageUrl = "", imageName = "", url = null, timestamp = null, status = "", sourceKind = "" }) {
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
  if (parsed) bubble.append(linkCard(parsed));
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const sourceLabel = sourceKind === "livekit_browser_transcript" ? "语音转写 · " : "";
  meta.textContent = status === "pending" ? "发送中…" : status === "failed" ? "发送失败 · 未保存" : sourceLabel + displayTime(timestamp);
  stack.append(bubble, meta);
  row.append(avatar, stack);
  list.append(row);
  conversation.scrollTop = conversation.scrollHeight;
  return { row, meta };
}

function updateUrlPreview() {
  const url = firstUrl(input.value);
  urlPreview.hidden = !url;
  urlHost.textContent = url ? url.hostname : "";
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
    const response = await fetch(`/api/v2/history?session_id=${encodeURIComponent(sessionId)}`, { credentials: "same-origin" });
    const payload = await jsonResponse(response);
    const events = Array.isArray(payload.events) ? payload.events : [];
    for (const event of events) {
      const role = event.role || event.speaker;
      if (!["user", "assistant"].includes(role)) continue;
      addMessage({ role, text: event.text || event.content || "",
        imageUrl: event.image_id ? `/api/v2/image/${encodeURIComponent(event.image_id)}` : "",
        url: event.url || null, timestamp: event.created_at || event.timestamp,
        sourceKind: event.source_kind || "" });
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
    addMessage({ role: "assistant", text: result.reply });
    input.value = "";
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
      input.value = "";
      clearImage();
      showStatus(error.message || "消息已保存，端倪暂时无法回复。");
    } else {
      visible.row.classList.add("failed");
      visible.meta.textContent = error.httpStatus && error.httpStatus < 500
        ? "发送失败 · 未保存" : "发送状态未确认 · 刷新查看";
      showStatus(error.message || "消息发送失败，请稍后重试。");
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
