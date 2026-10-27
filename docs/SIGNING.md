# 代码签名 / Code signing

没有签名的 exe 会被 Windows SmartScreen 拦下（“Windows 已保护你的电脑”），一些杀毒软件也会误报。
开源项目可以通过 **SignPath Foundation** 免费获得代码签名证书。

## 步骤

1. 先把项目公开发布到 GitHub，并至少发布一个版本（SignPath 要求项目公开、有实际用户）。
2. 在 <https://signpath.org/apply> 提交申请，填写项目地址。他们会检查：
   - 开源许可证（我们是 MIT）；
   - 构建是在 CI 里自动完成的（我们的 `.github/workflows/release.yml`）；
   - 项目有隐私说明、不含恶意行为（见 README 的“安全”部分）。
3. 通过后，在 SignPath 网站上创建：
   - 项目，slug 填 `opensweep`；
   - 签名策略，slug 填 `release-signing`；
   - 关联 GitHub 仓库（Trusted Build System）。
4. 在 GitHub 仓库 Settings → Secrets and variables → Actions 里添加：
   - Secret `SIGNPATH_API_TOKEN`：SignPath 里生成的 API Token；
   - Variable `SIGNPATH_ORGANIZATION_ID`：SignPath 的组织 ID。
5. 之后推送 `v*` 标签，Release 流程会自动把 exe 送去签名，再打包安装程序。

> 注意：SignPath 免费证书初期的 SmartScreen 信誉为零，刚开始仍可能出现提示，下载量上来后会逐渐消失。
> 想立刻消除提示需要购买 EV 证书（每年数百美元），开源项目一般不需要。

## Steps (English)

1. Publish the project on GitHub with at least one release.
2. Apply at <https://signpath.org/apply>.
3. In SignPath create project `opensweep`, signing policy `release-signing`, and link the GitHub repository.
4. Add the `SIGNPATH_API_TOKEN` secret and the `SIGNPATH_ORGANIZATION_ID` variable to the repository.
5. Push a `v*` tag; the release workflow signs the executables before building the installer.
