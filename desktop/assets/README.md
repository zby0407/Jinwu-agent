# 品牌资源

- `logo-source.jpg`：项目方提供的金乌标识原图。
- `icon.png`：1024×1024 应用图标母版（圆角、透明边角）。
- `icon.icns`：macOS 图标，由 `icon.png` 经 `iconutil` 生成。
- `icon.ico`：Windows 多尺寸图标，由 `icon.png` 生成。
- `../electron/ui/assets/logo.png`：配置向导/启动页使用的 512×512 圆形标识。

重新生成图标时保持圆形日食主体、透明边角和 22% 圆角半径，避免把原图右下角的黑色导出区域带进成品。
