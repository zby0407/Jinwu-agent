const started = performance.now();
setInterval(() => {
  document.getElementById('elapsed').textContent = `此阶段已用时 ${Math.floor((performance.now() - started) / 1000)} 秒`;
}, 1000);
