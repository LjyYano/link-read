# 1. 私有开发范围

本仓库维护 LinkRead 桌面应用及其自带的 Python 后端，采用[专有源码许可](LICENSE)。CLI 在独立的 [link-read](https://github.com/LjyYano/link-read) 仓库采用 GPL-3.0-only；两个项目分别构建与维护。

# 2. 不使用 CLA

当前由维护者及已明确取得开发授权的协作者维护。本仓库欢迎问题反馈和建议，但**不默认接受外部源码贡献，也不要求签署新的 CLA**。

如需引入其他作者的代码，应先确认其现有许可允许本产品的专有分发，或取得单独的充分授权，并保留署名与许可。贡献者保留版权；提交 PR、获得私库访问权限或反馈建议均不自动转让著作权或授予额外商业再许可。

不得从 GPL CLI 仓库直接同步新的社区代码，再仅靠改名或替换 LICENSE 将其用于闭源 GUI。源代码可见、独立仓库、不同语言和子进程调用均不能替代所需权利。来源记录见 [docs/licensing.md](docs/licensing.md)。

# 3. 本地开发与验证

按 [README.md](README.md) 创建本仓库自己的虚拟环境，不依赖相邻的 CLI checkout 或已安装的 `linkread` 包。

```bash
.venv/bin/python -m pytest -q
cd desktop
npm ci
npm run build
node --test tests/*.test.mjs
```

遵守 [AGENTS.md](AGENTS.md)，保留原始字幕、阅读状态和隐私边界。修改 Parser、Extractor、Schema 或跨语言协议时补充对应离线测试。付费模型与 ASR 验证必须明确区分本地模拟和真实调用。

# 4. 提交规则

提交前说明行为变更、验证结果与未验证范围。不得提交 API Key、Cookie、个人阅读资料、音频缓存、签名私钥或构建产物。

不创建、恢复、触发或重跑 GitHub Actions。提交与合并消息保留 `[skip ci]`。在支持的平台本地完成 App 构建、签名、公证和安装验证；其他平台的通过结果不冒充 macOS 发布验收。
