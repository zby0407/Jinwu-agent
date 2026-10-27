"use strict";

const api = window.jinwuSetup;
if (!api) {
  document.getElementById("error").textContent = "桌面配置接口不可用，请重新安装金乌。";
  document.getElementById("error").classList.remove("hidden");
  document.getElementById("submit").disabled = true;
} else {
  init().catch((error) => showError(error?.message || String(error)));
}

const elements = {
  form: document.getElementById("setup-form"),
  provider: document.getElementById("provider"),
  model: document.getElementById("model"),
  apiKey: document.getElementById("api-key"),
  baseUrl: document.getElementById("base-url"),
  apiKeyField: document.getElementById("api-key-field"),
  baseUrlField: document.getElementById("base-url-field"),
  error: document.getElementById("error"),
  success: document.getElementById("success"),
  submit: document.getElementById("submit"),
  cancel: document.getElementById("cancel"),
  configPath: document.getElementById("config-path"),
};

let schema = [];

async function init() {
  const status = await api.getStatus();
  if (!status || status.ok !== true) {
    throw new Error(status?.error || "无法读取当前配置状态");
  }
  schema = Array.isArray(status.schema) ? status.schema : [];
  if (schema.length === 0) {
    throw new Error("安装包中的供应商配置缺失，请重新安装金乌。");
  }
  elements.provider.replaceChildren(
    ...schema.map((spec) => new Option(spec.label, spec.id)),
  );
  if (status.provider && schema.some((spec) => spec.id === status.provider)) {
    elements.provider.value = status.provider;
  }
  if (status.configPath) {
    elements.configPath.textContent = `配置文件：${status.configPath}`;
  }
  applyProviderDefaults();
  elements.provider.addEventListener("change", applyProviderDefaults);
  elements.form.addEventListener("submit", onSubmit);
  elements.cancel.addEventListener("click", () => {
    void api.cancel();
  });
  elements.apiKey.focus();
}

function currentSpec() {
  return schema.find((spec) => spec.id === elements.provider.value) || schema[0];
}

function applyProviderDefaults() {
  const spec = currentSpec();
  if (!spec) return;
  elements.model.value = spec.defaultModel || "";
  elements.apiKey.value = "";
  elements.apiKey.placeholder = spec.placeholder || "";
  elements.apiKeyField.classList.toggle("hidden", !spec.requiresApiKey);
  elements.apiKey.required = Boolean(spec.requiresApiKey);
  elements.baseUrlField.classList.toggle("hidden", !spec.requiresBaseUrl);
  elements.baseUrl.required = Boolean(spec.requiresBaseUrl);
  elements.baseUrl.value = "";
  hideMessages();
}

async function onSubmit(event) {
  event.preventDefault();
  hideMessages();
  const spec = currentSpec();
  const model = elements.model.value.trim();
  const apiKey = elements.apiKey.value.trim();
  const baseUrl = elements.baseUrl.value.trim();

  if (!spec) return showError("请选择模型供应商。");
  if (spec.requiresApiKey && !apiKey) return showError("请输入 API Key。");
  if (spec.requiresBaseUrl && !baseUrl) return showError("请输入 Base URL。");
  if (baseUrl) {
    try {
      const parsed = new URL(baseUrl);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        throw new Error("scheme");
      }
    } catch {
      return showError("Base URL 必须是有效的 http(s) 地址。");
    }
  }

  setBusy(true);
  try {
    const result = await api.save({
      provider: spec.id,
      model,
      api_key: apiKey,
      base_url: baseUrl,
    });
    if (!result || result.ok !== true) {
      throw new Error(result?.error || "保存配置失败");
    }
    elements.success.classList.remove("hidden");
    elements.submit.textContent = "正在启动…";
  } catch (error) {
    setBusy(false);
    showError(error?.message || String(error));
  }
}

function showError(message) {
  elements.error.textContent = message;
  elements.error.classList.remove("hidden");
}

function hideMessages() {
  elements.error.classList.add("hidden");
  elements.success.classList.add("hidden");
}

function setBusy(busy) {
  elements.submit.disabled = busy;
  elements.cancel.disabled = busy;
  elements.provider.disabled = busy;
  elements.model.disabled = busy;
  elements.apiKey.disabled = busy;
  elements.baseUrl.disabled = busy;
  if (!busy) elements.submit.textContent = "保存并启动";
}
