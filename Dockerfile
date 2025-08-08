FROM python:3.13-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=ai_chat_server.settings

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    libasound2-dev \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*


# 复制requirements.txt安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 创建必要的目录并设置权限
RUN mkdir -p staticfiles && \
    mkdir -p /app/data && \
    chmod 755 /app && \
    chmod 755 /app/data

# 暴露端口
EXPOSE 8000
EXPOSE 8443

# 创建启动脚本到系统目录（不会被挂载覆盖）
RUN echo '#!/bin/bash\n\
cd /app\n\
echo "🔄 检查和修复数据库目录权限..."\n\
mkdir -p /app/data\n\
chown -R root:root /app/data\n\
chmod -R 755 /app/data\n\
echo "🗑️ 清理可能存在的错误db.sqlite3目录..."\n\
if [ -d "/app/db.sqlite3" ]; then\n\
    echo "发现db.sqlite3目录，正在删除..."\n\
    rm -rf /app/db.sqlite3\n\
fi\n\
echo "📋 应用目录信息:"\n\
ls -la /app/\n\
echo "📋 数据库目录信息:"\n\
ls -la /app/data/\n\
echo "🧪 测试数据库文件创建权限..."\n\
touch /app/data/test.txt && rm -f /app/data/test.txt && echo "✅ 写入权限正常" || echo "❌ 写入权限异常"\n\
echo "🧹 清理静态文件目录..."\n\
rm -rf /app/staticfiles/*\n\
echo "📁 收集静态文件..."\n\
python manage.py collectstatic --noinput --clear\n\
echo "🔄 正在执行数据库迁移..."\n\
echo "🔧 创建数据库迁移文件..."\n\
python manage.py makemigrations\n\
echo "📊 应用数据库迁移..."\n\
python manage.py migrate\n\
echo "👤 创建超级用户..."\n\
echo "from django.contrib.auth.models import User; User.objects.filter(username='"'"'admin'"'"').exists() or User.objects.create_superuser('"'"'admin'"'"', '"'"'admin@example.com'"'"', '"'"'admin'"'"')" | python manage.py shell\n\
echo "✅ 数据库初始化完成"\n\
echo "🚀 启动Django ASGI服务器..."\n\
exec python run_asgi.py' > /usr/local/bin/start.sh && \
    chmod +x /usr/local/bin/start.sh

# 启动命令
CMD ["/usr/local/bin/start.sh"] 