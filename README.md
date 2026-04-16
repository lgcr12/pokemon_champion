# Pokemon Champion

基于 OCR 的宝可梦队伍解析与导出工具。支持截图识别、文本解析、字段校对、冲突检测，以及 PKHeX / Showdown / JSON 多格式导出。

## 功能说明

- 截图 OCR：上传队伍截图自动识别宝可梦、道具、特性、性格、努力值、招式。
- 文本解析：支持直接粘贴 OCR 文本或手工文本进行解析。
- 混合修正：截图识别后可继续手动补充文本修正结果。
- 字段锁定：重新解析时可锁定指定字段，避免覆盖已修正内容。
- 纠错编辑器：逐只编辑字段，支持“一键应用到全队”。
- 智能检测：检查 EV/IV 越界、EV 总和异常、缺失字段、可疑招式等。
- 多格式导出：一键下载 PKHeX 文本、Showdown 文本、JSON。
- 批量处理：多截图批量识别并打包 ZIP 下载。
- 历史记录：本地保存、检索、恢复、两次结果差异对比。
- 统计看板：EV 分布、性格分布、招式频次等可视化。

## 技术框架

- 前端交互：Streamlit（单页工作台 + 侧栏操作舱）
- 识别与解析：OCR + 规则解析引擎（两列队伍场景优化）
- 数据映射：本地词典 JSON（宝可梦/道具/特性/招式）
- 持久化：本地 JSON 历史存储
- 导出层：文本/结构化 JSON/批量 ZIP

## 技术栈

- Python 3.10+
- Streamlit
- PaddleOCR（通过 `main.py` 调用 OCR 识别）
- 标准库：`json`、`pathlib`、`zipfile`、`difflib`、`tempfile` 等

## 下载说明

### 1) 下载源码（推荐）

方式 A（Git）：

```bash
git clone https://github.com/lgcr12/pokemon_champion.git
cd pokemon_champion
```

方式 B（GitHub 网页）：

1. 打开仓库主页  
2. 点击 `Code`  
3. 选择 `Download ZIP`  
4. 解压后进入项目目录

### 2) 安装依赖

```bash
pip install -r requirements.txt
```

## 使用说明

### 1) 启动 UI

```bash
streamlit run app.py
```

默认地址：

- `http://127.0.0.1:8501`

### 2) 基本流程

1. 在侧栏选择输入模式：`截图 OCR / 纯文本粘贴 / 混合修正 / 批量截图`
2. 配置识别语言、球种、字段锁定等参数
3. 点击“开始生成”
4. 在结果区进行校对和编辑
5. 下载目标格式（PKHeX / Showdown / JSON / 批量 ZIP）

### 3) 命令行模式（可选）

图片识别：

```bash
python main.py --image "your_screenshot.png" --output "pkhex_sets.txt"
```

文本解析：

```bash
python main.py --text "ocr.txt" --output "pkhex_sets.txt"
```

## 项目结构

- `app.py`：Streamlit UI 与交互逻辑
- `main.py`：OCR、解析、映射、导出核心逻辑
- `history_storage.py`：本地历史记录读写
- `data/pkhex_history.json`：历史记录文件
- `simple_pokedex.json` / `ability_list.json` / `item_list.json` / `move_list.json`：词典数据

## 注意事项

- OCR 质量强依赖截图清晰度与字段完整度。
- 若出现串字段，建议使用“字段锁定 + 纠错编辑器”流程修正。
- 若端口被占用，可改端口启动：`streamlit run app.py --server.port 8502`
