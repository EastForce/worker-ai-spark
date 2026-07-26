# 脚本（scripts）

> 本目录存放项目使用的脚本。当前仅依赖 Python 标准库，不引入第三方包。

---

## validate_benchmarks.py

校验 `../benchmarks/first-batch.zh-CN.jsonl` 的基本结构与内容。

**依赖**：仅 Python 标准库（无需 `pip install`）。

**运行**：

```bash
python validate_benchmarks.py
```

也可显式指定文件路径：

```bash
python validate_benchmarks.py ../benchmarks/first-batch.zh-CN.jsonl
```

**检查项**：

- JSONL 每行可解析；
- 正好包含 24 道首批题目；
- ID 唯一且格式为 `WAI-001` … `WAI-024`；
- 必填字段齐全；
- 数组字段（`observation_points`、`severe_deductions`、`follow_up`）类型正确且非空；
- `language` 为 `zh-CN`；
- `status` 为允许值（`draft` / `review` / `stable`）；
- 标题、情境、问题均不为空。

**输出**：

- 成功：`Benchmark validation passed.` / `Total cases: 24` / `Errors: 0`；
- 失败：输出具体行号、题目 ID、原因，并以非零退出码结束。

该脚本也由 `.github/workflows/validate-benchmarks.yml` 在每次 push 与 Pull Request 时自动运行。
