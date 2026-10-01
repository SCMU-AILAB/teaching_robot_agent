# 开发约定

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
