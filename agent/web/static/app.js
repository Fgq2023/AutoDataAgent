"use strict";
const $ = (id) => document.getElementById(id);
const labels = {queued:"排队中",running:"执行中",cancelling:"正在取消",cancelled:"已取消",finished:"已完成",
  finished_with_failures:"部分失败",failed:"运行失败",interrupted:"执行被中断",awaiting_clarification:"等待确认",
  pending:"待执行",succeeded:"成功",recovered:"恢复成功",skipped:"已跳过"};
const activeStates = new Set(["queued","running","cancelling"]);
let activeJob = null;
let cursor = 0;
let eventHistory = [];
let clarificationKey = "";
let previewKey = "";
let timer = null;
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function message(text = "") { $("message").textContent = text; }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {"Content-Type":"application/json", ...options.headers}});
  if (!response.ok) {
    const detail = await response.json().catch(() => ({detail: response.statusText}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : JSON.stringify(detail.detail));
  }
  return response.json();
}
function selectedSources() {
  return Array.from($("sources").querySelectorAll("input:checked"), (item) => item.value);
}
async function loadSources() {
  const selected = new Set(selectedSources());
  const result = await api("/api/sources");
  $("sources").replaceChildren();
  for (const source of result.sources) {
    const label = node("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = source.path;
    input.checked = selected.has(source.path);
    label.append(input, node("span", source.path + " · " + source.kind));
    $("sources").append(label);
  }
  if (!result.sources.length) $("sources").append(node("p", "工作区暂无支持的数据文件", "muted"));
}
async function history() {
  const result = await api("/api/runs");
  $("history").replaceChildren();
  for (const job of result.runs) {
    const button = node("button", job.goal);
    button.append(node("small", (labels[job.status] || job.status) + " · " + job.job_id.slice(0, 8)));
    button.onclick = () => selectJob(job.job_id);
    $("history").append(button);
  }
}
async function selectJob(id) {
  clearTimeout(timer);
  activeJob = id;
  cursor = 0;
  eventHistory = [];
  previewKey = "";
  clarificationKey = "";
  $("preview").replaceChildren();
  $("events").replaceChildren();
  const job = await api("/api/runs/" + id);
  $("goal").value = job.spec.goal;
  const sources = new Set(job.spec.sources);
  for (const input of $("sources").querySelectorAll("input")) input.checked = sources.has(input.value);
  const supplement = {...job.spec};
  delete supplement.goal;
  delete supplement.sources;
  $("spec").value = JSON.stringify(supplement, null, 2);
  await poll();
}
function renderClarification(job) {
  const waiting = job.status === "awaiting_clarification";
  $("clarification").hidden = !waiting;
  const key = JSON.stringify([job.job_id, job.questions, waiting]);
  if (key === clarificationKey) return;
  clarificationKey = key;
  $("questions").replaceChildren();
  if (!waiting) return;
  for (const [id, question] of Object.entries(job.questions)) {
    const label = node("label", question);
    if (id === "sources" && !(id in job.spec.questions)) {
      label.append(node("p", "请在左侧勾选数据文件，再确认继续。", "muted"));
    } else {
      const input = document.createElement("input");
      input.type = "text";
      input.required = true;
      input.dataset.question = id;
      input.value = job.spec.answers[id] || "";
      input.setAttribute("aria-label", question);
      label.append(input);
    }
    $("questions").append(label);
  }
}
function renderSteps(job) {
  $("steps").replaceChildren();
  const states = new Map(job.steps.map((step) => [step.step_id, step]));
  for (const step of job.plan.steps || []) {
    const state = states.get(step.step_id) || {};
    const row = node("tr");
    const title = node("td", step.step_id + " · " + step.tool);
    title.append(node("small", step.description || ""));
    row.append(title, node("td", (step.depends_on || []).join(", ") || "—"),
      node("td", labels[state.status] || state.status || "待执行"), node("td", String(state.attempts || 0)));
    if (state.error) row.title = state.error;
    $("steps").append(row);
  }
  if (!$("steps").children.length) {
    const row = node("tr");
    const cell = node("td", job.status === "running" ? "正在检查数据与规划任务…" : "尚无执行计划", "empty");
    cell.colSpan = 4;
    row.append(cell);
    $("steps").append(row);
  }
}
async function previewArtifact(jobId, artifact) {
  const key = jobId + "/" + artifact.artifact_id;
  previewKey = key;
  const data = await api("/api/runs/" + jobId + "/artifacts/" + artifact.artifact_id + "/preview");
  if (previewKey !== key) return;
  $("preview").replaceChildren(node("h3", artifact.name));
  const url = "/api/runs/" + jobId + "/artifacts/" + artifact.artifact_id;
  if (data.kind === "figure") {
    const image = document.createElement("img");
    image.src = url;
    image.alt = artifact.name;
    $("preview").append(image);
  } else if (data.kind === "table") {
    const table = document.createElement("table");
    const header = node("tr");
    for (const column of data.columns) header.append(node("th", column));
    table.append(header);
    for (const values of data.rows) {
      const row = node("tr");
      for (const value of values) row.append(node("td", value));
      table.append(row);
    }
    $("preview").append(table);
  } else {
    $("preview").append(node("pre", data.content));
  }
  if (data.truncated) $("preview").append(node("p", "预览已截断，可下载完整文件。", "muted small"));
  const download = node("a", "下载完整产物", "button");
  download.href = url;
  download.download = artifact.name;
  $("preview").append(download);
}
function renderArtifacts(job) {
  $("artifacts").replaceChildren();
  for (const artifact of job.artifacts) {
    const button = node("button", artifact.name + " · " + (artifact.verified ? artifact.kind : artifact.valid ? "待验证" : "已失效"));
    button.disabled = !artifact.verified;
    button.onclick = () => previewArtifact(job.job_id, artifact).catch((error) => message(error.message));
    $("artifacts").append(button);
  }
  if (!job.artifacts.length) $("artifacts").append(node("p", "尚无产物", "muted"));
}
const eventNames = {verification:"验证检查",failure_feedback:"错误反馈",parameter_repair:"参数修复",
  tool_replaced:"替换工具",replan_started:"开始重规划",retry_started:"工具重试",run_resumed:"从检查点恢复",
  run_stopped:"运行停止",model_request_failed:"模型请求失败",model_transport_retry:"模型传输重试",
  step_finished:"步骤结束",run_finished:"运行结束",repair_rejected:"修复被拒绝",replan:"重规划"};
function renderEvents() {
  $("events").replaceChildren();
  for (const event of eventHistory.filter((item) => item.event in eventNames)) {
    const element = node("div", undefined, "event");
    element.append(node("strong", eventNames[event.event] + " · " + (event.payload.step_id || "") + " · " + event.ts),
      node("pre", JSON.stringify(event.payload, null, 2)));
    $("events").append(element);
  }
}
async function poll() {
  const id = activeJob;
  if (!id) return;
  try {
    const [job, events] = await Promise.all([api("/api/runs/" + id), api("/api/runs/" + id + "/events?after=" + cursor)]);
    if (id !== activeJob) return;
    cursor = events.cursor;
    eventHistory = [...eventHistory, ...events.events].slice(-1000);
    $("run-title").textContent = "任务 " + id.slice(0, 8);
    $("run-goal").textContent = job.spec.goal;
    $("run-status").textContent = labels[job.status] || job.status;
    $("cancel").disabled = !(activeStates.has(job.status) || job.status === "awaiting_clarification");
    $("resume").disabled = !job.run_dir || !["interrupted","cancelled","failed","finished_with_failures"].includes(job.status);
    $("start").disabled = activeStates.has(job.status);
    $("export").classList.toggle("disabled", !job.run_dir || activeStates.has(job.status));
    $("export").href = "/api/runs/" + id + "/export";
    $("budget").textContent = "累计运行时间 " + Number(job.budget.elapsed || 0).toFixed(1) +
      " 秒 · 模型请求 " + job.budget.model_requests + " 次 · 模式 " + job.config.mode +
      " · Python " + job.config.sandbox_backend;
    if (job.error) message(job.error);
    renderClarification(job);
    renderSteps(job);
    renderArtifacts(job);
    renderEvents();
    if (activeStates.has(job.status)) timer = setTimeout(poll, 1000);
    else await history();
  } catch (error) {
    message(error.message);
    timer = setTimeout(poll, 2500);
  }
}
$("start-form").onsubmit = async (event) => {
  event.preventDefault();
  message();
  try {
    const supplement = $("spec").value.trim() ? JSON.parse($("spec").value) : {};
    if (!supplement || Array.isArray(supplement) || typeof supplement !== "object") throw new Error("TaskSpec 必须是 JSON 对象");
    const job = await api("/api/runs", {method:"POST", body:JSON.stringify({
      spec:{...supplement, goal:$("goal").value, sources:selectedSources()},
      mode:$("mode").value, sandbox_backend:$("backend").value, inject_failure:$("injection").value || null})});
    await selectJob(job.job_id);
    await history();
  } catch (error) { message(error.message); }
};
$("clarify-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const answers = {};
    for (const input of $("questions").querySelectorAll("input")) answers[input.dataset.question] = input.value;
    await api("/api/runs/" + activeJob + "/clarify", {method:"POST", body:JSON.stringify({answers,sources:selectedSources()})});
    message();
    await poll();
  } catch (error) { message(error.message); }
};
for (const action of ["cancel","resume"]) {
  $(action).onclick = async () => {
    try {
      await api("/api/runs/" + activeJob + "/" + action, {method:"POST"});
      message();
      clearTimeout(timer);
      await poll();
    } catch (error) { message(error.message); }
  };
}
$("mode").onchange = () => {
  $("injection").disabled = $("mode").value === "llm";
  if ($("injection").disabled) $("injection").value = "";
};
$("refresh-sources").onclick = () => loadSources().catch((error) => message(error.message));
$("refresh-history").onclick = () => history().catch((error) => message(error.message));
(async () => {
  try {
    const config = await api("/api/config");
    $("data-root").textContent = config.data_root;
    $("model-info").textContent = config.live_configured ? "已配置真实模型 · " + config.model : "真实模型尚未配置，固定演示可直接运行。";
    $("mode").querySelector('[value="llm"]').disabled = !config.live_configured;
    $("mode").value = config.live_configured ? config.mode : "mock";
    $("backend").value = config.sandbox_backend;
    $("mode").onchange();
    await Promise.all([loadSources(), history()]);
  } catch (error) { message(error.message); }
})();
