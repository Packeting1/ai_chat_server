import os
import sys
import django
from django.core.management import execute_from_command_line


def setup_django():
    """设置Django环境"""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ai_chat_server.settings')
    django.setup()

def run_migrations():
    """运行数据库迁移"""
    print("🔨 正在创建数据库迁移...")
    execute_from_command_line(['manage.py', 'makemigrations'])
    
    print("📊 正在应用数据库迁移...")
    execute_from_command_line(['manage.py', 'migrate'])

def create_superuser():
    """创建超级用户"""
    from django.contrib.auth.models import User
    
    username = 'admin'
    email = 'admin@example.com'
    password = 'admin'
    
    if not User.objects.filter(username=username).exists():
        print(f"👤 正在创建超级用户 '{username}'...")
        User.objects.create_superuser(username=username, email=email, password=password)
        print(f"✅ 超级用户创建成功！")
        print(f"   用户名: {username}")
        print(f"   密码: {password}")
    else:
        print(f"ℹ️  超级用户 '{username}' 已存在")

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
    print("🚀 Django AI实时聊天系统初始化")
    print("=" * 50)
    
    # 设置Django环境
    setup_django()
    
    try:
        # 运行数据库迁移
        run_migrations()
        
        # 创建超级用户
        create_superuser()
        
        # 创建默认配置
        create_default_config()
        
    except Exception as e:
        print(f"\n❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main() 