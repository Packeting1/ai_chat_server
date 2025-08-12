#!/bin/bash
set -e

echo "📁 确保必要的目录存在..."
mkdir -p /workspace/data
mkdir -p /workspace/staticfiles
chmod -R 755 /workspace/data

echo "🧹 清理静态文件目录..."
rm -rf staticfiles/*

echo "📁 收集静态文件..."
python manage.py collectstatic --noinput --clear

echo "🚀 初始化Django应用..."
python setup_django.py

echo "🚀 启动Django ASGI服务器..."
exec python run_asgi.py