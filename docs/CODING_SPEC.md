# 编码规范适用说明

来源：项目负责人提供的 `CODING_SPEC.pdf` v1.0。项目负责人本人负责审核。

## 当前代码约定

- 标识符使用英文；注释与 Google 风格文档字符串使用中文。
- 每个源文件第一行写相对于仓库根目录的路径注释，包括包初始化文件和测试。
- 模块、函数、变量使用 snake_case；类使用 PascalCase；模块私有常量使用 `_UPPER_SNAKE`。
- 显式构造函数参数加前导下划线，例如 `_robot`、`_store`；`self` 保留。自动生成的 dataclass 构造参数是数据字段，沿用公共字段名，不人为改成私有字段。
- 私有实现成员使用前导下划线；公开方法和属性保持明确的公共契约。
- 完整标注函数参数、返回值及实例属性；覆写方法使用 `@override`；可空类型使用 `T | None`。
- 短函数采用一行说明；复杂函数包含适用的 `Args`、`Returns`、`Raises`。摘要使用英文句点，兼容 Ruff 的 Google 风格标点检查。
- 复杂流程使用规范中的 Step 分段注释；行内注释解释原因。
- 模块日志通过 `logging.getLogger(__name__)` 获取，消息以 `[ClassName.method_name]` 开头。使用延迟格式化参数，避免无效日志级别下提前拼接文本。
- `main.py` 仅调用 `run_app()`。日志初始化在应用入口执行，级别由环境变量 `LOG_LEVEL` 控制。
- 控制台演示保留面向操作者的输出；运行时的生命周期信息使用日志。
- 公共模块导入时不连接设备、不创建后台任务、不修改全局代理。当前依赖通过构造函数注入，无需模块级服务单例；未来确需单例时使用懒加载。
- 凭据和部署地址使用环境变量；`.env.example` 可提交，真实环境文件和生成产物必须忽略。当前程序不会自动加载 `.env`，运行前须导出环境变量。

## 当前目录与规范分层的对应关系

当前项目是独立的 Python 动作基础层，尚未实现 FastAPI 或 Flutter。保留已约定的领域模块，按以下关系落实职责分层，未将目录整体迁移为 PDF 示例中的 `backend/core/`：

| 规范中的层 | 当前目录 |
| --- | --- |
| 业务逻辑与服务 | `runtime/`、`skills/`、`robot/`，以及预留的 `agent/`、`education/`、`perception/`、`providers/` |
| 数据持久化 | `storage/`，当前为内存实现 |
| 共享模型 | `domain/models.py`，当前为内部 dataclass |
| 通用校验工具 | `domain/validation.py` |
| 应用入口与装配 | `main.py`、`app/` |

未来 HTTP 请求与响应模型使用 Pydantic，采用 `Model`、`Request` 或 `Response` 后缀；内部 dataclass 不因该条规范而强行改成 Pydantic。FastAPI 路由与依赖注入应独立于核心服务，明确 `response_model`、HTTP 状态码和返回类型。

## 前端与接口规则

- 以 `API_CONTRACT.md` 作为 HTTP 接口路径、参数、响应和错误码的唯一真相来源。
- 接口增删改先更新合同，经项目负责人确认后实现和联调；删除前确认前端已移除调用。紧急修复当天补齐合同。
- 资源路径使用复数名词，通过 HTTP 方法表达操作。
- 统一响应为 `code`、`message`、`data`；成功为 `code=0`，失败的 `data=null`。
- 错误码按 `400xx` 参数、`401xx` 认证、`403xx` 权限、`404xx` 资源、`500xx` 内部错误分类；具体接口由合同约定。
- PDF 成功/失败示例将“用户不存在”写作 `40001`，与后续错误码表不一致。实际合同按资源不存在归入 `404xx`，不得照抄冲突示例。
- Flutter 使用 feature-first 目录，网络请求封装在客户端或服务中，Widget 不直接发请求。
- Flutter 文件使用 snake_case，类使用 PascalCase，方法及变量使用 camelCase，私有成员加下划线；避免滥用 dynamic。
- 每个文件一个主 Widget，顺序为控制器和状态、生命周期、私有方法、build；文档注释使用中文 `///`。
- 前端接口路径只在 `frontend/lib/core/constants/api_paths.dart` 定义。
- CODEOWNERS 需要审核人的真实 GitHub 用户名或团队名，且须有仓库写权限。强制审核还需要在 GitHub 分支规则中启用代码所有者审核；本地文件不能代替平台设置。

## 交付检查

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run python -m unittest discover -s tests -v
```

Ruff 包含命名与 Google 文档检查；basedpyright 要求零错误、零警告。不得通过忽略业务文件、测试文件或批量抑制诊断绕过问题。

分支使用 `feature/xxx`、`fix/xxx`、`refactor/xxx`，提交采用 `<type>(<scope>): <subject>`。支持 `feat`、`fix`、`refactor`、`docs`、`test`、`chore`、`perf`。主分支变更通过项目负责人审核后的合并请求完成。
