# 豆瓣图书画廊脚本

该仓库提供了一个简单的 Python 脚本，可以批量导入书名并自动从豆瓣抓取书籍的标题、作者、评分、简介、封面等信息，最终生成一个画廊式的 HTML 页面展示结果。

## 环境准备

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 使用方法

1. **准备输入文件**：
   - `.txt`：每行一个书名。
   - `.csv`：默认读取第一列作为书名。
   - `.json`：可以是书名字符串列表，也可以是包含 `title` 字段的对象列表。

2. **运行脚本**：

```bash
python douban_gallery.py --input books.txt --output gallery.html --title "我的书架"
```

参数说明：
- `--input`：必填，指向包含书名的文件。
- `--output`：可选，指定生成的 HTML 文件，默认 `book_gallery.html`。
- `--title`：可选，设置页面标题。
- `--delay`：可选，请求间隔（秒），默认 `2.0`，用于控制抓取节奏。
- `--retries`：可选，失败重试次数，默认 `2`。

脚本运行后，会在当前目录生成指定的 HTML 文件，打开即可看到画廊式展示的书籍信息。

> **提示**：豆瓣网页结构可能变化，请求频率也请适当控制以免被限制。本脚本仅用于学习交流。
