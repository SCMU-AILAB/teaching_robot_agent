# 开发约定

- 以用户提供的 `CODING_SPEC.pdf` v1.0 为规范来源；仓库适用说明见 `docs/CODING_SPEC.md`。
- 项目负责人（用户本人）负责代码和接口合同审核。提交供审核不等于已经批准，不代替用户批准或合并。
- 每个 Python 源文件第一行必须是相对路径注释。标识符使用英文，注释和文档字符串使用中文。
- 显式构造函数的业务参数使用前导下划线；`self` 和自动生成的数据模型字段保留语言与数据契约约定。
- 文档字符串采用 Google 风格：简单函数一行，复杂函数说明参数、返回值及异常。摘要末尾用英文句点以兼容 Ruff。
- 复杂流程使用 `# ========== Step1: 中文说明 ==========` 分段；行内注释说明设计意图。
- 日志使用模块 `logger` 和 `[ClassName.method_name] 描述` 格式；日志配置仅在应用入口执行，日志不得输出凭据。
- 禁止模块导入时连接设备、启动线程或任务、修改全局代理。密钥、密码和部署地址使用环境变量。
- HTTP 接口增删改先更新 `API_CONTRACT.md`，经项目负责人确认再实现；未来 Flutter 路径统一维护在 `frontend/lib/core/constants/api_paths.dart`。
- 分支使用 `feature/`、`fix/`、`refactor/`；提交使用 `<type>(<scope>): <subject>`。主分支通过审核后的合并请求更新。

- 使用 Python 3.12+；开发工具和版本由 `pyproject.toml`、`uv.lock` 管理。
- Python 改动交付前必须通过以下检查，basedpyright 要求零错误、零警告：

  ```bash
  uv run ruff check .
  uv run ruff format --check .
  uv run basedpyright
  uv run python -m unittest discover -s tests -v
  ```

- 为函数、实例属性和覆写方法提供明确的类型标注，覆写方法使用 `@override`。
- 外部输入先进行实际校验，再收窄为具体类型；保留非法参数、超时和停止确认的检查。
- 修复诊断时不得通过降低检查级别、排除业务或测试文件、批量添加 `ignore` / `noqa` 绕过问题。
