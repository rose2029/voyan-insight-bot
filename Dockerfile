FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（lxml 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir flask

# 复制项目代码
COPY . .

# 暴露端口
EXPOSE 5000

# 启动 Web 服务
CMD ["python", "server.py"]
