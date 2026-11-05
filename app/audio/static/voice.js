"use strict";

const ui = Object.fromEntries(["record", "stop", "cancel", "text", "send", "new", "status", "configuration", "session", "history"].map(id => [id, document.getElementById(id)]));
let phase = "idle";
let generation = 0;
let recording = null;
let controller = null;
let transcriptionId = null;
let sessionId = null;
let sending = false;
let pendingMessage = null;

function status(message, error = false) {
  ui.status.textContent = message;
  ui.status.classList.toggle("error", error);
}

function render() {
  const busy = phase !== "idle";
  ui.record.disabled = busy || sending || Boolean(pendingMessage);
  ui.stop.disabled = phase !== "recording";
  ui.cancel.disabled = !busy || sending;
  ui.send.disabled = busy || sending || !ui.text.value.trim();
  ui.text.readOnly = busy || sending || Boolean(pendingMessage);
  ui.new.disabled = busy || sending;
  ui.send.textContent = pendingMessage ? "重试同一请求" : "发送给 Agent";
  document.querySelectorAll("input[name=mode]").forEach(input => { input.disabled = busy || sending; });
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let value;
  try { value = await response.json(); } catch { value = {}; }
  if (!response.ok) {
    const error = new Error(value.detail?.message || (typeof value.detail === "string" ? value.detail : `请求失败（${response.status}）`));
    error.http = true;
    throw error;
  }
  return value;
}

async function dispose(capture) {
  if (!capture) return;
  clearInterval(capture.timer);
  clearTimeout(capture.limit);
  capture.stream?.getTracks().forEach(track => track.stop());
  capture.source?.disconnect();
  capture.node?.disconnect();
  capture.gain?.disconnect();
  if (capture.context && capture.context.state !== "closed") {
    await capture.context.close().catch(() => {});
  }
}

async function startRecording() {
  const token = ++generation;
  const capture = {chunks: [], count: 0, mode: document.querySelector("input[name=mode]:checked").value};
  recording = capture;
  phase = "permission";
  ui.text.value = "";
  status("请允许使用麦克风。");
  render();
  try {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
      throw new Error("请使用支持录音的浏览器，通过 localhost 或 HTTPS 打开本页。");
    }
    capture.stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1}, video: false});
    if (token !== generation) { await dispose(capture); return; }
    capture.context = new AudioContext();
    await capture.context.resume();
    await capture.context.audioWorklet.addModule("/voice-worklet.js");
    if (token !== generation) { await dispose(capture); return; }
    capture.rate = capture.context.sampleRate;
    if (capture.rate > 96000) throw new Error("当前麦克风采样率过高，请在设备设置中选择 48 kHz 后重试。");
    capture.node = new AudioWorkletNode(capture.context, "voice-capture");
    capture.node.port.onmessage = ({data}) => {
      if (token !== generation) return;
      if (data.type === "chunk") {
        const remaining = capture.rate * 60 - capture.count;
        const samples = data.samples.slice(0, Math.max(0, remaining));
        if (samples.length) { capture.chunks.push(samples); capture.count += samples.length; }
        if (capture.count >= capture.rate * 60 && phase === "recording") stopRecording();
      } else if (data.type === "stopped") {
        capture.stopped?.();
      }
    };
    capture.source = capture.context.createMediaStreamSource(capture.stream);
    capture.gain = capture.context.createGain();
    capture.gain.gain.value = 0;
    capture.source.connect(capture.node).connect(capture.gain).connect(capture.context.destination);
    phase = "recording";
    const started = Date.now();
    const update = () => status(`录音中 ${Math.min(60, Math.floor((Date.now() - started) / 1000))} / 60 秒，点击停止后转写。`);
    capture.timer = setInterval(update, 500);
    capture.limit = setTimeout(stopRecording, 60000);
    update(); render();
  } catch (error) {
    await dispose(capture);
    if (token !== generation) return;
    recording = null; phase = "idle";
    status(error.name === "NotAllowedError" ? "麦克风权限被拒绝，请在浏览器地址栏授权后重试。" : error.message, true);
    render();
  }
}

