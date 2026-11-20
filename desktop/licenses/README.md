# 1. App 许可文件

`npm run app` 通过 `scripts/bundle_sidecar.py`，使用同一构建 Python 执行 `scripts/collect_licenses.py`。开发启动和普通 `npm run build` 不运行收集器。

收集器保留项目声明、固定版本的阅读器组件、Python 依赖与 PyInstaller / CPython 原文、已安装 npm 生产依赖，以及 `cargo metadata --locked --filter-platform` 解析出的 Rust 依赖原文。`manifest.json` 记录版本、来源、锁文件及每份声明的 SHA-256。部分构建依赖会作为保守超集保留。

- 构建目录：`desktop/build/licenses/`。
- App 目录：`LinkRead.app/Contents/Resources/licenses/`。
- onefile sidecar 同时嵌入 `licenses/`，离开 App 复制该文件时仍保留声明。
- `scripts/vendor_licenses.json` 绑定当前浏览器 bundle 的实际版本与哈希；更新 bundle 时须同步核对来源和许可。

这些文件不替代对原生动态库、源码提供义务及发行条款的审查。FFmpeg 仍由用户安装，未被本脚本打包。

# 2. 缺失原文时

缺少许可正文、上游声明的文件、依赖安装或版本对应关系时，App 打包中止，并报告具体组件。应先重新安装准确的上游包；如果上游发布包遗漏正文，可将准确版本的**未修改上游许可和版权声明**放入：

```text
desktop/licenses/overrides/<ecosystem>/<name>/<version>/
```

`ecosystem` 为 `python`、`npm`、`rust` 或 `runtime`。名称中不属于字母、数字、点、下划线、连字符的字符用下划线替换，例如 `@tauri-apps/api` 对应 `_tauri-apps_api`。保留上游声明的相对路径，同时提供 `SOURCE.json`，记录准确版本和用于取得原文的 HTTPS 来源地址：

```json
{
  "version": "该依赖的准确版本",
  "source": "对应版本的上游许可文件或源码归档 HTTPS 地址"
}
```

仅有 SPDX 名称、`NOTICE`、`AUTHORS` 或来源记录不能代替许可正文。不要填空白占位文件或自行改写上游许可。

# 3. 本地验证

在仓库根目录执行：

```bash
.venv/bin/python -m unittest discover -s desktop/tests -p 'test_license_collection.py'
```

完整收集需要目标平台的 Python 依赖、`npm ci` 结果和 Rust 工具链。构建入口根据 Python 架构选择 macOS target；`npm run app` 中收集失败会停止后续打包。
