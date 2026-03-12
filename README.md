# survivalStory

末日废墟生存战（AI 对抗版）V1 项目代码仓库。

## 项目管理（uv）

- 创建环境并安装开发依赖：`uv sync --group dev`
- 运行单元测试（当前）：`uv run python -m unittest discover -s tests -p 'test_*.py'`
- 运行 pytest（可选）：`uv run pytest`
- 运行 ruff（可选）：`uv run ruff check .`

## 阶段计划

当前实现进度按阶段推进，详见：
- `docs/v1/实施计划.md`
- `docs/v1/技术选型清单.md`
