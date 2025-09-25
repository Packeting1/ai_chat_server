"""
StreamChatConsumer迁移脚本
安全地从V1迁移到V2版本
"""
import os
import shutil
from datetime import datetime

def backup_original_consumer():
    """备份原始consumers.py文件"""
    backup_name = f"consumers_v1_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
    backup_path = os.path.join(os.path.dirname(__file__), backup_name)
    
    original_path = os.path.join(os.path.dirname(__file__), "consumers.py")
    
    if os.path.exists(original_path):
        shutil.copy2(original_path, backup_path)
        print(f"✅ 原始consumers.py已备份到: {backup_name}")
        return backup_path
    else:
        print("❌ 未找到原始consumers.py文件")
        return None

def create_migration_consumer():
    """创建迁移版本的consumers.py，可以在V1和V2之间切换"""
    migration_content = '''"""
StreamChatConsumer迁移版本
支持在V1和V2之间切换
"""
import os

# 通过环境变量控制使用哪个版本
USE_CONSUMER_V2 = os.environ.get('USE_CONSUMER_V2', 'false').lower() == 'true'

if USE_CONSUMER_V2:
    print("🔄 使用StreamChatConsumer V2 (重构版本)")
    from .stream_consumer_v2 import StreamChatConsumerV2 as StreamChatConsumer
    from .consumers_original import UploadConsumer
else:
    print("🔄 使用StreamChatConsumer V1 (原始版本)")
    from .consumers_original import StreamChatConsumer, UploadConsumer

# 导出类供routing使用
__all__ = ['StreamChatConsumer', 'UploadConsumer']
'''
    
    consumers_path = os.path.join(os.path.dirname(__file__), "consumers.py")
    
    with open(consumers_path, 'w', encoding='utf-8') as f:
        f.write(migration_content)
    
    print("✅ 迁移版本consumers.py已创建")

def rename_original_consumer():
    """重命名原始consumers.py为consumers_original.py"""
    original_path = os.path.join(os.path.dirname(__file__), "consumers.py")
    new_path = os.path.join(os.path.dirname(__file__), "consumers_original.py")
    
    if os.path.exists(original_path) and not os.path.exists(new_path):
        os.rename(original_path, new_path)
        print("✅ 原始consumers.py已重命名为consumers_original.py")
        return True
    else:
        print("⚠️ consumers_original.py已存在或原文件不存在")
        return False

def migrate_to_v2():
    """执行完整的迁移流程"""
    print("🚀 开始StreamChatConsumer迁移流程...")
    print("=" * 50)
    
    # 1. 备份原始文件
    print("1. 备份原始文件...")
    backup_path = backup_original_consumer()
    
    if not backup_path:
        print("❌ 迁移失败：无法备份原始文件")
        return False
    
    # 2. 重命名原始文件
    print("\\n2. 重命名原始文件...")
    if not rename_original_consumer():
        print("❌ 迁移失败：无法重命名原始文件")
        return False
    
    # 3. 创建迁移版本
    print("\\n3. 创建迁移版本...")
    create_migration_consumer()
    
    print("\\n" + "=" * 50)
    print("✅ 迁移完成！")
    print("\\n📋 使用说明:")
    print("- 默认使用V1版本（原始版本）")
    print("- 要切换到V2版本，设置环境变量: export USE_CONSUMER_V2=true")
    print("- 要切回V1版本，设置环境变量: export USE_CONSUMER_V2=false")
    print("\\n🔧 测试建议:")
    print("1. 先在开发环境测试V2版本")
    print("2. 确认所有功能正常后再在生产环境切换")
    print("3. 如有问题可随时切回V1版本")
    print(f"\\n💾 备份文件位置: {os.path.basename(backup_path)}")
    
    return True

def rollback_migration():
    """回滚迁移，恢复原始状态"""
    print("🔄 开始回滚迁移...")
    
    # 查找备份文件
    backup_files = [f for f in os.listdir(os.path.dirname(__file__)) 
                   if f.startswith('consumers_v1_backup_') and f.endswith('.py')]
    
    if not backup_files:
        print("❌ 未找到备份文件，无法回滚")
        return False
    
    # 使用最新的备份文件
    latest_backup = sorted(backup_files)[-1]
    backup_path = os.path.join(os.path.dirname(__file__), latest_backup)
    consumers_path = os.path.join(os.path.dirname(__file__), "consumers.py")
    
    # 恢复原始文件
    shutil.copy2(backup_path, consumers_path)
    
    print(f"✅ 已从备份文件 {latest_backup} 恢复原始consumers.py")
    print("🔄 迁移已回滚，系统恢复到原始状态")
    
    return True

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "rollback":
        rollback_migration()
    else:
        migrate_to_v2()