async function wavBlob(capture) {
  if (!capture.count) throw new Error("没有录到声音，请检查麦克风后重试。");
  const offline = new OfflineAudioContext(1, Math.ceil(capture.count * 16000 / capture.rate), 16000);
  const buffer = offline.createBuffer(1, capture.count, capture.rate);
  const channel = buffer.getChannelData(0);
  let offset = 0;
  for (const chunk of capture.chunks) { channel.set(chunk, offset); offset += chunk.length; }
  capture.chunks = [];
  const source = offline.createBufferSource();
  source.buffer = buffer; source.connect(offline.destination); source.start();
  const rendered = await offline.startRendering();
  const pcm = rendered.getChannelData(0);
  const bytes = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(bytes);
  const label = (index, text) => { for (let i = 0; i < text.length; i++) view.setUint8(index + i, text.charCodeAt(i)); };
  label(0, "RIFF"); view.setUint32(4, 36 + pcm.length * 2, true); label(8, "WAVE");
  label(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); label(36, "data"); view.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const value = Math.max(-1, Math.min(1, pcm[i]));
    view.setInt16(44 + i * 2, value < 0 ? value * 32768 : value * 32767, true);
  }
  return new Blob([bytes], {type: "audio/wav"});
}

async function stopRecording() {
  if (phase !== "recording") return;
  const capture = recording;
  const token = generation;
  phase = "transcribing";
  clearInterval(capture.timer); clearTimeout(capture.limit);
  status("正在处理录音并转写，请稍候…"); render();
  try {
    await new Promise(resolve => {
      capture.stopped = resolve;
      capture.node.port.postMessage("stop");
      setTimeout(resolve, 500);
    });
    await dispose(capture);
    if (token !== generation) return;
    const blob = await wavBlob(capture);
    if (token !== generation) return;
    recording = null;
    transcriptionId = crypto.randomUUID();
    const form = new FormData();
    form.append("file", blob, "recording.wav");
    form.append("request_id", transcriptionId);
    form.append("mode", capture.mode);
    controller = new AbortController();
    const result = await api("/audio/transcriptions", {method: "POST", body: form, signal: controller.signal});
    if (token !== generation) return;
    ui.text.value = result.text;
    status(result.simulated ? "模拟完成，下面是预设文字。请编辑后再发送。" : "识别完成，请检查和修改文字，再点击发送。");
  } catch (error) {
    if (token === generation) status(error.message, true);
  } finally {
    if (token === generation) {
      controller = null; transcriptionId = null; recording = null; phase = "idle"; render();
    }
  }
}

async function cancelRecording() {
  ++generation;
  const id = transcriptionId;
  controller?.abort(); controller = null; transcriptionId = null;
  const capture = recording; recording = null;
  await dispose(capture);
  capture?.chunks.splice(0);
  if (id) fetch(`/audio/transcriptions/${id}`, {method: "DELETE"}).catch(() => {});
  phase = "idle"; ui.text.value = ""; status("本次录音已取消。"); render();
}

function appendMessage(role, content) {
  const item = document.createElement("li");
  item.className = role;
  item.textContent = `${role === "user" ? "你" : "Agent"}\n${content}`;
  ui.history.append(item);
}

async function sendMessage() {
  const text = ui.text.value.trim();
  if (!text || sending || phase !== "idle") return;
  sending = true; status("正在等待 Agent 回复…"); render();
  try {
    if (!sessionId) {
      const session = await api("/sessions", {method: "POST"});
      sessionId = session.session_id;
      ui.session.textContent = `会话 ${sessionId}`;
    }
    pendingMessage ||= {content: text, request_id: crypto.randomUUID()};
    const result = await api(`/sessions/${sessionId}/messages`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(pendingMessage),
    });
    appendMessage("user", pendingMessage.content); appendMessage("assistant", result.reply);
    pendingMessage = null; ui.text.value = ""; status("已收到回复，可以继续录音或输入文字。");
  } catch (error) {
    if (error.http) pendingMessage = null;
    status(error.http ? error.message : "连接中断，发送结果尚未确认。可点击重试同一请求，或开启新会话。", true);
  } finally { sending = false; render(); }
}

ui.record.addEventListener("click", startRecording);
ui.stop.addEventListener("click", stopRecording);
ui.cancel.addEventListener("click", cancelRecording);
ui.send.addEventListener("click", sendMessage);
ui.text.addEventListener("input", render);
ui.new.addEventListener("click", () => {
  sessionId = null; pendingMessage = null; ui.history.replaceChildren(); ui.text.value = "";
  ui.session.textContent = "发送第一条文字后创建新会话。"; status("已准备新会话。"); render();
});
window.addEventListener("pagehide", () => {
  ++generation; controller?.abort();
  const id = transcriptionId;
  if (id) fetch(`/audio/transcriptions/${id}`, {method: "DELETE", keepalive: true}).catch(() => {});
  dispose(recording);
});
api("/audio/status").then(value => {
  ui.configuration.textContent = value.configured ? "腾讯云识别已配置，可选择真实识别。" : "腾讯云识别尚未配置，可以先使用模拟流程或直接输入文字。";
}).catch(error => { ui.configuration.textContent = error.message; });
render();
