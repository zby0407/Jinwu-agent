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
  proxy: document.getElementById("proxy-mode"),
  proxyField: document.getElementById("proxy-field"),
  proxySource: document.getElementById("proxy-source"),
  test: document.getElementById("test"),
  cancelTest: document.getElementById("cancel-test"),
  testResult: document.getElementById("test-result"),
};

let schema = [];
let credentialProviders = [];

async function init() {
  const status = await api.getStatus();
  if (!status || status.ok !== true) {
    throw new Error(status?.error || "无法读取当前配置状态");
  }
  schema = Array.isArray(status.schema) ? status.schema : [];
  credentialProviders = status.credentialProviders ?? [];
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
  if (status.model) elements.model.value = status.model;
  elements.proxy.value = status.proxyMode;
  elements.proxySource.textContent = status.proxyModeOverridden
    ? `实际生效：${status.proxyMode === 'direct' ? '直连' : '跟随系统代理'}；来源：${status.proxyModeSourcePath || '环境变量 JW_DASHSCOPE_PROXY_MODE'}。此覆盖仍有效，保存表单不会移除覆盖。`
    : '来源：config.yaml（未设置时跟随系统代理）。';
  if (status.configured) {
    elements.cancel.textContent = '关闭';
    elements.submit.textContent = '保存并应用';
  }
  if (status.pending) {
    elements.success.textContent = '已有待生效设置，任务结束后应用；也会在下次启动时应用。';
    elements.success.classList.remove('hidden');
  }
  elements.test.addEventListener('click', onTest);
  elements.cancelTest.addEventListener('click', () => { void api.cancelTest(); });
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
  elements.apiKey.placeholder = credentialProviders.includes(spec.id) ? '已保存密钥，留空保留' : spec.placeholder || "";
  elements.apiKeyField.classList.toggle("hidden", !spec.requiresApiKey);
  elements.apiKey.required = Boolean(spec.requiresApiKey) && !credentialProviders.includes(spec.id);
  elements.proxyField.classList.toggle('hidden', spec.id !== 'dashscope');
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
  if (elements.apiKey.required && !apiKey) return showError("请输入 API Key。");
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
      proxy_mode: elements.proxy.value,
    });
    if (!result || result.ok !== true) {
      throw new Error(result?.error || "保存配置失败");
    }
    elements.success.classList.remove("hidden");
    elements.apiKey.value = '';
    elements.success.textContent = result.pending
      ? '配置已暂存，任务运行或排队时不会重启。空闲后自动应用，也可在下次启动时应用。'
      : '配置已保存，正在重启服务；环境变量覆盖仍然有效。';
    elements.submit.textContent = result.pending ? '等待应用' : '正在启动…';
    elements.cancel.disabled = false;
  } catch (error) {
    setBusy(false);
    showError(error?.message || String(error));
  }
}

async function onTest() {
  hideMessages();
  setBusy(true);
  elements.cancelTest.classList.remove('hidden');
  elements.testResult.textContent = '正在测试连接…';
  try {
    const result = await api.test({ provider: elements.provider.value,
      model: elements.model.value.trim(), api_key: elements.apiKey.value.trim(),
      base_url: elements.baseUrl.value.trim(), proxy_mode: elements.proxy.value });
    elements.testResult.textContent = result.code
      ? `${result.code} · ${result.route === 'direct' ? '直连' : '跟随系统代理'} · ${result.message || result.error} ${result.suggestion || ''}${Number.isFinite(result.elapsedMs) ? `（${result.elapsedMs} ms）` : ''}`
      : result.error || '测试未完成';
  } catch {
    elements.testResult.textContent = '测试未完成，请重新打开设置后再试。';
  } finally {
    elements.cancelTest.classList.add('hidden');
    setBusy(false);
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
  elements.proxy.disabled = busy;
  elements.test.disabled = busy;
  if (!busy) elements.submit.textContent = "保存并应用";
}
