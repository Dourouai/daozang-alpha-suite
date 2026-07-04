# 数据源第一步

## 推荐起点

第一步先用 `chenditc/investment_data` 的 Qlib 格式社区数据包。

原因：

- 它已经导出成 Qlib 可直接读取的目录结构。
- 它比我们从零抓取全 A 历史数据快很多。
- 它的数据源组合包含 Tushare、AKShare、Yahoo、BaoStock 等，并做了合并校验。
- Qlib 官方 README 当前也把它作为社区替代数据源示例。

## 本项目默认目录

不要一开始写到 home 目录，先放在项目内：

```text
data/qlib/cn_data
```

这样删除、重建、迁移都更清楚。

## 下载命令

```bash
cd /Users/yancy/Documents/vibe-project/daozang-alpha-suite/daozang-alpha
bash scripts/setup_chenditc_qlib_data.sh
```

脚本会：

1. 创建 `data/downloads`。
2. 下载 GitHub latest release 的 `qlib_bin.tar.gz`。
3. 解压到 `data/qlib/cn_data`。
4. 提示运行 `doctor` 检查。

## 检查命令

```bash
PYTHONPATH=src python3 -m daozang_alpha doctor
```

成功时至少应该看到：

```text
[OK] provider path
[OK] provider shape
```

如果 `pyqlib` 还没安装，`doctor` 会继续提示安装研究依赖。

安装 `pyqlib` 后，可以进一步读取样本行情：

```bash
PYTHONPATH=src .venv/bin/python -m daozang_alpha smoke-test-data
```

## LightGBM OpenMP

Apple Silicon Mac 上，LightGBM 可能报：

```text
Library not loaded: @rpath/libomp.dylib
```

如果本机没有 Homebrew，可以先复用 `scikit-learn` wheel 自带的 `libomp.dylib`：

```bash
bash scripts/fix_lightgbm_libomp.sh
```

## 风险提示

这个数据源适合个人研究和原型验证，但不能直接当成无条件可靠的数据底座。

后续必须抽样检查：

- 复权价格。
- 停牌日期。
- 涨跌停价格。
- 退市股票。
- 指数成分和股票池历史变动。
- 成交额和异常价格。

先跑通，不盲信。
