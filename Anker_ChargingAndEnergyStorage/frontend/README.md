# 电击小子 · 家庭能源工作台

此目录包含当前 React/Three.js 工作台与同源 Python 适配器。中央家庭能源场景、右侧 Agent 和数据使用同一帧；预计、已回读与独立历史实验分开显示。网页和飞书复用同一用户范围、持久记忆及确认流程。

这是源码基线。它包含环境机只读接入候选，但没有部署该候选或完成环境机连通验收。远端控制保持关闭。

## 构建与只读历史预览

在仓库根目录准备 Python 3.12 环境并安装 `../requirements.txt` 所列依赖；该文件的实际仓库路径为 `Anker_ChargingAndEnergyStorage/requirements.txt`。前端适配器顶层导入本地 solver，因此启动也需要 numpy、scipy 和 onnxruntime，不能当作仅标准库应用。

```powershell
python -m pip install -r Anker_ChargingAndEnergyStorage/requirements.txt
cd Anker_ChargingAndEnergyStorage/frontend
npm ci
npm run build
python server.py --port 19388
```

历史工作台入口为 `http://127.0.0.1:19388/?view=professional`，冻结实验入口为 `?view=history`。发布包含 report-4 三站 XML、一套明确标注的 mock CSV 和冻结 FV JSON。它们是历史／仿真输入，不能视为当前设备或真实到账收益。

没有 Gateway、用户配对及私有本机 HA 安装时，家庭 Agent 页面会显示连接缺失。构建或历史预览不会自动启动 Agent、HA、环境机或控制循环。

## Agent 与环境边界

- 既有本机单家庭闭环使用 `local_debug1_sim`；保存偏好与执行首 15 分钟分别确认，未知结果沿原 episode 核对。
- 环境机数据只在设置抽屉主动读取，固定同源 `GET /api/assistant/environment`；来源为 `release_official_ha_unverified`，不替换本机 HUD 或执行卡。
- 环境机配置示例位于 `../energy_runtime/release-pipeline.example.json`。实际配置只放当前用户私有目录；此仓库不包含任何真实认证信息。
- 远端控制、单槽退出、下一 tick 回读和结算契约尚未放行。不能直接运行队友三站 main 或 `stack.ps1 Start` 来替代 Agent 单槽确认。

本机 HA 实控依赖未发布的个人安装、官方插件、实体映射和 webhook。此源码不能宣称 clone 后即可复现 HA 实控。模型解释服务需用户自行提供私有配置，本基线未调用任何模型 API。

## 开发与验证

```powershell
npm run dev
npm run build
```

React / TypeScript / Vite / Motion / Three.js 使用已提交的 `package-lock.json`。本地字体随构建打包；组件来源见 `THIRD_PARTY_NOTICES.md` 和 `licenses/`。

本次公开发布检查见 [`docs/delivery/2026-10-17/github-baseline`](../../docs/delivery/2026-10-17/github-baseline/README.md)。不包含个人资料、运行数据库、原始控制收据、训练全集或当前 served dist。
