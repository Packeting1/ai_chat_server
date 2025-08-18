import sys

import django
from django.core.management import execute_from_command_line


def setup_django():
    """设置Django环境"""
    django.setup()


def run_migrations():
    """运行数据库迁移"""
    print("🔨 正在创建数据库迁移...")
    execute_from_command_line(["manage.py", "makemigrations"])

    print("📊 正在应用数据库迁移...")
    execute_from_command_line(["manage.py", "migrate"])


def create_superuser():
    """创建超级用户"""
    from django.contrib.auth.models import User

    username = "admin"
    email = "admin@example.com"
    password = "admin"

    if not User.objects.exists():
        print(f"👤 正在创建超级用户 '{username}'...")
        User.objects.create_superuser(username=username, email=email, password=password)
        print("✅ 超级用户创建成功！")
        print(f"   用户名: {username}")
        print(f"   密码: {password}")


def create_default_config():
    """创建默认系统配置"""
    from app.models import SystemConfig

    config, created = SystemConfig.objects.get_or_create(pk=1)
    if created:
        print("⚙️  已创建默认系统配置")
    else:
        print("ℹ️  系统配置已存在")

    return config


def main():
    """主函数"""

    try:
        setup_django()
        run_migrations()
        create_superuser()
        create_default_config()

    except Exception as e:
        print(f"\n❌ 初始化失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
