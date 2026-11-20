# 1. CLI 与桌面应用分别授权

| 项目 | 许可与维护方式 |
| --- | --- |
| [link-read](https://github.com/LjyYano/link-read) CLI | GPL-3.0-only，免费提供，无 CLA，贡献者保留版权 |
| 本仓库的桌面应用、自带 Python 后端及自有资源 | [LinkRead Desktop Source License](../LICENSE)，独立私有维护，计划商业发行 |
| 第三方代码与依赖 | 各自的上游许可，见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) |

该模式参考 [Mole 的公开贡献说明](https://github.com/tw93/Mole/blob/3fd0005ed71ce6df1a9e837a1b8f493dbad242f6/CONTRIBUTING.md#licensing)。Mole 的私有 App 源码许可不公开，本仓库使用自己的专有条款，不声称逐字复制了其商业合同。

# 2. 独立运行与历史来源

桌面应用自带 `linkread_desktop` Python 后端，不要求安装 CLI 的 `linkread` 包，不执行 `linkread` 命令，也不需要相邻 CLI 仓库。开发与构建只使用本仓库内的代码和声明的第三方依赖。

本项目从维护者既有的 `link-read` 快照 `a2297a5a7fe98d50435ce6bed1449ffca92e2aca` 迁移，**有共同代码历史**。独立仓库和改包名本身不产生新的版权授权。本次专有版本仅适用于维护者拥有版权或已获充分授权的材料，不将以后社区的 GPL 贡献自动纳入。

早期 `c344033ef61e2a570529afc0b37c1bb3b2449933` 等版本使用 Apache-2.0，文本保留在 [LICENSES/Apache-2.0.txt](../LICENSES/Apache-2.0.txt)。`a2297a5` 使用过按组件许可及 GPL 输出例外。此前已合法获得的权利不会因迁移撤销；本次也不宣称这些私有代码已经对外发行。

# 3. 贡献与生成内容

本仓库无 CLA，不默认接受外部源码贡献。未来引入贡献须核对现有许可或取得充分授权，贡献者版权不因 PR 自动转移，详见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

专有源码许可允许个人非商业评估和已授权的项目开发；正式使用与再分发需要相应商业授权。用户正常生成的阅读网页和 Markdown 可以按许可使用和分享，第三方组件与文章、字幕、图片的原有权利独立保留。这不是对整个应用源码的开放许可。

# 4. 商业发布边界

仓库保持私有。支付、激活、订阅及最终用户购买条款尚未实现；源代码许可不代表已完成商业发行。

发行时以实际构建产物的依赖和许可证清单为准，履行所需声明及源码提供义务。PyInstaller 的商业打包例外不免除其他依赖义务；yt-dlp 的 Python wheel 与其官方预编译二进制不可混同；FFmpeg 当前由用户安装，不随 App 分发。平台签名、公证和实机验收另见 [迁移与验证记录](migration.md)。

# 5. 参考资料

- [GNU：权利人可以提供多种许可](https://www.gnu.org/licenses/gpl-faq.en.html#ReleaseUnderGPLAndNF)
- [Apache-2.0 正文](https://www.apache.org/licenses/LICENSE-2.0)
- [Mole 的许可及独立 App 说明](https://github.com/tw93/Mole/blob/3fd0005ed71ce6df1a9e837a1b8f493dbad242f6/CONTRIBUTING.md#licensing)
